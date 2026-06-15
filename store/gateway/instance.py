from dataclasses import dataclass, asdict, field
from re import L
from typing import Dict, Any, List
import time
import os

MAX_SIZE = 99999  # 默认最大值


@dataclass
class Instance:
    """
    Prefill/Decode 实例类
    """
    instance_ip: str  # 实例IP 包含端口
    max_batch_size: int = MAX_SIZE  # 最大batch
    current_batch_size: int = 0  # 当前batch
    predicted_available_time: float = 0.0
    real_available_time: float = 0.0
    last_updated: float = field(default_factory=time.time)  # 上次更新时间
    GPU_type: str = os.getenv("MY_GPU_TYPE", "NVIDIA RTX5090")  # 资源池使用的GPU类型

    # 实例性能参数
    flops: float = float(os.getenv("MY_FLOPS", 17.9))  # 算力参数
    mem_bandwidth: float = float(os.getenv("MY_MEM_BANDWIDTH", 800.0))  # Decode 节点特有参数: Mem Bandwidth
    max_prompt_throughput: float = float(
        os.getenv("MY_MAX_PROMPT_THROUGHPUT", 37829.0))  # Prefill 节点特有参数: Max Prompt Throughput

    # 实例负载参数
    running_reqs: int = 0  # 当前正在运行的请求数
    waiting_reqs: int = 0  # 当前正在等待运行的请求数
    prompt_throughput: float = 0.0  # 当前的Prompt Throughput
    generate_throughput: float = 0.0  # 当前的Generate Throughput
    kv_cache_usage_percent: float = 0.0
    gpu_util_percent: float = 0.0
    gpu_memory_util_percent: float = 0.0
    online: bool = False

    def __post_init__(self):
        if self.predicted_available_time <= 0:
            self.predicted_available_time = self.last_updated
        if self.real_available_time <= 0:
            self.real_available_time = self.last_updated

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
