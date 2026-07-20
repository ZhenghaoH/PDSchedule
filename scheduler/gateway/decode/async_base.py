import logging
import math
from typing import Any, Dict, List, Optional

from core.schemas.request import BaseRequest
from store.gateway.manager import GatewayStoreManager


class AsyncBaseScheduler:
    """Decode async scheduler."""

    def __init__(self, storage=None):
        self.storage = storage if storage is not None else GatewayStoreManager()

    def lowest_generate_throughput(self) -> str:
        decode_instances = self.storage.active_decode_objs
        if not decode_instances:
            return ""

        instances = self.storage.instance_table
        return min(
            decode_instances,
            key=lambda instance_ip: (
                instances.get(instance_ip).generate_throughput
                if instances.get(instance_ip)
                else 0.0
            ),
        )

    def round_robin(self) -> str:
        if hasattr(self.storage, "next_decode_round_robin"):
            return self.storage.next_decode_round_robin()
        return self.lowest_generate_throughput()

    def distserve(self, request: BaseRequest) -> str:
        return self.storage.reserve_distserve_batch("decode", [request.Request_ID])

    def _decode_instance_generate_throughput(self, instance_ip: str) -> float:
        instance = self.storage.instance_table.get(instance_ip)
        if instance is None:
            return 0.0
        return float(getattr(instance, "generate_throughput", 0.0) or 0.0)

    def _decode_instance_load_score(
        self,
        instance_ip: str,
        active_batch: List[Dict[str, Any]],
    ) -> tuple:
        return (
            len(active_batch),
            self._decode_instance_generate_throughput(instance_ip),
            instance_ip,
        )

    def _request_to_active_item(self, request: BaseRequest) -> Dict[str, Any]:
        prompt = getattr(request, "Prompt", None)
        service = getattr(request, "Service", None)
        prompt_tokens = int(getattr(prompt, "token_length", 0) or 0)
        return {
            "request_id": str(getattr(request, "Request_ID", "")),
            "prompt_tokens": prompt_tokens,
            "max_tokens": int(getattr(prompt, "max_tokens", 0) or 0),
            "slo_tpot_ms": float(getattr(service, "SLO_TPOT", 0) or 0),
            "current_context_len": prompt_tokens,
        }

    async def _predict_request_tpot_ms(self, batch_size: int, context_len: int) -> float:
        predict_func = None
        if hasattr(self.storage, "get_tpot_step_predictor"):
            predict_func = self.storage.get_tpot_step_predictor()
        if predict_func is None:
            return math.inf

        try:
            predicted_tpot_sec = await predict_func(batch_size, context_len)
        except Exception as exc:
            logging.warning("[Decode SLO Admission] TPOT predictor failed: %s", exc)
            return math.inf

        if predicted_tpot_sec is None or not math.isfinite(predicted_tpot_sec):
            return math.inf
        return max(0.0, float(predicted_tpot_sec) * 1000.0)

    async def _evaluate_candidate_batch(
        self,
        instance_ip: str,
        candidate: List[Dict[str, Any]],
    ) -> Optional[Dict[str, Any]]:
        batch_size = len(candidate)
        if batch_size <= 0:
            return None

        request_results = []
        violation_count = 0
        min_margin = math.inf
        worst_request_id = ""
        worst_predicted_tpot_ms = math.inf
        worst_slo_tpot_ms = 0.0

        for item in candidate:
            prompt_tokens = int(item.get("prompt_tokens") or 0)
            max_tokens = int(item.get("max_tokens") or 0)
            context_len = max(0, prompt_tokens + max_tokens)
            predicted_tpot_ms = await self._predict_request_tpot_ms(batch_size, context_len)
            if not math.isfinite(predicted_tpot_ms):
                return None

            slo_tpot_ms = float(item.get("slo_tpot_ms") or 0.0)
            margin = slo_tpot_ms - predicted_tpot_ms
            if margin < 0:
                violation_count += 1
            if margin < min_margin:
                min_margin = margin
                worst_request_id = str(item.get("request_id") or "")
                worst_predicted_tpot_ms = predicted_tpot_ms
                worst_slo_tpot_ms = slo_tpot_ms

            request_results.append(
                {
                    "request_id": str(item.get("request_id") or ""),
                    "context_len": context_len,
                    "slo_tpot_ms": slo_tpot_ms,
                    "predicted_tpot_ms": predicted_tpot_ms,
                    "margin": margin,
                }
            )

        return {
            "instance_ip": instance_ip,
            "batch_size": batch_size,
            "violation_count": violation_count,
            "min_margin": min_margin,
            "worst_request_id": worst_request_id,
            "worst_predicted_tpot_ms": worst_predicted_tpot_ms,
            "worst_slo_tpot_ms": worst_slo_tpot_ms,
            "request_results": request_results,
        }

    async def _slo_tpot_admission(self, request: BaseRequest) -> str:
        decode_instances = self.storage.active_decode_objs
        if not decode_instances:
            return ""

        new_item = self._request_to_active_item(request)
        best_safe = None
        best_unsafe = None

        for instance_ip in decode_instances:
            active_batch = self.storage.get_decode_active_batch(instance_ip)
            candidate = [*active_batch, new_item]
            evaluation = await self._evaluate_candidate_batch(instance_ip, candidate)
            if evaluation is None:
                continue
            evaluation["active_count"] = len(active_batch)
            evaluation["generate_throughput"] = self._decode_instance_generate_throughput(instance_ip)
            evaluation["load_score"] = self._decode_instance_load_score(instance_ip, active_batch)

            if evaluation["violation_count"] == 0:
                if (
                    best_safe is None
                    or evaluation["load_score"] < best_safe["load_score"]
                    or (
                        evaluation["load_score"] == best_safe["load_score"]
                        and evaluation["min_margin"] > best_safe["min_margin"]
                    )
                ):
                    best_safe = evaluation
                continue

            if (
                best_unsafe is None
                or evaluation["violation_count"] < best_unsafe["violation_count"]
                or (
                    evaluation["violation_count"] == best_unsafe["violation_count"]
                    and evaluation["min_margin"] > best_unsafe["min_margin"]
                )
                or (
                    evaluation["violation_count"] == best_unsafe["violation_count"]
                    and evaluation["min_margin"] == best_unsafe["min_margin"]
                    and evaluation["load_score"] < best_unsafe["load_score"]
                )
            ):
                best_unsafe = evaluation

        if best_safe is not None:
            logging.info(
                "[Decode SLO Admission] Selected %s with load_score=%s and TPOT margin %.3f ms; "
                "worst_request=%s predicted_tpot=%.3f ms slo=%.3f ms",
                best_safe["instance_ip"],
                best_safe["load_score"],
                best_safe["min_margin"],
                best_safe["worst_request_id"],
                best_safe["worst_predicted_tpot_ms"],
                best_safe["worst_slo_tpot_ms"],
            )
            return best_safe["instance_ip"]

        if best_unsafe is not None:
            logging.info(
                "[Decode SLO Admission] No safe active batch found; selected %s with "
                "minimum violations=%d, load_score=%s, worst_margin=%.3f ms; worst_request=%s "
                "predicted_tpot=%.3f ms slo=%.3f ms",
                best_unsafe["instance_ip"],
                best_unsafe["violation_count"],
                best_unsafe["load_score"],
                best_unsafe["min_margin"],
                best_unsafe["worst_request_id"],
                best_unsafe["worst_predicted_tpot_ms"],
                best_unsafe["worst_slo_tpot_ms"],
            )
            return best_unsafe["instance_ip"]

        fallback = self.lowest_generate_throughput()
        logging.info(
            "[Decode SLO Admission] No valid TPOT prediction; fallback to lowest generate_throughput: %s",
            fallback,
        )
        return fallback

    async def schedule_async(self, request: BaseRequest, strategy: str = "slo_tpot_admission") -> str:
        if strategy == "slo_tpot_admission":
            return await self._slo_tpot_admission(request)
        if strategy == "round_robin":
            return self.round_robin()
        if strategy == "lowest_generate_throughput":
            return self.lowest_generate_throughput()
        if strategy == "distserve":
            return self.distserve(request)
        logging.warning(
            "[Decode Scheduler] Unknown strategy %s; fallback to lowest_generate_throughput",
            strategy,
        )
        return self.lowest_generate_throughput()

    def schedule(self, request: BaseRequest) -> str:
        """Backward-compatible synchronous entry point."""
        return self.lowest_generate_throughput()
