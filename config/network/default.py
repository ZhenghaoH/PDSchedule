from dataclasses import dataclass


@dataclass
class NetworkConfig:
    bottleneck_bandwidth: float = 10 * 1e9
    round_trip_time: float = 0.0005
    communication_overhead_factor: float = 1.0
