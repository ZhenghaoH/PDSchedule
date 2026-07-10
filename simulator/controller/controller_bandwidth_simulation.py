"""Offline controller scheduling simulation for P/D pair bandwidth usage.

This script replays BurstGPT requests against a small built-in topology that
matches the current 170/169/167/166 experiment setup. It mirrors the normalized
fitness used by scheduler/controller/scheduler_PSO.py closely enough for
offline analysis without importing the live controller store objects.
"""

from __future__ import annotations

import argparse
import bisect
import csv
import heapq
import json
import math
from collections import Counter, defaultdict, deque
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import DefaultDict, Dict, Iterable, List, Optional, Tuple


QWEN25_7B_KV_BYTES_PER_TOKEN = 57344
DEFAULT_DATASET = Path(__file__).resolve().parents[1] / "dataset" / "BurstGPT_1.csv"


@dataclass(frozen=True)
class PrefillPoolSpec:
    name: str
    machine: str
    flops: float
    max_prompt_throughput: float


@dataclass(frozen=True)
class DecodePoolSpec:
    name: str
    machine: str
    mem_bandwidth: float
    max_decode_req_num: float


@dataclass
class SimulationResult:
    scenario: str
    arrival_mode: str
    qps: Optional[float]
    dataset: str
    processed_requests: int
    timestamp_start: Optional[float]
    timestamp_end: Optional[float]
    simulated_duration_s: float
    local_bandwidth_mbps: float
    cross_bandwidth_mbps: float
    pair_counts: Dict[str, int]
    pair_request_percent: Dict[str, float]
    pair_kv_gib: Dict[str, float]
    local_requests: int
    cross_requests: int
    local_request_percent: float
    cross_request_percent: float
    local_kv_gib: float
    cross_kv_gib: float
    cross_avg_mbps: float
    cross_avg_utilization_percent: float


PREFILL_POOLS: Dict[str, PrefillPoolSpec] = {
    "P1": PrefillPoolSpec("P1", "170", flops=209.6, max_prompt_throughput=25499.77),
    "P2": PrefillPoolSpec("P2", "169", flops=104.8, max_prompt_throughput=12749.885),
    "P3": PrefillPoolSpec("P3", "167", flops=35.58, max_prompt_throughput=5646.747),
}

DECODE_POOLS: Dict[str, DecodePoolSpec] = {
    "D1": DecodePoolSpec("D1", "170", mem_bandwidth=3584.0, max_decode_req_num=256.0),
    "D2": DecodePoolSpec("D2", "169", mem_bandwidth=1792.0, max_decode_req_num=128.0),
    "D3": DecodePoolSpec("D3", "166", mem_bandwidth=936.0, max_decode_req_num=32.0),
}


def clamp(value: float, lower: float = 0.0, upper: float = 1.0) -> float:
    return min(max(float(value), lower), upper)


def build_rank_map(values: Dict[str, float]) -> Dict[str, float]:
    max_value = max((max(float(value), 0.0) for value in values.values()), default=0.0)
    if max_value <= 0:
        return {name: 0.5 for name in values}
    return {name: clamp(value / max_value, 0.01, 0.99) for name, value in values.items()}


PREFILL_RANKS = build_rank_map({name: spec.flops for name, spec in PREFILL_POOLS.items()})
DECODE_RANKS = build_rank_map({name: spec.mem_bandwidth for name, spec in DECODE_POOLS.items()})


class RequestRanker:
    def __init__(self, window_size: int = 2000):
        self.window_size = window_size
        self.history_queue: deque[int] = deque()
        self.sorted_values: List[int] = []

    def get_rank_after_update(self, value: int) -> float:
        if len(self.history_queue) >= self.window_size:
            old_value = self.history_queue.popleft()
            idx = bisect.bisect_left(self.sorted_values, old_value)
            if idx < len(self.sorted_values) and self.sorted_values[idx] == old_value:
                self.sorted_values.pop(idx)

        self.history_queue.append(value)
        bisect.insort(self.sorted_values, value)
        if not self.sorted_values:
            return 0.5
        idx = bisect.bisect_left(self.sorted_values, value)
        return clamp(idx / len(self.sorted_values), 0.01, 0.99)


