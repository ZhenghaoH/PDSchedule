from __future__ import annotations

import random
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Protocol, Union

try:
    from .prefill_pso_scheduler import (
        PrefillPSOScheduler,
        PrefillScheduleResult,
        PrefillSimInstance,
        PrefillSimRequest,
    )
    from .vllm_v1_scheduler import VllmRequest, VllmV1SchedulerSimulator
    from .ttft_model import DEFAULT_PREFILL_TTFT_MODEL_PATH
    from .vllm_execution_calculator import VllmExecutionCalculator, VllmQueueSnapshot
except ImportError:
    from prefill_pso_scheduler import (
        PrefillPSOScheduler,
        PrefillScheduleResult,
        PrefillSimInstance,
        PrefillSimRequest,
    )
    from vllm_v1_scheduler import VllmRequest, VllmV1SchedulerSimulator
    from ttft_model import DEFAULT_PREFILL_TTFT_MODEL_PATH
    from vllm_execution_calculator import VllmExecutionCalculator, VllmQueueSnapshot


@dataclass(frozen=True)
class QueueRequestView:
    request_id: str
    original_arrival_ms: float
    prompt_tokens: int
    slo_ttft_ms: float


@dataclass(frozen=True)
class PlannedRequest:
    request_id: str
    batch_order: int
    prefill_instance: str
    planned_start_ms: float
    planned_finish_ms: float
    batch_tokens: int
    plan_position: int


@dataclass(frozen=True)
class ScheduleDecision:
    planned_requests: list[PlannedRequest]
    dispatch_request_ids: list[str]
    next_time_ms: float
    raw_result: Optional[PrefillScheduleResult] = None


class SchedulingStrategy(Protocol):
    name: str

    def plan(
        self,
        pending_requests: list[QueueRequestView],
        current_time_ms: float,
        last_plan: Optional[PrefillScheduleResult] = None,
        vllm_snapshot: Optional[VllmQueueSnapshot] = None,
        available_waiting_tokens: Optional[int] = None,
    ) -> ScheduleDecision:
        ...


class DirectSchedulingStrategy:
    name = "direct"

    def __init__(
        self,
        instance_id: str = "prefill-0",
        token_time_ms: float = 0.01,
        batch_overhead_ms: float = 0.0,
    ) -> None:
        self.instance_id = instance_id
        self.token_time_ms = token_time_ms
        self.batch_overhead_ms = batch_overhead_ms

    def plan(
        self,
        pending_requests: list[QueueRequestView],
        current_time_ms: float,
        last_plan: Optional[PrefillScheduleResult] = None,
        vllm_snapshot: Optional[VllmQueueSnapshot] = None,
        available_waiting_tokens: Optional[int] = None,
    ) -> ScheduleDecision:
        del last_plan, vllm_snapshot
        selected = self._select_dispatch_batch(pending_requests, available_waiting_tokens)
        selected_ids = {req.request_id for req in selected}
        total_tokens = sum(req.prompt_tokens for req in selected)
        planned_finish_ms = current_time_ms + self.batch_overhead_ms + total_tokens * self.token_time_ms
        planned = [
            PlannedRequest(
                request_id=req.request_id,
                batch_order=1,
                prefill_instance=self.instance_id,
                planned_start_ms=current_time_ms,
                planned_finish_ms=planned_finish_ms,
                batch_tokens=total_tokens,
                plan_position=index,
            )
            for index, req in enumerate(selected)
        ]
        return ScheduleDecision(
            planned_requests=planned,
            dispatch_request_ids=[req.request_id for req in pending_requests if req.request_id in selected_ids],
            next_time_ms=planned_finish_ms,
        )

    def _select_dispatch_batch(
        self,
        pending_requests: list[QueueRequestView],
        available_waiting_tokens: Optional[int],
    ) -> list[QueueRequestView]:
        if available_waiting_tokens is None:
            return list(pending_requests)
        selected = []
        total_tokens = 0
        for req in pending_requests:
            if selected and total_tokens + req.prompt_tokens > available_waiting_tokens:
                break
            selected.append(req)
            total_tokens += req.prompt_tokens
            if total_tokens >= available_waiting_tokens:
                break
        return selected


