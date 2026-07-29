from scheduler.gateway.prefill.strategy import (
    PrefillScheduleContext,
    PrefillScheduleResult,
    PrefillStrategy,
    candidate_snapshot,
)


class RoundRobinPrefillStrategy(PrefillStrategy):
    name = "round_robin"

    async def schedule(
        self,
        context: PrefillScheduleContext,
    ) -> PrefillScheduleResult:
        candidates = candidate_snapshot(self.storage)
        snapshots = {}
        for request in context.requests:
            request.Task.batch_order = 1
            request.Task.prefill_instance = self.storage.next_prefill_round_robin()
            snapshots[str(request.Request_ID)] = candidates
        return PrefillScheduleResult(
            requests=context.requests,
            dispatch_policy="IMMEDIATE",
            selection_reason="next_round_robin_instance_load_ignored",
            candidate_snapshots=snapshots,
        )
