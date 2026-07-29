from core.schemas.request import BaseRequest
from scheduler.gateway.decode.strategy import (
    DecodeScheduleResult,
    DecodeStrategy,
    candidate_snapshot,
)


class RoundRobinDecodeStrategy(DecodeStrategy):
    name = "round_robin"

    async def schedule(self, request: BaseRequest) -> DecodeScheduleResult:
        return DecodeScheduleResult(
            selected_instance=self.storage.next_decode_round_robin(),
            selection_reason="next_round_robin_instance_load_ignored",
            candidates=candidate_snapshot(self.storage),
        )
