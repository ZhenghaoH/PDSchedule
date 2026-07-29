"""Offline profiler for DistServe's Prefill saturation length L_m."""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import math
import os
import shutil
import statistics
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional, Sequence


DEFAULT_PROMPT_LENGTHS = "128,256,512,1024,2048,4096,8192"
DEFAULT_MODEL_PATH = (
    "/workspace/llm-stack/models/deepseek-ai/DeepSeek-R1-Distill-Qwen-7B"
)


def parse_positive_ints(value: str, *, strictly_increasing: bool = False) -> list[int]:
    try:
        values = [int(part.strip()) for part in value.split(",") if part.strip()]
    except ValueError as exc:
        raise argparse.ArgumentTypeError("expected comma-separated integers") from exc
    if not values or any(item <= 0 for item in values):
        raise argparse.ArgumentTypeError("values must be positive integers")
    if strictly_increasing and any(left >= right for left, right in zip(values, values[1:])):
        raise argparse.ArgumentTypeError("prompt lengths must be strictly increasing")
    return values


def parse_prompt_lengths(value: str) -> list[int]:
    return parse_positive_ints(value, strictly_increasing=True)


def parse_gpu_indices(value: str) -> list[int]:
    try:
        values = [int(part.strip()) for part in value.split(",") if part.strip()]
    except ValueError as exc:
        raise argparse.ArgumentTypeError("GPU indices must be comma-separated integers") from exc
    if not values or any(item < 0 for item in values):
        raise argparse.ArgumentTypeError("GPU indices must be non-negative integers")
    if len(set(values)) != len(values):
        raise argparse.ArgumentTypeError("GPU indices must not contain duplicates")
    return values


def parse_host(value: str) -> str:
    host = value.strip()
    if not host:
        raise argparse.ArgumentTypeError("host must not be empty")
    if "://" in host or any(character in host for character in "/?#"):
        raise argparse.ArgumentTypeError(
            "host must be IP:PORT or hostname:port without protocol or URL path"
        )
    if any(character.isspace() for character in host):
        raise argparse.ArgumentTypeError("host must not contain whitespace")

    if host.startswith("["):
        closing_bracket = host.find("]")
        if closing_bracket <= 1 or closing_bracket + 1 >= len(host):
            raise argparse.ArgumentTypeError("IPv6 host must use [IPv6]:port format")
        if host[closing_bracket + 1] != ":" or "]" in host[closing_bracket + 1 :]:
            raise argparse.ArgumentTypeError("IPv6 host must use [IPv6]:port format")
        hostname = host[1:closing_bracket]
        port_text = host[closing_bracket + 2 :]
    else:
        if host.count(":") != 1:
            raise argparse.ArgumentTypeError("host must use IP:PORT or hostname:port format")
        hostname, port_text = host.rsplit(":", 1)

    if not hostname or not port_text.isdigit():
        raise argparse.ArgumentTypeError("host port must be an integer")
    port = int(port_text)
    if not 1 <= port <= 65535:
        raise argparse.ArgumentTypeError("host port must be in [1, 65535]")
    return host


def build_base_url(host: str) -> str:
    return f"http://{parse_host(host)}/v1"


