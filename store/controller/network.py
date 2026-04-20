import time
import threading
from dataclasses import dataclass, field
from typing import Dict, List, Tuple, Union
from enum import Enum

# --- 导入节点定义 ---
from store.controller.pool_base import PrefillPool, DecodePool, PoolType
from store.controller.pool_manager import PoolManager

# =============================================================================
#                                 1. Network (数据实体)
# =============================================================================

@dataclass
class Network:
    """
    Network实体类：表示 Prefill节点 和 Decode节点 之间的网络链路状态。
    """
    prefill_pool: "PrefillPool"  
    decode_pool: "DecodePool"   
    bandwidth: float             # Mbps
    rtt: float                   # ms
    last_updated: float = field(default_factory=time.time)

    def to_dict(self) -> Dict:
        """序列化为字典 (仅存ID，避免循环引用)"""
        return {
            "prefill_pool_id": self.prefill_pool.pool_id,
            "decode_pool_id": self.decode_pool.pool_id,
            "bandwidth": self.bandwidth,
            "rtt": self.rtt,
            "last_updated": self.last_updated
        }

    def __repr__(self):
        return (f"Network(P{self.prefill_pool.pool_id} -> D{self.decode_pool.pool_id}, "
                f"BW={self.bandwidth:.1f}, RTT={self.rtt:.1f})")


# =============================================================================
#                                 2. ResourceManager (管理层)
# =============================================================================

