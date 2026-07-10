from __future__ import annotations

import argparse
import json
import random
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable, Optional, Union


@dataclass(frozen=True)
class PrefillSimRequest:
    request_id: str
    arrival_ms: float
    prompt_tokens: int
    slo_ttft_ms: float

    def __post_init__(self) -> None:
        if self.prompt_tokens <= 0:
            raise ValueError("prompt_tokens must be positive")
        if self.arrival_ms < 0:
            raise ValueError("arrival_ms must be non-negative")
        if self.slo_ttft_ms <= 0:
            raise ValueError("slo_ttft_ms must be positive")


@dataclass(frozen=True)
class PrefillSimInstance:
    instance_id: str
    available_time_ms: float = 0.0
    active: bool = True


@dataclass(frozen=True)
class PrefillScheduledRequest:
    request_id: str
    prefill_instance: str
    batch_order: int
    arrival_ms: float
    prompt_tokens: int
    finish_ms: float
    ttft_ms: float


@dataclass(frozen=True)
class PrefillBatchPlan:
    batch_order: int
    instance_id: str
    request_ids: list[str]
    total_tokens: int
    start_ms: float
    finish_ms: float
    duration_ms: float
    goodput_tokens: int


@dataclass(frozen=True)
class PrefillScheduleResult:
    requests: list[PrefillScheduledRequest]
    batches: list[PrefillBatchPlan]
    score: float
    target_instance: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class _SchedulableRequest:
    source: PrefillSimRequest
    deadline_ms: float
    solo_time_ms: float
    slack_ms: float

    @property
    def request_id(self) -> str:
        return self.source.request_id

    @property
    def prompt_tokens(self) -> int:
        return self.source.prompt_tokens


@dataclass(frozen=True)
class _ChunkPiece:
    request: _SchedulableRequest
    tokens: int
    completed: bool


@dataclass(frozen=True)
class _FullChunk:
    pieces: list[_ChunkPiece]
    total_tokens: int

    @property
    def request_ids(self) -> list[str]:
        ids = []
        for piece in self.pieces:
            request_id = piece.request.request_id
            if request_id not in ids:
                ids.append(request_id)
        return ids


@dataclass
class _Particle:
    position: list[float]
    velocity: list[float]
    best_position: list[float]
    best_score: float = float("inf")

    @classmethod
    def random(cls, dim: int, lower: float, upper: float, rng: random.Random) -> "_Particle":
        position = [rng.uniform(lower, upper) for _ in range(dim)]
        velocity = [rng.uniform(-1.0, 1.0) for _ in range(dim)]
        return cls(position=position, velocity=velocity, best_position=list(position))

    @classmethod
    def from_position(cls, position: list[float], rng: random.Random) -> "_Particle":
        velocity = [rng.uniform(-1.0, 1.0) for _ in position]
        return cls(position=list(position), velocity=velocity, best_position=list(position))


