from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from core.schemas.request import BaseRequest
from store.gateway.manager import GatewayStoreManager


@dataclass(frozen=True)
class PrefillStrategyCapabilities:
    trigger_on_arrival: bool = False
    allow_preemption: bool = True
    allow_adaptive_switch: bool = True


@dataclass
class PrefillScheduleContext:
    requests: List[BaseRequest]
    last_plan: Optional[List[BaseRequest]] = None
    inflight_prefill: Optional[List[Any]] = None


@dataclass
class PrefillScheduleResult:
    requests: List[BaseRequest]
    dispatch_policy: str
    selection_reason: str
    candidate_snapshots: Dict[str, List[Dict[str, Any]]] = field(
        default_factory=dict
    )


def candidate_snapshot(storage: GatewayStoreManager) -> List[Dict[str, Any]]:
    instances = storage.instance_table
    return [
        {
            "instance": instance_ip,
            "prompt_throughput": float(
                getattr(instances.get(instance_ip), "prompt_throughput", 0.0)
                or 0.0
            ),
            "running_reqs": int(
                getattr(instances.get(instance_ip), "running_reqs", 0) or 0
            ),
            "waiting_reqs": int(
                getattr(instances.get(instance_ip), "waiting_reqs", 0) or 0
            ),
            "unfinished_prompt_tokens": storage.get_distserve_effective_load(
                "prefill", instance_ip
            ),
        }
        for instance_ip in storage.active_prefill_objs
    ]


class PrefillStrategy:
    name = ""
    capabilities = PrefillStrategyCapabilities()

    def __init__(self, storage: GatewayStoreManager):
        self.storage = storage

    async def schedule(
        self,
        context: PrefillScheduleContext,
    ) -> PrefillScheduleResult:
        raise NotImplementedError

    def on_schedule_discarded(self, requests: List[BaseRequest]) -> None:
        return

    def on_request_finished(self, request: BaseRequest) -> None:
        return

    def on_switch_out(self, requests: List[BaseRequest]) -> None:
        return
