"""Offline gateway-level SLO-aware instance schedulers.

The schedulers here mirror the target behavior of the real gateway schedulers:

- Prefill follows the objective used by ``slo_aware_prefill``: batch-level lateness
  penalty with predicted availability and inflight prefill taken into account.
- Decode follows ``decode/async_base.py::slo_tpot_admission``: prefer safe
  candidate batches, otherwise minimize violations and maximize worst margin.
"""

from __future__ import annotations

import pickle
import warnings
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Deque, Dict, List, Optional, Tuple

from .vllm_continuous_batching import (
    DecodeRuntimeRequest,
    DecodeStep,
    PrefillBatch,
    PrefillRuntimeRequest,
)


@dataclass(frozen=True)
class TTFTProfile:
    seconds_per_token: float
    intercept_seconds: float

    def predict_ms(self, tokens: int) -> float:
        return max(0.0, (max(0, int(tokens)) * self.seconds_per_token + self.intercept_seconds) * 1000.0)


@dataclass(frozen=True)
class TPOTProfile:
    d1: float
    d2: float
    d3: float
    d4: float

    def predict_step_ms(self, batch_size: int, context_len: int) -> float:
        pred = (
            self.d1 * max(0, int(batch_size)) * max(0, int(context_len))
            + self.d2 * max(0, int(batch_size))
            + self.d3 * max(0, int(context_len))
            + self.d4
        )
        return max(0.0001, float(pred)) * 1000.0


@dataclass
class PrefillCompletion:
    request_id: str
    instance_id: str
    arrival_ms: float
    complete_ms: float
    ttft_ms: float
    prompt_tokens: int


@dataclass
class DecodeCompletion:
    request_id: str
    instance_id: str
    start_ms: float
    complete_ms: float
    decode_ms: float
    tpot_ms: float


@dataclass
class StartedPrefillBatch:
    instance_id: str
    start_ms: float
    end_ms: float
    request_ids: List[str]
    completions: List[PrefillCompletion]


@dataclass
class StartedDecodeStep:
    instance_id: str
    start_ms: float
    end_ms: float


