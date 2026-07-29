from typing import List, Optional, Tuple

from core.schemas.request import BaseRequest
from scheduler.gateway.prefill.jsq import JSQPrefillStrategy
from scheduler.gateway.prefill.slo_aware_prefill import SLOAwarePrefillStrategy
from scheduler.gateway.prefill.round_robin import RoundRobinPrefillStrategy
from scheduler.gateway.prefill.strategy import (
    PrefillScheduleContext,
    PrefillScheduleResult,
    PrefillStrategy,
    PrefillStrategyCapabilities,
)
from store.gateway.manager import GatewayStoreManager


class SchedulerManager:
    STRATEGY_TYPES = (
        SLOAwarePrefillStrategy,
        JSQPrefillStrategy,
        RoundRobinPrefillStrategy,
    )

    def __init__(
        self,
        strategy: str = "slo_aware_prefill",
        storage: Optional[GatewayStoreManager] = None,
    ):
        self.storage = storage if storage is not None else GatewayStoreManager()
        self._strategies = {
            strategy_type.name: strategy_type(self.storage)
            for strategy_type in self.STRATEGY_TYPES
        }
        self._strategy = ""
        self.strategy = strategy

    @property
    def strategy(self) -> str:
        return self._strategy

    @strategy.setter
    def strategy(self, strategy: str) -> None:
        if strategy not in self._strategies:
            raise ValueError(
                f"Unsupported Prefill scheduler strategy: {strategy}"
            )
        self._strategy = strategy

    @property
    def strategies(self) -> Tuple[str, ...]:
        return tuple(self._strategies)

    def get_strategy(self, strategy: Optional[str] = None) -> PrefillStrategy:
        selected = strategy or self.strategy
        try:
            return self._strategies[selected]
        except KeyError as exc:
            raise ValueError(
                f"Unsupported Prefill scheduler strategy: {selected}"
            ) from exc

    def get_capabilities(
        self,
        strategy: Optional[str] = None,
    ) -> PrefillStrategyCapabilities:
        return self.get_strategy(strategy).capabilities

    @staticmethod
    def _annotate_selection(
        result: PrefillScheduleResult,
        strategy: str,
    ) -> None:
        for request in result.requests:
            metadata = dict(
                getattr(request.Task, "scheduler_metadata", {}) or {}
            )
            metadata["gateway_prefill_selection"] = {
                "strategy": strategy,
                "selected_instance": getattr(
                    request.Task, "prefill_instance", ""
                ),
                "selection_source": "prefill_gateway_scheduler",
                "reason": result.selection_reason,
                "candidates": result.candidate_snapshots.get(
                    str(request.Request_ID), []
                ),
                "planned_batch_order": getattr(
                    request.Task, "batch_order", None
                ),
                "planned_start_s": getattr(
                    request.Task, "slo_planned_start", None
                ),
                "planned_finish_s": getattr(
                    request.Task, "slo_planned_finish", None
                ),
            }
            request.Task.scheduler_metadata = metadata

    async def schedule_batch(
        self,
        queue: List[BaseRequest],
        last_plan: Optional[List[BaseRequest]] = None,
        inflight_prefill: Optional[List[object]] = None,
        strategy: Optional[str] = None,
    ) -> Tuple[List[BaseRequest], str]:
        if not queue:
            return [], "IMMEDIATE"

        selected_strategy = strategy or self.strategy
        implementation = self.get_strategy(selected_strategy)
        result = await implementation.schedule(
            PrefillScheduleContext(
                requests=queue,
                last_plan=last_plan,
                inflight_prefill=inflight_prefill,
            )
        )
        self._annotate_selection(result, selected_strategy)
        return result.requests, result.dispatch_policy

    @staticmethod
    def _request_strategy_name(request: BaseRequest) -> Optional[str]:
        metadata = dict(
            getattr(request.Task, "scheduler_metadata", {}) or {}
        )
        selection = metadata.get("gateway_prefill_selection") or {}
        strategy = selection.get("strategy")
        if isinstance(strategy, str) and strategy:
            return strategy
        if "jsq" in metadata:
            return "jsq"
        return None

    def on_schedule_discarded(
        self,
        requests: List[BaseRequest],
        strategy: Optional[str] = None,
    ) -> None:
        if strategy is not None:
            self.get_strategy(strategy).on_schedule_discarded(requests)
            return
        grouped = {}
        for request in requests:
            name = self._request_strategy_name(request)
            if name in self._strategies:
                grouped.setdefault(name, []).append(request)
        for name, grouped_requests in grouped.items():
            self._strategies[name].on_schedule_discarded(grouped_requests)

    def on_request_finished(self, request: BaseRequest) -> None:
        strategy = self._request_strategy_name(request)
        if strategy in self._strategies:
            self._strategies[strategy].on_request_finished(request)

    def on_switch_out(
        self,
        strategy: str,
        requests: List[BaseRequest],
    ) -> None:
        self.get_strategy(strategy).on_switch_out(requests)