class PrefillPSOScheduler:
    """Offline Prefill-side PSO scheduler for synchronous batch simulation."""

    def __init__(
        self,
        max_batch_tokens: int = 10240,
        max_batch_size: int = 100,
        token_time_ms: float = 0.01,
        batch_overhead_ms: float = 0.0,
        pso_particles: int = 30,
        pso_iterations: int = 20,
        random_seed: Optional[int] = 0,
        w: float = 0.7,
        c1: float = 1.4,
        c2: float = 1.4,
        heuristic_batch_tokens: int = 32000,
        lateness_weight: float = 0.001,
        latency_weight: float = 0.000001,
    ) -> None:
        if max_batch_tokens <= 0:
            raise ValueError("max_batch_tokens must be positive")
        if max_batch_size <= 0:
            raise ValueError("max_batch_size must be positive")
        if token_time_ms < 0:
            raise ValueError("token_time_ms must be non-negative")
        if batch_overhead_ms < 0:
            raise ValueError("batch_overhead_ms must be non-negative")
        if pso_particles <= 0:
            raise ValueError("pso_particles must be positive")
        if pso_iterations <= 0:
            raise ValueError("pso_iterations must be positive")
        if heuristic_batch_tokens <= 0:
            raise ValueError("heuristic_batch_tokens must be positive")

        self.max_batch_tokens = max_batch_tokens
        self.max_batch_size = max_batch_size
        self.token_time_ms = token_time_ms
        self.batch_overhead_ms = batch_overhead_ms
        self.pso_particles = pso_particles
        self.pso_iterations = pso_iterations
        self.random_seed = random_seed
        self.w = w
        self.c1 = c1
        self.c2 = c2
        self.heuristic_batch_tokens = heuristic_batch_tokens
        self.lateness_weight = lateness_weight
        self.latency_weight = latency_weight

    def schedule(
        self,
        requests: Iterable[PrefillSimRequest],
        instance: PrefillSimInstance,
        current_time_ms: float = 0.0,
        last_plan: Optional[Union[PrefillScheduleResult, dict[str, int]]] = None,
    ) -> PrefillScheduleResult:
        request_list = list(requests)
        if not request_list:
            return PrefillScheduleResult(
                requests=[],
                batches=[],
                score=0.0,
                target_instance=instance.instance_id,
            )
        if not instance.active:
            raise ValueError("instance must be active")

        start_time_ms = max(current_time_ms, instance.available_time_ms)
        schedulable = self._wrap_requests(request_list, start_time_ms)
        rng = random.Random(self.random_seed)
        dim = len(schedulable)
        lower, upper = 0.0, float(dim) + 1.0
        swarm = self._build_initial_swarm(schedulable, last_plan, lower, upper, rng)

        while len(swarm) < self.pso_particles:
            swarm.append(_Particle.random(dim, lower, upper, rng))

        global_best_score = float("inf")
        global_best_position = list(swarm[0].position)
        global_best_batches: list[_FullChunk] = []
        global_best_timing: list[tuple[float, float, int]] = []

        for iter_idx in range(self.pso_iterations):
            g_best_position = global_best_position if iter_idx > 0 else swarm[0].best_position
            for particle in swarm:
                score, batches, timing = self._evaluate_particle(
                    schedulable,
                    particle.position,
                    start_time_ms,
                )
                if score < particle.best_score:
                    particle.best_score = score
                    particle.best_position = list(particle.position)
                if score < global_best_score:
                    global_best_score = score
                    global_best_position = list(particle.position)
                    global_best_batches = batches
                    global_best_timing = timing

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
                    particle.position[index] = max(lower, min(upper, particle.position[index]))

        return self._build_result(
            global_best_batches,
            global_best_timing,
            global_best_score,
            instance.instance_id,
        )

    def _predict_duration_ms(self, total_tokens: int) -> float:
        return self.batch_overhead_ms + total_tokens * self.token_time_ms

    def _wrap_requests(
        self,
        requests: list[PrefillSimRequest],
        start_time_ms: float,
    ) -> list[_SchedulableRequest]:
        wrapped = []
        for req in requests:
            solo_time_ms = self._predict_duration_ms(req.prompt_tokens)
            deadline_ms = req.arrival_ms + req.slo_ttft_ms
            slack_ms = deadline_ms - start_time_ms - solo_time_ms
            wrapped.append(
                _SchedulableRequest(
                    source=req,
                    deadline_ms=deadline_ms,
                    solo_time_ms=solo_time_ms,
                    slack_ms=slack_ms,
                )
            )
        return wrapped

    def _build_initial_swarm(
        self,
        requests: list[_SchedulableRequest],
        last_plan: Optional[Union[PrefillScheduleResult, dict[str, int]]],
        lower: float,
        upper: float,
        rng: random.Random,
    ) -> list[_Particle]:
        swarm: list[_Particle] = []
        history = self._history_map(last_plan)
        if history:
            warm_position = self._history_position(requests, history)
            swarm.append(_Particle.from_position(warm_position, rng))

        heuristic_specs = [
            lambda item: item.slack_ms,
            lambda item: item.prompt_tokens,
            lambda _item: 0,
        ]
        for sort_key in heuristic_specs:
            position = self._generate_heuristic_position(requests, sort_key)
            swarm.append(_Particle.from_position(position, rng))

        return swarm[: self.pso_particles]

    def _history_map(
        self,
        last_plan: Optional[PrefillScheduleResult | dict[str, int]],
    ) -> dict[str, int]:
        if last_plan is None:
            return {}
        if isinstance(last_plan, PrefillScheduleResult):
            return {req.request_id: req.batch_order for req in last_plan.requests}
        return dict(last_plan)

    def _history_position(
        self,
        requests: list[_SchedulableRequest],
        history: dict[str, int],
    ) -> list[float]:
        valid_batches = [batch for batch in history.values() if batch > 0]
        max_prev_batch = max(valid_batches) if valid_batches else 0
        next_offset = 0
        position = []
        for req in requests:
            if req.request_id in history and history[req.request_id] > 0:
                position.append(float(history[req.request_id]) + 0.5)
            else:
                position.append(float(max_prev_batch + 1) + next_offset * 0.01)
                next_offset += 1
        return position

    def _generate_heuristic_position(self, requests: list[_SchedulableRequest], sort_key) -> list[float]:
        sorted_indices = sorted(range(len(requests)), key=lambda index: sort_key(requests[index]))
        position = [0.0] * len(requests)
        batch_index = 0
        batch_tokens = 0
        batch_size = 0

        for index in sorted_indices:
            req = requests[index]
            if batch_size and (
                batch_tokens + req.prompt_tokens > self.heuristic_batch_tokens
                or batch_size >= self.max_batch_size
            ):
                batch_index += 1
                batch_tokens = 0
                batch_size = 0

            position[index] = float(batch_index) + 0.5
            batch_tokens += req.prompt_tokens
            batch_size += 1

        return position

    def _evaluate_particle(
        self,
        requests: list[_SchedulableRequest],
        position: list[float],
        start_time_ms: float,
    ) -> tuple[float, list[_FullChunk], list[tuple[float, float, int]]]:
        chunks, timing = self._build_ready_chunks(requests, position, start_time_ms)
        finish_times: dict[str, float] = {}
        total_goodput_tokens = 0
        lateness_tie_breaker = 0.0
        latency_tie_breaker = 0.0

        for chunk, (_chunk_start_ms, finish_ms, _goodput_tokens) in zip(chunks, timing):
            completed_requests = [
                piece.request for piece in chunk.pieces if piece.completed
            ]
            for req in completed_requests:
                finish_times[req.request_id] = finish_ms

            goodput_tokens = self._completed_goodput_tokens(completed_requests, finish_ms)
            total_goodput_tokens += goodput_tokens
            lateness_tie_breaker += self._completed_lateness_penalty(
                completed_requests,
                finish_ms,
            )
            latency_tie_breaker += len(completed_requests) * max(0.0, finish_ms - start_time_ms)

        score = (
            -float(total_goodput_tokens)
            + self.lateness_weight * lateness_tie_breaker
            + self.latency_weight * latency_tie_breaker
        )
        return score, chunks, timing

    def _build_ready_chunks(
        self,
        requests: list[_SchedulableRequest],
        position: list[float],
        start_time_ms: float,
    ) -> tuple[list[_FullChunk], list[tuple[float, float, int]]]:
        chunks = []
        timing = []
        priority = {
            req.request_id: value
            for value, req in zip(position, requests)
        }
        remaining_tokens = {
            req.request_id: req.prompt_tokens
            for req in requests
        }
        request_by_id = {req.request_id: req for req in requests}
        current_time_ms = start_time_ms

        while any(tokens > 0 for tokens in remaining_tokens.values()):
            ready_requests = [
                req
                for req in requests
                if remaining_tokens[req.request_id] > 0
                and req.source.arrival_ms <= current_time_ms
            ]
            if not ready_requests:
                current_time_ms = min(
                    req.source.arrival_ms
                    for req in requests
                    if remaining_tokens[req.request_id] > 0
                )
                continue

            chunk_start_ms = current_time_ms
            current_pieces: list[_ChunkPiece] = []
            current_tokens = 0
            for req in sorted(
                ready_requests,
                key=lambda item: (priority[item.request_id], item.source.arrival_ms, item.request_id),
            ):
                while remaining_tokens[req.request_id] > 0 and current_tokens < self.max_batch_tokens:
                    available = self.max_batch_tokens - current_tokens
                    tokens = min(remaining_tokens[req.request_id], available)
                    remaining_tokens[req.request_id] -= tokens
                    current_tokens += tokens
                    current_pieces.append(
                        _ChunkPiece(
                            request=req,
                            tokens=tokens,
                            completed=remaining_tokens[req.request_id] == 0,
                        )
                    )
                if current_tokens == self.max_batch_tokens:
                    break

            if not current_pieces:
                next_arrival = min(
                    request_by_id[request_id].source.arrival_ms
                    for request_id, tokens in remaining_tokens.items()
                    if tokens > 0
                )
                current_time_ms = max(current_time_ms, next_arrival)
                continue

            chunk = _FullChunk(pieces=current_pieces, total_tokens=current_tokens)
            duration_ms = self._predict_duration_ms(chunk.total_tokens)
            finish_ms = chunk_start_ms + duration_ms
            completed_requests = [
                piece.request for piece in chunk.pieces if piece.completed
            ]
            goodput_tokens = self._completed_goodput_tokens(completed_requests, finish_ms)
            chunks.append(chunk)
            timing.append((chunk_start_ms, finish_ms, goodput_tokens))
            current_time_ms = finish_ms

        return chunks, timing

    def _completed_goodput_tokens(
        self,
        completed_requests: list[_SchedulableRequest],
        finish_ms: float,
    ) -> int:
        return sum(
            req.prompt_tokens
            for req in completed_requests
            if finish_ms <= req.deadline_ms
        )

    def _completed_lateness_penalty(
        self,
        completed_requests: list[_SchedulableRequest],
        finish_ms: float,
    ) -> float:
        penalty = 0.0
        for req in completed_requests:
            lateness_ms = max(0.0, finish_ms - req.deadline_ms)
            if lateness_ms > 0:
                penalty += lateness_ms / max(1.0, req.source.slo_ttft_ms)
        return penalty

    def _build_result(
        self,
        batches: list[_FullChunk],
        timing: list[tuple[float, float, int]],
        score: float,
        instance_id: str,
    ) -> PrefillScheduleResult:
        scheduled_requests = []
        batch_plans = []
        for index, (batch, (start_ms, finish_ms, goodput_tokens)) in enumerate(
            zip(batches, timing),
            start=1,
        ):
            batch_plans.append(
                PrefillBatchPlan(
                    batch_order=index,
                    instance_id=instance_id,
                    request_ids=batch.request_ids,
                    total_tokens=batch.total_tokens,
                    start_ms=start_ms,
                    finish_ms=finish_ms,
                    duration_ms=finish_ms - start_ms,
                    goodput_tokens=goodput_tokens,
                )
            )
            for piece in batch.pieces:
                if not piece.completed:
                    continue
                req = piece.request
                scheduled_requests.append(
                    PrefillScheduledRequest(
                        request_id=req.request_id,
                        prefill_instance=instance_id,
                        batch_order=index,
                        arrival_ms=req.source.arrival_ms,
                        prompt_tokens=req.prompt_tokens,
                        finish_ms=finish_ms,
                        ttft_ms=finish_ms - req.source.arrival_ms,
                    )
                )

        return PrefillScheduleResult(
            requests=scheduled_requests,
            batches=batch_plans,
            score=score,
            target_instance=instance_id,
        )


