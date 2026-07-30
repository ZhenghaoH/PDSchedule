from types import MappingProxyType
from typing import Any, Dict, Optional, Tuple

from core.schemas.request import Prompt, Service
from scheduler.controller.manager import StrategyManager
from scheduler.controller.resource_state import (
    ResourceSnapshot,
    ResourceStateManager,
)
from scheduler.controller.strategy import GlobalScheduleContext
from store.controller.network import NetworkManager
from store.controller.pool_manager import PoolManager


class GlobalScheduler:
    """Thin controller routing facade over strategy and resource managers."""

    def __init__(
        self,
        pool_manager: PoolManager,
        network_manager: NetworkManager,
        strategy_manager: Optional[StrategyManager] = None,
        resource_state: Optional[ResourceStateManager] = None,
    ):
        self.pool_manager = pool_manager
        self.network_manager = network_manager
        self.strategy_manager = strategy_manager or StrategyManager()
        self.resource_state = resource_state or ResourceStateManager(
            pool_manager,
            network_manager,
        )
        self.refresh_resources()
        print(
            "[GlobalScheduler] Initializing... "
            f"routing={self.routing_status()}"
        )

    @property
    def current_mode(self) -> str:
        return self.strategy_manager.strategy

    @property
    def available_strategies(self) -> Tuple[str, ...]:
        return self.strategy_manager.available_strategies

    @property
    def strategies(self):
        """Read-only compatibility view of the registered implementations."""
        return MappingProxyType(
            {
                name: self.strategy_manager.get_strategy(name)
                for name in self.available_strategies
            }
        )

    @property
    def prefill_pools(self):
        return self.resource_state.snapshot().prefill_pools

    @property
    def decode_pools(self):
        return self.resource_state.snapshot().decode_pools

    @property
    def networks(self):
        return self.resource_state.snapshot().networks

    @property
    def network_map(self):
        return self.resource_state.snapshot().network_map

    def refresh_resources(self) -> ResourceSnapshot:
        return self.resource_state.refresh()

    def sync_state(self) -> ResourceSnapshot:
        """Compatibility alias for periodic reconciliation callers."""
        return self.refresh_resources()

    @staticmethod
    def _uses_single_gateway_route(snapshot: ResourceSnapshot) -> bool:
        return (
            len(snapshot.prefill_pools) == 1
            and len(snapshot.decode_pools) == 1
        )

    def uses_single_gateway_route(self) -> bool:
        return self._uses_single_gateway_route(self.resource_state.snapshot())

    def routing_status(self) -> str:
        snapshot = self.resource_state.snapshot()
        if not snapshot.prefill_pools or not snapshot.decode_pools:
            topology = "resource_wait"
        elif self._uses_single_gateway_route(snapshot):
            topology = "single_gateway_direct"
        else:
            topology = "multi_gateway_scheduled"
        return (
            f"mode=global, topology={topology}, "
            f"scheduler={self.current_mode}, resources=v{snapshot.version}"
            f"(p={len(snapshot.prefill_pools)},"
            f"d={len(snapshot.decode_pools)},"
            f"network={len(snapshot.networks)}), "
            f"available={','.join(self.available_strategies)}"
        )

    def switch_mode(self, mode: str) -> bool:
        if self.strategy_manager.switch_strategy(mode):
            print(
                "[GlobalScheduler] Switched strategy to: "
                f"{self.current_mode.upper()}"
            )
            return True
        print(
            f"[GlobalScheduler] Error: Mode '{mode}' invalid. "
            f"Available: {list(self.available_strategies)}"
        )
        return False

    def schedule(
        self,
        prompt_obj: Prompt,
        service_obj: Service,
        request_id: int,
    ) -> Dict[str, Any]:
        snapshot = self.resource_state.snapshot()
        if not snapshot.prefill_pools or not snapshot.decode_pools:
            raise RuntimeError("Resources empty. Wait for pool registration.")

        if self._uses_single_gateway_route(snapshot):
            prefill = snapshot.prefill_pools[0]
            decode = snapshot.decode_pools[0]
            print(
                "[GlobalScheduler] Single P/D gateway topology detected; "
                f"direct route P{prefill.pool_id}"
                f"({prefill.proxy_ip_address}:{prefill.proxy_port}) -> "
                f"D{decode.pool_id}"
                f"({decode.proxy_ip_address}:{decode.proxy_port})"
            )
            return {
                "P_proxy_addr": prefill.proxy_ip_address,
                "P_proxy_port": prefill.proxy_port,
                "D_proxy_addr": decode.proxy_ip_address,
                "D_proxy_port": decode.proxy_port,
            }

        token_length = getattr(prompt_obj, "token_length", 0)
        max_tokens = getattr(prompt_obj, "max_tokens", None)
        max_tokens_fallback = max_tokens is None
        if max_tokens_fallback:
            max_tokens = token_length

        context = GlobalScheduleContext(
            token_length=token_length,
            max_tokens=max_tokens,
            slo_ttft=getattr(service_obj, "SLO_TTFT", 0.0),
            slo_tpot=getattr(service_obj, "SLO_TPOT", 0.0),
            compress_factor=getattr(
                service_obj,
                "Compress_factor",
                1.0,
            ),
            request_id=request_id,
            max_tokens_fallback=max_tokens_fallback,
        )
        return self.strategy_manager.schedule(
            context,
            snapshot,
        ).to_proxy_dict()