def is_local_pair(prefill_name: str, decode_name: str) -> bool:
    return PREFILL_POOLS[prefill_name].machine == DECODE_POOLS[decode_name].machine


def link_bandwidth_mbps(
    prefill_name: str,
    decode_name: str,
    local_bandwidth_mbps: float,
    cross_bandwidth_mbps: float,
) -> float:
    return local_bandwidth_mbps if is_local_pair(prefill_name, decode_name) else cross_bandwidth_mbps


def link_rtt_ms(prefill_name: str, decode_name: str) -> float:
    return 0.05 if is_local_pair(prefill_name, decode_name) else 0.5


def transfer_time_ms(token_length: int, bandwidth_mbps: float, compress_factor: float) -> float:
    safe_bandwidth = max(0.0, float(bandwidth_mbps))
    if safe_bandwidth <= 0:
        return float("inf")
    transfer_bytes = max(0, int(token_length)) * QWEN25_7B_KV_BYTES_PER_TOKEN * compress_factor
    return (transfer_bytes * 8.0) / (safe_bandwidth * 1000.0)


def network_time_ms(
    token_length: int,
    prefill_name: str,
    decode_name: str,
    local_bandwidth_mbps: float,
    cross_bandwidth_mbps: float,
    compress_factor: float,
) -> float:
    bandwidth = link_bandwidth_mbps(prefill_name, decode_name, local_bandwidth_mbps, cross_bandwidth_mbps)
    return link_rtt_ms(prefill_name, decode_name) + transfer_time_ms(token_length, bandwidth, compress_factor)


def capacity_loss(capacity_rank: float, request_rank: float) -> float:
    diff = clamp(capacity_rank) - clamp(request_rank)
    if diff >= 0:
        return clamp(0.5 * diff)
    return clamp(abs(diff))


def scheduler_fitness(
    request_rank: float,
    token_length: int,
    prefill_name: str,
    decode_name: str,
    prefill_loads: Dict[str, float],
    decode_loads: Dict[str, float],
    network_loads: Dict[Tuple[str, str], float],
    local_bandwidth_mbps: float,
    cross_bandwidth_mbps: float,
    compress_factor: float,
) -> float:
    net_times = [
        network_time_ms(token_length, p_name, d_name, local_bandwidth_mbps, cross_bandwidth_mbps, compress_factor)
        for p_name in PREFILL_POOLS
        for d_name in DECODE_POOLS
    ]
    min_net_time = min(net_times)
    max_net_time = max(net_times)

    selected_net_time = network_time_ms(
        token_length,
        prefill_name,
        decode_name,
        local_bandwidth_mbps,
        cross_bandwidth_mbps,
        compress_factor,
    )
    denominator = max_net_time - min_net_time
    net_time_loss = 0.0 if denominator <= 1e-9 else (selected_net_time - min_net_time) / denominator
    net_load = clamp(network_loads.get((prefill_name, decode_name), 0.0))
    network_loss = clamp(0.5 * clamp(net_time_loss) + 0.5 * net_load)

    ability_loss = (
        capacity_loss(PREFILL_RANKS[prefill_name], request_rank)
        + capacity_loss(DECODE_RANKS[decode_name], request_rank)
    ) / 2.0
    load_loss = (
        clamp(prefill_loads.get(prefill_name, 0.0))
        + clamp(decode_loads.get(decode_name, 0.0))
        + net_load
    ) / 3.0
    return (0.45 * ability_loss) + (0.10 * network_loss) + (0.45 * load_loss)


def _variance(values: List[float]) -> float:
    if not values:
        return 0.0
    avg = sum(values) / len(values)
    return sum((value - avg) ** 2 for value in values) / len(values)