@dataclass
class _RequestLevelParticle:
    position: list[float]
    velocity: list[float]
    best_position: list[float]
    best_score: float = float("inf")

    @classmethod
    def from_position(cls, position: list[float], rng: random.Random) -> "_RequestLevelParticle":
        return cls(
            position=list(position),
            velocity=[rng.uniform(-1.0, 1.0) for _ in position],
            best_position=list(position),
        )

    @classmethod
    def random(cls, dim: int, rng: random.Random) -> "_RequestLevelParticle":
        position = [rng.uniform(0.0, float(dim) + 1.0) for _ in range(dim)]
        return cls.from_position(position, rng)


@dataclass(frozen=True)
class _RollingPrediction:
    finish_by_id: dict[str, float]
    dispatch_by_id: dict[str, float]
    batches: list[list[QueueRequestView]]


class RequestLevelPSOSLOAwareStrategy:
    name = "pso"

    def __init__(
        self,
        instance_id: str = "prefill-0",
        max_batch_tokens: int = 10240,
        token_time_ms: float = 0.01,
        batch_overhead_ms: float = 0.0,
        pso_particles: int = 30,
        pso_iterations: int = 20,
        random_seed: Optional[int] = 0,
        ttft_model_path: Optional[Union[Path, str]] = DEFAULT_PREFILL_TTFT_MODEL_PATH,
        lateness_weight: float = 0.001,
        latency_weight: float = 0.000001,
        w: float = 0.7,
        c1: float = 1.4,
        c2: float = 1.4,
    ) -> None:
        self.instance_id = instance_id
        self.max_batch_tokens = max_batch_tokens
        self.vllm = VllmV1SchedulerSimulator(
            max_num_batched_tokens=max_batch_tokens,
            token_time_ms=token_time_ms,
            batch_overhead_ms=batch_overhead_ms,
            ttft_model_path=ttft_model_path,
        )
        self.token_time_ms = token_time_ms
        self.pso_particles = pso_particles
        self.pso_iterations = pso_iterations
        self.random_seed = random_seed
        self.lateness_weight = lateness_weight
        self.latency_weight = latency_weight
        self.w = w
        self.c1 = c1
        self.c2 = c2

    def plan(
        self,
        pending_requests: list[QueueRequestView],
        current_time_ms: float,
        last_plan: Optional[PrefillScheduleResult] = None,
        vllm_snapshot: Optional[VllmQueueSnapshot] = None,
        available_waiting_tokens: Optional[int] = None,
    ) -> ScheduleDecision:
        del last_plan
        if not pending_requests:
            return ScheduleDecision([], [], current_time_ms)

        ordered = self._search_best_order(pending_requests, current_time_ms, vllm_snapshot)
        planned = self._build_plans(ordered, current_time_ms, vllm_snapshot)
        dispatch_ids = self._select_dispatch_batch(ordered, available_waiting_tokens)
        next_time_ms = max(
            plan.planned_finish_ms
            for plan in planned
            if plan.request_id in set(dispatch_ids)
        )
        return ScheduleDecision(
            planned_requests=planned,
            dispatch_request_ids=dispatch_ids,
            next_time_ms=next_time_ms,
        )

    def _search_best_order(
        self,
        requests: list[QueueRequestView],
        current_time_ms: float,
        vllm_snapshot: Optional[VllmQueueSnapshot],
    ) -> list[QueueRequestView]:
        if len(requests) == 1:
            return list(requests)

        rng = random.Random(self.random_seed)
        dim = len(requests)
        swarm = self._build_initial_swarm(requests, rng)
        while len(swarm) < self.pso_particles:
            swarm.append(_RequestLevelParticle.random(dim, rng))

        global_best_score = float("inf")
        global_best_position = list(swarm[0].position)
        for iter_index in range(self.pso_iterations):
            g_best_position = global_best_position if iter_index > 0 else swarm[0].best_position
            for particle in swarm:
                score = self._evaluate_position(requests, particle.position, current_time_ms, vllm_snapshot)
                if score < particle.best_score:
                    particle.best_score = score
                    particle.best_position = list(particle.position)
                if score < global_best_score:
                    global_best_score = score
                    global_best_position = list(particle.position)

                for index in range(dim):
                    r1 = rng.random()
                    r2 = rng.random()
                    particle.velocity[index] = (
                        self.w * particle.velocity[index]
                        + self.c1 * r1 * (particle.best_position[index] - particle.position[index])
                        + self.c2 * r2 * (g_best_position[index] - particle.position[index])
                    )
                    particle.velocity[index] = max(-2.0, min(2.0, particle.velocity[index]))
                    particle.position[index] += particle.velocity[index]
                    particle.position[index] = max(0.0, min(float(dim) + 1.0, particle.position[index]))

        return self._order_from_position(requests, global_best_position)

    def _build_initial_swarm(
        self,
        requests: list[QueueRequestView],
        rng: random.Random,
    ) -> list[_RequestLevelParticle]:
        positions = [
            self._position_from_order(requests, sorted(requests, key=lambda item: item.original_arrival_ms)),
            self._position_from_order(
                requests,
                sorted(
                    requests,
                    key=lambda item: (
                        item.original_arrival_ms
                        + item.slo_ttft_ms
                        - item.prompt_tokens * self.token_time_ms,
                        item.original_arrival_ms,
                    ),
                ),
            ),
            self._position_from_order(requests, sorted(requests, key=lambda item: item.prompt_tokens)),
        ]
        return [_RequestLevelParticle.from_position(position, rng) for position in positions[: self.pso_particles]]

    def _position_from_order(
        self,
        requests: list[QueueRequestView],
        ordered: list[QueueRequestView],
    ) -> list[float]:
        priority = {req.request_id: float(index) for index, req in enumerate(ordered)}
        return [priority[req.request_id] for req in requests]

    def _order_from_position(
        self,
        requests: list[QueueRequestView],
        position: list[float],
    ) -> list[QueueRequestView]:
        return [
            req
            for _value, req in sorted(
                zip(position, requests),
                key=lambda item: (item[0], item[1].original_arrival_ms, item[1].request_id),
            )
        ]

    def _evaluate_position(
        self,
        requests: list[QueueRequestView],
        position: list[float],
        current_time_ms: float,
        vllm_snapshot: Optional[VllmQueueSnapshot],
    ) -> float:
        ordered = self._order_from_position(requests, position)
        prediction = self._predict_rolling_future(ordered, current_time_ms, vllm_snapshot)
        by_id = {req.request_id: req for req in requests}
        goodput_tokens = 0
        lateness_penalty = 0.0
        latency_penalty = 0.0
        for request_id, original in by_id.items():
            complete_time_ms = prediction.finish_by_id[request_id]
            deadline_ms = original.original_arrival_ms + original.slo_ttft_ms
            if complete_time_ms <= deadline_ms:
                goodput_tokens += original.prompt_tokens
            else:
                lateness_penalty += (
                    complete_time_ms - deadline_ms
                ) / max(1.0, original.slo_ttft_ms)
            latency_penalty += max(0.0, complete_time_ms - original.original_arrival_ms)
        return (
            -float(goodput_tokens)
            + self.lateness_weight * lateness_penalty
            + self.latency_weight * latency_penalty
        )

    def _build_plans(
        self,
        ordered: list[QueueRequestView],
        current_time_ms: float,
        vllm_snapshot: Optional[VllmQueueSnapshot],
    ) -> list[PlannedRequest]:
        prediction = self._predict_rolling_future(ordered, current_time_ms, vllm_snapshot)
        batch_order_by_id = {}
        batch_tokens_by_id = {}
        position_by_id = {}
        for batch_order, batch in enumerate(prediction.batches, start=1):
            batch_tokens = sum(req.prompt_tokens for req in batch)
            for position, req in enumerate(batch):
                batch_order_by_id[req.request_id] = batch_order
                batch_tokens_by_id[req.request_id] = batch_tokens
                position_by_id[req.request_id] = position
        return [
            PlannedRequest(
                request_id=req.request_id,
                batch_order=batch_order_by_id[req.request_id],
                prefill_instance=self.instance_id,
                planned_start_ms=prediction.dispatch_by_id[req.request_id],
                planned_finish_ms=prediction.finish_by_id[req.request_id],
                batch_tokens=batch_tokens_by_id[req.request_id],
                plan_position=position_by_id[req.request_id],
            )
            for index, req in enumerate(ordered)
        ]

    def _predict_rolling_future(
        self,
        ordered: list[QueueRequestView],
        current_time_ms: float,
        vllm_snapshot: Optional[VllmQueueSnapshot],
    ) -> _RollingPrediction:
        dispatch_batches = self._split_dispatch_batches(ordered)
        dispatch_by_id: dict[str, float] = {}
        calculator = VllmExecutionCalculator(
            max_num_batched_tokens=self.max_batch_tokens,
            token_time_ms=self.token_time_ms,
            batch_overhead_ms=0.0,
            ttft_model_path=None,
        )
        calculator.ttft_model = self.vllm.ttft_model

        running_delay_end_ms = None
        simulation_start_ms = current_time_ms
        if vllm_snapshot is not None:
            running_delay_end_ms = vllm_snapshot.running_end_ms
            simulation_start_ms = max(
                current_time_ms,
                running_delay_end_ms if running_delay_end_ms is not None else current_time_ms,
            )
            snapshot_requests = [
                VllmRequest(
                    request_id=f"__vllm_snapshot_{index}_{req.request_id}",
                    arrival_ms=simulation_start_ms,
                    prompt_tokens=req.remaining_tokens,
                )
                for index, req in enumerate(vllm_snapshot.queued_requests)
                if req.remaining_tokens > 0
            ]
            if snapshot_requests:
                calculator.add_requests(snapshot_requests, simulation_start_ms)

        batch_index = 0
        current = current_time_ms
        epsilon_ms = 0.000001

        def dispatch_batch(batch: list[QueueRequestView], dispatch_time_ms: float, arrival_ms: float) -> None:
            rounded_arrival_ms = round(arrival_ms, 6)
            for req in batch:
                dispatch_by_id[req.request_id] = rounded_arrival_ms
            calculator.add_requests(
                [
                    VllmRequest(
                        request_id=req.request_id,
                        arrival_ms=rounded_arrival_ms,
                        prompt_tokens=req.prompt_tokens,
                    )
                    for req in batch
                ],
                dispatch_time_ms,
            )

        def fill_waiting_while_running(dispatch_time_ms: float, arrival_ms: float) -> None:
            nonlocal batch_index
            while (
                batch_index < len(dispatch_batches)
                and calculator.waiting_tokens < self.max_batch_tokens
            ):
                dispatch_batch(dispatch_batches[batch_index], dispatch_time_ms, arrival_ms)
                batch_index += 1

        while batch_index < len(dispatch_batches) or calculator.running_end_ms is not None or calculator.has_pending_work or running_delay_end_ms is not None:
            if running_delay_end_ms is not None:
                fill_waiting_while_running(current, simulation_start_ms)
                current = max(current, running_delay_end_ms)
                running_delay_end_ms = None
                continue

            completed = calculator.complete_running_if_due(current)
            has_waiting = calculator.has_waiting_work

            if calculator.running_end_ms is None:
                if not has_waiting and batch_index < len(dispatch_batches):
                    dispatch_batch(dispatch_batches[batch_index], current, current)
                    batch_index += 1
                started = calculator.start_next_batch_if_idle(current)
                if started is not None:
                    current = started.start_ms
                    fill_waiting_while_running(current, current + epsilon_ms)

            running_end = calculator.running_end_ms
            if running_end is not None and running_end > current:
                current = running_end
                continue
            if calculator.next_arrival_ms is not None and calculator.next_arrival_ms > current:
                current = calculator.next_arrival_ms
                continue
            if not completed and batch_index >= len(dispatch_batches) and calculator.running_end_ms is None and not calculator.has_pending_work:
                break

        result = calculator.finish_all()
        finish_by_id = {
            record.request_id: record.complete_time_ms
            for record in result.requests
            if not record.request_id.startswith("__vllm_snapshot_")
        }
        return _RollingPrediction(
            finish_by_id=finish_by_id,
            dispatch_by_id=dispatch_by_id,
            batches=dispatch_batches,
        )

    def _split_dispatch_batches(self, ordered: list[QueueRequestView]) -> list[list[QueueRequestView]]:
        batches: list[list[QueueRequestView]] = []
        current_batch: list[QueueRequestView] = []
        total_tokens = 0
        for req in ordered:
            if current_batch and total_tokens + req.prompt_tokens > self.max_batch_tokens:
                batches.append(current_batch)
                current_batch = []
                total_tokens = 0
            current_batch.append(req)
            total_tokens += req.prompt_tokens
            if total_tokens >= self.max_batch_tokens:
                batches.append(current_batch)
                current_batch = []
                total_tokens = 0
        if current_batch:
            batches.append(current_batch)
        return batches

    def _to_vllm_requests(
        self,
        ordered: list[QueueRequestView],
        current_time_ms: float,
        vllm_snapshot: Optional[VllmQueueSnapshot] = None,
    ) -> list[VllmRequest]:
        simulation_start_ms = current_time_ms
        prefix: list[VllmRequest] = []
        if vllm_snapshot is not None:
            simulation_start_ms = max(
                current_time_ms,
                vllm_snapshot.running_end_ms
                if vllm_snapshot.running_end_ms is not None
                else current_time_ms,
            )
            prefix = [
                VllmRequest(
                    request_id=f"__vllm_snapshot_{index}_{req.request_id}",
                    arrival_ms=simulation_start_ms,
                    prompt_tokens=req.remaining_tokens,
                )
                for index, req in enumerate(vllm_snapshot.queued_requests)
                if req.remaining_tokens > 0
            ]

        return prefix + [
            VllmRequest(
                request_id=req.request_id,
                arrival_ms=simulation_start_ms,
                prompt_tokens=req.prompt_tokens,
            )
            for index, req in enumerate(ordered)
        ]

    def _select_dispatch_batch(
        self,
        ordered: list[QueueRequestView],
        available_waiting_tokens: Optional[int] = None,
    ) -> list[str]:
        selected = []
        total_tokens = 0
        token_budget = self.max_batch_tokens if available_waiting_tokens is None else available_waiting_tokens
        for req in ordered:
            if selected and total_tokens + req.prompt_tokens > token_budget:
                break
            selected.append(req.request_id)
            total_tokens += req.prompt_tokens
            if total_tokens >= token_budget:
                break
        return selected


