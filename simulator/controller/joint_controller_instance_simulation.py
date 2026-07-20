"""Joint controller, gateway, network, and vLLM batching simulation."""

from __future__ import annotations

import argparse
import bisect
import copy
import csv
import heapq
import json
import os
from math import ceil
from collections import Counter, defaultdict, deque
from dataclasses import asdict, dataclass
from pathlib import Path
from statistics import mean
from typing import DefaultDict, Dict, List, Optional, Tuple

try:
    from .controller_bandwidth_simulation import (
        DECODE_POOLS,
        DECODE_RANKS,
        DEFAULT_DATASET,
        PREFILL_POOLS,
        PREFILL_RANKS,
        QWEN25_7B_KV_BYTES_PER_TOKEN,
        adaptive_variance_fitness,
        clamp,
        is_local_pair,
        link_bandwidth_mbps,
        scheduler_fitness,
    )
    from ..gateway.slo_instance_scheduler import (
        SLOAwareDecodePoolSimulator,
        SLOAwarePrefillPoolSimulator,
        load_tpot_profile,
        load_ttft_profile,
    )
except ImportError:
    from controller_bandwidth_simulation import (
        DECODE_POOLS,
        DECODE_RANKS,
        DEFAULT_DATASET,
        PREFILL_POOLS,
        PREFILL_RANKS,
        QWEN25_7B_KV_BYTES_PER_TOKEN,
        adaptive_variance_fitness,
        clamp,
        is_local_pair,
        link_bandwidth_mbps,
        scheduler_fitness,
    )
    import sys

    sys.path.append(str(Path(__file__).resolve().parents[1]))
    from gateway.slo_instance_scheduler import (  # type: ignore
        SLOAwareDecodePoolSimulator,
        SLOAwarePrefillPoolSimulator,
        load_tpot_profile,
        load_ttft_profile,
    )


PROFILE_ROOT = Path(__file__).resolve().parents[1] / "profile"
DEFAULT_RESULT = Path(__file__).resolve().parents[1] / "result" / "latest_joint_result.json"
DEFAULT_COMPARISON_RESULT = Path(__file__).resolve().parents[1] / "result" / "latest_joint_comparison.json"
DEFAULT_COMPARISON_FIGURE = Path(__file__).resolve().parents[1] / "result" / "latest_joint_comparison.png"
POLICIES = (
    "pso_slo",
    "round_robin",
    "least_load",
    "adaptive_variance",
    "distserve_batched",
    "distserve",
)
DIST_SERVE_DIRECT_PREFILL_BATCH_TOKENS = 4096


@dataclass
class RequestRecord:
    request_id: str
    arrival_ms: float
    prompt_tokens: int
    response_tokens: int
    prefill_pool: str
    decode_pool: str
    prefill_instance: str
    decode_instance: str
    prefill_done_ms: float
    kv_done_ms: float
    decode_done_ms: float
    ttft_ms: float
    tpot_ms: float
    e2e_ms: float
    ttft_met: bool
    tpot_met: bool
    e2e_met: bool
    all_slo_met: bool


class RequestRanker:
    def __init__(self, window_size: int = 2000):
        self.window_size = window_size
        self.queue: deque[int] = deque()
        self.sorted_values: List[int] = []

    def rank_after_update(self, value: int) -> float:
        if len(self.queue) >= self.window_size:
            old = self.queue.popleft()
            idx = bisect.bisect_left(self.sorted_values, old)
            if idx < len(self.sorted_values) and self.sorted_values[idx] == old:
                self.sorted_values.pop(idx)
        self.queue.append(value)
        bisect.insort(self.sorted_values, value)
        idx = bisect.bisect_left(self.sorted_values, value)
        return clamp(idx / max(1, len(self.sorted_values)), 0.01, 0.99)


def percentile(values: List[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, int(round((len(ordered) - 1) * q))))
    return ordered[idx]


def stats(values: List[float]) -> Dict[str, float]:
    return {
        "avg": mean(values) if values else 0.0,
        "p50": percentile(values, 0.50),
        "p90": percentile(values, 0.90),
        "p95": percentile(values, 0.95),
        "p99": percentile(values, 0.99),
    }


def profile_path(machine: str, kind: str) -> Path:
    if kind == "ttft":
        return PROFILE_ROOT / f"profile_{machine}" / "TTFT_predictor" / "artifacts" / "prefill_ttft_regressor.pkl"
    return PROFILE_ROOT / f"profile_{machine}" / "TPOT_predictor" / "artifacts" / "decode_tpot_regressor.pkl"


def iter_dataset(dataset: Path, limit: Optional[int], qps: Optional[float], time_scale: float):
    with dataset.open(newline="", encoding="utf-8") as handle:
        for index, row in enumerate(csv.DictReader(handle), start=1):
            if limit is not None and index > limit:
                break
            raw_ts = float(row["Timestamp"])
            arrival_s = (index - 1) / qps if qps is not None else raw_ts / time_scale
            yield (
                f"req-{index}",
                arrival_s * 1000.0,
                max(1, int(float(row["Request tokens"]))),
                max(1, int(float(row["Response tokens"]))),
            )


