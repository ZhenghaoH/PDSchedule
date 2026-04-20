from dataclasses import dataclass, field
import time


@dataclass
class NetworkConfig:
    bottleneck_bandwidth: float = 10 * 1e9
    round_trip_time: float = 0.0005
    communication_overhead_factor: float = 1.0


@dataclass
class InstanceLink:
    prefill_instance: str = ""  # P实例IP 带端口
    decode_instance: str = ""  # D实例IP 带端口
    prefill_proxy: str = ""  # P代理IP 带端口
    decode_proxy: str = ""  # D代理IP 带端口
    last_updated: float = field(default_factory=time.time)  # 上次更新时间
    count: int = 0  # 使用此连接进行KV Cache传输的次数
    network: NetworkConfig = field(default_factory=NetworkConfig)  # 链路网络状态
