from dataclasses import dataclass
from typing import Optional, Protocol, TYPE_CHECKING

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
    selection_source: str = "controller_scheduler"

    def to_proxy_dict(self):
        return {
            "P_proxy_addr": self.prefill_address,
            "P_proxy_port": self.prefill_port,
            "D_proxy_addr": self.decode_address,
            "D_proxy_port": self.decode_port,
        }


class GlobalStrategy(Protocol):
    name: str

    def schedule(
        self,
        context: GlobalScheduleContext,
        resources: "ResourceSnapshot",
    ) -> GlobalScheduleDecision:
        ...