def adaptive_variance_fitness(
    request_rank: float,
    token_length: int,
    prefill_name: str,
    decode_name: str,
    prefill_loads: Dict[str, float],
    decode_loads: Dict[str, float],
    network_loads: Dict[Tuple[str, str], float],
    local_bandwidth_mbps: float,
    cross_bandwidth_mbps: float,
    compress_factor: float,
    prefill_time_ms: float,
    decode_time_ms: float,
    decode_request_rank: Optional[float] = None,
    ability_weight: float = 0.5,
    load_weight: float = 0.5,
) -> float:
    """Dynamic ability mismatch plus variance-based workload imbalance.

    Smaller is better. Prefill/decode time estimates are supplied by the caller
    so the online joint simulator can use its TTFT/TPOT profiles.
    """
    net_times = {
        (p_name, d_name): network_time_ms(
            token_length,
            p_name,
            d_name,
            local_bandwidth_mbps,
            cross_bandwidth_mbps,
            compress_factor,
        )
        for p_name in PREFILL_POOLS
        for d_name in DECODE_POOLS
    }
    min_net_time = min(net_times.values())
    max_net_time = max(net_times.values())
    selected_net_time = net_times[(prefill_name, decode_name)]
    denominator = max_net_time - min_net_time
    normalized_net_time = 0.0 if denominator <= 1e-9 else (selected_net_time - min_net_time) / denominator
    network_capacity_rank = clamp(1.0 - normalized_net_time, 0.01, 0.99)

    safe_prefill_time = max(0.0, float(prefill_time_ms))
    safe_network_time = max(0.0, float(selected_net_time))
    safe_decode_time = max(0.0, float(decode_time_ms))
    e2e_est_ms = max(safe_prefill_time + safe_network_time + safe_decode_time, 1e-9)

    alpha_p = safe_prefill_time / e2e_est_ms
    alpha_n = safe_network_time / e2e_est_ms
    alpha_d = safe_decode_time / e2e_est_ms

    effective_decode_rank = request_rank if decode_request_rank is None else decode_request_rank
    p_loss = capacity_loss(PREFILL_RANKS[prefill_name], request_rank)
    d_loss = capacity_loss(DECODE_RANKS[decode_name], effective_decode_rank)
    n_loss = capacity_loss(network_capacity_rank, request_rank)
    ability_loss = alpha_p * p_loss + alpha_n * n_loss + alpha_d * d_loss

    delta_p = clamp(token_length / max(1.0, PREFILL_POOLS[prefill_name].max_prompt_throughput))
    delta_n = clamp(safe_network_time / e2e_est_ms)
    delta_d = clamp(1.0 / max(1.0, DECODE_POOLS[decode_name].max_decode_req_num))

    prefill_vector = [
        clamp(prefill_loads.get(name, 0.0) + (delta_p if name == prefill_name else 0.0))
        for name in PREFILL_POOLS
    ]
    decode_vector = [
        clamp(decode_loads.get(name, 0.0) + (delta_d if name == decode_name else 0.0))
        for name in DECODE_POOLS
    ]
    network_vector = [
        clamp(network_loads.get((p_name, d_name), 0.0) + (delta_n if (p_name, d_name) == (prefill_name, decode_name) else 0.0))
        for p_name in PREFILL_POOLS
        for d_name in DECODE_POOLS
    ]

    load_loss = (
        0.33 * _variance(prefill_vector)
        + 0.33 * _variance(decode_vector)
        + 0.33 * _variance(network_vector)
    )
    return ability_weight * ability_loss + load_weight * load_loss