class ChunkLevelPSOSchedulingStrategy:
    name = "pso_chunk"

    def __init__(
        self,
        instance_id: str = "prefill-0",
        max_batch_tokens: int = 10240,
        token_time_ms: float = 0.01,
        batch_overhead_ms: float = 0.0,
        pso_particles: int = 30,
        pso_iterations: int = 20,
        random_seed: Optional[int] = 0,
    ) -> None:
        self.instance_id = instance_id
        self.scheduler = PrefillPSOScheduler(
            max_batch_tokens=max_batch_tokens,
            token_time_ms=token_time_ms,
            batch_overhead_ms=batch_overhead_ms,
            pso_particles=pso_particles,
            pso_iterations=pso_iterations,
            random_seed=random_seed,
        )

    def plan(
        self,
        pending_requests: list[QueueRequestView],
        current_time_ms: float,
        last_plan: Optional[PrefillScheduleResult] = None,
        vllm_snapshot: Optional[VllmQueueSnapshot] = None,
        available_waiting_tokens: Optional[int] = None,
    ) -> ScheduleDecision:
        del vllm_snapshot, available_waiting_tokens
        pso_requests = [
            PrefillSimRequest(
                request_id=req.request_id,
                arrival_ms=req.original_arrival_ms,
                prompt_tokens=req.prompt_tokens,
                slo_ttft_ms=req.slo_ttft_ms,
            )
            for req in pending_requests
        ]
        result = self.scheduler.schedule(
            pso_requests,
            PrefillSimInstance(self.instance_id, available_time_ms=current_time_ms),
            current_time_ms=current_time_ms,
            last_plan=last_plan,
        )
        first_seen = self._first_seen_plans(result)
        planned = [
            first_seen[req.request_id]
            for req in pending_requests
            if req.request_id in first_seen
        ]
        if not planned:
            return ScheduleDecision([], [], current_time_ms, result)

        first_batch_order = min(item.batch_order for item in planned)
        dispatch_ids = [
            item.request_id
            for item in sorted(planned, key=lambda item: item.plan_position)
            if item.batch_order == first_batch_order
        ]
        next_time_ms = max(
            item.planned_finish_ms
            for item in planned
            if item.batch_order == first_batch_order
        )
        return ScheduleDecision(
            planned_requests=planned,
            dispatch_request_ids=dispatch_ids,
            next_time_ms=next_time_ms,
            raw_result=result,
        )

    def _first_seen_plans(self, result: PrefillScheduleResult) -> dict[str, PlannedRequest]:
        plans = {}
        for batch in result.batches:
            for position, request_id in enumerate(batch.request_ids):
                if request_id in plans:
                    continue
                plans[request_id] = PlannedRequest(
                    request_id=request_id,
                    batch_order=batch.batch_order,
                    prefill_instance=batch.instance_id,
                    planned_start_ms=batch.start_ms,
                    planned_finish_ms=batch.finish_ms,
                    batch_tokens=batch.total_tokens,
                    plan_position=position,
                )
        return plans


PSOSchedulingStrategy = RequestLevelPSOSLOAwareStrategy
