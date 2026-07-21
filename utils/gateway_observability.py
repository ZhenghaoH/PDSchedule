from __future__ import annotations

import json
import logging
import os
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LOG_DIR = PROJECT_ROOT / "gateway_logs"


def _safe_float(value: Any) -> Optional[float]:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result


def duration_ms(start_s: Any, end_s: Any) -> Optional[float]:
    start = _safe_float(start_s)
    end = _safe_float(end_s)
    if start is None or end is None:
        return None
    return max(0.0, (end - start) * 1000.0)


def resolve_gateway_log_dir(log_dir: Optional[str | Path] = None) -> Path:
    configured = log_dir or os.getenv("FLEXPD_GATEWAY_LOG_DIR")
    if not configured:
        return DEFAULT_LOG_DIR
    path = Path(configured).expanduser()
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path.resolve()


class GatewayObservability:
    """Append-only gateway schedule, timing, and resource logs."""

    def __init__(
        self,
        role: str,
        *,
        log_dir: Optional[str | Path] = None,
        resource_interval_s: Optional[float] = None,
        zero_pause_s: Optional[float] = None,
    ) -> None:
        normalized_role = str(role).strip().lower()
        if normalized_role not in {"prefill", "decode"}:
            raise ValueError(f"Unsupported gateway role: {role}")
        self.role = normalized_role
        self.log_dir = resolve_gateway_log_dir(log_dir)
        self.schedule_path = self.log_dir / f"{self.role}_gateway_schedule.log"
        self.timing_path = self.log_dir / f"{self.role}_gateway_timing.log"
        self.resource_path = self.log_dir / f"{self.role}_gateway_resource.log"
        self.resource_interval_s = max(
            0.0,
            float(
                resource_interval_s
                if resource_interval_s is not None
                else os.getenv("FLEXPD_RESOURCE_LOG_INTERVAL_SEC", "1")
            ),
        )
        self.zero_pause_s = max(
            0.0,
            float(
                zero_pause_s
                if zero_pause_s is not None
                else os.getenv("FLEXPD_RESOURCE_ZERO_PAUSE_SEC", "30")
            ),
        )
        self._write_lock = threading.RLock()
        self._resource_states: Dict[str, Dict[str, Any]] = {}
        self._last_write_warning_s = 0.0
        self._ensure_log_dir()

    def _ensure_log_dir(self) -> bool:
        try:
            self.log_dir.mkdir(parents=True, exist_ok=True)
            return True
        except Exception as exc:
            self._warn_write_failure(exc)
            return False

    def _warn_write_failure(self, exc: Exception) -> None:
        now = time.monotonic()
        if now - self._last_write_warning_s < 30.0:
            return
        self._last_write_warning_s = now
        logging.warning(
            "[GatewayObservability] Cannot write gateway logs under %s: %s",
            self.log_dir,
            exc,
        )

    @staticmethod
    def _base_record(log_type: str, role: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        record = {
            "log_type": log_type,
            "gateway_role": role,
            "logged_at": datetime.now().astimezone().isoformat(timespec="milliseconds"),
            "logged_at_epoch_s": time.time(),
        }
        record.update(payload)
        return record

    def _append(self, path: Path, record: Dict[str, Any]) -> bool:
        with self._write_lock:
            try:
                line = json.dumps(
                    record,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    default=str,
                ) + "\n"
                if not self._ensure_log_dir():
                    return False
                with path.open("a", encoding="utf-8", newline="") as stream:
                    stream.write(line)
                return True
            except Exception as exc:
                self._warn_write_failure(exc)
                return False

    def record_schedule(self, **payload: Any) -> bool:
        return self._append(
            self.schedule_path,
            self._base_record("schedule", self.role, dict(payload)),
        )

    def record_timing(self, event: str, **payload: Any) -> bool:
        timing_payload = {"event": event, **payload}
        return self._append(
            self.timing_path,
            self._base_record("timing", self.role, timing_payload),
        )

    def _resource_action(self, key: str, nonzero: bool, now_s: float) -> Optional[str]:
        state = self._resource_states.setdefault(
            key,
            {
                "zero_since_s": None,
                "paused": False,
                "last_write_s": float("-inf"),
            },
        )

        if nonzero:
            was_paused = bool(state["paused"])
            state["zero_since_s"] = None
            state["paused"] = False
            if was_paused:
                state["last_write_s"] = now_s
                return "logging_resumed"
            if now_s - float(state["last_write_s"]) >= self.resource_interval_s:
                state["last_write_s"] = now_s
                return "sample"
            return None

        if state["zero_since_s"] is None:
            state["zero_since_s"] = now_s

        zero_duration_s = now_s - float(state["zero_since_s"])
        if zero_duration_s >= self.zero_pause_s:
            if not state["paused"]:
                state["paused"] = True
                state["last_write_s"] = now_s
                return "logging_paused"
            return None

        if now_s - float(state["last_write_s"]) >= self.resource_interval_s:
            state["last_write_s"] = now_s
            return "sample"
        return None

    def _record_resource_group(
        self,
        *,
        key: str,
        resource_kind: str,
        instance: str,
        metrics: Dict[str, Any],
        nonzero: bool,
        now_s: float,
    ) -> bool:
        action = self._resource_action(key, nonzero, now_s)
        if action is None:
            return False
        payload = {
            "resource_kind": resource_kind,
            "instance": instance,
            "record_state": action,
            "sample_epoch_s": now_s,
            **metrics,
        }
        return self._append(
            self.resource_path,
            self._base_record("resource", self.role, payload),
        )

    def record_instance_resources(
        self,
        instance: str,
        *,
        gpu_devices: Optional[list[Dict[str, Any]]] = None,
        network: Optional[Dict[str, Any]] = None,
        now_s: Optional[float] = None,
    ) -> int:
        current = time.time() if now_s is None else float(now_s)
        written = 0

        if isinstance(network, dict) and network:
            tx = _safe_float(network.get("tx_mbps")) or 0.0
            rx = _safe_float(network.get("rx_mbps")) or 0.0
            usage = _safe_float(network.get("usage_percent")) or 0.0
            if self._record_resource_group(
                key=f"network:{instance}:{network.get('interface', '')}",
                resource_kind="network",
                instance=instance,
                metrics=dict(network),
                nonzero=any(value > 0.0 for value in (tx, rx, usage)),
                now_s=current,
            ):
                written += 1

        for device in gpu_devices or []:
            if not isinstance(device, dict):
                continue
            gpu_index = device.get("gpu_index", "unknown")
            gpu_util = _safe_float(device.get("gpu_util_percent")) or 0.0
            memory_util = _safe_float(device.get("gpu_memory_util_percent")) or 0.0
            if self._record_resource_group(
                key=f"gpu:{instance}:{gpu_index}",
                resource_kind="gpu",
                instance=instance,
                metrics=dict(device),
                nonzero=gpu_util > 0.0 or memory_util > 0.0,
                now_s=current,
            ):
                written += 1

        return written


_OBSERVERS: Dict[str, GatewayObservability] = {}
_OBSERVERS_LOCK = threading.Lock()


def get_gateway_observer(role: str) -> GatewayObservability:
    normalized = str(role).strip().lower()
    with _OBSERVERS_LOCK:
        observer = _OBSERVERS.get(normalized)
        if observer is None:
            observer = GatewayObservability(normalized)
            _OBSERVERS[normalized] = observer
        return observer


def build_end_to_end_timing(
    timing: Dict[str, Any],
    *,
    decode_arrival_s: Any = None,
    decode_forward_s: Any = None,
    decode_first_chunk_s: Any = None,
    decode_end_s: Any = None,
) -> Dict[str, Any]:
    prefill_arrival_s = timing.get("prefill_arrival_s")
    prefill_start_s = timing.get("prefill_start_s")
    prefill_end_s = timing.get("prefill_end_s")
    return {
        "prefill_queue_ms": duration_ms(prefill_arrival_s, prefill_start_s),
        "prefill_ms": duration_ms(prefill_start_s, prefill_end_s),
        "decode_gateway_queue_ms": duration_ms(decode_arrival_s, decode_forward_s),
        "observed_network_transfer_ms": duration_ms(
            decode_forward_s, decode_first_chunk_s
        ),
        "network_time_scope": "nixl_pull_plus_vllm_admission_plus_first_token",
        "decode_stream_ms": duration_ms(decode_first_chunk_s, decode_end_s),
        "decode_total_ms": duration_ms(decode_forward_s, decode_end_s),
        "gateway_queue_total_ms": _sum_optional(
            duration_ms(prefill_arrival_s, prefill_start_s),
            duration_ms(decode_arrival_s, decode_forward_s),
        ),
        "gateway_e2e_ms": duration_ms(prefill_arrival_s, decode_end_s),
    }


def _sum_optional(*values: Optional[float]) -> Optional[float]:
    if any(value is None for value in values):
        return None
    return sum(float(value) for value in values if value is not None)