def choose_pair(
    request_rank: float,
    token_length: int,
    prefill_loads: Dict[str, float],
    decode_loads: Dict[str, float],
    network_loads: Dict[Tuple[str, str], float],
    local_bandwidth_mbps: float,
    cross_bandwidth_mbps: float,
    compress_factor: float,
) -> Tuple[str, str, float]:
    best: Optional[Tuple[float, str, str]] = None
    for prefill_name in PREFILL_POOLS:
        for decode_name in DECODE_POOLS:
            score = scheduler_fitness(
                request_rank,
                token_length,
                prefill_name,
                decode_name,
                prefill_loads,
                decode_loads,
                network_loads,
                local_bandwidth_mbps,
                cross_bandwidth_mbps,
                compress_factor,
            )
            if best is None or score < best[0]:
                best = (score, prefill_name, decode_name)
    assert best is not None
    score, prefill_name, decode_name = best
    return prefill_name, decode_name, score


def iter_requests(dataset: Path, limit: Optional[int]) -> Iterable[Tuple[int, int, int, int]]:
    with dataset.open(newline="", encoding="utf-8") as handle:
        for index, row in enumerate(csv.DictReader(handle), start=1):
            if limit is not None and index > limit:
                break
            timestamp = int(float(row["Timestamp"]))
            request_tokens = max(1, int(float(row["Request tokens"])))
            response_tokens = max(1, int(float(row["Response tokens"])))
            yield index, timestamp, request_tokens, response_tokens