class NetworkManager:
    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        """单例模式"""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super(NetworkManager, cls).__new__(cls)
                    cls._instance._init_db()
        return cls._instance

    def _init_db(self):
        """初始化内存存储"""

        # 网络存储: Key为 (prefill_id, decode_id) 元组
        self._networks: Dict[Tuple[int, int], Network] = {}
        
        # 读写锁
        self.db_lock = threading.RLock()

        self.pool_manager = PoolManager()
        
        # 初始化所有 PD 池之间的连接
        self._initialize_all_pd_connections()
        
    def _initialize_all_pd_connections(self):
        """初始化所有 Prefill 池和 Decode 池之间的连接"""
        with self.db_lock:
            # 获取所有 Prefill 池和 Decode 池
            prefill_pools = list(self.pool_manager._pools.get(PoolType.PREFILL.value, {}).values())
            decode_pools = list(self.pool_manager._pools.get(PoolType.DECODE.value, {}).values())
            
            # 为每个 Prefill 池和 Decode 池之间创建连接
            for prefill_pool in prefill_pools:
                for decode_pool in decode_pools:
                    link_key = (prefill_pool.pool_id, decode_pool.pool_id)
                    if link_key not in self._networks:
                        # 创建默认的网络连接
                        new_net = Network(
                            prefill_pool=prefill_pool,
                            decode_pool=decode_pool,
                            bandwidth=1000.0,  # 默认带宽
                            rtt=10.0  # 默认延迟
                        )
                        self._networks[link_key] = new_net
                        print(f"[Manager] Initialized link: {new_net}")
    
    def update_connections_for_pool(self, pool_id: int, pool_type: PoolType):
        """当资源池更新时，更新相关的网络连接状态"""
        with self.db_lock:
            # 找出与该池相关的所有连接
            affected_links = []
            
            if pool_type == PoolType.PREFILL:
                # 所有以该 Prefill 池为起点的连接
                for (p_id, d_id) in list(self._networks.keys()):
                    if p_id == pool_id:
                        affected_links.append((p_id, d_id))
            elif pool_type == PoolType.DECODE:
                # 所有以该 Decode 池为终点的连接
                for (p_id, d_id) in list(self._networks.keys()):
                    if d_id == pool_id:
                        affected_links.append((p_id, d_id))
            
            # 更新每个受影响的连接
            for link_key in affected_links:
                prefill_id, decode_id = link_key
                if link_key in self._networks:
                    net = self._networks[link_key]
                    
                    # 获取两端池的可用带宽
                    prefill_pool = net.prefill_pool
                    decode_pool = net.decode_pool
                    
                    # 根据池类型和当前负载计算可用带宽
                    # Prefill 池使用 flops，Decode 池使用 mem_bandwidth
                    prefill_total = getattr(prefill_pool, 'flops', 1000.0)
                    decode_total = getattr(decode_pool, 'mem_bandwidth', 1000.0)
                    
                    # 计算可用带宽（总带宽 * (1 - 负载)）
                    prefill_available = prefill_total * (1 - min(prefill_pool.current_load, 1.0))
                    decode_available = decode_total * (1 - min(decode_pool.current_load, 1.0))
                    
                    # 取较低的带宽值
                    new_bandwidth = min(prefill_available, decode_available)
                    
                    # 更新网络连接
                    net.bandwidth = new_bandwidth
                    net.last_updated = time.time()
                    print(f"[Manager] Updated link {net} with bandwidth {new_bandwidth}")
    
    def add_pool(self, pool):
        """当添加新池时，创建与所有对应类型池的连接"""
        with self.db_lock:
            if isinstance(pool, PrefillPool):
                # 为新 Prefill 池创建与所有 Decode 池的连接
                decode_pools = list(self.pool_manager._pools.get(PoolType.DECODE.value, {}).values())
                for decode_pool in decode_pools:
                    link_key = (pool.pool_id, decode_pool.pool_id)
                    if link_key not in self._networks:
                        new_net = Network(
                            prefill_pool=pool,
                            decode_pool=decode_pool,
                            bandwidth=1000.0,
                            rtt=10.0
                        )
                        self._networks[link_key] = new_net
                        print(f"[Manager] Added link: {new_net}")
            elif isinstance(pool, DecodePool):
                # 为新 Decode 池创建与所有 Prefill 池的连接
                prefill_pools = list(self.pool_manager._pools.get(PoolType.PREFILL.value, {}).values())
                for prefill_pool in prefill_pools:
                    link_key = (prefill_pool.pool_id, pool.pool_id)
                    if link_key not in self._networks:
                        new_net = Network(
                            prefill_pool=prefill_pool,
                            decode_pool=pool,
                            bandwidth=1000.0,
                            rtt=10.0
                        )
                        self._networks[link_key] = new_net
                        print(f"[Manager] Added link: {new_net}")

    # -------------------------------------------------------------------------
    # A. 网络管理 (Network Management)
    # -------------------------------------------------------------------------

    def update_network_status(self, prefill_id: int, decode_id: int, bandwidth: float, rtt: float) -> bool:
        """
        更新网络链路状态。如果链路不存在，且两端节点都存在，则创建新链路对象。
        """
        link_key = (prefill_id, decode_id)
        
        with self.db_lock:
            current_time = time.time()
            
            # 1. 尝试更新现有链路
            if link_key in self._networks:
                net = self._networks[link_key]
                net.bandwidth = bandwidth
                net.rtt = rtt
                net.last_updated = current_time
                # print(f"[Manager] Link P{prefill_id}->D{decode_id} updated.")
                return True
            
            # 2. 如果不存在，创建新链路 (必须先获取到两端的节点对象引用)
            p_pool = self.pool_manager.get_pool_by_id(PoolType.PREFILL, prefill_id)
            d_pool = self.pool_manager.get_pool_by_id(PoolType.DECODE, decode_id)
            
            if not p_pool or not d_pool:
                # 如果任意一端节点没注册，就无法创建链路
                print(f"[Manager] Cannot create link P{prefill_id}->D{decode_id}: Node missing.")
                return False
            
            # 3. 组装 Network 对象
            new_net = Network(
                prefill_pool=p_pool,  # 引用真实对象
                decode_pool=d_pool,   # 引用真实对象
                bandwidth=bandwidth,
                rtt=rtt,
                last_updated=current_time
            )
            self._networks[link_key] = new_net
            print(f"[Manager] New Link created: {new_net}")
            return True

    def get_all_networks(self, timeout: int = 60) -> List[Network]:
        """获取所有活跃的网络连接"""
        active_nets = []
        now = time.time()
        with self.db_lock:
            for net in self._networks.values():
                if now - net.last_updated <= timeout:
                    active_nets.append(net)
        return active_nets
    