def kv_transfer_ms(tokens: int, bandwidth_mbps: float, compress_factor: float) -> float:
    if bandwidth_mbps <= 0:
        return float("inf")
    bytes_ = max(1, tokens) * QWEN25_7B_KV_BYTES_PER_TOKEN * compress_factor
    return (bytes_ * 8.0) / (bandwidth_mbps * 1000.0)


def instance_policies_for_controller_policy(policy: str) -> Tuple[str, str]:
    if policy == "pso_slo":
        return "slo_aware", "slo_aware"
    if policy == "adaptive_variance":
        return "slo_aware", "slo_aware_least_load_safe"
    if policy in ("round_robin", "least_load"):
        return policy, policy
    if policy in ("distserve", "distserve_batched"):
        # DistServe performs global instance selection in the gateway simulation.
        return "slo_aware", "slo_aware"
    raise ValueError(f"unknown policy: {policy}")


def take_distserve_prefill_batch(pending: deque, max_batch_tokens: int) -> List[Dict]:
    """Pop one FCFS DistServe logical batch from the gateway queue.

    A head request whose prompt is at least L_m runs alone. Otherwise requests
    are admitted in arrival order until the next one would exceed L_m.
    """
    if not pending:
        return []
    limit = max(1, int(max_batch_tokens))
    first_tokens = int(pending[0]["prompt_tokens"])
    if first_tokens >= limit:
        return [pending.popleft()]
    batch: List[Dict] = []
    total_tokens = 0
    while pending:
        next_tokens = int(pending[0]["prompt_tokens"])
        if batch and total_tokens + next_tokens > limit:
            break
        batch.append(pending.popleft())
        total_tokens += next_tokens
        if total_tokens >= limit:
            break
    return batch


def select_distserve_prefill_instance(prefill_pools) -> Tuple[str, str]:
    candidates = (
        (instance.effective_request_load(), instance.instance_id, pool_name)
        for pool_name, pool in prefill_pools.items()
        for instance in pool.instances
    )
    _load, instance_id, pool_name = min(candidates)
    return pool_name, instance_id


def select_distserve_decode_instance(decode_pools) -> Tuple[str, str]:
    candidates = (
        (instance.effective_request_load(), instance.instance_id, pool_name)
        for pool_name, pool in decode_pools.items()
        for instance in pool.instances
    )
    _load, instance_id, pool_name = min(candidates)
    return pool_name, instance_id


def build_prefill_pools(max_num_batched_tokens: int, instance_policy: str):
    return {
        name: SLOAwarePrefillPoolSimulator(
            name,
            load_ttft_profile(profile_path(spec.machine, "ttft")),
            instance_count=2,
            max_num_batched_tokens=max_num_batched_tokens,
            policy=instance_policy,
        )
        for name, spec in PREFILL_POOLS.items()
    }


def build_decode_pools(instance_policy: str):
    return {
        name: SLOAwareDecodePoolSimulator(
            name,
            load_tpot_profile(profile_path(spec.machine, "tpot")),
            max_decode_req_num=int(spec.max_decode_req_num),
            instance_count=2,
            policy=instance_policy,
        )
        for name, spec in DECODE_POOLS.items()
    }


def load_prefill_profiles():
    return {
        name: load_ttft_profile(profile_path(spec.machine, "ttft"))
        for name, spec in PREFILL_POOLS.items()
    }


def load_decode_profiles():
    return {
        name: load_tpot_profile(profile_path(spec.machine, "tpot"))
        for name, spec in DECODE_POOLS.items()
    }


def normalized_prefill_loads(prefill_pools, now_ms: float) -> Dict[str, float]:
    return {
        name: sum(
            req.remaining_tokens
            for inst in pool.instances
            for req in inst.queue
        ) / max(1.0, PREFILL_POOLS[name].max_prompt_throughput)
        for name, pool in prefill_pools.items()
    }


def normalized_decode_loads(decode_pools) -> Dict[str, float]:
    return {
        name: sum(
            len(inst.active) + len(inst.waiting) + (1 if inst.running_step is not None else 0)
            for inst in pool.instances
        ) / max(1.0, DECODE_POOLS[name].max_decode_req_num)
        for name, pool in decode_pools.items()
    }


def select_pair_pso(
    req_rank: float,
    prompt_tokens: int,
    prefill_loads: Dict[str, float],
    decode_loads: Dict[str, float],
    network_loads: Dict[Tuple[str, str], float],
    args: argparse.Namespace,
) -> Tuple[str, str]:
    best = None
    for p_name in PREFILL_POOLS:
        for d_name in DECODE_POOLS:
            score = scheduler_fitness(
                req_rank,
                prompt_tokens,
                p_name,
                d_name,
                prefill_loads,
                decode_loads,
                network_loads,
                args.local_bandwidth_mbps,
                args.cross_bandwidth_mbps,
                args.compress_factor,
            )
            if best is None or score < best[0]:
                best = (score, p_name, d_name)
    assert best is not None
    return best[1], best[2]