def simulate(
    dataset: Path,
    limit: Optional[int],
    scenario: str,
    time_scale: float,
    local_bandwidth_mbps: float,
    cross_bandwidth_mbps: float,
    compress_factor: float,
    decode_tokens_per_second: float,
    qps: Optional[float],
) -> SimulationResult:
    dynamic = scenario in {"dynamic", "burst"}
    arrival_mode = "fixed_qps" if qps is not None else "dataset_timestamp"
    ranker = RequestRanker()

    pair_counts: Counter[Tuple[str, str]] = Counter()
    pair_kv_bytes: DefaultDict[Tuple[str, str], float] = defaultdict(float)
    prefill_active: Counter[str] = Counter()
    decode_active: Counter[str] = Counter()
    network_active_bytes: DefaultDict[Tuple[str, str], float] = defaultdict(float)
    prefill_heap: List[Tuple[float, str]] = []
    decode_heap: List[Tuple[float, str]] = []
    network_heap: List[Tuple[float, Tuple[str, str], float]] = []

    timestamp_start: Optional[float] = None
    timestamp_end: Optional[float] = None
    processed_requests = 0

    for request_index, timestamp, request_tokens, response_tokens in iter_requests(dataset, limit):
        if qps is not None:
            scaled_time = (request_index - 1) / qps
        else:
            scaled_time = timestamp / time_scale
        if timestamp_start is None:
            timestamp_start = scaled_time
        timestamp_end = scaled_time

        if dynamic:
            while prefill_heap and prefill_heap[0][0] <= scaled_time:
                _, prefill_name = heapq.heappop(prefill_heap)
                prefill_active[prefill_name] -= 1
            while decode_heap and decode_heap[0][0] <= scaled_time:
                _, decode_name = heapq.heappop(decode_heap)
                decode_active[decode_name] -= 1
            while network_heap and network_heap[0][0] <= scaled_time:
                _, pair_key, active_bytes = heapq.heappop(network_heap)
                network_active_bytes[pair_key] -= active_bytes

            prefill_loads = {
                name: prefill_active[name] / max(1.0, spec.max_prompt_throughput / 1000.0)
                for name, spec in PREFILL_POOLS.items()
            }
            decode_loads = {
                name: decode_active[name] / max(1.0, spec.max_decode_req_num)
                for name, spec in DECODE_POOLS.items()
            }
            network_loads = {}
            for prefill_name in PREFILL_POOLS:
                for decode_name in DECODE_POOLS:
                    bandwidth = link_bandwidth_mbps(
                        prefill_name,
                        decode_name,
                        local_bandwidth_mbps,
                        cross_bandwidth_mbps,
                    )
                    bytes_per_second = bandwidth * 1_000_000.0 / 8.0
                    network_loads[(prefill_name, decode_name)] = (
                        network_active_bytes[(prefill_name, decode_name)] / bytes_per_second
                        if bytes_per_second > 0
                        else 1.0
                    )
        else:
            prefill_loads = {name: 0.0 for name in PREFILL_POOLS}
            decode_loads = {name: 0.0 for name in DECODE_POOLS}
            network_loads = {
                (prefill_name, decode_name): 0.0
                for prefill_name in PREFILL_POOLS
                for decode_name in DECODE_POOLS
            }

        request_rank = ranker.get_rank_after_update(request_tokens)
        prefill_name, decode_name, _ = choose_pair(
            request_rank,
            request_tokens,
            prefill_loads,
            decode_loads,
            network_loads,
            local_bandwidth_mbps,
            cross_bandwidth_mbps,
            compress_factor,
        )

        pair_key = (prefill_name, decode_name)
        kv_bytes = request_tokens * QWEN25_7B_KV_BYTES_PER_TOKEN * compress_factor
        pair_counts[pair_key] += 1
        pair_kv_bytes[pair_key] += kv_bytes
        processed_requests += 1

        if dynamic:
            prefill_duration = request_tokens / PREFILL_POOLS[prefill_name].max_prompt_throughput
            decode_duration = response_tokens / max(decode_tokens_per_second, 1e-9)
            transfer_duration = network_time_ms(
                request_tokens,
                prefill_name,
                decode_name,
                local_bandwidth_mbps,
                cross_bandwidth_mbps,
                compress_factor,
            ) / 1000.0
            heapq.heappush(prefill_heap, (scaled_time + prefill_duration, prefill_name))
            heapq.heappush(decode_heap, (scaled_time + decode_duration, decode_name))
            heapq.heappush(network_heap, (scaled_time + transfer_duration, pair_key, kv_bytes))
            prefill_active[prefill_name] += 1
            decode_active[decode_name] += 1
            network_active_bytes[pair_key] += kv_bytes

    total = max(processed_requests, 1)
    local_requests = 0
    cross_requests = 0
    local_kv_bytes = 0.0
    cross_kv_bytes = 0.0
    for pair_key, count in pair_counts.items():
        kv_bytes = pair_kv_bytes[pair_key]
        if is_local_pair(*pair_key):
            local_requests += count
            local_kv_bytes += kv_bytes
        else:
            cross_requests += count
            cross_kv_bytes += kv_bytes

    simulated_duration_s = max(0.0, (timestamp_end or 0.0) - (timestamp_start or 0.0))
    cross_avg_mbps = (
        (cross_kv_bytes * 8.0) / (simulated_duration_s * 1_000_000.0)
        if simulated_duration_s > 0
        else 0.0
    )
    pair_counts_str = {f"{p}->{d}": count for (p, d), count in sorted(pair_counts.items())}
    pair_request_percent = {key: (value / total) * 100.0 for key, value in pair_counts_str.items()}
    pair_kv_gib = {
        f"{p}->{d}": pair_kv_bytes[(p, d)] / (1024.0**3)
        for p, d in sorted(pair_kv_bytes)
    }

    return SimulationResult(
        scenario=scenario,
        arrival_mode=arrival_mode,
        qps=qps,
        dataset=str(dataset),
        processed_requests=processed_requests,
        timestamp_start=timestamp_start,
        timestamp_end=timestamp_end,
        simulated_duration_s=simulated_duration_s,
        local_bandwidth_mbps=local_bandwidth_mbps,
        cross_bandwidth_mbps=cross_bandwidth_mbps,
        pair_counts=pair_counts_str,
        pair_request_percent=pair_request_percent,
        pair_kv_gib=pair_kv_gib,
        local_requests=local_requests,
        cross_requests=cross_requests,
        local_request_percent=(local_requests / total) * 100.0,
        cross_request_percent=(cross_requests / total) * 100.0,
        local_kv_gib=local_kv_bytes / (1024.0**3),
        cross_kv_gib=cross_kv_bytes / (1024.0**3),
        cross_avg_mbps=cross_avg_mbps,
        cross_avg_utilization_percent=(cross_avg_mbps / cross_bandwidth_mbps) * 100.0
        if cross_bandwidth_mbps > 0
        else 0.0,
    )


