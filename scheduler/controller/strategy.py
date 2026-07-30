from dataclasses import dataclass, field
from typing import Dict, Any, Optional, Protocol, TYPE_CHECKING

if TYPE_CHECKING:
    from scheduler.controller.resource_state import ResourceSnapshot


@dataclass(frozen=True)
class GlobalScheduleContext:
    token_length: int
    max_tokens: int
    slo_ttft: float
    slo_tpot: float
    compress_factor: float
    request_id: Optional[int] = None
    max_tokens_fallback: bool = False


@dataclass(frozen=True)
class GlobalScheduleDecision:
    prefill_address: str
    prefill_port: int
    decode_address: str
    decode_port: int
    strategy: str
    connector: str = "nixl"
    connector_descriptor: Dict[str, Any] = field(
        default_factory=lambda: {
            "name": "nixl",
            "version": "1",
            "metadata": {},
        }
    )
    selection_source: str = "controller_scheduler"

    def to_proxy_dict(self):
        return {
            "P_proxy_addr": self.prefill_address,
            "P_proxy_port": self.prefill_port,
            "D_proxy_addr": self.decode_address,
            "D_proxy_port": self.decode_port,
            "PD_connector": self.connector,
            "PD_connector_descriptor": dict(self.connector_descriptor),
        }


class GlobalStrategy(Protocol):
    name: str

    def schedule(
        self,
        context: GlobalScheduleContext,
        resources: "ResourceSnapshot",
    ) -> GlobalScheduleDecision:
        ...