@dataclass
class PrefillInstanceState:
    instance_id: str
    profile: TTFTProfile
    max_num_batched_tokens: int = 4096
    queue: Deque[PrefillRuntimeRequest] = field(default_factory=deque)
    available_time_ms: float = 0.0
    busy_ms: float = 0.0
    prompt_throughput: float = 0.0
    batches: List[PrefillBatch] = field(default_factory=list)
    completed: Dict[str, PrefillCompletion] = field(default_factory=dict)
    running_request_ids: set[str] = field(default_factory=set)

    def enqueue(self, request_id: str, arrival_ms: float, prompt_tokens: int) -> None:
        self.queue.append(
            PrefillRuntimeRequest(
                request_id=request_id,
                arrival_ms=arrival_ms,
                prompt_tokens=prompt_tokens,
                remaining_tokens=prompt_tokens,
            )
        )

    def has_work(self) -> bool:
        return bool(self.queue)

    def effective_request_load(self) -> int:
        """Count unique queued and currently executing requests."""
        return len({req.request_id for req in self.queue} | self.running_request_ids)

    def complete_running_batch(self, request_ids: List[str]) -> None:
        self.running_request_ids.difference_update(request_ids)

    def can_start_batch(self, now_ms: float) -> bool:
        return bool(self.queue) and self.available_time_ms <= now_ms and self.queue[0].arrival_ms <= now_ms

    def estimate_next_batch(self, now_ms: float) -> Tuple[float, float, List[str]]:
        if not self.queue:
            return 0.0, self.available_time_ms, []
        start_ms = max(now_ms, self.available_time_ms, self.queue[0].arrival_ms)
        budget = self.max_num_batched_tokens
        scheduled_tokens = 0
        completed_ids: List[str] = []
        request_ids: List[str] = []
        for req in self.queue:
            if req.arrival_ms > start_ms or budget <= 0:
                break
            tokens = min(req.remaining_tokens, budget)
            if tokens <= 0:
                continue
            budget -= tokens
            scheduled_tokens += tokens
            request_ids.append(req.request_id)
            if req.remaining_tokens <= tokens:
                completed_ids.append(req.request_id)
            if budget <= 0:
                break
        end_ms = start_ms + self.profile.predict_ms(scheduled_tokens)
        return scheduled_tokens, end_ms, completed_ids

    def next_batch_penalty(self, now_ms: float, slo_ttft_ms: float) -> Tuple[float, float]:
        _tokens, finish_ms, completed_ids = self.estimate_next_batch(now_ms)
        if not completed_ids:
            return 0.0, finish_ms
        by_id = {req.request_id: req for req in self.queue}
        penalty = 0.0
        for rid in completed_ids:
            req = by_id[rid]
            deadline = req.arrival_ms + slo_ttft_ms
            penalty += max(0.0, finish_ms - deadline) / max(1.0, slo_ttft_ms)
        return penalty, finish_ms

    def run_next_batch(self, now_ms: float, slo_ttft_ms: float) -> List[PrefillCompletion]:
        if not self.queue:
            return []
        start_ms = max(now_ms, self.available_time_ms, self.queue[0].arrival_ms)
        budget = self.max_num_batched_tokens
        scheduled_tokens = 0
        request_ids: List[str] = []
        completed_runtime: List[PrefillRuntimeRequest] = []

        while self.queue and budget > 0 and self.queue[0].arrival_ms <= start_ms:
            req = self.queue[0]
            tokens = min(req.remaining_tokens, budget)
            if tokens <= 0:
                break
            req.remaining_tokens -= tokens
            req.processed_tokens += tokens
            budget -= tokens
            scheduled_tokens += tokens
            request_ids.append(req.request_id)
            if req.remaining_tokens == 0:
                completed_runtime.append(self.queue.popleft())
            else:
                break

        duration_ms = self.profile.predict_ms(scheduled_tokens)
        end_ms = start_ms + duration_ms
        self.available_time_ms = end_ms
        self.busy_ms += duration_ms
        self.prompt_throughput = scheduled_tokens / max(duration_ms / 1000.0, 1e-9)

        completions = [
            PrefillCompletion(
                request_id=req.request_id,
                instance_id=self.instance_id,
                arrival_ms=req.arrival_ms,
                complete_ms=end_ms,
                ttft_ms=end_ms - req.arrival_ms,
                prompt_tokens=req.prompt_tokens,
            )
            for req in completed_runtime
        ]
        for item in completions:
            self.completed[item.request_id] = item

        self.batches.append(
            PrefillBatch(
                instance_id=self.instance_id,
                start_ms=start_ms,
                end_ms=end_ms,
                scheduled_tokens=scheduled_tokens,
                request_ids=request_ids,
                completed_request_ids=[item.request_id for item in completions],
            )
        )
        self.running_request_ids = set(request_ids)
        return completions

    def start_next_batch(self, now_ms: float, slo_ttft_ms: float) -> Optional[StartedPrefillBatch]:
        if not self.can_start_batch(now_ms):
            return None
        completions = self.run_next_batch(now_ms, slo_ttft_ms)
        if not self.batches:
            return None
        batch = self.batches[-1]
        return StartedPrefillBatch(
            instance_id=self.instance_id,
            start_ms=batch.start_ms,
            end_ms=batch.end_ms,
            request_ids=list(batch.request_ids),
            completions=completions,
        )


