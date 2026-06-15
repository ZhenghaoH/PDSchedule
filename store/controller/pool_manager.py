import threading
import time
from typing import Dict, List, Union, Optional, Tuple

# 导入上面定义的类
from store.controller.pool_base import BasePool, PrefillPool, DecodePool, PoolType


class PoolManager:
    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super(PoolManager, cls).__new__(cls)
                    cls._instance._init_db()
        return cls._instance

    def _init_db(self):
        # 主存储: {'prefill': {id: Obj}, 'decode': {id: Obj}}
        self._pools: Dict[str, Dict[int, BasePool]] = {
            PoolType.PREFILL.value: {},
            PoolType.DECODE.value: {}
        }

        # 反向索引: (type_str, ip, port) -> pool_id
        self._ip_index: Dict[Tuple[str, str, int], int] = {}
        self.db_lock = threading.RLock()

    def add_decode_load(self, pool_id: int, load: float) -> bool:
        """在调度时为 Decode 池直接增加一笔逻辑负载。"""
        with self.db_lock:
            target_storage = self._pools[PoolType.DECODE.value]
            if pool_id not in target_storage:
                print(f"[Manager] Error: decode Pool {pool_id} not registered.")
                return False

            pool = target_storage[pool_id]
            normalized_load = max(0.0, float(load))
            pool.current_load = max(0.0, pool.current_load + normalized_load)
            pool.last_updated = time.time()
            # print(
            #     f"[Manager] Decode Pool {pool_id} add_load: "
            #     f"load={normalized_load:.4f}, "
            #     f"effective={pool.current_load:.4f}"
            # )
            return True

    def sub_decode_load(self, pool_id: int, load: float) -> bool:
        """在请求结束时为 Decode 池减去一笔逻辑负载。"""
        with self.db_lock:
            target_storage = self._pools[PoolType.DECODE.value]
            if pool_id not in target_storage:
                return False

            pool = target_storage[pool_id]
            reduced_load = max(0.0, float(load))
            if reduced_load <= 0:
                return False

            pool.current_load = max(0.0, pool.current_load - reduced_load)
            pool.last_updated = time.time()
            print(
                f"[Manager] Decode Pool {pool_id} sub_load: "
                f"load={reduced_load:.4f}, "
                f"effective={pool.current_load:.4f}"
            )
            return True

    def touch_pool(self, pool_type: PoolType, pool_id: int) -> bool:
        with self.db_lock:
            target_storage = self._pools[pool_type.value]
            if pool_id not in target_storage:
                return False
            target_storage[pool_id].last_updated = time.time()
            return True

    def get_pool_by_id(self, pool_type: PoolType, pool_id: int) -> Optional[BasePool]:
        """
        根据 ID 获取节点对象。
        供 NetworkManager 在创建链路时使用。
        """
        type_key = pool_type.value
        with self.db_lock:
            return self._pools[type_key].get(pool_id)

    def get_id_by_ip_port(self, pool_type: PoolType, ip: str, port: int) -> Optional[int]:
        """
        根据 (类型, IP, Port) 查找 ID。
        时间复杂度: O(1)
        """
        type_key = pool_type.value
        index_key = (type_key, ip, port)

        with self.db_lock:
            # 直接从哈希表中获取，无需遍历
            return self._ip_index.get(index_key)

    # ==========================================
    # 统一接口 A1: 注册 (Register)
    # ==========================================
    def register_Pool(self,
                      Pool_type: PoolType,
                      ip: str,
                      port: int,
                      capacity: float,
                      max_prompt_throughput: float = None,
                      max_decode_req_num: int = 128) -> int:
        """
        注册或更新资源池
        返回分配的池ID
        """
        type_key = Pool_type.value

        with self.db_lock:
            current_time = time.time()
            target_storage = self._pools[type_key]

            # 防重复检查：根据IP和端口查找是否已存在相同的池
            index_key = (type_key, ip, port)
            existing_pool_id = self._ip_index.get(index_key)
            
            # 如果已存在相同类型、IP和端口的池，直接使用之前的ID
            if existing_pool_id is not None:
                print(f"[Manager] Info: {type_key} Pool with IP {ip}:{port} already exists with ID {existing_pool_id}. Using existing ID.")
                pool_obj = target_storage[existing_pool_id]
                pool_obj.last_updated = current_time
                
                if Pool_type == PoolType.PREFILL:
                    pool_obj.flops = capacity
                    print(f"[Manager] Info: {type_key} Pool {existing_pool_id} flops updated to {capacity}.")
                elif Pool_type == PoolType.DECODE:
                    pool_obj.mem_bandwidth = capacity
                    pool_obj.max_decode_req_num = max(1, int(max_decode_req_num))
                    print(f"[Manager] Info: {type_key} Pool {existing_pool_id} mem_bandwidth updated to {capacity}.")
                
                return existing_pool_id

            # 分配新ID
            if target_storage:
                # 简单的自增策略：最大ID + 1
                new_pool_id = max(target_storage.keys()) + 1
            else:
                # 如果是第一个节点，从不同的起始值开始
                if Pool_type == PoolType.PREFILL:
                    new_pool_id = 1
                else:  # DECODE
                    new_pool_id = 201

            # 创建新池
            if Pool_type == PoolType.PREFILL:
                # 对于 PrefillPool，确保 max_prompt_throughput 有值
                if max_prompt_throughput is None:
                    max_prompt_throughput = 0.0
                new_Pool = PrefillPool(
                    pool_id=new_pool_id, proxy_ip_address=ip, proxy_port=port,
                    last_updated=current_time, flops=capacity, max_prompt_throughput=max_prompt_throughput
                )
            elif Pool_type == PoolType.DECODE:
                new_Pool = DecodePool(
                    pool_id=new_pool_id, proxy_ip_address=ip, proxy_port=port,
                    last_updated=current_time, mem_bandwidth=capacity,
                    max_decode_req_num=max(1, int(max_decode_req_num))
                )
            else:
                return -1

            target_storage[new_pool_id] = new_Pool
            print(f"[Manager] {type_key.capitalize()} Pool {new_pool_id} registered.")

            # 更新反向索引 O(1)
            self._ip_index[index_key] = new_pool_id

            return new_pool_id

    # ==========================================
    # 统一接口 A2: 更新状态 (Update/Heartbeat)
    # ==========================================
    def update_Pool_status(self, Pool_type: PoolType, pool_id: int, load: float) -> bool:
        """
        统一心跳接口。
        因为 update 逻辑对于两种节点完全一样（都是更新 load 和 time），
        所以代码高度复用。
        """
        type_key = Pool_type.value

        with self.db_lock:
            target_storage = self._pools[type_key]

            if pool_id not in target_storage:
                print(f"[Manager] Error: {type_key} Pool {pool_id} not registered.")
                return False

            Pool = target_storage[pool_id]
            Pool.current_load = load
            Pool.last_updated = time.time()

            # print(f"[Manager] {type_key} Pool {pool_id} heartbeat: load={load}")
            return True

    def update_decode_running_reqs(self, pool_id: int, running_reqs: int, max_decode_req_num: int) -> bool:
        """按 Decode 池 running requests 数归一化更新负载。"""
        with self.db_lock:
            target_storage = self._pools[PoolType.DECODE.value]
            if pool_id not in target_storage:
                print(f"[Manager] Error: decode Pool {pool_id} not registered.")
                return False

            pool = target_storage[pool_id]
            safe_running_reqs = max(0, int(running_reqs))
            safe_max_decode_req_num = max(1, int(max_decode_req_num))
            normalized_load = min(max(safe_running_reqs / safe_max_decode_req_num, 0.0), 1.0)

            pool.running_reqs = safe_running_reqs
            pool.max_decode_req_num = safe_max_decode_req_num
            pool.current_load = normalized_load
            pool.last_updated = time.time()
            return True

    def unregister_Pool(self, Pool_type: PoolType, pool_id: int) -> bool:
        """注销资源池，只删除对应类型和 ID 的池记录。"""
        type_key = Pool_type.value

        with self.db_lock:
            target_storage = self._pools[type_key]
            if pool_id not in target_storage:
                print(f"[Manager] Error: {type_key} Pool {pool_id} not registered.")
                return False

            pool = target_storage.pop(pool_id)
            index_key = (type_key, pool.proxy_ip_address, pool.proxy_port)
            if self._ip_index.get(index_key) == pool_id:
                del self._ip_index[index_key]
            print(f"[Manager] {type_key.capitalize()} Pool {pool_id} unregistered.")
            return True

    # ==========================================
    # 统一接口 B: 获取活跃节点 (Read)
    # ==========================================
    def get_active_Pools(self, Pool_type: PoolType, timeout: int = 3000) -> List[Union[PrefillPool, DecodePool]]:
        """根据类型获取活跃节点列表"""
        type_key = Pool_type.value
        active_Pools = []
        now = time.time()

        with self.db_lock:
            target_storage = self._pools[type_key]
            for pid, Pool in target_storage.items():
                if now - Pool.last_updated <= timeout:
                    active_Pools.append(Pool)
                else:
                    print(f"[Manager] Pool {pid} ({type_key}) is offline.")

        return active_Pools

    def show_pool_id(self):
        """FIXME: 展示id和pool的对应关系，以后可能更改为更通用的接口"""
        with self.db_lock:
            print()
            print("PoolType | ID  | Address")
            print("---------|-----|----------------")
            for pool_type in [PoolType.PREFILL, PoolType.DECODE]:
                for v in self._pools[pool_type.value].values():
                    print(f"{pool_type.value:8} | {v.pool_id:3} | {v.proxy_ip_address}:{v.proxy_port}")
            print()

    def add_req(self, req_obj):
        """FIXME: 用于演示的临时函数 存储请求"""
        if not hasattr(self, '_req_list'):
            self._req_list = []
        self._req_list.append(req_obj)

    def get_reqs(self):
        """FIXME: 用于演示的临时函数 获取req_list，如果不存在则创建空列表"""
        if not hasattr(self, '_req_list'):
            self._req_list = []
        return self._req_list