def select_pair_round_robin(rr_index: int) -> Tuple[str, str, int]:
    pairs = [(p_name, d_name) for p_name in PREFILL_POOLS for d_name in DECODE_POOLS]
    p_name, d_name = pairs[rr_index % len(pairs)]
    return p_name, d_name, rr_index + 1


def select_pair_least_load(prefill_loads: Dict[str, float], decode_loads: Dict[str, float]) -> Tuple[str, str]:
    p_name = min(PREFILL_POOLS, key=lambda name: (prefill_loads.get(name, 0.0), name))
    d_name = min(DECODE_POOLS, key=lambda name: (decode_loads.get(name, 0.0), name))
    return p_name, d_name


def estimate_prefill_time_ms(prefill_profiles, p_name: str, prompt_tokens: int) -> float:
    return prefill_profiles[p_name].predict_ms(prompt_tokens)


def estimate_decode_batch_size(decode_pools, d_name: str) -> int:
    pool = decode_pools[d_name]
    running_reqs = sum(
        len(inst.active) + len(inst.waiting) + (1 if inst.running_step is not None else 0)
        for inst in pool.instances
    )
    instance_count = max(1, len(pool.instances))
    return max(1, ceil((running_reqs + 1) / instance_count))


def estimate_decode_time_ms(
    decode_profiles,
    decode_pools,
    d_name: str,
    prompt_tokens: int,
    response_tokens: int,
) -> float:
    batch_size = estimate_decode_batch_size(decode_pools, d_name)
    step_ms = decode_profiles[d_name].predict_step_ms(batch_size=batch_size, context_len=prompt_tokens)
    return max(1, int(response_tokens)) * step_ms


def select_pair_adaptive_variance(
    req_rank: float,
    decode_req_rank: float,
    prompt_tokens: int,
    response_tokens: int,
    prefill_loads: Dict[str, float],
    decode_loads: Dict[str, float],
    network_loads: Dict[Tuple[str, str], float],
    prefill_profiles,
    decode_profiles,
    decode_pools,
    args: argparse.Namespace,
) -> Tuple[str, str]:
    best = None
    for p_name in PREFILL_POOLS:
        prefill_time_ms = estimate_prefill_time_ms(prefill_profiles, p_name, prompt_tokens)
        for d_name in DECODE_POOLS:
            decode_time_ms = estimate_decode_time_ms(
                decode_profiles,
                decode_pools,
                d_name,
                prompt_tokens,
                response_tokens,
            )
            score = adaptive_variance_fitness(
                req_rank,
                prompt_tokens,
                p_name,
                d_name,
                prefill_loads,
                decode_loads,
                network_loads,
                args.local_bandwidth_mbps,
                args.cross_bandwidth_mbps,
                args.compress_factor,
                prefill_time_ms,
                decode_time_ms,
                decode_request_rank=decode_req_rank,
            )
            if best is None or score < best[0]:
                best = (score, p_name, d_name)
    assert best is not None
    return best[1], best[2]