class SLOAwarePrefillPoolSimulator:
    def __init__(
        self,
        pool_name: str,
        profile: TTFTProfile,
        instance_count: int = 2,
        max_num_batched_tokens: int = 4096,
        policy: str = "slo_aware",
    ):
        self.pool_name = pool_name
        self.policy = policy
        self._rr_index = 0
        self.instances = [
            PrefillInstanceState(f"{pool_name}-P{i + 1}", profile, max_num_batched_tokens=max_num_batched_tokens)
            for i in range(instance_count)
        ]

    def enqueue(self, request_id: str, arrival_ms: float, prompt_tokens: int, slo_ttft_ms: float) -> str:
        best = self._select_instance(arrival_ms, slo_ttft_ms)
        best.enqueue(request_id, arrival_ms, prompt_tokens)
        return best.instance_id

    def enqueue_to_instance(
        self,
        instance_id: str,
        request_id: str,
        arrival_ms: float,
        prompt_tokens: int,
    ) -> None:
        self.get_instance(instance_id).enqueue(request_id, arrival_ms, prompt_tokens)

    def get_instance(self, instance_id: str) -> PrefillInstanceState:
        for instance in self.instances:
            if instance.instance_id == instance_id:
                return instance
        raise ValueError(f"unknown prefill instance: {instance_id}")

    def complete_batch(self, instance_id: str, request_ids: List[str]) -> None:
        self.get_instance(instance_id).complete_running_batch(request_ids)

    def _select_instance(self, arrival_ms: float, slo_ttft_ms: float) -> PrefillInstanceState:
        if self.policy == "round_robin":
            target = self.instances[self._rr_index % len(self.instances)]
            self._rr_index += 1
            return target
        if self.policy == "least_load":
            return min(self.instances, key=lambda inst: (inst.prompt_throughput, inst.instance_id))
        if self.policy != "slo_aware":
            raise ValueError(f"unknown prefill instance policy: {self.policy}")
        best = min(
            self.instances,
            key=lambda inst: (
                inst.next_batch_penalty(arrival_ms, slo_ttft_ms)[0],
                inst.next_batch_penalty(arrival_ms, slo_ttft_ms)[1],
                len(inst.queue),
            ),
        )
        return best

    def drain_until_idle(self, slo_ttft_ms: float) -> List[PrefillCompletion]:
        completions: List[PrefillCompletion] = []
        while any(inst.has_work() for inst in self.instances):
            candidates = [inst for inst in self.instances if inst.has_work()]
            inst = min(candidates, key=lambda item: (item.available_time_ms, item.queue[0].arrival_ms))
            now_ms = max(inst.available_time_ms, inst.queue[0].arrival_ms)
            completions.extend(inst.run_next_batch(now_ms, slo_ttft_ms))
            inst.complete_running_batch(inst.batches[-1].request_ids)
        return completions

    def start_ready_batches(self, now_ms: float, slo_ttft_ms: float) -> List[StartedPrefillBatch]:
        started: List[StartedPrefillBatch] = []
        for inst in self.instances:
            batch = inst.start_next_batch(now_ms, slo_ttft_ms)
            if batch is not None:
                started.append(batch)
        return started