def load_requests_json(path: Path) -> list[PrefillSimRequest]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict):
        data = data["requests"]
    return [
        PrefillSimRequest(
            request_id=str(item["request_id"]),
            arrival_ms=float(item["arrival_ms"]),
            prompt_tokens=int(item["prompt_tokens"]),
            slo_ttft_ms=float(item["slo_ttft_ms"]),
        )
        for item in data
    ]


def _demo_requests() -> list[PrefillSimRequest]:
    return [
        PrefillSimRequest("id1", 0.0, 4096, 180.0),
        PrefillSimRequest("id2", 4.0, 8192, 220.0),
        PrefillSimRequest("id3", 8.0, 2048, 140.0),
        PrefillSimRequest("id4", 12.0, 6144, 260.0),
    ]


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Simulate Prefill-side synchronous PSO scheduling.")
    parser.add_argument("--input", type=Path, help="JSON request file.")
    parser.add_argument("--instance-id", default="prefill-0")
    parser.add_argument("--instance-available-ms", type=float, default=0.0)
    parser.add_argument("--max-batch-tokens", type=int, default=10240)
    parser.add_argument("--token-time-ms", type=float, default=0.01)
    parser.add_argument("--batch-overhead-ms", type=float, default=0.0)
    parser.add_argument("--pso-particles", type=int, default=30)
    parser.add_argument("--pso-iterations", type=int, default=20)
    parser.add_argument("--random-seed", type=int, default=0)
    parser.add_argument("--indent", type=int, default=2)
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    requests = load_requests_json(args.input) if args.input else _demo_requests()
    scheduler = PrefillPSOScheduler(
        max_batch_tokens=args.max_batch_tokens,
        token_time_ms=args.token_time_ms,
        batch_overhead_ms=args.batch_overhead_ms,
        pso_particles=args.pso_particles,
        pso_iterations=args.pso_iterations,
        random_seed=args.random_seed,
    )
    result = scheduler.schedule(
        requests,
        PrefillSimInstance(
            instance_id=args.instance_id,
            available_time_ms=args.instance_available_ms,
        ),
    )
    print(json.dumps(result.to_dict(), ensure_ascii=False, indent=args.indent))


if __name__ == "__main__":
    main()