def simulate(args: argparse.Namespace) -> Dict:
    time_scale = args.time_scale if args.time_scale else (100.0 if args.scenario == "burst" else 1.0)
    qps = args.qps if args.qps and args.qps > 0 else None
    prefill_instance_policy, decode_instance_policy = instance_policies_for_controller_policy(args.policy)
    prefill_max_num_batched_tokens = (
        DIST_SERVE_DIRECT_PREFILL_BATCH_TOKENS
        if args.policy == "distserve"
        else args.max_num_batched_tokens
    )
    prefill_pools = build_prefill_pools(prefill_max_num_batched_tokens, prefill_instance_policy)
    decode_pools = build_decode_pools(decode_instance_policy)
    prefill_profiles = load_prefill_profiles()
    decode_profiles = load_decode_profiles()
    prompt_ranker = RequestRanker()
    decode_ranker = RequestRanker()
    rr_index = 0
    event_seq = 0

    pair_counts: Counter[str] = Counter()
    prefill_instance_counts: Counter[str] = Counter()
    decode_instance_counts: Counter[str] = Counter()
    network_bytes: DefaultDict[str, float] = defaultdict(float)
    network_busy_ms: DefaultDict[str, float] = defaultdict(float)
    network_available_ms: DefaultDict[str, float] = defaultdict(float)
    network_loads: Dict[Tuple[str, str], float] = {(p, d): 0.0 for p in PREFILL_POOLS for d in DECODE_POOLS}
    network_active_mbps: DefaultDict[Tuple[str, str], float] = defaultdict(float)
    selected_pairs: Dict[str, Tuple[str, str]] = {}
    event_counts: Counter[str] = Counter()
    prefill_completions = {}
    decode_completions = {}
    distserve_pending: deque = deque()
    distserve_dispatch_scheduled = False
    events = []

    def push_event(time_ms: float, event_type: str, payload: Dict) -> None:
        nonlocal event_seq
        event_seq += 1
        heapq.heappush(events, (time_ms, event_seq, event_type, payload))

    def start_prefill_ready(pool_name: str, now_ms: float) -> None:
        for batch in prefill_pools[pool_name].start_ready_batches(now_ms, args.slo_ttft_ms):
            push_event(
                batch.end_ms,
                "prefill_done",
                {
                    "pool_name": pool_name,
                    "instance_id": batch.instance_id,
                    "request_ids": batch.request_ids,
                    "completions": batch.completions,
                },
            )

    def start_decode_ready(pool_name: str, now_ms: float) -> None:
        for step in decode_pools[pool_name].start_ready_steps(now_ms):
            push_event(
                step.end_ms,
                "decode_step_done",
                {
                    "pool_name": pool_name,
                    "instance_id": step.instance_id,
                },
            )

    def schedule_kv_transfer(request_id: str, prompt_tokens: int, p_name: str, d_name: str, ready_ms: float) -> float:
        bandwidth = link_bandwidth_mbps(p_name, d_name, args.local_bandwidth_mbps, args.cross_bandwidth_mbps)
        transfer_ms = kv_transfer_ms(prompt_tokens, bandwidth, args.compress_factor)
        link_key = f"{p_name}->{d_name}"
        transfer_start = max(ready_ms, network_available_ms[link_key])
        transfer_end = transfer_start + transfer_ms
        network_available_ms[link_key] = transfer_end
        bytes_ = prompt_tokens * QWEN25_7B_KV_BYTES_PER_TOKEN * args.compress_factor
        network_busy_ms[link_key] += transfer_ms
        network_bytes[link_key] += bytes_
        active_mbps = (bytes_ * 8.0) / max(transfer_ms, 1e-9) / 1000.0
        payload = {
            "request_id": request_id,
            "p_name": p_name,
            "d_name": d_name,
            "link_key": link_key,
            "active_mbps": active_mbps,
        }
        push_event(transfer_start, "kv_start", payload)
        push_event(transfer_end, "kv_done", payload)
        return transfer_end

    raw_requests = list(iter_dataset(args.dataset, args.limit, qps, time_scale))
    request_by_id = {rid: (arrival, prompt, response) for rid, arrival, prompt, response in raw_requests}
    for request_id, arrival_ms, prompt_tokens, response_tokens in raw_requests:
        push_event(
            arrival_ms,
            "arrival",
            {
                "request_id": request_id,
                "arrival_ms": arrival_ms,
                "prompt_tokens": prompt_tokens,
                "response_tokens": response_tokens,
            },
        )

    while events:
        now_ms, _seq, event_type, payload = heapq.heappop(events)
        event_counts[event_type] += 1

        if event_type == "arrival":
            request_id = payload["request_id"]
            prompt_tokens = payload["prompt_tokens"]
            response_tokens = payload["response_tokens"]
            if args.policy == "distserve_batched":
                distserve_pending.append(payload)
                if not distserve_dispatch_scheduled:
                    distserve_dispatch_scheduled = True
                    push_event(now_ms, "distserve_dispatch", {})
                continue

            if args.policy == "distserve":
                p_name, instance_id = select_distserve_prefill_instance(prefill_pools)
                selected_pairs[request_id] = (p_name, "")
                prefill_pools[p_name].enqueue_to_instance(
                    instance_id,
                    request_id,
                    now_ms,
                    prompt_tokens,
                )
                prefill_instance_counts[instance_id] += 1
                start_prefill_ready(p_name, now_ms)
                continue

            prefill_loads = normalized_prefill_loads(prefill_pools, now_ms)
            decode_loads = normalized_decode_loads(decode_pools)
            req_rank = prompt_ranker.rank_after_update(prompt_tokens)
            decode_req_rank = decode_ranker.rank_after_update(response_tokens)
            if args.policy == "pso_slo":
                p_name, d_name = select_pair_pso(
                    req_rank,
                    prompt_tokens,
                    prefill_loads,
                    decode_loads,
                    network_loads,
                    args,
                )
            elif args.policy == "round_robin":
                p_name, d_name, rr_index = select_pair_round_robin(rr_index)
            elif args.policy == "least_load":
                p_name, d_name = select_pair_least_load(prefill_loads, decode_loads)
            elif args.policy == "adaptive_variance":
                p_name, d_name = select_pair_adaptive_variance(
                    req_rank,
                    decode_req_rank,
                    prompt_tokens,
                    response_tokens,
                    prefill_loads,
                    decode_loads,
                    network_loads,
                    prefill_profiles,
                    decode_profiles,
                    decode_pools,
                    args,
                )
            else:
                raise ValueError(f"unknown policy: {args.policy}")
            selected_pairs[request_id] = (p_name, d_name)
            pair_counts[f"{p_name}->{d_name}"] += 1
            instance_id = prefill_pools[p_name].enqueue(request_id, now_ms, prompt_tokens, args.slo_ttft_ms)
            prefill_instance_counts[instance_id] += 1
            start_prefill_ready(p_name, now_ms)
            continue

        if event_type == "distserve_dispatch":
            distserve_dispatch_scheduled = False
            batch = take_distserve_prefill_batch(
                distserve_pending,
                args.distserve_prefill_batch_tokens,
            )
            if batch:
                p_name, instance_id = select_distserve_prefill_instance(prefill_pools)
                for request in batch:
                    request_id = request["request_id"]
                    selected_pairs[request_id] = (p_name, "")
                    prefill_pools[p_name].enqueue_to_instance(
                        instance_id,
                        request_id,
                        request["arrival_ms"],
                        request["prompt_tokens"],
                    )
                    prefill_instance_counts[instance_id] += 1
                start_prefill_ready(p_name, now_ms)
            if distserve_pending:
                distserve_dispatch_scheduled = True
                push_event(now_ms, "distserve_dispatch", {})
            continue

        if event_type == "prefill_done":
            pool_name = payload["pool_name"]
            prefill_pools[pool_name].complete_batch(payload["instance_id"], payload["request_ids"])
            for completion in payload["completions"]:
                prefill_completions[completion.request_id] = completion
                p_name, d_name = selected_pairs[completion.request_id]
                if args.policy in ("distserve", "distserve_batched"):
                    d_name, d_instance_id = select_distserve_decode_instance(decode_pools)
                    selected_pairs[completion.request_id] = (p_name, d_name)
                    pair_counts[f"{p_name}->{d_name}"] += 1
                transfer_end = schedule_kv_transfer(
                    completion.request_id,
                    completion.prompt_tokens,
                    p_name,
                    d_name,
                    completion.complete_ms,
                )
                if args.policy in ("distserve", "distserve_batched"):
                    _arrival_ms, prompt_tokens, response_tokens = request_by_id[completion.request_id]
                    decode_pools[d_name].enqueue_to_instance(
                        d_instance_id,
                        completion.request_id,
                        transfer_end,
                        prompt_tokens,
                        response_tokens,
                    )
                    decode_instance_counts[d_instance_id] += 1
            start_prefill_ready(pool_name, now_ms)
            continue

        if event_type == "kv_start":
            p_name = payload["p_name"]
            d_name = payload["d_name"]
            active_mbps = payload["active_mbps"]
            capacity = link_bandwidth_mbps(p_name, d_name, args.local_bandwidth_mbps, args.cross_bandwidth_mbps)
            network_active_mbps[(p_name, d_name)] += active_mbps
            network_loads[(p_name, d_name)] = clamp(network_active_mbps[(p_name, d_name)] / max(capacity, 1e-9), 0.0, 1.0)
            continue

        if event_type == "kv_done":
            request_id = payload["request_id"]
            p_name = payload["p_name"]
            d_name = payload["d_name"]
            active_mbps = payload["active_mbps"]
            capacity = link_bandwidth_mbps(p_name, d_name, args.local_bandwidth_mbps, args.cross_bandwidth_mbps)
            network_active_mbps[(p_name, d_name)] = max(0.0, network_active_mbps[(p_name, d_name)] - active_mbps)
            network_loads[(p_name, d_name)] = clamp(network_active_mbps[(p_name, d_name)] / max(capacity, 1e-9), 0.0, 1.0)
            if args.policy not in ("distserve", "distserve_batched"):
                _arrival_ms, prompt_tokens, response_tokens = request_by_id[request_id]
                instance_id = decode_pools[d_name].enqueue(request_id, now_ms, prompt_tokens, response_tokens, args.slo_tpot_ms)
                decode_instance_counts[instance_id] += 1
            start_decode_ready(d_name, now_ms)
            continue

        if event_type == "decode_step_done":
            pool_name = payload["pool_name"]
            completions = decode_pools[pool_name].complete_step(payload["instance_id"])
            for completion in completions:
                decode_completions[completion.request_id] = completion
            start_decode_ready(pool_name, now_ms)
            continue

        raise ValueError(f"unknown event type: {event_type}")

    records: List[RequestRecord] = []
    for request_id, (arrival_ms, prompt_tokens, response_tokens) in request_by_id.items():
        p_name, d_name = selected_pairs[request_id]
        pc = prefill_completions[request_id]
        dc = decode_completions[request_id]
        kv_done = dc.start_ms
        e2e = dc.complete_ms - arrival_ms
        records.append(
            RequestRecord(
                request_id=request_id,
                arrival_ms=arrival_ms,
                prompt_tokens=prompt_tokens,
                response_tokens=response_tokens,
                prefill_pool=p_name,
                decode_pool=d_name,
                prefill_instance=pc.instance_id,
                decode_instance=dc.instance_id,
                prefill_done_ms=pc.complete_ms,
                kv_done_ms=kv_done,
                decode_done_ms=dc.complete_ms,
                ttft_ms=pc.ttft_ms,
                tpot_ms=dc.tpot_ms,
                e2e_ms=e2e,
                ttft_met=pc.ttft_ms <= args.slo_ttft_ms,
                tpot_met=dc.tpot_ms <= args.slo_tpot_ms,
                e2e_met=e2e <= args.slo_e2e_ms,
                all_slo_met=(
                    pc.ttft_ms <= args.slo_ttft_ms
                    and dc.tpot_ms <= args.slo_tpot_ms
                    and e2e <= args.slo_e2e_ms
                ),
            )
        )

    duration_ms = max((r.decode_done_ms for r in records), default=0.0) - min((r.arrival_ms for r in records), default=0.0)
    duration_s = max(duration_ms / 1000.0, 1e-9)
    ttft_values = [r.ttft_ms for r in records]
    tpot_values = [r.tpot_ms for r in records]
    e2e_values = [r.e2e_ms for r in records]

    instance_util = {}
    for pool in prefill_pools.values():
        for inst in pool.instances:
            instance_util[inst.instance_id] = inst.busy_ms / max(duration_ms, 1e-9)
    for pool in decode_pools.values():
        for inst in pool.instances:
            instance_util[inst.instance_id] = inst.busy_ms / max(duration_ms, 1e-9)

    network_stats = {}
    for key, bytes_ in sorted(network_bytes.items()):
        p_name, d_name = key.split("->")
        bandwidth = link_bandwidth_mbps(p_name, d_name, args.local_bandwidth_mbps, args.cross_bandwidth_mbps)
        avg_mbps = (bytes_ * 8.0) / (duration_s * 1_000_000.0)
        network_stats[key] = {
            "bytes": bytes_,
            "gib": bytes_ / (1024.0**3),
            "busy_ms": network_busy_ms[key],
            "avg_mbps": avg_mbps,
            "utilization_percent": (avg_mbps / bandwidth) * 100.0 if bandwidth > 0 else 0.0,
        }

    result = {
        "requests": len(records),
        "simulation_mode": "online_event_driven",
        "event_counts": dict(event_counts),
        "policy": args.policy,
        "instance_policy": {
            "prefill": prefill_instance_policy,
            "decode": decode_instance_policy,
        },
        "arrival_mode": "fixed_qps" if qps else "dataset_timestamp",
        "qps": qps,
        "slo": {"ttft_ms": args.slo_ttft_ms, "tpot_ms": args.slo_tpot_ms, "e2e_ms": args.slo_e2e_ms},
        "pair_counts": dict(pair_counts),
        "prefill_instance_counts": dict(prefill_instance_counts),
        "decode_instance_counts": dict(decode_instance_counts),
        "instance_utilization": instance_util,
        "network": network_stats,
        "latency": {"ttft": stats(ttft_values), "tpot": stats(tpot_values), "e2e": stats(e2e_values)},
        "slo_attainment": {
            "ttft": sum(r.ttft_met for r in records) / max(1, len(records)),
            "tpot": sum(r.tpot_met for r in records) / max(1, len(records)),
            "e2e": sum(r.e2e_met for r in records) / max(1, len(records)),
            "all": sum(r.all_slo_met for r in records) / max(1, len(records)),
        },
        "goodput_requests_per_second": sum(r.all_slo_met for r in records) / duration_s,
        "request_records": [asdict(record) for record in records],
        "distserve": {
            "mode": (
                "direct_jsq_vllm_batching"
                if args.policy == "distserve"
                else "gateway_fcfs_batching"
            ),
            "prefill_max_num_batched_tokens": prefill_max_num_batched_tokens,
            "prefill_batch_tokens": args.distserve_prefill_batch_tokens,
        } if args.policy in ("distserve", "distserve_batched") else None,
        "decode_steps": sum(len(inst.steps) for pool in decode_pools.values() for inst in pool.instances),
    }
    return result