@dataclass
class DecodeInstanceState:
    instance_id: str
    profile: TPOTProfile
    max_active: int
    waiting: Deque[DecodeRuntimeRequest] = field(default_factory=deque)
    active: List[DecodeRuntimeRequest] = field(default_factory=list)
    next_time_ms: float = 0.0
    busy_ms: float = 0.0
    generate_throughput: float = 0.0
    steps: List[DecodeStep] = field(default_factory=list)
    completed: Dict[str, DecodeCompletion] = field(default_factory=dict)
    running_step: Optional[DecodeStep] = None

    def candidate_evaluation(self, request: DecodeRuntimeRequest, slo_tpot_ms: float) -> Dict[str, float]:
        candidate = [*self.active, request]
        batch_size = max(1, len(candidate))
        violation_count = 0
        min_margin = float("inf")
        for item in candidate:
            context_len = item.prompt_tokens + item.generated_tokens
            pred_tpot_ms = self.profile.predict_step_ms(batch_size, context_len)
            margin = slo_tpot_ms - pred_tpot_ms
            if margin < 0:
                violation_count += 1
            min_margin = min(min_margin, margin)
        return {"violation_count": violation_count, "min_margin": min_margin}

    def load_score(self) -> int:
        running_batch_size = len(self.running_step.request_ids) if self.running_step is not None else 0
        return len(self.active) + len(self.waiting) + running_batch_size

    def effective_request_load(self) -> int:
        """Count each active/waiting/running request exactly once."""
        request_ids = {req.request_id for req in self.active}
        request_ids.update(req.request_id for req in self.waiting)
        if self.running_step is not None:
            request_ids.update(self.running_step.request_ids)
        return len(request_ids)

    def enqueue(self, req: DecodeRuntimeRequest) -> None:
        self.waiting.append(req)

    def has_work(self) -> bool:
        return bool(self.running_step or self.waiting or self.active)

    def can_start_step(self, now_ms: float) -> bool:
        if self.running_step is not None:
            return False
        if not self.active and self.waiting and self.waiting[0].kv_ready_ms > now_ms:
            return False
        return bool(self.active or self.waiting)

    def start_next_step(self, now_ms: float) -> Optional[StartedDecodeStep]:
        if not self.can_start_step(now_ms):
            return None
        if not self.active and self.waiting:
            self.next_time_ms = max(self.next_time_ms, self.waiting[0].kv_ready_ms, now_ms)
        else:
            self.next_time_ms = max(self.next_time_ms, now_ms)

        while (
            self.waiting
            and self.waiting[0].kv_ready_ms <= self.next_time_ms
        ):
            req = self.waiting.popleft()
            req.start_ms = self.next_time_ms
            self.active.append(req)

        if not self.active:
            return None

        batch_size = len(self.active)
        step_ms = max(
            self.profile.predict_step_ms(batch_size, req.prompt_tokens + req.generated_tokens)
            for req in self.active
        )
        start_ms = self.next_time_ms
        end_ms = start_ms + step_ms
        self.busy_ms += step_ms
        self.generate_throughput = batch_size / max(step_ms / 1000.0, 1e-9)
        self.running_step = DecodeStep(
            instance_id=self.instance_id,
            start_ms=start_ms,
            end_ms=end_ms,
            batch_size=batch_size,
            request_ids=[req.request_id for req in self.active],
        )
        self.steps.append(self.running_step)
        self.next_time_ms = end_ms
        return StartedDecodeStep(self.instance_id, start_ms, end_ms)

    def complete_running_step(self) -> List[DecodeCompletion]:
        if self.running_step is None:
            return []
        step = self.running_step
        self.running_step = None
        completions: List[DecodeCompletion] = []
        still_active: List[DecodeRuntimeRequest] = []
        step_ms = step.end_ms - step.start_ms
        step_ids = set(step.request_ids)
        for req in self.active:
            if req.request_id not in step_ids:
                still_active.append(req)
                continue
            req.generated_tokens += 1
            req.tpot_sum_ms += step_ms
            req.tpot_steps += 1
            if req.generated_tokens >= req.response_tokens:
                complete = DecodeCompletion(
                    request_id=req.request_id,
                    instance_id=self.instance_id,
                    start_ms=req.start_ms if req.start_ms is not None else step.start_ms,
                    complete_ms=step.end_ms,
                    decode_ms=step.end_ms - (req.start_ms if req.start_ms is not None else step.start_ms),
                    tpot_ms=req.tpot_sum_ms / max(1, req.tpot_steps),
                )
                self.completed[req.request_id] = complete
                completions.append(complete)
            else:
                still_active.append(req)
        self.active = still_active
        return completions

    def run_until_idle(self, slo_tpot_ms: float) -> List[DecodeCompletion]:
        completions: List[DecodeCompletion] = []
        while self.waiting or self.active:
            if not self.active and self.waiting:
                self.next_time_ms = max(self.next_time_ms, self.waiting[0].kv_ready_ms)
            while (
                self.waiting
                and self.waiting[0].kv_ready_ms <= self.next_time_ms
            ):
                req = self.waiting.popleft()
                req.start_ms = self.next_time_ms
                self.active.append(req)

            if not self.active:
                continue

            batch_size = len(self.active)
            step_ms = max(
                self.profile.predict_step_ms(batch_size, req.prompt_tokens + req.generated_tokens)
                for req in self.active
            )
            start_ms = self.next_time_ms
            end_ms = start_ms + step_ms
            self.busy_ms += step_ms
            self.generate_throughput = batch_size / max(step_ms / 1000.0, 1e-9)
            self.steps.append(
                DecodeStep(
                    instance_id=self.instance_id,
                    start_ms=start_ms,
                    end_ms=end_ms,
                    batch_size=batch_size,
                    request_ids=[req.request_id for req in self.active],
                )
            )

            still_active: List[DecodeRuntimeRequest] = []
            for req in self.active:
                req.generated_tokens += 1
                req.tpot_sum_ms += step_ms
                req.tpot_steps += 1
                if req.generated_tokens >= req.response_tokens:
                    complete = DecodeCompletion(
                        request_id=req.request_id,
                        instance_id=self.instance_id,
                        start_ms=req.start_ms if req.start_ms is not None else start_ms,
                        complete_ms=end_ms,
                        decode_ms=end_ms - (req.start_ms if req.start_ms is not None else start_ms),
                        tpot_ms=req.tpot_sum_ms / max(1, req.tpot_steps),
                    )
                    self.completed[req.request_id] = complete
                    completions.append(complete)
                else:
                    still_active.append(req)
            self.active = still_active
            self.next_time_ms = end_ms
        return completions


