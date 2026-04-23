import threading
from typing import List, Tuple

from store.controller.pool_base import DecodePool, PrefillPool


# ==========================================
#   轮询调度策略实现 (Round Robin)
# ==========================================
class RoundRobin_Scheduler:
    """
    轮询调度器 (Round-Robin Scheduler)
    在所有可能的 Prefill/Decode 配对中按顺序轮询选择。
    """

    def __init__(self):
        self._pair_index = 0
        self._lock = threading.Lock()

    def schedule(
        self,
        prefill_pools: List[PrefillPool],
        decode_pools: List[DecodePool],
    ) -> Tuple[str, int, str, int]:
        """
        执行调度：
        1. 检查资源池是否为空
        2. 动态构造所有 P-D 配对
        3. 使用单一轮询索引选择当前配对
        4. 返回选中节点的地址
        """
        if not prefill_pools or not decode_pools:
            raise ValueError("Nodes list is empty")

        with self._lock:
            all_pairs = [
                (p_node, d_node)
                for p_node in prefill_pools
                for d_node in decode_pools
            ]

            pair_idx = self._pair_index % len(all_pairs)
            p_node, d_node = all_pairs[pair_idx]
            self._pair_index = (self._pair_index + 1) % len(all_pairs)

            print(
                f"[RoundRobin] pair_index={pair_idx} "
                f"selected_prefill=({getattr(p_node, 'pool_id', 'N/A')}, {p_node.proxy_ip_address}:{p_node.proxy_port}) "
                f"selected_decode=({getattr(d_node, 'pool_id', 'N/A')}, {d_node.proxy_ip_address}:{d_node.proxy_port})"
            )

        return (
            p_node.proxy_ip_address,
            p_node.proxy_port,
            d_node.proxy_ip_address,
            d_node.proxy_port,
        )


# ==========================================
#   成对 PD 轮询调度策略实现 (Paired Round Robin)
# ==========================================
class PairedRoundRobin_Scheduler:
    """
    成对 PD 轮询调度器。

    与 RoundRobin_Scheduler 不同，这里不构造所有 P-D 笛卡尔积，而是把
    排序后的 Prefill 池和 Decode 池按位置固定配对：

        P1 -> D1
        P2 -> D2
        ...

    调度时只在这些固定 pair 中轮询。这样当轮到 P1 时，一定选择它绑定的 D1。
    """

    def __init__(self):
        self._pair_index = 0
        self._lock = threading.Lock()

    @staticmethod
    def _sort_by_pool_id(pools):
        return sorted(pools, key=lambda pool: getattr(pool, "pool_id", 0))

    def schedule(
        self,
        prefill_pools: List[PrefillPool],
        decode_pools: List[DecodePool],
    ) -> Tuple[str, int, str, int]:
        """
        执行调度：
        1. 检查资源池是否为空
        2. 按 pool_id 排序 P 池和 D 池
        3. 按相同位置建立固定 P-D pair
        4. 使用单一轮询索引选择当前 pair
        5. 返回选中 pair 的地址
        """
        if not prefill_pools or not decode_pools:
            raise ValueError("Nodes list is empty")

        with self._lock:
            sorted_prefills = self._sort_by_pool_id(prefill_pools)
            sorted_decodes = self._sort_by_pool_id(decode_pools)
            pair_count = min(len(sorted_prefills), len(sorted_decodes))

            if pair_count <= 0:
                raise ValueError("No valid paired nodes available")

            if len(sorted_prefills) != len(sorted_decodes):
                print(
                    "[PairedRoundRobin] Warning: Prefill/Decode pool counts differ. "
                    f"prefill={len(sorted_prefills)}, decode={len(sorted_decodes)}, "
                    f"paired={pair_count}. Extra pools are ignored."
                )

            pair_idx = self._pair_index % pair_count
            p_node = sorted_prefills[pair_idx]
            d_node = sorted_decodes[pair_idx]
            self._pair_index = (self._pair_index + 1) % pair_count

            print(
                f"[PairedRoundRobin] pair_index={pair_idx} "
                f"selected_prefill=({getattr(p_node, 'pool_id', 'N/A')}, {p_node.proxy_ip_address}:{p_node.proxy_port}) "
                f"selected_decode=({getattr(d_node, 'pool_id', 'N/A')}, {d_node.proxy_ip_address}:{d_node.proxy_port})"
            )

        return (
            p_node.proxy_ip_address,
            p_node.proxy_port,
            d_node.proxy_ip_address,
            d_node.proxy_port,
        )