def print_summary(result: Dict) -> None:
    print("\nJoint Controller/Instance Simulation")
    print("=" * 100)
    print(f"simulation_mode: {result.get('simulation_mode')}")
    print(f"policy: {result['policy']}, instance_policy: {result['instance_policy']}")
    print(f"requests: {result['requests']}, arrival_mode: {result['arrival_mode']}, qps: {result['qps']}")
    print(f"slo_attainment: {result['slo_attainment']}")
    print(f"goodput_requests_per_second: {result['goodput_requests_per_second']:.3f}")
    if result.get("distserve"):
        distserve = result["distserve"]
        print(
            "distserve: "
            f"mode={distserve['mode']}, "
            f"prefill_max_num_batched_tokens={distserve['prefill_max_num_batched_tokens']}"
        )
    print(f"latency: {result['latency']}")
    print("-" * 100)
    print("Pair counts:")
    for key, value in sorted(result["pair_counts"].items(), key=lambda item: item[1], reverse=True):
        print(f"  {key}: {value}")
    print("Instance utilization:")
    for key, value in sorted(result["instance_utilization"].items()):
        print(f"  {key}: {value * 100:.2f}%")
    print("Network:")
    for key, value in sorted(result["network"].items()):
        print(f"  {key}: {value['gib']:.3f} GiB, avg={value['avg_mbps']:.3f} Mbps, util={value['utilization_percent']:.3f}%")
    print(f"decode_steps: {result['decode_steps']}")
    print("=" * 100)


