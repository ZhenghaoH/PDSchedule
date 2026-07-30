import threading

from scheduler.controller.resource_state import ResourceSnapshot
from scheduler.controller.strategy import (
    GlobalScheduleContext,
    GlobalScheduleDecision,
)


class LeastLoadStrategy:
    name = "least_load"

    def __init__(self):
        self._lock = threading.Lock()

    def schedule(
        self,
        context: GlobalScheduleContext,
        resources: ResourceSnapshot,
    ) -> GlobalScheduleDecision:
        del context
        if not resources.prefill_pools or not resources.decode_pools:
            raise ValueError("Nodes list is empty")

        with self._lock:
            selected_prefill = min(
                resources.prefill_pools,
                key=lambda pool: getattr(pool, "current_load", 0.0),
            )
            selected_decode = min(
                resources.decode_pools,
                key=lambda pool: getattr(pool, "current_load", 0.0),
            )
            prefill_loads = [
                (pool.pool_id, getattr(pool, "current_load", 0.0))
                for pool in resources.prefill_pools
            ]
            decode_loads = [
                (pool.pool_id, getattr(pool, "current_load", 0.0))
                for pool in resources.decode_pools
            ]
            print(
                f"[LeastLoad] prefill_loads={prefill_loads} "
                f"decode_loads={decode_loads} "
                f"selected_prefill=({selected_prefill.pool_id}, "
                f"{selected_prefill.current_load}) "
                f"selected_decode=({selected_decode.pool_id}, "
                f"{selected_decode.current_load})"
            )

        return GlobalScheduleDecision(
            prefill_address=selected_prefill.proxy_ip_address,
            prefill_port=selected_prefill.proxy_port,
            decode_address=selected_decode.proxy_ip_address,
            decode_port=selected_decode.proxy_port,
            strategy=self.name,
        )
