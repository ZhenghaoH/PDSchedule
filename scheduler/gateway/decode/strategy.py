from dataclasses import dataclass, field
from typing import Any, Dict, List

from core.schemas.request import BaseRequest
from store.gateway.manager import GatewayStoreManager


@dataclass
class DecodeScheduleResult:
    selected_instance: str
    selection_reason: str
    candidates: List[Dict[str, Any]] = field(default_factory=list)
    details: Dict[str, Any] = field(default_factory=dict)


def generate_throughput(storage: GatewayStoreManager, instance_ip: str) -> float:
    instance = storage.instance_table.get(instance_ip)
    if instance is None:
        return 0.0
    return float(getattr(instance, "generate_throughput", 0.0) or 0.0)


def candidate_snapshot(storage: GatewayStoreManager) -> List[Dict[str, Any]]:
    instances = storage.instance_table
    return [
        {
            "instance": instance_ip,
            "running_reqs": int(
                getattr(instances.get(instance_ip), "running_reqs", 0) or 0
            ),
            "waiting_reqs": int(
                getattr(instances.get(instance_ip), "waiting_reqs", 0) or 0
            ),
            "generate_throughput": generate_throughput(storage, instance_ip),
            "kv_cache_usage_percent": float(
                getattr(
                    instances.get(instance_ip),
                    "kv_cache_usage_percent",
                    0.0,
                )
                or 0.0
            ),
        }
        for instance_ip in storage.active_decode_objs
    ]


class DecodeStrategy:
    name = ""

    def __init__(self, storage: GatewayStoreManager):
        self.storage = storage

    async def schedule(self, request: BaseRequest) -> DecodeScheduleResult:
        raise NotImplementedError
