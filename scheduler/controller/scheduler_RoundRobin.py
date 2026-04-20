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