def percentile(values: Sequence[float], quantile: float) -> Optional[float]:
    if not values:
        return None
    ordered = sorted(float(value) for value in values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * quantile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def numeric_summary(values: Iterable[float]) -> dict[str, Optional[float]]:
    data = [float(value) for value in values]
    if not data:
        return {key: None for key in ("mean", "median", "stddev", "cv", "min", "max", "p95")}
    mean = statistics.mean(data)
    stddev = statistics.stdev(data) if len(data) > 1 else 0.0
    return {
        "mean": mean,
        "median": statistics.median(data),
        "stddev": stddev,
        "cv": stddev / mean if mean else None,
        "min": min(data),
        "max": max(data),
        "p95": percentile(data, 0.95),
    }


@dataclass
class GpuSample:
    timestamp_perf: float
    gpu_index: int
    gpu_util_percent: Optional[float]
    memory_util_percent: Optional[float]
    memory_used_bytes: Optional[int]
    memory_total_bytes: Optional[int]
    power_watts: Optional[float]
    temperature_c: Optional[float]
    graphics_clock_mhz: Optional[float]
    sm_clock_mhz: Optional[float]
    errors: dict[str, str] = field(default_factory=dict)


@dataclass
class RequestMeasurement:
    prompt_tokens: int
    repeat_index: int
    request_id: str
    status: Optional[int]
    ttft_seconds: Optional[float]
    throughput_tokens_per_second: Optional[float]
    error: str
    gpu_samples: list[GpuSample]

    @property
    def succeeded(self) -> bool:
        return self.error == "" and self.ttft_seconds is not None


class NvmlMonitor:
    """Samples selected physical GPUs from one background thread."""

    def __init__(self, gpu_indices: Sequence[int], interval_ms: int, nvml: Any = None):
        if interval_ms <= 0:
            raise ValueError("GPU sampling interval must be positive")
        if nvml is None:
            try:
                import pynvml as nvml_module
            except ImportError as exc:
                raise RuntimeError(
                    "nvidia-ml-py is required for GPU profiling; install requirements.txt"
                ) from exc
            nvml = nvml_module
        self.nvml = nvml
        self.gpu_indices = list(gpu_indices)
        self.interval_seconds = interval_ms / 1000.0
        self.handles: dict[int, Any] = {}
        self.device_info: list[dict[str, Any]] = []
        self._samples: list[GpuSample] = []
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._thread_error: Optional[BaseException] = None
        self._initialized = False

    @staticmethod
    def _text(value: Any) -> str:
        return value.decode("utf-8", errors="replace") if isinstance(value, bytes) else str(value)

    def initialize(self) -> None:
        self.nvml.nvmlInit()
        self._initialized = True
        try:
            count = int(self.nvml.nvmlDeviceGetCount())
            for index in self.gpu_indices:
                if index >= count:
                    raise RuntimeError(f"GPU index {index} is unavailable; NVML reports {count} devices")
                handle = self.nvml.nvmlDeviceGetHandleByIndex(index)
                self.handles[index] = handle
                memory = self.nvml.nvmlDeviceGetMemoryInfo(handle)
                self.device_info.append(
                    {
                        "gpu_index": index,
                        "uuid": self._text(self.nvml.nvmlDeviceGetUUID(handle)),
                        "name": self._text(self.nvml.nvmlDeviceGetName(handle)),
                        "memory_total_bytes": int(memory.total),
                    }
                )
        except Exception:
            self.close()
            raise

    def metadata(self) -> dict[str, Any]:
        driver = self._text(self.nvml.nvmlSystemGetDriverVersion())
        nvml_version = self._text(self.nvml.nvmlSystemGetNVMLVersion())
        return {"driver_version": driver, "nvml_version": nvml_version, "devices": self.device_info}

    def _optional(self, name: str, callback: Any, errors: dict[str, str]) -> Any:
        try:
            return callback()
        except Exception as exc:
            errors[name] = str(exc)
            return None

    def _sample_device(self, index: int, handle: Any, timestamp: float) -> GpuSample:
        errors: dict[str, str] = {}
        utilization = self._optional(
            "utilization", lambda: self.nvml.nvmlDeviceGetUtilizationRates(handle), errors
        )
        memory = self._optional("memory", lambda: self.nvml.nvmlDeviceGetMemoryInfo(handle), errors)
        power_mw = self._optional(
            "power", lambda: self.nvml.nvmlDeviceGetPowerUsage(handle), errors
        )
        temperature = self._optional(
            "temperature",
            lambda: self.nvml.nvmlDeviceGetTemperature(handle, self.nvml.NVML_TEMPERATURE_GPU),
            errors,
        )
        graphics_clock = self._optional(
            "graphics_clock",
            lambda: self.nvml.nvmlDeviceGetClockInfo(handle, self.nvml.NVML_CLOCK_GRAPHICS),
            errors,
        )
        sm_clock_type = getattr(self.nvml, "NVML_CLOCK_SM", None)
        sm_clock = self._optional(
            "sm_clock",
            lambda: self.nvml.nvmlDeviceGetClockInfo(handle, sm_clock_type),
            errors,
        ) if sm_clock_type is not None else None
        return GpuSample(
            timestamp_perf=timestamp,
            gpu_index=index,
            gpu_util_percent=float(utilization.gpu) if utilization is not None else None,
            memory_util_percent=float(utilization.memory) if utilization is not None else None,
            memory_used_bytes=int(memory.used) if memory is not None else None,
            memory_total_bytes=int(memory.total) if memory is not None else None,
            power_watts=float(power_mw) / 1000.0 if power_mw is not None else None,
            temperature_c=float(temperature) if temperature is not None else None,
            graphics_clock_mhz=float(graphics_clock) if graphics_clock is not None else None,
            sm_clock_mhz=float(sm_clock) if sm_clock is not None else None,
            errors=errors,
        )

    def sample_once(self) -> None:
        timestamp = time.perf_counter()
        samples = [self._sample_device(index, handle, timestamp) for index, handle in self.handles.items()]
        with self._lock:
            self._samples.extend(samples)

    def _run(self) -> None:
        try:
            while not self._stop_event.is_set():
                self.sample_once()
                self._stop_event.wait(self.interval_seconds)
        except BaseException as exc:
            self._thread_error = exc
            self._stop_event.set()

    def start(self) -> None:
        if self._thread is not None:
            raise RuntimeError("GPU monitor is already running")
        with self._lock:
            self._samples = []
        self._thread_error = None
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, name="distserve-lm-nvml", daemon=True)
        self._thread.start()

    def stop(self) -> list[GpuSample]:
        thread = self._thread
        if thread is None:
            return []
        self._stop_event.set()
        thread.join(timeout=max(1.0, self.interval_seconds * 4))
        self._thread = None
        if thread.is_alive():
            raise RuntimeError("GPU sampling thread did not stop")
        if self._thread_error is not None:
            raise RuntimeError(f"GPU sampling failed: {self._thread_error}") from self._thread_error
        with self._lock:
            return list(self._samples)

    def close(self) -> None:
        if self._thread is not None:
            self.stop()
        if self._initialized:
            self.nvml.nvmlShutdown()
            self._initialized = False


