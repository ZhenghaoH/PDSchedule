from typing import List

from core.schemas.request import BaseRequest
from scheduler.gateway.prefill.strategy import (
    PrefillScheduleContext,
    PrefillScheduleResult,
    PrefillStrategy,
    PrefillStrategyCapabilities,
    candidate_snapshot,
)


class JSQPrefillStrategy(PrefillStrategy):
    name = "jsq"
    capabilities = PrefillStrategyCapabilities(
        trigger_on_arrival=True,
        allow_preemption=False,
        allow_adaptive_switch=False,
    )

    @staticmethod
    def _prompt_tokens(request: BaseRequest) -> int:
        try:
            value = int(getattr(request.Prompt, "token_length", 0) or 0)
        except (TypeError, ValueError):
            value = 0
        return max(1, value)

    def _reserve(self, request: BaseRequest) -> str:
        return self.storage.reserve_distserve_batch(
            "prefill",
            [request.Request_ID],
            request_loads=[self._prompt_tokens(request)],
        )

    def _release(self, request: BaseRequest) -> None:
        self.storage.release_distserve_reservation(
            "prefill",
            request.Request_ID,
            getattr(request.Task, "prefill_instance", ""),
        )

    async def schedule(
        self,
        context: PrefillScheduleContext,
    ) -> PrefillScheduleResult:
        scheduled = []
        snapshots = {}
        for position, request in enumerate(context.requests):
            snapshots[str(request.Request_ID)] = candidate_snapshot(self.storage)
            selected_instance = self._reserve(request)
            if not selected_instance:
                continue

            request.Task.batch_order = 1
            request.Task.prefill_instance = selected_instance
            request.Task.slo_plan_position = position
            metadata = dict(
                getattr(request.Task, "scheduler_metadata", {}) or {}
            )
            metadata["jsq"] = {
                "role": "prefill",
                "dispatch_mode": "direct_jsq",
                "load_metric": "unfinished_prompt_tokens",
                "request_id": str(request.Request_ID),
                "prompt_tokens": self._prompt_tokens(request),
                "selected_instance": selected_instance,
                "selected_unfinished_tokens": (
                    self.storage.get_distserve_effective_load(
                        "prefill", selected_instance
                    )
                ),
            }
            request.Task.scheduler_metadata = metadata
            scheduled.append(request)

        return PrefillScheduleResult(
            requests=scheduled,
            dispatch_policy="IMMEDIATE",
            selection_reason="minimum_unfinished_prompt_tokens",
            candidate_snapshots=snapshots,
        )

    def on_schedule_discarded(self, requests: List[BaseRequest]) -> None:
        for request in requests:
            self._release(request)

    def on_request_finished(self, request: BaseRequest) -> None:
        self._release(request)

    def on_switch_out(self, requests: List[BaseRequest]) -> None:
        for request in requests:
            self._release(request)
            metadata = dict(
                getattr(request.Task, "scheduler_metadata", {}) or {}
            )
            metadata.pop("jsq", None)
            request.Task.scheduler_metadata = metadata