def print_result(result: SimulationResult) -> None:
    print()
    print("Controller Bandwidth Simulation")
    print("=" * 88)
    print(f"scenario: {result.scenario}")
    print(f"arrival_mode: {result.arrival_mode}")
    print(f"qps: {result.qps if result.qps is not None else 'N/A'}")
    print(f"dataset: {result.dataset}")
    print(f"processed_requests: {result.processed_requests}")
    print(f"simulated_duration_s: {result.simulated_duration_s:.2f}")
    print(f"local_bandwidth_mbps: {result.local_bandwidth_mbps:.2f}")
    print(f"cross_bandwidth_mbps: {result.cross_bandwidth_mbps:.2f}")
    print("-" * 88)
    print(f"{'pair':<10} {'requests':>12} {'req_%':>10} {'kv_GiB':>14}")
    print("-" * 88)
    for pair_name, count in sorted(result.pair_counts.items(), key=lambda item: item[1], reverse=True):
        print(
            f"{pair_name:<10} {count:>12} "
            f"{result.pair_request_percent[pair_name]:>9.2f}% "
            f"{result.pair_kv_gib[pair_name]:>14.2f}"
        )
    print("-" * 88)
    print(
        f"local: requests={result.local_requests} "
        f"({result.local_request_percent:.2f}%), kv={result.local_kv_gib:.2f} GiB"
    )
    print(
        f"cross: requests={result.cross_requests} "
        f"({result.cross_request_percent:.2f}%), kv={result.cross_kv_gib:.2f} GiB"
    )
    print(
        f"cross_avg_mbps={result.cross_avg_mbps:.2f}, "
        f"cross_avg_utilization={result.cross_avg_utilization_percent:.2f}%"
    )
    print("=" * 88)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Simulate controller P/D scheduling bandwidth usage.")
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--limit", type=int, default=200000)
    parser.add_argument("--scenario", choices=["static", "dynamic", "burst"], default="dynamic")
    parser.add_argument(
        "--time-scale",
        type=float,
        default=None,
        help="Timestamp compression factor. Defaults to 1 for dynamic and 100 for burst.",
    )
    parser.add_argument("--local-bandwidth-mbps", type=float, default=36000.0)
    parser.add_argument("--cross-bandwidth-mbps", type=float, default=1000.0)
    parser.add_argument("--compress-factor", type=float, default=1.0)
    parser.add_argument("--decode-tokens-per-second", type=float, default=20.0)
    parser.add_argument(
        "--qps",
        type=float,
        default=200,
        help="Replay requests at a fixed QPS while preserving BurstGPT token distribution.",
    )
    parser.add_argument("--output-json", type=Path, default=None)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.time_scale is None:
        time_scale = 100.0 if args.scenario == "burst" else 1.0
    else:
        time_scale = max(args.time_scale, 1e-9)
    qps = None if args.qps is None else max(args.qps, 1e-9)

    result = simulate(
        dataset=args.dataset,
        limit=args.limit,
        scenario=args.scenario,
        time_scale=time_scale,
        local_bandwidth_mbps=args.local_bandwidth_mbps,
        cross_bandwidth_mbps=args.cross_bandwidth_mbps,
        compress_factor=args.compress_factor,
        decode_tokens_per_second=args.decode_tokens_per_second,
        qps=qps,
    )
    print_result(result)

    if args.output_json:
        args.output_json.parent.mkdir(parents=True, exist_ok=True)
        with args.output_json.open("w", encoding="utf-8") as handle:
            json.dump(asdict(result), handle, ensure_ascii=False, indent=2)
        print(f"wrote: {args.output_json}")


if __name__ == "__main__":
    main()
