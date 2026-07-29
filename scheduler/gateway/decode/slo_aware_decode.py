import logging
import math
from typing import Any, Dict, List, Optional

from core.schemas.request import BaseRequest
from scheduler.gateway.decode.lowest_generation_throughput import (
    LowestGenerationThroughputDecodeStrategy,
)
from scheduler.gateway.decode.strategy import (
    DecodeScheduleResult,
    DecodeStrategy,
    candidate_snapshot,
    generate_throughput,
)


class SLOAwareDecodeStrategy(DecodeStrategy):
    name = "slo_aware_decode"

    def _load_score(
        self,
        instance_ip: str,
        active_batch: List[Dict[str, Any]],
    ) -> tuple:
        return (
            len(active_batch),
            generate_throughput(self.storage, instance_ip),
            instance_ip,
        )

    @staticmethod
    def _request_to_active_item(request: BaseRequest) -> Dict[str, Any]:
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

    async def _predict_request_tpot_ms(
        self,
        batch_size: int,
        context_len: int,
    ) -> float:
        predict_func = self.storage.get_tpot_step_predictor()
        if predict_func is None:
            return math.inf
        try:
            predicted_tpot_sec = await predict_func(batch_size, context_len)
        except Exception as exc:
            logging.warning(
                "[SLOAwareDecodeStrategy] TPOT predictor failed: %s",
                exc,
            )
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
            context_len = max(
                0,
                int(item.get("prompt_tokens") or 0)
                + int(item.get("max_tokens") or 0),
            )
            predicted_tpot_ms = await self._predict_request_tpot_ms(
                batch_size,
                context_len,
            )
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

    async def schedule(self, request: BaseRequest) -> DecodeScheduleResult:
        best_safe = None
        best_unsafe = None
        new_item = self._request_to_active_item(request)

        for instance_ip in self.storage.active_decode_objs:
            active_batch = self.storage.get_decode_active_batch(instance_ip)
            evaluation = await self._evaluate_candidate_batch(
                instance_ip,
                [*active_batch, new_item],
            )
            if evaluation is None:
                continue
            evaluation["active_count"] = len(active_batch)
            evaluation["generate_throughput"] = generate_throughput(
                self.storage,
                instance_ip,
            )
            evaluation["load_score"] = self._load_score(
                instance_ip,
                active_batch,
            )

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
                    evaluation["violation_count"]
                    == best_unsafe["violation_count"]
                    and evaluation["min_margin"] > best_unsafe["min_margin"]
                )
                or (
                    evaluation["violation_count"]
                    == best_unsafe["violation_count"]
                    and evaluation["min_margin"] == best_unsafe["min_margin"]
                    and evaluation["load_score"] < best_unsafe["load_score"]
                )
            ):
                best_unsafe = evaluation

        selected = best_safe or best_unsafe
        if selected is not None:
            safe = best_safe is not None
            logging.info(
                "[SLOAwareDecodeStrategy] Selected %s: safe=%s "
                "violations=%d margin=%.3f ms load_score=%s",
                selected["instance_ip"],
                safe,
                selected["violation_count"],
                selected["min_margin"],
                selected["load_score"],
            )
            return DecodeScheduleResult(
                selected_instance=selected["instance_ip"],
                selection_reason=(
                    "safe_candidate_minimum_load_score"
                    if safe
                    else "no_safe_candidate_minimum_slo_violations"
                ),
                candidates=candidate_snapshot(self.storage),
                details={"selected_evaluation": selected},
            )

        fallback = await LowestGenerationThroughputDecodeStrategy(
            self.storage
        ).schedule(request)
        fallback.selection_reason = (
            "tpot_prediction_unavailable_"
            "fallback_minimum_generation_throughput"
        )
        logging.info(
            "[SLOAwareDecodeStrategy] TPOT prediction unavailable; "
            "fallback to lowest generation throughput: %s",
            fallback.selected_instance,
        )
        return fallback
