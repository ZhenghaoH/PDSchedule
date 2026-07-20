from typing import List, Dict, Any, Optional, Callable, Awaitable
import threading
from copy import deepcopy
import os
import time

from .instance import Instance
from .link import InstanceLink


class GatewayStoreManager:
    """存储管理"""
    PREFILL_SCHEDULER_STRATEGIES = frozenset({"least_load", "round_robin", "pso_new", "distserve"})
    DECODE_SCHEDULER_STRATEGIES = frozenset({
        "round_robin",
        "lowest_generate_throughput",
        "slo_tpot_admission",
        "distserve",
    })
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
                    self.PROFILE_MODE = False
                    self.SCHEDULER_STRATEGY = "pso_new"
                    self.DECODE_SCHEDULER_STRATEGY = "slo_tpot_admission"
                    self.ENABLE_ADAPTIVE_SWITCH = False
                    
                    self.TTFT_WARMUP_ENABLED = False
                    self.TTFT_WARMUP_REPEATS = 3
                    self.TTFT_TARGET: Optional[str] = None
                    self.TTFT_MODEL_ID: Optional[str] = None
                    self.TTFT_TOKENIZER_PATH: Optional[str] = None
                    self.TTFT_MODEL_FILE: Optional[str] = None
                    self.TTFT_PREDICTOR_READY = False
                    self.TTFT_PREDICT_FUNC: Optional[Callable[[int, int], Awaitable[float]]] = None

                    self.TPOT_WARMUP_ENABLED = False
                    self.TPOT_WARMUP_REPEATS = 2
                    self.TPOT_TARGET: Optional[str] = None
                    self.TPOT_MODEL_ID: Optional[str] = None
                    self.TPOT_TOKENIZER_PATH: Optional[str] = None
                    self.TPOT_MODEL_FILE: Optional[str] = None
                    self.TPOT_PREDICTOR_READY = False
                    self.TPOT_PREDICT_FUNC: Optional[Callable[[int, int, int], Awaitable[float]]] = None
                    self.TPOT_STEP_PREDICTOR_READY = False
                    self.TPOT_STEP_PREDICT_FUNC: Optional[Callable[[int, int], Awaitable[float]]] = None
                    self._decode_active_batches: Dict[str, Dict[str, Dict[str, Any]]] = {}
                    self._distserve_reservations: Dict[str, Dict[str, Dict[str, Dict[str, Any]]]] = {
                        "prefill": {},
                        "decode": {},
                    }
                    self.DIST_SERVE_RESERVATION_TTL = 120.0
                    self.DIST_SERVE_PREFILL_BATCH_TOKENS = 2048
                    self._prefill_round_robin_index = 0
                    self._decode_round_robin_index = 0

                    self.KDN_BASE_URL = ""

                    self.LOOP_TASK_TIME = 0.03  # 轮询上报任务时间
                    self.MAX_DECODE_REQ_NUM = int(os.getenv("MY_MAX_DECODE_REQ_NUM", 128))

                    self._initialized = True  # 确保不重复初始化

    def set_pool_id(self, pool_id: Optional[int]):
        """设置pool_id"""
        with self._data_lock:
            self._pool_id = pool_id

    def get_pool_id(self):
        with self._data_lock:
            return self._pool_id

    def register_ttft_predictor(self, predict_func: Callable[[int, int], Awaitable[float]]):
        """Register the in-process TTFT predictor used by synchronous scheduling."""
        with self._data_lock:
            self.TTFT_PREDICT_FUNC = predict_func
            self.TTFT_PREDICTOR_READY = True

    def clear_ttft_predictor(self):
        """Mark the in-process TTFT predictor as unavailable."""
        with self._data_lock:
            self.TTFT_PREDICT_FUNC = None
            self.TTFT_PREDICTOR_READY = False

    def get_ttft_predictor(self):
        """Return the registered in-process TTFT predictor function, if ready."""
        with self._data_lock:
            if not self.TTFT_PREDICTOR_READY:
                return None
            return self.TTFT_PREDICT_FUNC

    def register_tpot_predictor(self, predict_func: Callable[[int, int, int], Awaitable[float]]):
        """Register the in-process TPOT predictor used by decode proxy lifecycle."""
        with self._data_lock:
            self.TPOT_PREDICT_FUNC = predict_func
            self.TPOT_PREDICTOR_READY = True

    def clear_tpot_predictor(self):
        """Mark the in-process TPOT predictor as unavailable."""
        with self._data_lock:
            self.TPOT_PREDICT_FUNC = None
            self.TPOT_PREDICTOR_READY = False

    def get_tpot_predictor(self):
        """Return the registered in-process TPOT predictor function, if ready."""
        with self._data_lock:
            if not self.TPOT_PREDICTOR_READY:
                return None
            return self.TPOT_PREDICT_FUNC

    def register_tpot_step_predictor(self, predict_func: Callable[[int, int], Awaitable[float]]):
        """Register the in-process single-step TPOT predictor."""
        with self._data_lock:
            self.TPOT_STEP_PREDICT_FUNC = predict_func
            self.TPOT_STEP_PREDICTOR_READY = True

    def clear_tpot_step_predictor(self):
        """Mark the in-process single-step TPOT predictor as unavailable."""
        with self._data_lock:
            self.TPOT_STEP_PREDICT_FUNC = None
            self.TPOT_STEP_PREDICTOR_READY = False

    def get_tpot_step_predictor(self):
        """Return the registered single-step TPOT predictor function, if ready."""
        with self._data_lock:
            if not self.TPOT_STEP_PREDICTOR_READY:
                return None
            return self.TPOT_STEP_PREDICT_FUNC

    def add_decode_active_request(self, instance_ip: str, request: Any):
        """Track a request currently admitted to a Decode instance."""
        if not instance_ip or request is None:
            return
        request_id = str(getattr(request, "Request_ID", ""))
        if not request_id:
            return
        prompt = getattr(request, "Prompt", None)
        service = getattr(request, "Service", None)
        with self._data_lock:
            active_batch = self._decode_active_batches.setdefault(instance_ip, {})
            prompt_tokens = int(getattr(prompt, "token_length", 0) or 0)
            active_batch[request_id] = {
                "request_id": request_id,
                "prompt_tokens": prompt_tokens,
                "max_tokens": int(getattr(prompt, "max_tokens", 0) or 0),
                "slo_tpot_ms": float(getattr(service, "SLO_TPOT", 0) or 0),
                "current_context_len": prompt_tokens,
                "start_time": time.time(),
            }

    def remove_decode_active_request(self, instance_ip: str, request_id: Any):
        """Remove a Decode active request when its stream finishes."""
        if not instance_ip:
            return
        request_key = str(request_id)
        with self._data_lock:
            active_batch = self._decode_active_batches.get(instance_ip)
            if not active_batch:
                return
            active_batch.pop(request_key, None)
            if not active_batch:
                self._decode_active_batches.pop(instance_ip, None)

    def get_decode_active_batch(self, instance_ip: str) -> List[Dict[str, Any]]:
        """Return a snapshot of currently active Decode requests for an instance."""
        with self._data_lock:
            active_batch = self._decode_active_batches.get(instance_ip, {})
            return [dict(item) for item in active_batch.values()]

    def next_decode_round_robin(self) -> str:
        """Return the next active Decode instance using round-robin order."""
        with self._data_lock:
            active_instances = [
                instance_ip for instance_ip in self._decode_objs
                if self._instance_table.get(instance_ip)
                and self._instance_table[instance_ip].online
            ]
            if not active_instances:
                return ""
            index = self._decode_round_robin_index % len(active_instances)
            self._decode_round_robin_index = (index + 1) % len(active_instances)
            return active_instances[index]

    def next_prefill_round_robin(self) -> str:
        """Return the next active Prefill instance using round-robin order."""
        with self._data_lock:
            active_instances = [
                instance_ip for instance_ip in self._prefill_objs
                if self._instance_table.get(instance_ip)
                and self._instance_table[instance_ip].online
            ]
            if not active_instances:
                return ""
            index = self._prefill_round_robin_index % len(active_instances)
            self._prefill_round_robin_index = (index + 1) % len(active_instances)
            return active_instances[index]

    def _ensure_distserve_state_locked(self):
        if not hasattr(self, "_distserve_reservations"):
            self._distserve_reservations = {"prefill": {}, "decode": {}}
        if not hasattr(self, "DIST_SERVE_RESERVATION_TTL"):
            self.DIST_SERVE_RESERVATION_TTL = 120.0

    def _cleanup_distserve_reservations_locked(self, now: Optional[float] = None):
        self._ensure_distserve_state_locked()
        current = time.time() if now is None else float(now)
        ttl = max(1.0, float(self.DIST_SERVE_RESERVATION_TTL))
        for role_reservations in self._distserve_reservations.values():
            for instance_ip in list(role_reservations):
                reservations = role_reservations[instance_ip]
                for request_id in list(reservations):
                    if current - float(reservations[request_id].get("created_at", current)) > ttl:
                        reservations.pop(request_id, None)
                if not reservations:
                    role_reservations.pop(instance_ip, None)

    def _distserve_effective_load_locked(self, role: str, instance_ip: str) -> int:
        self._cleanup_distserve_reservations_locked()
        instance = self._instance_table.get(instance_ip)
        heartbeat_load = 0
        if instance is not None:
            heartbeat_load = max(0, int(getattr(instance, "running_reqs", 0) or 0))
            heartbeat_load += max(0, int(getattr(instance, "waiting_reqs", 0) or 0))
        reservations = self._distserve_reservations[role].get(instance_ip, {})
        highest_target = max(
            (int(item.get("target_load", 0)) for item in reservations.values()),
            default=0,
        )
        return max(heartbeat_load, highest_target)

    def get_distserve_effective_load(self, role: str, instance_ip: str) -> int:
        if role not in {"prefill", "decode"}:
            raise ValueError(f"Unsupported DistServe role: {role}")
        with self._data_lock:
            return self._distserve_effective_load_locked(role, instance_ip)

    def reserve_distserve_batch(self, role: str, request_ids: List[Any]) -> str:
        """Atomically select the least-loaded active instance and reserve a batch."""
        if role not in {"prefill", "decode"}:
            raise ValueError(f"Unsupported DistServe role: {role}")
        request_keys = [str(request_id) for request_id in request_ids if str(request_id)]
        if not request_keys:
            return ""

        with self._data_lock:
            self._cleanup_distserve_reservations_locked()
            role_reservations = self._distserve_reservations[role]
            existing_instances = {
                instance_ip
                for instance_ip, reservations in role_reservations.items()
                if any(request_id in reservations for request_id in request_keys)
            }
            if len(existing_instances) == 1:
                existing_instance = next(iter(existing_instances))
                if all(request_id in role_reservations[existing_instance] for request_id in request_keys):
                    return existing_instance
            if existing_instances:
                raise ValueError("DistServe request batch contains conflicting reservations")

            configured = self._prefill_objs if role == "prefill" else self._decode_objs
            candidates = [
                instance_ip
                for instance_ip in configured
                if self._instance_table.get(instance_ip)
                and self._instance_table[instance_ip].online
            ]
            if not candidates:
                return ""
            selected = min(
                candidates,
                key=lambda instance_ip: (
                    self._distserve_effective_load_locked(role, instance_ip),
                    instance_ip,
                ),
            )
            next_load = self._distserve_effective_load_locked(role, selected)
            reservations = role_reservations.setdefault(selected, {})
            created_at = time.time()
            for offset, request_id in enumerate(request_keys, start=1):
                reservations[request_id] = {
                    "target_load": next_load + offset,
                    "created_at": created_at,
                }
            return selected

    def release_distserve_reservation(
        self,
        role: str,
        request_id: Any,
        instance_ip: str = "",
    ) -> bool:
        if role not in {"prefill", "decode"}:
            raise ValueError(f"Unsupported DistServe role: {role}")
        request_key = str(request_id)
        with self._data_lock:
            self._ensure_distserve_state_locked()
            role_reservations = self._distserve_reservations[role]
            candidates = [instance_ip] if instance_ip else list(role_reservations)
            removed = False
            for candidate in candidates:
                reservations = role_reservations.get(candidate)
                if not reservations:
                    continue
                removed = reservations.pop(request_key, None) is not None or removed
                if not reservations:
                    role_reservations.pop(candidate, None)
            return removed

    def confirm_distserve_heartbeat(self, role: str, instance_ip: str, reported_load: int):
        """Drop reservations already reflected by a newer heartbeat snapshot."""
        if role not in {"prefill", "decode"}:
            raise ValueError(f"Unsupported DistServe role: {role}")
        with self._data_lock:
            self._cleanup_distserve_reservations_locked()
            reservations = self._distserve_reservations[role].get(instance_ip, {})
            confirmed = [
                request_id
                for request_id, item in reservations.items()
                if int(item.get("target_load", 0)) <= max(0, int(reported_load))
            ]
            for request_id in confirmed:
                reservations.pop(request_id, None)
            if not reservations:
                self._distserve_reservations[role].pop(instance_ip, None)

    def get_prefill_scheduler_strategy(self) -> str:
        with self._data_lock:
            return self.SCHEDULER_STRATEGY

    def set_prefill_scheduler_strategy(self, strategy: str) -> str:
        if strategy not in self.PREFILL_SCHEDULER_STRATEGIES:
            raise ValueError(f"Unsupported Prefill scheduler strategy: {strategy}")
        with self._data_lock:
            previous = self.SCHEDULER_STRATEGY
            self.SCHEDULER_STRATEGY = strategy
            return previous

    def get_decode_scheduler_strategy(self) -> str:
        with self._data_lock:
            return self.DECODE_SCHEDULER_STRATEGY

    def set_decode_scheduler_strategy(self, strategy: str) -> str:
        if strategy not in self.DECODE_SCHEDULER_STRATEGIES:
            raise ValueError(f"Unsupported Decode scheduler strategy: {strategy}")
        with self._data_lock:
            previous = self.DECODE_SCHEDULER_STRATEGY
            self.DECODE_SCHEDULER_STRATEGY = strategy
            return previous

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

    def _active_instances(self, instances: List[str]) -> List[str]:
        with self._data_lock:
            return [
                instance_ip for instance_ip in instances
                if self._instance_table.get(instance_ip)
                and self._instance_table[instance_ip].online
            ]

    @property
    def active_prefill_objs(self) -> List[str]:
        """获取当前在线的 prefill 实例列表。"""
        return self._active_instances(self._prefill_objs)

    @property
    def active_decode_objs(self) -> List[str]:
        """获取当前在线的 decode 实例列表。"""
        return self._active_instances(self._decode_objs)

    def set_config(self, prefills: Optional[List[str]] = None, decodes: Optional[List[str]] = None):
        """线程安全的配置设置方法"""
        with self._data_lock:
            if prefills is not None:
                self._prefill_objs = prefills.copy() if prefills else []
                for prefill_instance in self._prefill_objs:
                    self._instance_table[prefill_instance] = Instance(
                        instance_ip=prefill_instance, role="prefill"
                    )
            if decodes is not None:
                self._decode_objs = decodes.copy() if decodes else []
                for decode_instance in self._decode_objs:
                    self._instance_table[decode_instance] = Instance(
                        instance_ip=decode_instance, role="decode"
                    )

    def register_instance(
        self,
        instance_ip: str,
        role: str,
        connector: str,
        side_channel_host: str,
        side_channel_port: int,
        tp_size: int,
        vllm_port: int = 0,
    ) -> Instance:
        """Register or idempotently refresh an instance and its NIXL identity."""
        if role not in {"prefill", "decode"}:
            raise ValueError(f"Unsupported instance role: {role}")
        if not instance_ip or not side_channel_host:
            raise ValueError("instance_ip and side_channel_host are required")
        if connector != "NixlConnector":
            raise ValueError(f"Unsupported connector: {connector}")
        if int(side_channel_port) <= 0 or int(tp_size) <= 0:
            raise ValueError("side_channel_port and tp_size must be positive")

        with self._data_lock:
            existing = self._instance_table.get(instance_ip)
            if existing and existing.role and existing.role != role:
                raise ValueError(
                    f"Instance {instance_ip} is already registered as {existing.role}"
                )
            instance = existing or Instance(instance_ip=instance_ip)
            instance.role = role
            instance.connector = connector
            instance.side_channel_host = side_channel_host
            instance.side_channel_port = int(side_channel_port)
            instance.tp_size = int(tp_size)
            instance.vllm_port = int(vllm_port or 0)
            instance.last_updated = time.time()
            self._instance_table[instance_ip] = instance
            target = self._prefill_objs if role == "prefill" else self._decode_objs
            if instance_ip not in target:
                target.append(instance_ip)
            return deepcopy(instance)

    def get_nixl_metadata(self, instance_ip: str) -> Dict[str, Any]:
        with self._data_lock:
            instance = self._instance_table.get(instance_ip)
            return instance.nixl_metadata() if instance else {}

    def get_side_channel_addr(self, instance_ip: str) -> str:
        meta = self.get_nixl_metadata(instance_ip)
        host = meta.get("side_channel_host", "")
        port = meta.get("side_channel_port", 0)
        return f"{host}:{port}" if host and port else ""

    @property
    def instance_table(self) -> Dict[str, Any]:
        """获取nodes的深拷贝"""
        with self._data_lock:
            return deepcopy(self._instance_table)

    def set_instance_table(self, key: str, value: Any):
        """设置nodes的特定键值"""
        with self._data_lock:
            self._instance_table[key] = deepcopy(value)

    def has_instance(self, instance_ip: str) -> bool:
        with self._data_lock:
            return instance_ip in self._instance_table

    def mark_instance_offline(self, instance_ip: str) -> bool:
        with self._data_lock:
            if instance_ip not in self._instance_table:
                return False

            instance = self._instance_table[instance_ip]
            instance.online = False
            instance.running_reqs = 0
            instance.waiting_reqs = 0
            instance.prompt_throughput = 0.0
            instance.generate_throughput = 0.0
            instance.kv_cache_usage_percent = 0.0
            instance.gpu_util_percent = 0.0
            instance.gpu_memory_util_percent = 0.0
            instance.last_updated = time.time()
            self._ensure_distserve_state_locked()
            self._distserve_reservations["prefill"].pop(instance_ip, None)
            self._distserve_reservations["decode"].pop(instance_ip, None)
            self._remove_instance_links_locked(instance_ip)
            return True

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

    def _remove_instance_links_locked(self, instance_ip: str):
        keys_to_delete = [
            key for key, link in self._link_table.items()
            if key == instance_ip
            or getattr(link, "prefill_instance", "") == instance_ip
            or getattr(link, "decode_instance", "") == instance_ip
        ]
        for key in keys_to_delete:
            del self._link_table[key]

    def remove_instance_links(self, instance_ip: str):
        with self._data_lock:
            self._remove_instance_links_locked(instance_ip)

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
        """获取 Decode 池当前所有未完成请求总数。"""
        running_reqs = 0
        for instance_ip in self.active_decode_objs:
            instance = self.instance_table.get(instance_ip)
            if instance:
                running_reqs += max(0, int(instance.running_reqs))
                running_reqs += max(0, int(instance.waiting_reqs))
        return running_reqs

    def get_max_decode_req_num(self):
        """获取 Decode 池级最大 running requests 数。"""
        return max(1, int(self.MAX_DECODE_REQ_NUM))

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

    def get_predicted_availability(self, instance_ip: str) -> float:
        """Get the scheduler's predicted available time for an instance."""
        with self._lock:
            instance = self._instance_table.get(instance_ip)
            if not instance:
                return 0.0
            return instance.predicted_available_time

    def update_predicted_availability(self, instance_ip: str, predicted_time: float):
        """Update the scheduler-owned predicted availability timeline."""
        with self._lock:
            instance = self._instance_table.get(instance_ip)
            if not instance:
                return False
            instance.predicted_available_time = predicted_time
            instance.last_updated = time.time()
            return True

    def get_real_availability(self, instance_ip: str) -> float:
        """Get the latest observed real available time for an instance."""
        with self._lock:
            instance = self._instance_table.get(instance_ip)
            if not instance:
                return 0.0
            return instance.real_available_time

    def update_real_availability(self, instance_ip: str, real_time: float):
        """
        Update the observed real availability.

        If reality is later than the predicted timeline, push prediction forward.
        If reality is earlier, keep the predicted timeline so planned batches are
        not accidentally marked as free.
        """
        with self._lock:
            instance = self._instance_table.get(instance_ip)
            if not instance:
                return False
            instance.real_available_time = real_time
            if real_time > instance.predicted_available_time:
                instance.predicted_available_time = real_time
            instance.last_updated = time.time()
            return True