def load_tokenizer(tokenizer_path: str):
    from transformers import AutoTokenizer

    return AutoTokenizer.from_pretrained(tokenizer_path, trust_remote_code=True)


def generate_exact_token_prompt(tokenizer: Any, target_tokens: int, marker: str) -> str:
    base_ids = tokenizer.encode(f"[{marker}] DistServe Prefill profiling. ", add_special_tokens=False)
    filler_ids = tokenizer.encode(
        "A deterministic sentence supplies repeatable tokens for offline measurement. ",
        add_special_tokens=False,
    )
    if not filler_ids:
        raise ValueError("tokenizer produced no filler tokens")
    token_ids = (base_ids + filler_ids * (target_tokens // len(filler_ids) + 2))[:target_tokens]
    prompt = tokenizer.decode(token_ids, clean_up_tokenization_spaces=False)
    for _ in range(16):
        current = tokenizer.encode(prompt, add_special_tokens=False)
        if len(current) == target_tokens:
            return prompt
        current = current[:target_tokens]
        if len(current) < target_tokens:
            current += (filler_ids * (target_tokens // len(filler_ids) + 2))[: target_tokens - len(current)]
        prompt = tokenizer.decode(current, clean_up_tokenization_spaces=False)
    actual = len(tokenizer.encode(prompt, add_special_tokens=False))
    raise ValueError(f"failed to create exact-token prompt: expected={target_tokens}, actual={actual}")


async def measure_request(
    session: Any,
    url: str,
    model: str,
    prompt: str,
    prompt_tokens: int,
    repeat_index: int,
    headers: dict[str, str],
    monitor: Optional[NvmlMonitor],
    timeout_seconds: float,
) -> RequestMeasurement:
    request_id = f"distserve-lm-{uuid.uuid4().hex}"
    request_headers = dict(headers)
    request_headers["X-Request-Id"] = request_id
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "stream": True,
        "max_tokens": 1,
        "temperature": 0,
    }
    status: Optional[int] = None
    error = ""
    ttft: Optional[float] = None
    samples: list[GpuSample] = []
    start = time.perf_counter()
    if monitor is not None:
        monitor.start()
    try:
        async with session.post(
            url, json=payload, headers=request_headers, timeout=timeout_seconds
        ) as response:
            status = response.status
            if not 200 <= response.status < 300:
                error = f"HTTP {response.status}: {await response.text()}"
            else:
                buffer = ""
                async for raw in response.content.iter_any():
                    buffer += raw.decode("utf-8", errors="replace")
                    buffer = buffer.replace("\r\n", "\n")
                    while "\n\n" in buffer:
                        event, buffer = buffer.split("\n\n", 1)
                        for line in event.splitlines():
                            if not line.startswith("data:"):
                                continue
                            data = line[5:].strip()
                            if data and data != "[DONE]":
                                json.loads(data)
                                ttft = time.perf_counter() - start
                                break
                        if ttft is not None:
                            break
                    if ttft is not None:
                        break
                if ttft is None:
                    error = "response contained no streaming data event"
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
    finally:
        if monitor is not None:
            try:
                samples = monitor.stop()
            except Exception as exc:
                error = f"{error}; " if error else ""
                error += f"GPU monitor stop failed: {exc}"
                ttft = None
    throughput = prompt_tokens / ttft if ttft and ttft > 0 and not error else None
    return RequestMeasurement(
        prompt_tokens=prompt_tokens,
        repeat_index=repeat_index,
        request_id=request_id,
        status=status,
        ttft_seconds=ttft if not error else None,
        throughput_tokens_per_second=throughput,
        error=error,
        gpu_samples=samples,
    )


GPU_FIELDS = (
    "gpu_util_percent",
    "memory_util_percent",
    "memory_used_bytes",
    "power_watts",
    "temperature_c",
    "graphics_clock_mhz",
    "sm_clock_mhz",
)


def summarize_length(
    prompt_tokens: int,
    measurements: Sequence[RequestMeasurement],
    gpu_indices: Sequence[int],
    min_successes: int,
    max_cv: float,
    min_gpu_samples: int,
) -> dict[str, Any]:
    successful = [item for item in measurements if item.succeeded]
    throughput = numeric_summary(
        item.throughput_tokens_per_second
        for item in successful
        if item.throughput_tokens_per_second is not None
    )
    ttft = numeric_summary(item.ttft_seconds for item in successful if item.ttft_seconds is not None)
    gpu: dict[str, Any] = {}
    gpu_samples_valid = True
    for index in gpu_indices:
        samples = [sample for item in successful for sample in item.gpu_samples if sample.gpu_index == index]
        fields = {
            field_name: numeric_summary(
                getattr(sample, field_name)
                for sample in samples
                if getattr(sample, field_name) is not None
            )
            for field_name in GPU_FIELDS
        }
        util_count = sum(sample.gpu_util_percent is not None for sample in samples)
        gpu[str(index)] = {"sample_count": len(samples), "gpu_util_sample_count": util_count, **fields}
        gpu_samples_valid = gpu_samples_valid and util_count >= min_gpu_samples
    cv = throughput["cv"]
    stable = (
        len(successful) >= min_successes
        and cv is not None
        and cv <= max_cv
        and gpu_samples_valid
    )
    return {
        "prompt_tokens": prompt_tokens,
        "success_count": len(successful),
        "failure_count": len(measurements) - len(successful),
        "ttft_seconds": ttft,
        "throughput_tokens_per_second": throughput,
        "gpu": gpu,
        "stable": stable,
    }


def recommend_lm(
    summaries: Sequence[dict[str, Any]],
    plateau_points: int,
    saturation_ratio: float,
    stability_points: int,
    gpu_util_threshold: float,
    gpu_indices: Sequence[int],
) -> dict[str, Any]:
    stable = [item for item in summaries if item["stable"]]
    if len(stable) < plateau_points:
        return {"recommended_lm_tokens": None, "reason": "insufficient stable plateau points"}
    plateau_tail = stable[-plateau_points:]
    plateau_values = [item["throughput_tokens_per_second"]["median"] for item in plateau_tail]
    assert all(value is not None for value in plateau_values)
    plateau_values = [float(value) for value in plateau_values]
    if min(plateau_values) < max(plateau_values) * saturation_ratio:
        return {"recommended_lm_tokens": None, "reason": "longest stable points do not form a throughput plateau"}
    plateau = statistics.median(plateau_values)
    threshold = plateau * saturation_ratio

    def qualifies(item: dict[str, Any]) -> bool:
        median_throughput = item["throughput_tokens_per_second"]["median"]
        if not item["stable"] or median_throughput is None or median_throughput < threshold:
            return False
        return all(
            item["gpu"][str(index)]["gpu_util_percent"]["p95"] is not None
            and item["gpu"][str(index)]["gpu_util_percent"]["p95"] >= gpu_util_threshold
            for index in gpu_indices
        )

    for offset in range(0, len(summaries) - stability_points + 1):
        window = summaries[offset : offset + stability_points]
        if all(qualifies(item) for item in window):
            return {
                "recommended_lm_tokens": summaries[offset]["prompt_tokens"],
                "reason": "",
                "plateau_throughput_tokens_per_second": plateau,
                "saturation_threshold_tokens_per_second": threshold,
            }
    return {
        "recommended_lm_tokens": None,
        "reason": "no consecutive prompt lengths satisfy throughput and per-GPU utilization thresholds",
        "plateau_throughput_tokens_per_second": plateau,
        "saturation_threshold_tokens_per_second": threshold,
    }


def write_outputs(result: dict[str, Any], output_dir: Path) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    json_path = output_dir / f"distserve_lm_{timestamp}.json"
    csv_path = output_dir / f"distserve_lm_{timestamp}.csv"
    json_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    fieldnames = [
        "prompt_tokens", "gpu_index", "success_count", "failure_count", "stable",
        "median_ttft_seconds", "median_throughput_tokens_per_second", "throughput_cv",
        "gpu_sample_count", "gpu_util_mean", "gpu_util_p95", "gpu_util_max",
        "memory_util_p95", "memory_used_bytes_max", "power_watts_p95",
        "meets_throughput_threshold", "meets_gpu_threshold", "selected_lm",
    ]
    recommendation = result["recommendation"]
    throughput_threshold = recommendation.get("saturation_threshold_tokens_per_second")
    gpu_threshold = result["parameters"]["gpu_util_threshold"]
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for summary in result["summaries"]:
            for gpu_index, gpu in summary["gpu"].items():
                median_throughput = summary["throughput_tokens_per_second"]["median"]
                gpu_p95 = gpu["gpu_util_percent"]["p95"]
                writer.writerow({
                    "prompt_tokens": summary["prompt_tokens"],
                    "gpu_index": gpu_index,
                    "success_count": summary["success_count"],
                    "failure_count": summary["failure_count"],
                    "stable": summary["stable"],
                    "median_ttft_seconds": summary["ttft_seconds"]["median"],
                    "median_throughput_tokens_per_second": median_throughput,
                    "throughput_cv": summary["throughput_tokens_per_second"]["cv"],
                    "gpu_sample_count": gpu["gpu_util_sample_count"],
                    "gpu_util_mean": gpu["gpu_util_percent"]["mean"],
                    "gpu_util_p95": gpu_p95,
                    "gpu_util_max": gpu["gpu_util_percent"]["max"],
                    "memory_util_p95": gpu["memory_util_percent"]["p95"],
                    "memory_used_bytes_max": gpu["memory_used_bytes"]["max"],
                    "power_watts_p95": gpu["power_watts"]["p95"],
                    "meets_throughput_threshold": throughput_threshold is not None and median_throughput is not None and median_throughput >= throughput_threshold,
                    "meets_gpu_threshold": gpu_p95 is not None and gpu_p95 >= gpu_threshold,
                    "selected_lm": summary["prompt_tokens"] == recommendation.get("recommended_lm_tokens"),
                })
    shutil.copyfile(json_path, output_dir / "latest.json")
    shutil.copyfile(csv_path, output_dir / "latest.csv")
    return json_path, csv_path


def print_summary(summaries: Sequence[dict[str, Any]], recommendation: dict[str, Any]) -> None:
    print("\nprompt  success  median_ttft_ms  median_tokens/s  throughput_cv  gpu_p95")
    for item in summaries:
        ttft = item["ttft_seconds"]["median"]
        throughput = item["throughput_tokens_per_second"]["median"]
        cv = item["throughput_tokens_per_second"]["cv"]
        gpu_text = ",".join(
            f"{index}:{metrics['gpu_util_percent']['p95']:.1f}%"
            if metrics["gpu_util_percent"]["p95"] is not None else f"{index}:n/a"
            for index, metrics in item["gpu"].items()
        )
        print(
            f"{item['prompt_tokens']:>6}  {item['success_count']:>7}  "
            f"{ttft * 1000 if ttft is not None else float('nan'):>14.3f}  "
            f"{throughput if throughput is not None else float('nan'):>15.3f}  "
            f"{cv if cv is not None else float('nan'):>13.4f}  {gpu_text}"
        )
    if recommendation["recommended_lm_tokens"] is None:
        print(f"\nUnable to determine L_m: {recommendation['reason']}")
        return
    print(f"\nplateau_throughput_tokens_per_second={recommendation['plateau_throughput_tokens_per_second']:.3f}")
    print(f"saturation_threshold_tokens_per_second={recommendation['saturation_threshold_tokens_per_second']:.3f}")
    print(f"recommended_lm_tokens={recommendation['recommended_lm_tokens']}")
    print(
        "recommended_vllm_max_num_batched_tokens="
        f"{recommendation['recommended_lm_tokens']}"
    )


async def run(args: argparse.Namespace, monitor_factory: Any = NvmlMonitor) -> int:
    import aiohttp

    prompt_lengths = parse_prompt_lengths(args.prompt_lengths)
    gpu_indices = parse_gpu_indices(args.gpu_indices)
    min_successes = max(1, math.ceil(args.measure_repeats * args.min_success_ratio))
    headers = {
        "Authorization": f"Bearer {os.environ.get(args.api_key_env, 'EMPTY')}",
        "Content-Type": "application/json",
    }
    base_url = build_base_url(args.host)
    monitor = monitor_factory(gpu_indices, args.gpu_sample_interval_ms)
    monitor.initialize()
    try:
        tokenizer = load_tokenizer(args.tokenizer)
        timeout = aiohttp.ClientTimeout(total=args.timeout_seconds)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            model = args.model
            measurements: list[RequestMeasurement] = []
            url = f"{base_url}/chat/completions"
            for prompt_tokens in prompt_lengths:
                for repeat_index in range(-args.warmup_repeats, args.measure_repeats):
                    marker = f"{prompt_tokens}-{repeat_index}-{uuid.uuid4().hex}"
                    prompt = generate_exact_token_prompt(tokenizer, prompt_tokens, marker)
                    is_warmup = repeat_index < 0
                    measurement = await measure_request(
                        session, url, model, prompt, prompt_tokens, repeat_index,
                        headers, None if is_warmup else monitor, args.timeout_seconds,
                    )
                    phase = "warmup" if is_warmup else "measure"
                    print(
                        f"length={prompt_tokens} phase={phase} repeat={repeat_index} "
                        f"ttft={measurement.ttft_seconds} error={measurement.error or '-'}"
                    )
                    if not is_warmup:
                        measurements.append(measurement)
                    if args.request_interval_seconds > 0:
                        await asyncio.sleep(args.request_interval_seconds)
        summaries = [
            summarize_length(
                length,
                [item for item in measurements if item.prompt_tokens == length],
                gpu_indices,
                min_successes,
                args.max_cv,
                args.min_gpu_samples,
            )
            for length in prompt_lengths
        ]
        recommendation = recommend_lm(
            summaries, args.plateau_points, args.saturation_ratio,
            args.stability_points, args.gpu_util_threshold, gpu_indices,
        )
        result = {
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "base_url": base_url,
            "model": model,
            "tokenizer": args.tokenizer,
            "gpu": monitor.metadata(),
            "parameters": {
                "prompt_lengths": prompt_lengths,
                "warmup_repeats": args.warmup_repeats,
                "measure_repeats": args.measure_repeats,
                "min_success_ratio": args.min_success_ratio,
                "saturation_ratio": args.saturation_ratio,
                "plateau_points": args.plateau_points,
                "stability_points": args.stability_points,
                "max_cv": args.max_cv,
                "gpu_indices": gpu_indices,
                "gpu_sample_interval_ms": args.gpu_sample_interval_ms,
                "gpu_util_threshold": args.gpu_util_threshold,
                "min_gpu_samples": args.min_gpu_samples,
            },
            "measurements": [asdict(item) for item in measurements],
            "summaries": summaries,
            "recommendation": recommendation,
        }
        json_path, csv_path = write_outputs(result, Path(args.output_dir))
        print_summary(summaries, recommendation)
        print(f"json={json_path}\ncsv={csv_path}")
        return 0 if recommendation["recommended_lm_tokens"] is not None else 2
    finally:
        monitor.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Profile DistServe Prefill saturation length L_m",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--host",
        required=True,
        type=parse_host,
        help="Prefill vLLM IP:PORT or hostname:port (without protocol or path)",
    )
    parser.add_argument(
        "--model",
        default=DEFAULT_MODEL_PATH,
        help="served model name sent to the OpenAI-compatible endpoint",
    )
    parser.add_argument(
        "--tokenizer",
        default=DEFAULT_MODEL_PATH,
        help="local tokenizer path or Hugging Face ID",
    )
    parser.add_argument("--prompt-lengths", default=DEFAULT_PROMPT_LENGTHS)
    parser.add_argument("--warmup-repeats", type=int, default=2)
    parser.add_argument("--measure-repeats", type=int, default=8)
    parser.add_argument("--request-interval-seconds", type=float, default=0.25)
    parser.add_argument("--timeout-seconds", type=float, default=120.0)
    parser.add_argument("--saturation-ratio", type=float, default=0.95)
    parser.add_argument("--plateau-points", type=int, default=3)
    parser.add_argument("--stability-points", type=int, default=2)
    parser.add_argument("--max-cv", type=float, default=0.10)
    parser.add_argument("--min-success-ratio", type=float, default=0.75)
    parser.add_argument("--gpu-indices", default="0")
    parser.add_argument("--gpu-sample-interval-ms", type=int, default=20)
    parser.add_argument("--gpu-util-threshold", type=float, default=90.0)
    parser.add_argument("--min-gpu-samples", type=int, default=3)
    parser.add_argument("--output-dir", default="profile/artifacts/distserve_lm")
    parser.add_argument("--api-key-env", default="OPENAI_API_KEY")
    return parser


def validate_args(parser: argparse.ArgumentParser, args: argparse.Namespace) -> None:
    parse_host(args.host)
    parse_prompt_lengths(args.prompt_lengths)
    parse_gpu_indices(args.gpu_indices)
    if args.warmup_repeats < 0 or args.measure_repeats <= 0:
        parser.error("repeat counts must be non-negative and measure-repeats must be positive")
    if args.request_interval_seconds < 0 or args.timeout_seconds <= 0:
        parser.error("request interval must be non-negative and timeout must be positive")
    if not 0 < args.saturation_ratio <= 1 or not 0 < args.min_success_ratio <= 1:
        parser.error("ratio arguments must be in (0, 1]")
    if args.plateau_points <= 0 or args.stability_points <= 0 or args.min_gpu_samples <= 0:
        parser.error("point and sample counts must be positive")
    if not 0 <= args.max_cv or not 0 <= args.gpu_util_threshold <= 100:
        parser.error("max-cv must be non-negative and GPU threshold must be in [0, 100]")
    if args.gpu_sample_interval_ms <= 0:
        parser.error("GPU sampling interval must be positive")


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    validate_args(parser, args)
    try:
        return asyncio.run(run(args))
    except KeyboardInterrupt:
        print("profiling interrupted")
        return 130
    except Exception as exc:
        print(f"profiling failed: {type(exc).__name__}: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
