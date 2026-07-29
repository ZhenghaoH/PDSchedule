from core.schemas.request import BaseRequest
from scheduler.gateway.decode.strategy import (
    DecodeScheduleResult,
    DecodeStrategy,
    candidate_snapshot,
    generate_throughput,
)


class LowestGenerationThroughputDecodeStrategy(DecodeStrategy):
    name = "lowest_generation_throughput"

    async def schedule(self, request: BaseRequest) -> DecodeScheduleResult:
        decode_instances = self.storage.active_decode_objs
        selected = (
            min(
                decode_instances,
                key=lambda instance_ip: (
                    generate_throughput(self.storage, instance_ip),
                    instance_ip,
                ),
            )
            if decode_instances
            else ""
        )
        return DecodeScheduleResult(
            selected_instance=selected,
            selection_reason="minimum_generation_throughput",
            candidates=candidate_snapshot(self.storage),
        )
