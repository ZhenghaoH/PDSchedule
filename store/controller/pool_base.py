import time
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Dict, Any


# 定义节点类型枚举，防止手输字符串出错
class PoolType(Enum):
    PREFILL = "prefill"
    DECODE = "decode"


@dataclass
class BasePool:
    """
    资源池基类：包含所有节点共有的属性
        - pool_id: 推理池唯一标识符
        - proxy_ip_address: 推理池代理IP地址
        - proxy_port: 推理池代理端口号
        - current_load: 当前负载（默认0.0）
        - last_updated: 上次更新时间（默认当前时间）
        - GPU_type: GPU类型（默认空字符串）
    """
    pool_id: int
    proxy_ip_address: str
    proxy_port: int
    current_load: float = 0.0
    last_updated: float = field(default_factory=time.time)
    GPU_type: str = ""
    connector: Dict[str, Any] = field(
        default_factory=lambda: {
            "name": "nixl",
            "version": "1",
            "metadata": {},
        }
    )

    def to_dict(self) -> Dict[str, Any]:
        """通用序列化方法"""
        return asdict(self)


@dataclass
class PrefillPool(BasePool):
    """Prefill 节点特有属性: flops"""
    flops: float = 0.0
    max_prompt_throughput: float = 0.0


@dataclass
class DecodePool(BasePool):
    """Decode 节点特有属性: mem_bandwidth 与请求数容量"""
    mem_bandwidth: float = 0.0
    running_reqs: int = 0
    max_decode_req_num: int = 128
