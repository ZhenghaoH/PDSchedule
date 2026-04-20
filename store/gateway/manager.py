from typing import List, Dict, Any, Optional
import threading
from copy import deepcopy
import os
import time

from .instance import Instance
from .link import InstanceLink


class GatewayStoreManager:
    """存储管理"""
    _instance = None
    _lock = threading.RLock()
    _initialized = False

    def __new__(cls, *args, **kwargs):
        """单例模式实现"""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        """初始化存储实例，防止重复初始化"""
        if not self._initialized:
            with self._lock:
                if not self._initialized:
                    self._prefill_objs = []  # prefill 池或实例
                    self._decode_objs = []  # decode 池或实例
                    self._instance_table: Dict[str, Instance] = {}
                    self._link_table: Dict[str, InstanceLink] = {}  # 实例配对路由表
                    self._data_lock = threading.RLock()  # 数据操作锁

                    self._pool_id: Optional[int] = None
                    self._network_register_table: List[str] = []

                    self.host = "127.0.0.1"  # 临时存储 主机host
                    self.port = 8081  # 临时存储 主机监听端口
                    self.controller = ""  # 临时变量 控制器监听host
                    self.rank = 0  # 身份

                    self.USE_SYN = True  # 同步设置 是否启用同步
                    self.SYN_BATCH_SIZE = 1  # 同步设置 批处理批次
                    self.SYN_TIMEOUT = 1  # 同步设置 等待时间 秒

                    self.TRIGGER_MODE = "ARRIVAL"
                    self.PROFILE_MODE = True

                    self.KDN_BASE_URL = "http://172.18.0.169:9101"  # FIXME: 值传递有问题，记得修复

                    self.LOOP_TASK_TIME = 0.1  # 轮询上报任务时间

                    self._initialized = True  # 确保不重复初始化

    def set_pool_id(self, pool_id: Optional[int]):
        """设置pool_id"""
        with self._data_lock:
            self._pool_id = pool_id

    def get_pool_id(self):
        with self._data_lock:
            return self._pool_id

    @property
    def network_register_table(self) -> List[str]:
        """获取链路注册列表的副本"""
        with self._data_lock:
            return self._network_register_table.copy()

    def register_network(self, network: str):
        with self._data_lock:
            if network not in self._network_register_table:
                self._network_register_table.append(network)

    def unregister_network(self, network: str):
        with self._data_lock:
            if network in self._network_register_table:
                self._network_register_table.remove(network)

    @property
    def prefill_objs(self) -> List[str]:
        """获取prefill代理列表的副本"""
        with self._data_lock:
            return self._prefill_objs.copy()

    @property
    def decode_objs(self) -> List[str]:
        """获取decode代理列表的副本"""
        with self._data_lock:
            return self._decode_objs.copy()

    def set_config(self, prefills: Optional[List[str]] = None, decodes: Optional[List[str]] = None):
        """线程安全的配置设置方法"""
        with self._data_lock:
            if prefills is not None:
                self._prefill_objs = prefills.copy() if prefills else []
                for prefill_instance in self._prefill_objs:
                    self._instance_table[prefill_instance] = Instance(instance_ip=prefill_instance)
            if decodes is not None:
                self._decode_objs = decodes.copy() if decodes else []
                for decode_instance in self._decode_objs:
                    self._instance_table[decode_instance] = Instance(instance_ip=decode_instance)

    @property
    def instance_table(self) -> Dict[str, Any]:
        """获取nodes的深拷贝"""
        with self._data_lock:
            return deepcopy(self._instance_table)

    def set_instance_table(self, key: str, value: Any):
        """设置nodes的特定键值"""
        with self._data_lock:
            self._instance_table[key] = deepcopy(value)

    @property
    def link_table(self) -> Dict[str, Any]:
        """获取links的深拷贝"""
        with self._data_lock:
            return deepcopy(self._link_table)

    def set_link_table(self, key: str, value: Any):
        """设置links的特定键值"""
        with self._data_lock:
            self._link_table[key] = deepcopy(value)

    def remove_from_link_table(self, key: str):
        """从link_table中删除指定键"""
        with self._data_lock:
            if key in self._link_table:
                del self._link_table[key]

    def get_compute_params(self):
        """获取 GPUtype & FLOPS & memBandwidth & max_prompt_throughput，理论上一个池里每个实例都是一样的"""
        if len(self.instance_table) > 0:
            instance = list(self.instance_table.values())[0]
            return instance.GPU_type, instance.flops, instance.mem_bandwidth, instance.max_prompt_throughput
        else:
            return os.getenv("MY_GPU_TYPE", "NVIDIA RTX5090"), float(os.getenv("MY_FLOPS", 17.9)), float(
                os.getenv("MY_MEM_BANDWIDTH", 800.0)), float(os.getenv("MY_MAX_PROMPT_THROUGHPUT", 37829.0))

    def get_prefill_load(self):
        """获取池负载状态, 目前取值所有实例的当前prompt throughput"""
        # import random
        # return round(random.uniform(0.1, 0.8), 2)
        load = 0
        for _, instance in self.instance_table.items():
            prompt_throughput = instance.prompt_throughput
            load += prompt_throughput
        return load

    def get_decode_load(self):
        """Decode current_load 已切到控制器调度侧维护，网关侧暂不再负责计算。"""
        return 0.0

    def get_peer_links(self):
        """给出本推理池（Prefill）和对端的推理池（Decode）之间的所有连接"""
        peer_links = {}
        for link in self._link_table.values():
            peer_addr = link.decode_proxy
            if peer_addr not in peer_links:
                peer_links[peer_addr] = {
                    "prefill_ip": link.prefill_proxy.rsplit(":", 1)[0],
                    "prefill_port": link.prefill_proxy.rsplit(":", 1)[1],
                    "decode_ip": link.decode_proxy.rsplit(":", 1)[0],
                    "decode_port": link.decode_proxy.rsplit(":", 1)[1],
                    "bandwidth": link.network.bottleneck_bandwidth,
                    "rtt": link.network.round_trip_time,
                    "count": 1
                }
            else:
                peer_links[peer_addr]["count"] += 1
        return peer_links

    def get_availability(self, instance_ip: str) -> float:
        """获取实例预计可用时间"""
        with self._lock:
            if instance_ip in self._instance_table.keys():
                instance = self._instance_table[instance_ip]
                return instance.last_updated
            else:
                return 0.0

    def update_availability(self, instance_ip: str, actual_finish_time: float):
        """
        更新实例可用时间。
        如果实例不存在（例如动态扩容或端口变化），则自动注册它。
        """
        with self._lock:
            if instance_ip not in self._instance_table.keys():
                # logging.info(f"Registered new instance: {instance_ip}")
                self.prefill_objs.append(instance_ip)
                self._instance_table[instance_ip] = Instance(instance_ip=instance_ip)
            else:
                self._instance_table[instance_ip].last_updated = actual_finish_time