def comparison_metrics(result: Dict) -> Dict[str, float]:
    pair_total = sum(result["pair_counts"].values())
    cross_count = 0
    for key, count in result["pair_counts"].items():
        p_name, d_name = key.split("->")
        if not is_local_pair(p_name, d_name):
            cross_count += count
    network_values = list(result["network"].values())
    instance_values = list(result["instance_utilization"].values())
    network_util_values = [item["utilization_percent"] for item in network_values]
    decode_instance_values = [
        value * 100.0
        for name, value in result["instance_utilization"].items()
        if name.startswith("D")
    ]
    prefill_instance_values = [
        value * 100.0
        for name, value in result["instance_utilization"].items()
        if name.startswith("P")
    ]

    def coefficient_of_variation(values: List[float]) -> float:
        if not values:
            return 0.0
        avg = mean(values)
        if avg <= 0.0:
            return 0.0
        variance = sum((value - avg) ** 2 for value in values) / len(values)
        return (variance ** 0.5) / avg

    return {
        "ttft_slo": result["slo_attainment"]["ttft"],
        "tpot_slo": result["slo_attainment"]["tpot"],
        "e2e_slo": result["slo_attainment"]["e2e"],
        "all_slo": result["slo_attainment"]["all"],
        "goodput_requests_per_second": result["goodput_requests_per_second"],
        "ttft_avg_ms": result["latency"]["ttft"]["avg"],
        "ttft_p95_ms": result["latency"]["ttft"]["p95"],
        "ttft_p99_ms": result["latency"]["ttft"]["p99"],
        "tpot_avg_ms": result["latency"]["tpot"]["avg"],
        "tpot_p95_ms": result["latency"]["tpot"]["p95"],
        "tpot_p99_ms": result["latency"]["tpot"]["p99"],
        "e2e_avg_ms": result["latency"]["e2e"]["avg"],
        "e2e_p95_ms": result["latency"]["e2e"]["p95"],
        "e2e_p99_ms": result["latency"]["e2e"]["p99"],
        "network_gib": sum(item["gib"] for item in network_values),
        "network_avg_mbps": sum(item["avg_mbps"] for item in network_values),
        "network_max_util_percent": max((item["utilization_percent"] for item in network_values), default=0.0),
        "network_util_cv": coefficient_of_variation(network_util_values),
        "cross_pair_ratio": cross_count / max(1, pair_total),
        "instance_avg_util_percent": (mean(instance_values) * 100.0) if instance_values else 0.0,
        "instance_max_util_percent": (max(instance_values) * 100.0) if instance_values else 0.0,
        "decode_instance_util_cv": coefficient_of_variation(decode_instance_values),
        "prefill_instance_util_cv": coefficient_of_variation(prefill_instance_values),
    }


