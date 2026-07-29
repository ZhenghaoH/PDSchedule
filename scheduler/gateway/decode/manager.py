from typing import Any, Dict, Optional, Tuple

from core.schemas.request import BaseRequest
from scheduler.gateway.decode.lowest_generation_throughput import (
    LowestGenerationThroughputDecodeStrategy,
)
from scheduler.gateway.decode.round_robin import RoundRobinDecodeStrategy
from scheduler.gateway.decode.slo_aware_decode import SLOAwareDecodeStrategy
from scheduler.gateway.decode.strategy import DecodeStrategy
from store.gateway.manager import GatewayStoreManager


class SchedulerManager:
    STRATEGY_TYPES = (
        RoundRobinDecodeStrategy,
        LowestGenerationThroughputDecodeStrategy,
        SLOAwareDecodeStrategy,
    )

    def __init__(
        self,
        strategy: str = "slo_aware_decode",
        storage: Optional[GatewayStoreManager] = None,
    ):
        self.storage = storage if storage is not None else GatewayStoreManager()
        self._strategies = {
            strategy_type.name: strategy_type(self.storage)
            for strategy_type in self.STRATEGY_TYPES
        }
        self._strategy = ""
        self.last_decision: Dict[str, Any] = {}
        self.strategy = strategy

    @property
    def strategy(self) -> str:
        return self._strategy

    @strategy.setter
    def strategy(self, strategy: str) -> None:
        if strategy not in self._strategies:
            raise ValueError(
                f"Unsupported Decode scheduler strategy: {strategy}"
            )
        self._strategy = strategy

    @property
    def strategies(self) -> Tuple[str, ...]:
        return tuple(self._strategies)

    def get_strategy(self, strategy: Optional[str] = None) -> DecodeStrategy:
        selected = strategy or self.strategy
        try:
            return self._strategies[selected]
        except KeyError as exc:
            raise ValueError(
                f"Unsupported Decode scheduler strategy: {selected}"
            ) from exc

    async def schedule(
        self,
        request: BaseRequest,
        strategy: Optional[str] = None,
    ) -> str:
        selected_strategy = strategy or self.strategy
        result = await self.get_strategy(selected_strategy).schedule(request)
        self.last_decision = {
            "strategy": selected_strategy,
            "selected_instance": result.selected_instance,
            "selection_source": "decode_gateway_scheduler",
            "reason": result.selection_reason,
            "candidates": result.candidates,
            **result.details,
        }
        metadata = dict(
            getattr(request.Task, "scheduler_metadata", {}) or {}
        )
        metadata["gateway_decode_selection"] = dict(self.last_decision)
        request.Task.scheduler_metadata = metadata
        return result.selected_instance
