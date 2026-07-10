from __future__ import annotations

import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

try:
    from .prefill_pso_scheduler import PrefillScheduleResult
    from ..dataset.request_workload import GeneratedRequest
    from .scheduling_strategies import (
        PlannedRequest,
        QueueRequestView,
        SchedulingStrategy,
    )
    from ..instance.vllm_execution_calculator import VllmExecutionCalculator
    from ..instance.vllm_v1_scheduler import SimulationResult, VllmRequest
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from simulator.gateway.prefill_pso_scheduler import PrefillScheduleResult
    from simulator.dataset.request_workload import GeneratedRequest
    from simulator.gateway.scheduling_strategies import (
        PlannedRequest,
        QueueRequestView,
        SchedulingStrategy,
    )
    from simulator.instance.vllm_execution_calculator import VllmExecutionCalculator
    from simulator.instance.vllm_v1_scheduler import SimulationResult, VllmRequest


@dataclass
class SimRequestState:
    request_id: str
    original_arrival_ms: float
    prompt_tokens: int
    slo_ttft_ms: float
    status: str = "pending"
    batch_order: Optional[int] = None
    prefill_instance: Optional[str] = None
    planned_start_ms: Optional[float] = None
    planned_finish_ms: Optional[float] = None
    batch_tokens: Optional[int] = None
    plan_position: Optional[int] = None
    dispatch_time_ms: Optional[float] = None

    @classmethod
    def from_request(cls, request: GeneratedRequest) -> "SimRequestState":
        return cls(
            request_id=request.request_id,
            original_arrival_ms=request.arrival_ms,
            prompt_tokens=request.prompt_tokens,
            slo_ttft_ms=request.slo_ttft_ms,
        )

    def to_view(self) -> QueueRequestView:
        return QueueRequestView(
            request_id=self.request_id,
            original_arrival_ms=self.original_arrival_ms,
            prompt_tokens=self.prompt_tokens,
            slo_ttft_ms=self.slo_ttft_ms,
        )


@dataclass
class DispatchRecord:
    dispatch_order: int
    strategy: str
    dispatch_time_ms: float
    request_ids: list[str]
    total_tokens: int
    prefill_instance: str
    planned_start_ms: float
    planned_finish_ms: float
    role: str = "running"
    actual_start_ms: Optional[float] = None
    actual_finish_ms: Optional[float] = None


@dataclass(frozen=True)
class QueueManagerResult:
    strategy: str
    dispatch_records: list[DispatchRecord]
    request_states: list[SimRequestState]
    vllm_requests: list[VllmRequest]
    pso_results: list[PrefillScheduleResult]
    vllm_result: Optional[SimulationResult] = None

    def to_dict(self) -> dict:
        data = {
            "strategy": self.strategy,
            "dispatch_records": [asdict(record) for record in self.dispatch_records],
            "request_states": [asdict(state) for state in self.request_states],
            "vllm_requests": [asdict(request) for request in self.vllm_requests],
            "pso_results": [result.to_dict() for result in self.pso_results],
        }
        if self.vllm_result is not None:
            data["vllm_result"] = self.vllm_result.to_dict()
        return data