def build_comparison_result(args: argparse.Namespace) -> Dict:
    runs = {}
    comparison = {}
    for policy in POLICIES:
        policy_args = copy.copy(args)
        policy_args.policy = policy
        policy_args.compare_all = False
        runs[policy] = simulate(policy_args)
        comparison[policy] = comparison_metrics(runs[policy])
    return {
        "comparison_mode": True,
        "policies": list(POLICIES),
        "runs": runs,
        "comparison": comparison,
    }


def print_comparison_summary(result: Dict) -> None:
    print("\nJoint Controller/Instance Simulation Comparison")
    print("=" * 145)
    header = (
        f"{'policy':<18} {'TTFT':>7} {'TPOT':>7} {'E2E':>7} {'ALL':>7} {'goodput':>9} "
        f"{'TTFT_p95':>10} {'TPOT_p95':>10} {'E2E_p95':>10} "
        f"{'net_cv':>8} {'max_net%':>9} {'pre_cv':>8} {'dec_cv':>8} {'max_inst%':>10}"
    )
    print(header)
    print("-" * 145)
    for policy in result["policies"]:
        metrics = result["comparison"][policy]
        print(
            f"{policy:<18} "
            f"{metrics['ttft_slo']:>7.3f} {metrics['tpot_slo']:>7.3f} {metrics['e2e_slo']:>7.3f} "
            f"{metrics['all_slo']:>7.3f} {metrics['goodput_requests_per_second']:>9.3f} "
            f"{metrics['ttft_p95_ms']:>10.2f} {metrics['tpot_p95_ms']:>10.2f} {metrics['e2e_p95_ms']:>10.2f} "
            f"{metrics['network_util_cv']:>8.3f} {metrics['network_max_util_percent']:>9.3f} "
            f"{metrics['prefill_instance_util_cv']:>8.3f} {metrics['decode_instance_util_cv']:>8.3f} "
            f"{metrics['instance_max_util_percent']:>10.2f}"
        )
    print("=" * 145)


