import threading
from typing import List, Tuple

from store.controller.pool_base import PrefillPool, DecodePool

# ==========================================
#   最小负载调度策略实现 (Least Load)
# ==========================================
class LeastLoad_Scheduler:
    """
    最小负载调度器 (Least Load Scheduler)
    选择负载最低的 Prefill 和 Decode 节点，实现负载均衡。
    """
    def __init__(self):
        # 线程锁，保证并发调用的原子性
        self._lock = threading.Lock()

    def schedule(self, prefill_pools: List[PrefillPool], decode_pools: List[DecodePool]) -> Tuple[str, int, str, int]:
        """
        执行调度：
        1. 检查资源池是否为空
        2. 选择负载最低的 Prefill 节点
        3. 选择负载最低的 Decode 节点
        4. 返回选中节点的地址
        """
        if not prefill_pools or not decode_pools:
            raise ValueError("Nodes list is empty")
        
        with self._lock:
            # 1. 选择负载最低的 Prefill 节点
            min_load_p = float('inf')
            selected_p_node = None
            prefill_loads = []
            for p_node in prefill_pools:
                current_load = getattr(p_node, 'current_load', 0.0)
                prefill_loads.append((getattr(p_node, 'pool_id', 'N/A'), current_load))
                if current_load < min_load_p:
                    min_load_p = current_load
                    selected_p_node = p_node
            
            # 2. 选择负载最低的 Decode 节点
            min_load_d = float('inf')
            selected_d_node = None
            decode_loads = []
            for d_node in decode_pools:
                current_load = getattr(d_node, 'current_load', 0.0)
                decode_loads.append((getattr(d_node, 'pool_id', 'N/A'), current_load))
                if current_load < min_load_d:
                    min_load_d = current_load
                    selected_d_node = d_node

            print(
                f"[LeastLoad] prefill_loads={prefill_loads} "
                f"decode_loads={decode_loads} "
                f"selected_prefill=({getattr(selected_p_node, 'pool_id', 'N/A')}, {min_load_p}) "
                f"selected_decode=({getattr(selected_d_node, 'pool_id', 'N/A')}, {min_load_d})"
            )
        
        return (selected_p_node.proxy_ip_address, selected_p_node.proxy_port, 
                selected_d_node.proxy_ip_address, selected_d_node.proxy_port)
