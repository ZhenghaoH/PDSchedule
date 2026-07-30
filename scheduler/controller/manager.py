import threading
from typing import Dict, Optional, Tuple

from scheduler.controller.resource_state import ResourceSnapshot
from scheduler.controller.strategies.demand_aware import DemandAwareStrategy
from scheduler.controller.strategies.least_load import LeastLoadStrategy
from scheduler.controller.strategies.round_robin import RoundRobinStrategy
from scheduler.controller.strategy import (
    GlobalScheduleContext,
    GlobalScheduleDecision,
    GlobalStrategy,
)


class StrategyManager:
    STRATEGY_TYPES = (
        DemandAwareStrategy,
        LeastLoadStrategy,
        RoundRobinStrategy,
    )

    def __init__(self, strategy: str = "demand_aware"):
        self._lock = threading.RLock()
        self._strategies: Dict[str, GlobalStrategy] = {
            strategy_type.name: strategy_type()
            for strategy_type in self.STRATEGY_TYPES
        }
        self._strategy = ""
        if not self.switch_strategy(strategy):
            raise ValueError(f"Unsupported controller strategy: {strategy}")

    @property
    def strategy(self) -> str:
        with self._lock:
            return self._strategy

    @property
    def available_strategies(self) -> Tuple[str, ...]:
        with self._lock:
            return tuple(self._strategies)

    def get_strategy(self, strategy: Optional[str] = None) -> GlobalStrategy:
        with self._lock:
            selected = strategy or self._strategy
            try:
                return self._strategies[selected]
            except KeyError as exc:
                raise ValueError(
                    f"Unsupported controller strategy: {selected}"
                ) from exc

    def replace_strategy(
        self,
        name: str,
        strategy: GlobalStrategy,
    ) -> None:
        """Explicit test/integration hook for replacing a registered strategy."""
        normalized = name.lower()
        with self._lock:
            if normalized not in self._strategies:
                raise ValueError(
                    f"Unsupported controller strategy: {normalized}"
                )
            self._strategies[normalized] = strategy

    def switch_strategy(self, strategy: str) -> bool:
        normalized = strategy.lower()
        with self._lock:
            if normalized not in self._strategies:
                return False
            self._strategy = normalized
            return True

    def schedule(
        self,
        context: GlobalScheduleContext,
        resources: ResourceSnapshot,
    ) -> GlobalScheduleDecision:
        implementation = self.get_strategy()
        return implementation.schedule(context, resources)