class QueueManager:
    """Small synchronous queue/dispatch loop aligned with FlexPD proxy.sync."""

    def __init__(
        self,
        strategy: SchedulingStrategy,
        max_num_batched_tokens: int = 10240,
        token_time_ms: float = 0.01,
        batch_overhead_ms: float = 0.0,
        ttft_model_path=None,
    ) -> None:
        self.strategy = strategy
        self.max_num_batched_tokens = max_num_batched_tokens
        self.vllm = VllmExecutionCalculator(
            max_num_batched_tokens=max_num_batched_tokens,
            token_time_ms=token_time_ms,
            batch_overhead_ms=batch_overhead_ms,
            ttft_model_path=ttft_model_path,
        )

    def run(self, requests: list[GeneratedRequest]) -> QueueManagerResult:
        incoming = sorted(requests, key=lambda item: (item.arrival_ms, item.request_id))
        incoming_index = 0
        pending: list[SimRequestState] = []
        all_states: list[SimRequestState] = []
        dispatch_records: list[DispatchRecord] = []
        vllm_requests: list[VllmRequest] = []
        pso_results: list[PrefillScheduleResult] = []
        last_plan: Optional[PrefillScheduleResult] = None
        current_time_ms = 0.0
        dispatch_order = 0

        while incoming_index < len(incoming) or pending or self.vllm.running_end_ms is not None or self.vllm.has_pending_work:
            while (
                incoming_index < len(incoming)
                and incoming[incoming_index].arrival_ms <= current_time_ms
            ):
                state = SimRequestState.from_request(incoming[incoming_index])
                pending.append(state)
                all_states.append(state)
                incoming_index += 1

            self.vllm.complete_running_if_due(current_time_ms)

            if self.vllm.running_end_ms is None and pending:
                role = "running" if not self.vllm.has_waiting_work else "waiting"
                record, pso_result, pending = self._dispatch_next_batch(
                    pending,
                    current_time_ms,
                    dispatch_order + 1,
                    role,
                    last_plan,
                    vllm_requests,
                    available_waiting_tokens=None,
                )
                dispatch_order = record.dispatch_order
                dispatch_records.append(record)
                if pso_result is not None:
                    pso_results.append(pso_result)
                    last_plan = pso_result

            self.vllm.start_next_batch_if_idle(current_time_ms)
            while (
                self.vllm.running_end_ms is not None
                and pending
                and self.vllm.waiting_tokens < self.max_num_batched_tokens
            ):
                available_waiting_tokens = self.max_num_batched_tokens - self.vllm.waiting_tokens
                record, pso_result, pending = self._dispatch_next_batch(
                    pending,
                    current_time_ms,
                    dispatch_order + 1,
                    "waiting",
                    last_plan,
                    vllm_requests,
                    available_waiting_tokens=available_waiting_tokens,
                )
                dispatch_order = record.dispatch_order
                dispatch_records.append(record)
                if pso_result is not None:
                    pso_results.append(pso_result)
                    last_plan = pso_result
                if record.total_tokens <= 0:
                    break

            next_arrival_ms = incoming[incoming_index].arrival_ms if incoming_index < len(incoming) else None
            running_finish_ms = self.vllm.running_end_ms
            if running_finish_ms is None and not pending:
                if self.vllm.has_pending_work:
                    engine_arrival_ms = self.vllm.next_arrival_ms
                    if engine_arrival_ms is not None and engine_arrival_ms > current_time_ms:
                        current_time_ms = engine_arrival_ms
                    else:
                        self.vllm.start_next_batch_if_idle(current_time_ms)
                    continue
                if next_arrival_ms is None:
                    break
                current_time_ms = max(current_time_ms, next_arrival_ms)
                continue

            candidates = [
                time_ms
                for time_ms in (next_arrival_ms, running_finish_ms, self.vllm.next_arrival_ms)
                if time_ms is not None and time_ms > current_time_ms
            ]
            if not candidates:
                if running_finish_ms is not None:
                    current_time_ms = max(current_time_ms, running_finish_ms)
                elif next_arrival_ms is not None:
                    current_time_ms = max(current_time_ms, next_arrival_ms)
                else:
                    if pending:
                        continue
                    break
            else:
                current_time_ms = min(candidates)

        vllm_result = self.vllm.finish_all()
        self._align_dispatch_records_with_vllm(dispatch_records, vllm_result)

        return QueueManagerResult(
            strategy=self.strategy.name,
            dispatch_records=dispatch_records,
            request_states=all_states,
            vllm_requests=vllm_requests,
            pso_results=pso_results,
            vllm_result=vllm_result,
        )

    def _apply_plan(self, state: SimRequestState, plan: PlannedRequest) -> None:
        state.status = "scheduled"
        state.batch_order = plan.batch_order
        state.prefill_instance = plan.prefill_instance
        state.planned_start_ms = round(plan.planned_start_ms, 6)
        state.planned_finish_ms = round(plan.planned_finish_ms, 6)
        state.batch_tokens = plan.batch_tokens
        state.plan_position = plan.plan_position

    def _dispatch_next_batch(
        self,
        pending: list[SimRequestState],
        current_time_ms: float,
        dispatch_order: int,
        role: str,
        last_plan: Optional[PrefillScheduleResult],
        vllm_requests: list[VllmRequest],
        available_waiting_tokens: Optional[int] = None,
    ) -> tuple[DispatchRecord, Optional[PrefillScheduleResult], list[SimRequestState]]:
        decision = self.strategy.plan(
            [state.to_view() for state in pending],
            current_time_ms,
            last_plan=last_plan,
            vllm_snapshot=self.vllm.snapshot(current_time_ms),
            available_waiting_tokens=available_waiting_tokens,
        )
        plans = {plan.request_id: plan for plan in decision.planned_requests}
        for state in pending:
            plan = plans.get(state.request_id)
            if plan is not None:
                self._apply_plan(state, plan)

        dispatch_ids = set(decision.dispatch_request_ids)
        if not dispatch_ids:
            dispatch_ids = {pending[0].request_id}

        dispatch_states = [
            state
            for state in pending
            if state.request_id in dispatch_ids
        ]
        dispatch_states.sort(
            key=lambda item: (
                item.batch_order if item.batch_order is not None else 999999,
                item.plan_position if item.plan_position is not None else 999999,
                item.original_arrival_ms,
                item.request_id,
            )
        )
        dispatch_time_ms = max(
            current_time_ms,
            max(state.original_arrival_ms for state in dispatch_states),
        )
        total_tokens = sum(state.prompt_tokens for state in dispatch_states)
        planned_start_ms = min(
            state.planned_start_ms if state.planned_start_ms is not None else dispatch_time_ms
            for state in dispatch_states
        )
        planned_finish_ms = max(
            state.planned_finish_ms if state.planned_finish_ms is not None else decision.next_time_ms
            for state in dispatch_states
        )
        instance = dispatch_states[0].prefill_instance or "prefill-0"
        record = DispatchRecord(
            dispatch_order=dispatch_order,
            strategy=self.strategy.name,
            dispatch_time_ms=round(dispatch_time_ms, 6),
            request_ids=[state.request_id for state in dispatch_states],
            total_tokens=total_tokens,
            prefill_instance=instance,
            planned_start_ms=round(planned_start_ms, 6),
            planned_finish_ms=round(planned_finish_ms, 6),
            role=role,
        )

        for state in dispatch_states:
            state.status = "dispatched"
            state.dispatch_time_ms = round(dispatch_time_ms, 6)
            vllm_requests.append(VllmRequest(state.request_id, state.dispatch_time_ms, state.prompt_tokens))
        self.vllm.add_requests(
            [
                VllmRequest(
                    state.request_id,
                    state.dispatch_time_ms if state.dispatch_time_ms is not None else dispatch_time_ms,
                    state.prompt_tokens,
                )
                for state in dispatch_states
            ],
            dispatch_time_ms,
        )

        dispatched = {state.request_id for state in dispatch_states}
        remaining = [state for state in pending if state.request_id not in dispatched]
        return record, decision.raw_result, remaining

    def _align_dispatch_records_with_vllm(self, dispatch_records, vllm_result) -> None:
        first_batch_start_by_id = {}
        complete_by_id = {}
        for batch in vllm_result.batches:
            for chunk in batch.chunks:
                first_batch_start_by_id.setdefault(chunk.request_id, batch.start_ms)
        for request in vllm_result.requests:
            complete_by_id[request.request_id] = request.complete_time_ms

        for record in dispatch_records:
            starts = [
                first_batch_start_by_id[request_id]
                for request_id in record.request_ids
                if request_id in first_batch_start_by_id
            ]
            finishes = [
                complete_by_id[request_id]
                for request_id in record.request_ids
                if request_id in complete_by_id
            ]
            if starts:
                record.actual_start_ms = round(min(starts), 6)
            if finishes:
                record.actual_finish_ms = round(max(finishes), 6)