class SLOAwareDecodePoolSimulator:
    def __init__(
        self,
        pool_name: str,
        profile: TPOTProfile,
        max_decode_req_num: int,
        instance_count: int = 2,
        policy: str = "slo_aware",
    ):
        per_instance_limit = max(1, int(max_decode_req_num / max(1, instance_count)))
        self.pool_name = pool_name
        self.policy = policy
        self._rr_index = 0
        self.instances = [
            DecodeInstanceState(f"{pool_name}-D{i + 1}", profile, max_active=per_instance_limit)
            for i in range(instance_count)
        ]

    def enqueue(self, request_id: str, kv_ready_ms: float, prompt_tokens: int, response_tokens: int, slo_tpot_ms: float) -> str:
        req = DecodeRuntimeRequest(request_id, kv_ready_ms, prompt_tokens, response_tokens)
        target = self._select_instance(req, slo_tpot_ms)
        target.enqueue(req)
        return target.instance_id

    def enqueue_to_instance(
        self,
        instance_id: str,
        request_id: str,
        kv_ready_ms: float,
        prompt_tokens: int,
        response_tokens: int,
    ) -> None:
        req = DecodeRuntimeRequest(request_id, kv_ready_ms, prompt_tokens, response_tokens)
        self.get_instance(instance_id).enqueue(req)

    def get_instance(self, instance_id: str) -> DecodeInstanceState:
        for instance in self.instances:
            if instance.instance_id == instance_id:
                return instance
        raise ValueError(f"unknown decode instance: {instance_id}")

    def _select_instance(self, req: DecodeRuntimeRequest, slo_tpot_ms: float) -> DecodeInstanceState:
        if self.policy == "round_robin":
            target = self.instances[self._rr_index % len(self.instances)]
            self._rr_index += 1
            return target
        if self.policy == "least_load":
            return min(self.instances, key=lambda inst: (inst.generate_throughput, inst.instance_id))
        if self.policy not in ("slo_aware", "slo_aware_least_load_safe"):
            raise ValueError(f"unknown decode instance policy: {self.policy}")
        evaluations = [(inst, inst.candidate_evaluation(req, slo_tpot_ms)) for inst in self.instances]
        safe = [(inst, ev) for inst, ev in evaluations if ev["violation_count"] == 0]
        if safe:
            if self.policy == "slo_aware_least_load_safe":
                target = min(safe, key=lambda pair: (pair[0].load_score(), -pair[1]["min_margin"], pair[0].instance_id))[0]
            else:
                target = max(safe, key=lambda pair: (pair[1]["min_margin"], -len(pair[0].active), -len(pair[0].waiting)))[0]
        else:
            target = max(evaluations, key=lambda pair: (-pair[1]["violation_count"], pair[1]["min_margin"]))[0]
        return target

    def drain_until_idle(self, slo_tpot_ms: float) -> List[DecodeCompletion]:
        completions: List[DecodeCompletion] = []
        for inst in self.instances:
            completions.extend(inst.run_until_idle(slo_tpot_ms))
        return completions

    def start_ready_steps(self, now_ms: float) -> List[StartedDecodeStep]:
        started: List[StartedDecodeStep] = []
        for inst in self.instances:
            step = inst.start_next_step(now_ms)
            if step is not None:
                started.append(step)
        return started

    def complete_step(self, instance_id: str) -> List[DecodeCompletion]:
        for inst in self.instances:
            if inst.instance_id == instance_id:
                return inst.complete_running_step()
        return []


def load_ttft_profile(path: Path) -> TTFTProfile:
    with path.open("rb") as handle:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            state = pickle.load(handle)
    coeffs = state.get("coeffs", {}) if isinstance(state, dict) else {}
    return TTFTProfile(float(coeffs["tokens"]), float(coeffs["d"]))


def load_tpot_profile(path: Path) -> TPOTProfile:
    with path.open("rb") as handle:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            state = pickle.load(handle)
    coeffs = state.get("coeffs", {}) if isinstance(state, dict) else {}
    return TPOTProfile(float(coeffs["d1"]), float(coeffs["d2"]), float(coeffs["d3"]), float(coeffs["d4"]))
