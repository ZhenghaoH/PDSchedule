import random
from typing import List

from store.controller import PrefillPool, DecodePool, Network



# ==========================================
#   简单调度策略实现 (Random)
# ==========================================
class Random_Scheduler:
    """一个简单的随机调度器，作为备选策略"""

    def schedule(self, prefill_pools: List[PrefillPool], decode_pools: List[DecodePool]) -> tuple:
        self.p_pools = prefill_pools
        self.d_pools = decode_pools
    
        if not self.p_pools or not self.d_pools:
            raise ValueError("Nodes list is empty")
        
        p = random.choice(self.p_pools)
        d = random.choice(self.d_pools)
        
        # 简单增加负载模拟
        p.current_load += 0.1
        d.current_load += 0.1
        
        return p.proxy_ip_address, p.proxy_port, d.proxy_ip_address, d.proxy_port
