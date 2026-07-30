import threading

from scheduler.controller.resource_state import ResourceSnapshot
from scheduler.controller.strategy import (
    GlobalScheduleContext,
    GlobalScheduleDecision,
)


class RoundRobinStrategy:
    name = "round_robin"

    def __init__(self):
        self._pair_index = 0
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
            all_pairs = [
                (prefill, decode)
                for prefill in resources.prefill_pools
                for decode in resources.decode_pools
            ]
            pair_index = self._pair_index % len(all_pairs)
            prefill, decode = all_pairs[pair_index]
            self._pair_index = (self._pair_index + 1) % len(all_pairs)

            print(
                f"[RoundRobin] pair_index={pair_index} "
                f"selected_prefill=({prefill.pool_id}, "
                f"{prefill.proxy_ip_address}:{prefill.proxy_port}) "
                f"selected_decode=({decode.pool_id}, "
                f"{decode.proxy_ip_address}:{decode.proxy_port})"
            )

        return GlobalScheduleDecision(
            prefill_address=prefill.proxy_ip_address,
            prefill_port=prefill.proxy_port,
            decode_address=decode.proxy_ip_address,
            decode_port=decode.proxy_port,
            strategy=self.name,
        )
