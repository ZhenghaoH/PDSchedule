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
        if not resources.compatible_pairs:
            raise ValueError("No connector-compatible P/D gateway pair")

        with self._lock:
            selected_prefill, selected_decode = min(
                resources.compatible_pairs,
                key=lambda pair: (
                    getattr(pair[0], "current_load", 0.0),
                    getattr(pair[1], "current_load", 0.0),
                ),
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
            connector=selected_prefill.connector["name"],
            connector_descriptor=selected_prefill.connector,
        )
