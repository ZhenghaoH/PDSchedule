from typing import List, Dict, Any, Optional, Callable, Awaitable
import threading
from copy import deepcopy
import os
import time

from .instance import Instance
from connectors.manager import get_connector_manager


class GatewayStoreManager:
    """存储管理"""
    PREFILL_SCHEDULER_STRATEGIES = frozenset(
        {"slo_aware_prefill", "jsq", "round_robin"}
    )
    DECODE_SCHEDULER_STRATEGIES = frozenset(
        {
            "round_robin",
            "lowest_generation_throughput",
            "slo_aware_decode",
        }
    )
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
                    self._data_lock = threading.RLock()  # 数据操作锁

                    self._pool_id: Optional[int] = None
                    self.INSTANCE_HEARTBEAT_TTL_SEC = 30.0
                    self.INSTANCE_EXPIRY_SCAN_INTERVAL_SEC = 1.0
                    self.MANAGEMENT_CONNECT_TIMEOUT_SEC = 2.0
                    self.MANAGEMENT_READ_TIMEOUT_SEC = 3.0
                    self.REGISTRATION_RETRY_MAX_INTERVAL_SEC = 10.0
                    self._network_register_table: List[str] = []
                    self.connector = {
                        "name": "nixl",
                        "version": "1",
                        "metadata": {},
                    }

                    self.host = "127.0.0.1"  # 临时存储 主机host
                    self.port = 8081  # 临时存储 主机监听端口
                    self.controller = ""  # 临时变量 控制器监听host
                    self.rank = 0  # 身份

                    self.USE_SYN = True  # 同步设置 是否启用同步
                    self.SYN_BATCH_SIZE = 1  # 同步设置 批处理批次
                    self.SYN_TIMEOUT = 1  # 同步设置 等待时间 秒

                    self.TRIGGER_MODE = "ARRIVAL"
                    self.PROFILE_MODE = False
                    self.SCHEDULER_STRATEGY = "slo_aware_prefill"
                    self.DECODE_SCHEDULER_STRATEGY = "slo_aware_decode"
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
                    self._distserve_tie_break_cursor = {
                        "prefill": 0,
                        "decode": 0,
                    }
                    self.DIST_SERVE_RESERVATION_TTL = 240.0
                    self._prefill_round_robin_index = 0
                    self._decode_round_robin_index = 0

                    self.LOOP_TASK_TIME = 0.03  # 轮询上报任务时间
                    self.MAX_DECODE_REQ_NUM = int(os.getenv("MY_MAX_DECODE_REQ_NUM", 128))

                    self._initialized = True  # 确保不重复初始化

    def set_pool_id(self, pool_id: Optional[int]):
        """设置pool_id"""
        with self._data_lock:
            self._pool_id = pool_id

    def set_pool_id_if_current(self, expected: Optional[int], pool_id: Optional[int]) -> bool:
        """Update the Controller-assigned ID only if it has not changed."""
        with self._data_lock:
            if self._pool_id != expected:
                return False
            self._pool_id = pool_id
            return True

    def get_pool_id(self):
        with self._data_lock:
            return self._pool_id

    def set_connector(self, descriptor):
        validated = get_connector_manager().validate(descriptor)
        with self._data_lock:
            self.connector = validated.to_dict()

    def get_connector(self):
        with self._data_lock:
            return deepcopy(self.connector)

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
                if self._instance_is_schedulable_locked(instance_ip, "decode")
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
                if self._instance_is_schedulable_locked(instance_ip, "prefill")
            ]
            if not active_instances:
                return ""
            index = self._prefill_round_robin_index % len(active_instances)
            self._prefill_round_robin_index = (index + 1) % len(active_instances)
            return active_instances[index]

    def _ensure_distserve_state_locked(self):
        if not hasattr(self, "_distserve_reservations"):
            self._distserve_reservations = {"prefill": {}, "decode": {}}
        if not hasattr(self, "_distserve_tie_break_cursor"):
            self._distserve_tie_break_cursor = {"prefill": 0, "decode": 0}
        else:
            self._distserve_tie_break_cursor.setdefault("prefill", 0)
            self._distserve_tie_break_cursor.setdefault("decode", 0)
        if not hasattr(self, "DIST_SERVE_RESERVATION_TTL"):
            self.DIST_SERVE_RESERVATION_TTL = 240.0

    def _select_distserve_tie_break_locked(
        self,
        role: str,
        candidates: List[str],
        effective_loads: Dict[str, int],
    ) -> str:
        """Round-robin only among candidates sharing the minimum load."""
        self._ensure_distserve_state_locked()
        if not candidates:
            return ""

        minimum_load = min(effective_loads[instance_ip] for instance_ip in candidates)
        minimum_candidates = {
            instance_ip
            for instance_ip in candidates
            if effective_loads[instance_ip] == minimum_load
        }
        start = int(self._distserve_tie_break_cursor[role]) % len(candidates)
        for offset in range(len(candidates)):
            index = (start + offset) % len(candidates)
            selected = candidates[index]
            if selected in minimum_candidates:
                self._distserve_tie_break_cursor[role] = (index + 1) % len(candidates)
                return selected

        return ""

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
        reservations = self._distserve_reservations[role].get(instance_ip, {})
        if role == "prefill":
            return sum(
                max(1, int(item.get("load_units", 1) or 1))
                for item in reservations.values()
            )

        instance = self._instance_table.get(instance_ip)
        heartbeat_load = 0
        if instance is not None:
            heartbeat_load = max(0, int(getattr(instance, "running_reqs", 0) or 0))
            heartbeat_load += max(0, int(getattr(instance, "waiting_reqs", 0) or 0))
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

    def reserve_distserve_batch(
        self,
        role: str,
        request_ids: List[Any],
        request_loads: Optional[List[int]] = None,
    ) -> str:
        """Atomically select the least-loaded active instance and reserve requests.

        Prefill load is measured in unfinished prompt tokens. Decode retains its
        request-count load metric, including heartbeat reconciliation.
        """
        if role not in {"prefill", "decode"}:
            raise ValueError(f"Unsupported DistServe role: {role}")
        if role == "prefill" and (
            request_loads is None or len(request_loads) != len(request_ids)
        ):
            raise ValueError(
                "Prefill DistServe reservations require one prompt-token load per request"
            )

        reservation_items = []
        for index, request_id in enumerate(request_ids):
            request_key = str(request_id)
            if not request_key:
                continue
            load_units = 1
            if role == "prefill":
                try:
                    load_units = max(1, int(request_loads[index]))
                except (TypeError, ValueError):
                    load_units = 1
            reservation_items.append((request_key, load_units))

        request_keys = [request_key for request_key, _ in reservation_items]
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
                    if self._instance_is_schedulable_locked(existing_instance, role):
                        return existing_instance
                    reservations = role_reservations.get(existing_instance, {})
                    for request_id in request_keys:
                        reservations.pop(request_id, None)
                    if not reservations:
                        role_reservations.pop(existing_instance, None)
            if existing_instances:
                raise ValueError("DistServe request batch contains conflicting reservations")

            configured = self._prefill_objs if role == "prefill" else self._decode_objs
            candidates = [
                instance_ip
                for instance_ip in configured
                if self._instance_is_schedulable_locked(instance_ip, role)
            ]
            if not candidates:
                return ""
            effective_loads = {
                instance_ip: self._distserve_effective_load_locked(role, instance_ip)
                for instance_ip in candidates
            }
            selected = self._select_distserve_tie_break_locked(
                role,
                candidates,
                effective_loads,
            )
            next_load = effective_loads[selected]
            reservations = role_reservations.setdefault(selected, {})
            created_at = time.time()
            for offset, (request_id, load_units) in enumerate(reservation_items, start=1):
                if role == "prefill":
                    reservations[request_id] = {
                        "load_units": load_units,
                        "created_at": created_at,
                    }
                else:
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
        """Reconcile reservations with heartbeat state.

        Prefill reservations are request-level JSQ bookkeeping and remain until
        the request completion path releases them. Decode keeps the original
        heartbeat-confirmation behavior.
        """
        if role not in {"prefill", "decode"}:
            raise ValueError(f"Unsupported DistServe role: {role}")
        with self._data_lock:
            self._cleanup_distserve_reservations_locked()
            if role == "prefill":
                return
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

    def _active_instances(self, instances: List[str], role: str) -> List[str]:
        with self._data_lock:
            return [
                instance_ip for instance_ip in instances
                if self._instance_is_schedulable_locked(instance_ip, role)
            ]

    def _instance_is_schedulable_locked(
        self,
        instance_ip: str,
        role: Optional[str] = None,
        now: Optional[float] = None,
    ) -> bool:
        instance = self._instance_table.get(instance_ip)
        if not instance or not instance.online:
            return False
        if role is not None and instance.role != role:
            return False
        heartbeat = getattr(instance, "last_heartbeat_monotonic", None)
        if heartbeat is None:
            return False
        current = time.monotonic() if now is None else float(now)
        ttl = max(0.0, float(getattr(self, "INSTANCE_HEARTBEAT_TTL_SEC", 30.0)))
        return current - float(heartbeat) < ttl

    def is_instance_schedulable(self, instance_ip: str, role: Optional[str] = None) -> bool:
        with self._data_lock:
            return self._instance_is_schedulable_locked(instance_ip, role)

    @property
    def active_prefill_objs(self) -> List[str]:
        """获取当前在线的 prefill 实例列表。"""
        return self._active_instances(self._prefill_objs, "prefill")

    @property
    def active_decode_objs(self) -> List[str]:
        """获取当前在线的 decode 实例列表。"""
        return self._active_instances(self._decode_objs, "decode")

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
        connector: Dict[str, Any],
        vllm_port: int = 0,
        gpu_indices: Optional[List[int]] = None,
    ) -> Instance:
        """Register an instance while enforcing one connector per Pool."""
        if role not in {"prefill", "decode"}:
            raise ValueError(f"Unsupported instance role: {role}")
        if not instance_ip:
            raise ValueError("instance_ip is required")
        connector_manager = get_connector_manager()
        descriptor = connector_manager.validate(connector)
        pool_descriptor = connector_manager.validate(self.connector)
        descriptor = connector_manager.validate_instance(
            descriptor,
            pool_descriptor,
            role,
        )

        with self._data_lock:
            existing = self._instance_table.get(instance_ip)
            if existing and existing.role and existing.role != role:
                raise ValueError(
                    f"Instance {instance_ip} is already registered as {existing.role}"
                )
            for existing_ip, existing_instance in self._instance_table.items():
                if existing_ip == instance_ip or not existing_instance.connector:
                    continue
                if connector_manager.instance_conflict(
                    descriptor,
                    existing_instance.connector,
                ):
                    raise ValueError(
                        "connector instance identity or side-channel port range "
                        f"conflicts with registered instance {existing_ip}"
                    )
            instance = existing or Instance(
                instance_ip=instance_ip,
                last_heartbeat_monotonic=None,
            )
            instance.role = role
            instance.connector = descriptor.to_dict()
            instance.vllm_port = int(vllm_port or 0)
            if gpu_indices is not None:
                normalized_gpu_indices = []
                for value in gpu_indices:
                    index = int(value)
                    if index < 0:
                        raise ValueError("gpu_indices must be non-negative")
                    if index not in normalized_gpu_indices:
                        normalized_gpu_indices.append(index)
                instance.gpu_indices = normalized_gpu_indices
            instance.last_updated = time.time()
            self._instance_table[instance_ip] = instance
            target = self._prefill_objs if role == "prefill" else self._decode_objs
            if instance_ip not in target:
                target.append(instance_ip)
            return deepcopy(instance)

    def update_instance_heartbeat(
        self,
        instance_ip: str,
        role: str,
        metrics: Dict[str, Any],
    ) -> tuple[str, Optional[Instance], bool, bool]:
        """Validate and atomically merge a heartbeat from a registered proxy.

        Returns (status, instance snapshot, was_schedulable, had_heartbeat).
        """
        if not isinstance(metrics, dict):
            raise ValueError("heartbeat metrics must be an object")
        missing_metrics = {
            name for name in ("running_reqs", "waiting_reqs") if name not in metrics
        }
        if missing_metrics:
            raise ValueError(
                "heartbeat is missing required metrics: "
                + ", ".join(sorted(missing_metrics))
            )

        def integer_metric(name: str, default: int = 0) -> int:
            value = metrics.get(name, default)
            if isinstance(value, bool):
                raise ValueError(f"{name} must be a non-negative integer")
            try:
                normalized = int(value)
                exact = float(value) == normalized
            except (TypeError, ValueError, OverflowError) as exc:
                raise ValueError(f"{name} must be a non-negative integer") from exc
            if normalized < 0 or not exact:
                raise ValueError(f"{name} must be a non-negative integer")
            return normalized

        def float_metric(name: str, default: float = 0.0) -> float:
            import math

            value = metrics.get(name, default)
            if isinstance(value, bool):
                raise ValueError(f"{name} must be a finite number")
            try:
                normalized = float(value)
            except (TypeError, ValueError, OverflowError) as exc:
                raise ValueError(f"{name} must be a finite number") from exc
            if not math.isfinite(normalized):
                raise ValueError(f"{name} must be a finite number")
            return normalized

        running_reqs = integer_metric("running_reqs")
        waiting_reqs = integer_metric("waiting_reqs")
        normalized_metrics = {
            "running_reqs": running_reqs,
            "waiting_reqs": waiting_reqs,
            "prompt_throughput": float_metric("prompt_throughput"),
            "generate_throughput": float_metric("generate_throughput"),
            "kv_cache_usage_percent": float_metric("kv_cache_usage_percent"),
            "gpu_util_percent": float_metric("gpu_util_percent"),
            "gpu_memory_util_percent": float_metric("gpu_memory_util_percent"),
        }
        gpu_devices = metrics.get("gpu_devices", [])
        network_metric = metrics.get("network_metric", {})
        if not isinstance(gpu_devices, list):
            raise ValueError("gpu_devices must be a list")
        if not isinstance(network_metric, dict):
            raise ValueError("network_metric must be an object")
        if any(not isinstance(item, dict) for item in gpu_devices):
            raise ValueError("each gpu_devices entry must be an object")

        with self._data_lock:
            instance = self._instance_table.get(instance_ip)
            if instance is None:
                return "not_found", None, False, False
            if instance.role != role:
                return "role_conflict", None, False, False

            now_monotonic = time.monotonic()
            had_heartbeat = getattr(instance, "last_heartbeat_monotonic", None) is not None
            was_schedulable = self._instance_is_schedulable_locked(
                instance_ip, role, now=now_monotonic
            )
            if instance.online and not was_schedulable:
                self._mark_instance_offline_locked(instance)

            instance.running_reqs = normalized_metrics["running_reqs"]
            instance.waiting_reqs = normalized_metrics["waiting_reqs"]
            instance.prompt_throughput = normalized_metrics["prompt_throughput"]
            instance.generate_throughput = normalized_metrics["generate_throughput"]
            instance.kv_cache_usage_percent = normalized_metrics["kv_cache_usage_percent"]
            instance.gpu_util_percent = normalized_metrics["gpu_util_percent"]
            instance.gpu_memory_util_percent = normalized_metrics["gpu_memory_util_percent"]
            instance.gpu_devices = deepcopy(gpu_devices)
            instance.network_metric = deepcopy(network_metric)
            if instance.gpu_devices:
                reported_gpu_indices = []
                for item in instance.gpu_devices:
                    if item.get("gpu_index") is None:
                        continue
                    try:
                        gpu_index = int(item["gpu_index"])
                    except (TypeError, ValueError):
                        continue
                    if gpu_index >= 0 and gpu_index not in reported_gpu_indices:
                        reported_gpu_indices.append(gpu_index)
                if reported_gpu_indices:
                    instance.gpu_indices = reported_gpu_indices
            instance.online = True
            instance.last_heartbeat_monotonic = now_monotonic
            instance.last_updated = time.time()
            self.confirm_distserve_heartbeat(
                role,
                instance_ip,
                running_reqs + waiting_reqs,
            )
            return "updated", deepcopy(instance), was_schedulable, had_heartbeat

    def get_connector_metadata(self, instance_ip: str) -> Dict[str, Any]:
        with self._data_lock:
            instance = self._instance_table.get(instance_ip)
            return instance.connector_metadata() if instance else {}

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

    def _mark_instance_offline_locked(self, instance: Instance) -> None:
        instance.online = False
        instance.running_reqs = 0
        instance.waiting_reqs = 0
        instance.prompt_throughput = 0.0
        instance.generate_throughput = 0.0
        instance.kv_cache_usage_percent = 0.0
        instance.gpu_util_percent = 0.0
        instance.gpu_memory_util_percent = 0.0
        instance.gpu_devices = []
        instance.network_metric = {}
        instance.last_updated = time.time()
        self._ensure_distserve_state_locked()
        self._distserve_reservations["prefill"].pop(instance.instance_ip, None)
        self._distserve_reservations["decode"].pop(instance.instance_ip, None)

    def mark_instance_offline(self, instance_ip: str) -> bool:
        with self._data_lock:
            instance = self._instance_table.get(instance_ip)
            if instance is None:
                return False
            self._mark_instance_offline_locked(instance)
            return True

    def expire_stale_instances(self, now: Optional[float] = None) -> List[tuple[str, str, float]]:
        """Atomically mark expired proxies offline and return their identities."""
        current = time.monotonic() if now is None else float(now)
        ttl = max(0.0, float(getattr(self, "INSTANCE_HEARTBEAT_TTL_SEC", 30.0)))
        expired = []
        with self._data_lock:
            for instance_ip, instance in self._instance_table.items():
                heartbeat = getattr(instance, "last_heartbeat_monotonic", None)
                if not instance.online:
                    continue
                age = float("inf") if heartbeat is None else current - float(heartbeat)
                if age < ttl:
                    continue
                self._mark_instance_offline_locked(instance)
                expired.append((instance.role, instance_ip, age))
        return expired


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
        with self._data_lock:
            return sum(
                self._instance_table[instance_ip].prompt_throughput
                for instance_ip in self._prefill_objs
                if self._instance_is_schedulable_locked(instance_ip, "prefill")
            )

    def get_decode_load(self):
        """获取 Decode 池当前所有未完成请求总数。"""
        with self._data_lock:
            return sum(
                max(0, int(self._instance_table[instance_ip].running_reqs))
                + max(0, int(self._instance_table[instance_ip].waiting_reqs))
                for instance_ip in self._decode_objs
                if self._instance_is_schedulable_locked(instance_ip, "decode")
            )

    def get_max_decode_req_num(self):
        """获取 Decode 池级最大 running requests 数。"""
        return max(1, int(self.MAX_DECODE_REQ_NUM))

    def get_predicted_availability(self, instance_ip: str) -> float:
        """Get the scheduler's predicted available time for an instance."""
        with self._data_lock:
            instance = self._instance_table.get(instance_ip)
            if not instance:
                return 0.0
            return instance.predicted_available_time

    def update_predicted_availability(self, instance_ip: str, predicted_time: float):
        """Update the scheduler-owned predicted availability timeline."""
        with self._data_lock:
            instance = self._instance_table.get(instance_ip)
            if not instance:
                return False
            instance.predicted_available_time = predicted_time
            instance.last_updated = time.time()
            return True

    def get_real_availability(self, instance_ip: str) -> float:
        """Get the latest observed real available time for an instance."""
        with self._data_lock:
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
        with self._data_lock:
            instance = self._instance_table.get(instance_ip)
            if not instance:
                return False
            instance.real_available_time = real_time
            if real_time > instance.predicted_available_time:
                instance.predicted_available_time = real_time
            instance.last_updated = time.time()
            return True