def save_comparison_png(result: Dict, output_path: Path) -> Path:
    mpl_config_dir = output_path.parent / ".matplotlib"
    mpl_config_dir.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("MPLCONFIGDIR", str(mpl_config_dir))

    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    policies = result["policies"]
    comparison = result["comparison"]
    colors = ["#4c78a8", "#f58518", "#54a24b", "#b279a2", "#72b7b2", "#e45756"]

    fig, axes = plt.subplots(2, 3, figsize=(19, 10), dpi=160)
    fig.suptitle("Joint Controller/Instance Baseline Comparison", fontsize=15, fontweight="bold")

    def grouped_bars(ax, groups, metric_names, title, ylabel):
        width = min(0.8 / max(1, len(policies)), 0.22)
        x_positions = list(range(len(metric_names)))
        for offset, policy in enumerate(policies):
            values = [comparison[policy][name] for name in metric_names]
            center = (len(policies) - 1) / 2.0
            xs = [x + (offset - center) * width for x in x_positions]
            ax.bar(xs, values, width=width, label=policy, color=colors[offset % len(colors)])
        ax.set_xticks(x_positions)
        ax.set_xticklabels(groups)
        ax.set_title(title)
        ax.set_ylabel(ylabel)
        ax.grid(axis="y", alpha=0.25)
        ax.legend(fontsize=8)

    grouped_bars(
        axes[0][0],
        ["TTFT", "TPOT", "E2E", "ALL"],
        ["ttft_slo", "tpot_slo", "e2e_slo", "all_slo"],
        "SLO Attainment",
        "ratio",
    )
    axes[0][0].set_ylim(0.0, 1.05)

    grouped_bars(
        axes[0][1],
        ["TTFT p95", "TTFT p99", "TPOT p95", "TPOT p99", "E2E p95", "E2E p99"],
        ["ttft_p95_ms", "ttft_p99_ms", "tpot_p95_ms", "tpot_p99_ms", "e2e_p95_ms", "e2e_p99_ms"],
        "Latency Tail",
        "ms",
    )

    grouped_bars(
        axes[0][2],
        ["Goodput"],
        ["goodput_requests_per_second"],
        "Fixed-workload Goodput",
        "requests/s",
    )

    grouped_bars(
        axes[1][0],
        ["Net util CV", "Max link util %"],
        ["network_util_cv", "network_max_util_percent"],
        "Network Load Balance",
        "CV / percent",
    )

    grouped_bars(
        axes[1][1],
        ["Decode util CV", "Max util %"],
        ["decode_instance_util_cv", "instance_max_util_percent"],
        "Decode Instance Load Balance",
        "CV / percent",
    )

    grouped_bars(
        axes[1][2],
        ["Cross pair ratio"],
        ["cross_pair_ratio"],
        "Cross-machine Routing",
        "ratio",
    )

    fig.tight_layout(rect=(0, 0, 1, 0.96))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, format="png")
    plt.close(fig)
    return output_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Joint controller/gateway/vLLM batching simulation.")
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--limit", type=int, default=1000)
    parser.add_argument("--scenario", choices=["dynamic", "burst"], default="dynamic")
    parser.add_argument("--policy", choices=POLICIES, default="pso_slo")
    parser.add_argument("--compare-all", action="store_true")
    parser.add_argument("--qps", type=float, default=200)
    parser.add_argument("--time-scale", type=float, default=None)
    parser.add_argument("--slo-ttft-ms", type=float, default=3000.0)
    parser.add_argument("--slo-tpot-ms", type=float, default=40.0)
    parser.add_argument("--slo-e2e-ms", type=float, default=21500.0)
    parser.add_argument("--max-num-batched-tokens", type=int, default=4096)
    parser.add_argument(
        "--distserve-prefill-batch-tokens",
        type=int,
        default=2048,
        help="DistServe gateway FCFS logical-batch limit L_m (independent of vLLM batching).",
    )
    parser.add_argument("--local-bandwidth-mbps", type=float, default=36000.0)
    parser.add_argument("--cross-bandwidth-mbps", type=float, default=1000.0)
    parser.add_argument("--compress-factor", type=float, default=1.0)
    parser.add_argument("--output-json", type=Path, default=DEFAULT_RESULT)
    parser.add_argument("--figure-output", type=Path, default=DEFAULT_COMPARISON_FIGURE)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.compare_all:
        result = build_comparison_result(args)
        print_comparison_summary(result)
        if args.figure_output:
            figure_path = save_comparison_png(result, args.figure_output)
            print(f"wrote figure: {figure_path}")
        if args.output_json == DEFAULT_RESULT:
            args.output_json = DEFAULT_COMPARISON_RESULT
    else:
        result = simulate(args)
        print_summary(result)
    if args.output_json:
        args.output_json.parent.mkdir(parents=True, exist_ok=True)
        with args.output_json.open("w", encoding="utf-8") as handle:
            json.dump(result, handle, ensure_ascii=False, indent=2)
        print(f"wrote: {args.output_json}")


if __name__ == "__main__":
    main()
