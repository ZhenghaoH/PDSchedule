import bisect
import math
import pickle
import time
import warnings
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

import numpy as np

from store.controller.network import Network
from store.controller.pool_base import DecodePool, PrefillPool
from utils.kv_transfer import estimate_qwen25_7b_kv_transfer_bytes, estimate_transfer_time_ms


GLOBAL_SCHEDULE_LOG_PATH = Path(__file__).resolve().parents[2] / "global_schedule.log"
PROFILE_ROOT = Path(__file__).resolve().parents[2] / "simulator" / "profile"


def _emit_debug_log(text: str) -> None:
    print(text)
    try:
        with GLOBAL_SCHEDULE_LOG_PATH.open("a", encoding="utf-8") as log_file:
            log_file.write(text)
            if not text.endswith("\n"):
                log_file.write("\n")
    except Exception as exc:
        print(f"[AdaptiveVarianceScheduler] failed to write debug log: {exc}")


def _clamp(value: float, lower: float = 0.0, upper: float = 1.0) -> float:
    return min(max(float(value), lower), upper)


def _format_float(value, digits: int = 4) -> str:
    if value is None:
        return "N/A"
    try:
        if math.isinf(value):
            return "INF"
        return f"{float(value):.{digits}f}"
    except Exception:
        return str(value)


def _variance(values: List[float]) -> float:
    if not values:
        return 0.0
    avg = sum(values) / len(values)
    return sum((value - avg) ** 2 for value in values) / len(values)


def _capacity_loss(capacity_rank: float, request_rank: float) -> float:
    diff = _clamp(capacity_rank) - _clamp(request_rank)
    if diff >= 0:
        return _clamp(0.5 * diff)
    return _clamp(abs(diff))


class RequestRanker:
    def __init__(self, window_size: int = 2000):
        self.window_size = window_size
        self.history_queue = deque()
        self.sorted_values = []

    def update(self, value: int) -> None:
        if len(self.history_queue) >= self.window_size:
            old_value = self.history_queue.popleft()
            idx = bisect.bisect_left(self.sorted_values, old_value)
            if idx < len(self.sorted_values) and self.sorted_values[idx] == old_value:
                self.sorted_values.pop(idx)
        self.history_queue.append(value)
        bisect.insort(self.sorted_values, value)

    def get_rank(self, value: int) -> float:
        if not self.sorted_values:
            return 0.5
        idx = bisect.bisect_left(self.sorted_values, value)
        return _clamp(idx / len(self.sorted_values), 0.01, 0.99)


@dataclass(frozen=True)
class TTFTProfile:
    seconds_per_token: float
    intercept_seconds: float

    def predict_ms(self, tokens: int) -> float:
        return max(
            0.0,
            (max(0, int(tokens)) * self.seconds_per_token + self.intercept_seconds) * 1000.0,
        )


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


class AdaptiveVarianceScheduler:
    def __init__(
        self,
        num_particles: int = 20,
        max_iterations: int = 15,
        ability_weight: float = 0.8,
        load_weight: float = 0.2,
    ):
        self.num_particles = num_particles
        self.max_iterations = max_iterations
        self.w_inertia = 0.5
        self.c1 = 1.4
        self.c2 = 1.4
        self.ability_weight = ability_weight
        self.load_weight = load_weight
        self.prompt_ranker = RequestRanker(window_size=2000)
        self.decode_ranker = RequestRanker(window_size=2000)
        self._ttft_profiles: Dict[str, TTFTProfile] = {}
        self._tpot_profiles: Dict[str, TPOTProfile] = {}
        self._missing_profile_keys: Set[Tuple[str, str]] = set()

    def _calculate_pool_ranks(self, pools: List) -> Dict[int, float]:
        if not pools:
            return {}
        capacities = [
            max(float(getattr(pool, "flops", getattr(pool, "mem_bandwidth", 0.0)) or 0.0), 0.0)
            for pool in pools
        ]
        max_capacity = max(capacities)
        if max_capacity <= 0:
            return {pool.pool_id: 0.5 for pool in pools}
        return {
            pool.pool_id: _clamp(capacity / max_capacity, 0.01, 0.99)
            for pool, capacity in zip(pools, capacities)
        }

    def _network_time_ms(self, token_length: int, compress_factor: float, net: Optional[Network]) -> float:
        if net is None:
            return float("inf")
        bandwidth = float(getattr(net, "bandwidth", 0.0) or 0.0)
        if bandwidth <= 0:
            return float("inf")
        return float(getattr(net, "rtt", 0.0) or 0.0) + estimate_transfer_time_ms(
            token_length,
            compress_factor,
            bandwidth,
        )

    def _network_time_bounds(
        self,
        token_length: int,
        compress_factor: float,
        prefill_pools: List[PrefillPool],
        decode_pools: List[DecodePool],
        network_map: Dict[Tuple[int, int], Network],
    ) -> Tuple[float, float]:
        times = []
        for p_node in prefill_pools:
            for d_node in decode_pools:
                net = network_map.get((p_node.pool_id, d_node.pool_id))
                net_time = self._network_time_ms(token_length, compress_factor, net)
                if math.isfinite(net_time):
                    times.append(net_time)
        if not times:
            return 0.0, 0.0
        return min(times), max(times)

    def _network_capacity_rank(self, selected_time: float, min_time: float, max_time: float) -> float:
        if not math.isfinite(selected_time):
            return 0.01
        denominator = max_time - min_time
        if denominator <= 1e-9:
            normalized = 0.0
        else:
            normalized = (selected_time - min_time) / denominator
        return _clamp(1.0 - normalized, 0.01, 0.99)

    def _network_load(self, net: Optional[Network]) -> float:
        if net is None:
            return 1.0
        max_bandwidth = float(getattr(net, "max_bandwidth", 0.0) or 0.0)
        bandwidth = float(getattr(net, "bandwidth", 0.0) or 0.0)
        if max_bandwidth <= 0:
            return 0.0
        return _clamp(1.0 - bandwidth / max_bandwidth)

    def _network_delta(self, token_length: int, compress_factor: float, net: Optional[Network], net_time_ms: float) -> float:
        if net is None or not math.isfinite(net_time_ms) or net_time_ms <= 0:
            return 1.0
        max_bandwidth = float(getattr(net, "max_bandwidth", 0.0) or 0.0)
        if max_bandwidth <= 0:
            return 0.0
        transfer_bytes = estimate_qwen25_7b_kv_transfer_bytes(token_length, compress_factor)
        estimated_mbps = (transfer_bytes * 8.0) / max(net_time_ms, 1e-9) / 1000.0
        return _clamp(estimated_mbps / max_bandwidth)

    def _max_prompt_tokens(self, pool: PrefillPool) -> Tuple[float, str]:
        explicit = float(getattr(pool, "max_prompt_tokens", 0.0) or 0.0)
        if explicit > 0:
            return explicit, "max_prompt_tokens"
        throughput = float(getattr(pool, "max_prompt_throughput", 0.0) or 0.0)
        if throughput > 0:
            return throughput, "max_prompt_throughput"
        return 1.0, "fallback_1"

    def _prefill_load(self, pool: PrefillPool) -> float:
        explicit_tokens = float(getattr(pool, "prompt_tokens", 0.0) or 0.0)
        max_tokens, _source = self._max_prompt_tokens(pool)
        if explicit_tokens > 0:
            return _clamp(explicit_tokens / max_tokens)
        return _clamp(float(getattr(pool, "current_load", 0.0) or 0.0))

    def _decode_load(self, pool: DecodePool) -> float:
        max_reqs = max(1.0, float(getattr(pool, "max_decode_req_num", 0.0) or 0.0))
        running_reqs = float(getattr(pool, "running_reqs", 0.0) or 0.0)
        if running_reqs > 0:
            return _clamp(running_reqs / max_reqs)
        return _clamp(float(getattr(pool, "current_load", 0.0) or 0.0))

    def _infer_machine_id(self, pool) -> Optional[str]:
        for attr in ("machine", "machine_id", "hostname"):
            value = getattr(pool, attr, None)
            if value is not None:
                text = str(value).strip()
                if text:
                    return text.split("_")[-1].split("-")[-1]
        address = str(getattr(pool, "proxy_ip_address", "") or "").strip()
        if address:
            tail = address.split(".")[-1]
            if tail:
                return tail
        return None

    def _profile_path(self, machine_id: str, kind: str) -> Path:
        if kind == "ttft":
            return PROFILE_ROOT / f"profile_{machine_id}" / "TTFT_predictor" / "artifacts" / "prefill_ttft_regressor.pkl"
        return PROFILE_ROOT / f"profile_{machine_id}" / "TPOT_predictor" / "artifacts" / "decode_tpot_regressor.pkl"

    def _load_ttft_profile(self, pool: PrefillPool) -> Optional[TTFTProfile]:
        machine_id = self._infer_machine_id(pool)
        if not machine_id:
            return None
        key = ("ttft", machine_id)
        if key in self._ttft_profiles:
            return self._ttft_profiles[key]
        if key in self._missing_profile_keys:
            return None
        path = self._profile_path(machine_id, "ttft")
        try:
            with path.open("rb") as handle:
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    state = pickle.load(handle)
            coeffs = state.get("coeffs", {}) if isinstance(state, dict) else {}
            profile = TTFTProfile(float(coeffs["tokens"]), float(coeffs["d"]))
        except Exception:
            self._missing_profile_keys.add(key)
            return None
        self._ttft_profiles[key] = profile
        return profile

    def _load_tpot_profile(self, pool: DecodePool) -> Optional[TPOTProfile]:
        machine_id = self._infer_machine_id(pool)
        if not machine_id:
            return None
        key = ("tpot", machine_id)
        if key in self._tpot_profiles:
            return self._tpot_profiles[key]
        if key in self._missing_profile_keys:
            return None
        path = self._profile_path(machine_id, "tpot")
        try:
            with path.open("rb") as handle:
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    state = pickle.load(handle)
            coeffs = state.get("coeffs", {}) if isinstance(state, dict) else {}
            profile = TPOTProfile(
                float(coeffs["d1"]),
                float(coeffs["d2"]),
                float(coeffs["d3"]),
                float(coeffs["d4"]),
            )
        except Exception:
            self._missing_profile_keys.add(key)
            return None
        self._tpot_profiles[key] = profile
        return profile

    def _estimate_prefill_time_ms(self, prompt_tokens: int, pool: PrefillPool) -> Tuple[float, str]:
        profile = self._load_ttft_profile(pool)
        if profile is not None:
            return profile.predict_ms(prompt_tokens), "profile"
        throughput, _source = self._max_prompt_tokens(pool)
        return (max(1, int(prompt_tokens)) / max(throughput, 1e-9)) * 1000.0, "fallback"

    def _estimate_decode_time_ms(self, max_tokens: int, prompt_tokens: int, pool: DecodePool) -> Tuple[float, str]:
        profile = self._load_tpot_profile(pool)
        if profile is not None:
            step_ms = profile.predict_step_ms(batch_size=1, context_len=prompt_tokens)
            return max(1, int(max_tokens)) * step_ms, "profile"
        capacity = max(float(getattr(pool, "mem_bandwidth", 0.0) or 0.0), 1e-9)
        return (max(1, int(max_tokens)) / capacity) * 1000.0, "fallback"

    def _adaptive_variance_components(
        self,
        token_length: int,
        max_tokens: int,
        prompt_rank: float,
        decode_rank: float,
        compress_factor: float,
        p_node: PrefillPool,
        d_node: DecodePool,
        prefill_pools: List[PrefillPool],
        decode_pools: List[DecodePool],
        network_map: Dict[Tuple[int, int], Network],
        p_rank_map: Dict[int, float],
        d_rank_map: Dict[int, float],
        min_net_time_ms: float,
        max_net_time_ms: float,
    ) -> Dict[str, float]:
        net = network_map.get((p_node.pool_id, d_node.pool_id))
        net_time_ms = self._network_time_ms(token_length, compress_factor, net)
        network_rank = self._network_capacity_rank(net_time_ms, min_net_time_ms, max_net_time_ms)

        p_time_ms, p_time_source = self._estimate_prefill_time_ms(token_length, p_node)
        d_time_ms, d_time_source = self._estimate_decode_time_ms(max_tokens, token_length, d_node)
        safe_net_time = max(0.0, net_time_ms if math.isfinite(net_time_ms) else 1e9)
        e2e_est_ms = max(p_time_ms + safe_net_time + d_time_ms, 1e-9)
        alpha_p = p_time_ms / e2e_est_ms
        alpha_n = safe_net_time / e2e_est_ms
        alpha_d = d_time_ms / e2e_est_ms

        p_loss = _capacity_loss(p_rank_map.get(p_node.pool_id, 0.5), prompt_rank)
        d_loss = _capacity_loss(d_rank_map.get(d_node.pool_id, 0.5), decode_rank)
        n_loss = _capacity_loss(network_rank, prompt_rank)
        ability_loss = alpha_p * p_loss + alpha_n * n_loss + alpha_d * d_loss

        p_delta = _clamp(token_length / max(1.0, self._max_prompt_tokens(p_node)[0]))
        d_delta = _clamp(1.0 / max(1.0, float(getattr(d_node, "max_decode_req_num", 0.0) or 0.0)))
        n_delta = self._network_delta(token_length, compress_factor, net, net_time_ms)

        prefill_vector = [
            _clamp(self._prefill_load(pool) + (p_delta if pool.pool_id == p_node.pool_id else 0.0))
            for pool in prefill_pools
        ]
        decode_vector = [
            _clamp(self._decode_load(pool) + (d_delta if pool.pool_id == d_node.pool_id else 0.0))
            for pool in decode_pools
        ]
        network_vector = []
        for p_candidate in prefill_pools:
            for d_candidate in decode_pools:
                candidate_net = network_map.get((p_candidate.pool_id, d_candidate.pool_id))
                load = self._network_load(candidate_net)
                if p_candidate.pool_id == p_node.pool_id and d_candidate.pool_id == d_node.pool_id:
                    load += n_delta
                network_vector.append(_clamp(load))

        prefill_var = _variance(prefill_vector)
        decode_var = _variance(decode_vector)
        network_var = _variance(network_vector)
        load_loss = (prefill_var + decode_var + network_var) / 3.0
        fitness = self.ability_weight * ability_loss + self.load_weight * load_loss

        return {
            "fitness": fitness,
            "ability_loss": ability_loss,
            "load_loss": load_loss,
            "p_loss": p_loss,
            "d_loss": d_loss,
            "n_loss": n_loss,
            "alpha_p": alpha_p,
            "alpha_n": alpha_n,
            "alpha_d": alpha_d,
            "network_time_ms": net_time_ms,
            "network_rank": network_rank,
            "prefill_time_ms": p_time_ms,
            "prefill_time_source": p_time_source,
            "decode_time_ms": d_time_ms,
            "decode_time_source": d_time_source,
            "prefill_var": prefill_var,
            "decode_var": decode_var,
            "network_var": network_var,
            "prefill_load_after": prefill_vector[prefill_pools.index(p_node)],
            "decode_load_after": decode_vector[decode_pools.index(d_node)],
            "network_load_after": _clamp(self._network_load(net) + n_delta),
            "p_rank": p_rank_map.get(p_node.pool_id, 0.5),
            "d_rank": d_rank_map.get(d_node.pool_id, 0.5),
        }

    def _calculate_fitness(
        self,
        particle_pos: np.ndarray,
        token_length: int,
        max_tokens: int,
        prompt_rank: float,
        decode_rank: float,
        compress_factor: float,
        prefill_pools: List[PrefillPool],
        decode_pools: List[DecodePool],
        network_map: Dict[Tuple[int, int], Network],
        p_rank_map: Dict[int, float],
        d_rank_map: Dict[int, float],
        min_net_time_ms: float,
        max_net_time_ms: float,
    ) -> float:
        p_idx = int(np.floor(particle_pos[0])) % len(prefill_pools)
        d_idx = int(np.floor(particle_pos[1])) % len(decode_pools)
        p_node = prefill_pools[p_idx]
        d_node = decode_pools[d_idx]
        if (p_node.pool_id, d_node.pool_id) not in network_map:
            return 1e9
        return self._adaptive_variance_components(
            token_length,
            max_tokens,
            prompt_rank,
            decode_rank,
            compress_factor,
            p_node,
            d_node,
            prefill_pools,
            decode_pools,
            network_map,
            p_rank_map,
            d_rank_map,
            min_net_time_ms,
            max_net_time_ms,
        )["fitness"]

    def _emit_debug_info(
        self,
        request_id: Optional[int],
        token_length: int,
        max_tokens: int,
        max_tokens_fallback: bool,
        slo_ttft: float,
        slo_tpot: float,
        compress_factor: float,
        prompt_rank: float,
        decode_rank: float,
        prefill_pools: List[PrefillPool],
        decode_pools: List[DecodePool],
        network_map: Dict[Tuple[int, int], Network],
        p_rank_map: Dict[int, float],
        d_rank_map: Dict[int, float],
        min_net_time_ms: float,
        max_net_time_ms: float,
        selected_p: PrefillPool,
        selected_d: DecodePool,
        selected_components: Dict[str, float],
    ) -> None:
        lines = [
            "+" * 100,
            "[AdaptiveVarianceScheduler] 全局调度调试信息",
            "+" * 100,
            f"日志时间: {datetime.now().isoformat(timespec='seconds')}",
            f"Request_ID: {request_id if request_id is not None else 'N/A'}",
            f"prompt_tokens: {token_length}",
            f"max_tokens: {max_tokens}",
            f"max_tokens_fallback: {'YES' if max_tokens_fallback else 'NO'}",
            f"prompt_rank: {_format_float(prompt_rank)}",
            f"decode_rank: {_format_float(decode_rank)}",
            f"SLO_TTFT: {slo_ttft} ms",
            f"SLO_TPOT: {slo_tpot} ms",
            f"compress_factor: {compress_factor}",
            "-" * 100,
            "候选pair分解:",
            (
                "Pair | p_time_ms | p_time_src | d_time_ms | d_time_src | "
                "p_rank | d_rank | net_rank | p_loss | d_loss | n_loss | "
                "alpha_p | alpha_n | alpha_d | ability | load | fitness | "
                "p_load_after | d_load_after | n_load_after"
            ),
        ]

        for p_node in prefill_pools:
            for d_node in decode_pools:
                if (p_node.pool_id, d_node.pool_id) not in network_map:
                    continue
                comp = self._adaptive_variance_components(
                    token_length,
                    max_tokens,
                    prompt_rank,
                    decode_rank,
                    compress_factor,
                    p_node,
                    d_node,
                    prefill_pools,
                    decode_pools,
                    network_map,
                    p_rank_map,
                    d_rank_map,
                    min_net_time_ms,
                    max_net_time_ms,
                )
                lines.append(
                    f"P{p_node.pool_id}->D{d_node.pool_id} | "
                    f"{_format_float(comp['prefill_time_ms'])} | {comp['prefill_time_source']} | "
                    f"{_format_float(comp['decode_time_ms'])} | {comp['decode_time_source']} | "
                    f"{_format_float(comp['p_rank'])} | {_format_float(comp['d_rank'])} | "
                    f"{_format_float(comp['network_rank'])} | "
                    f"{_format_float(comp['p_loss'])} | {_format_float(comp['d_loss'])} | {_format_float(comp['n_loss'])} | "
                    f"{_format_float(comp['alpha_p'])} | {_format_float(comp['alpha_n'])} | {_format_float(comp['alpha_d'])} | "
                    f"{_format_float(comp['ability_loss'])} | {_format_float(comp['load_loss'])} | {_format_float(comp['fitness'])} | "
                    f"{_format_float(comp['prefill_load_after'])} | {_format_float(comp['decode_load_after'])} | "
                    f"{_format_float(comp['network_load_after'])}"
                )

        lines.extend(
            [
                "-" * 100,
                "最终选择:",
                (
                    f"selected_prefill=P{selected_p.pool_id} "
                    f"({selected_p.proxy_ip_address}:{selected_p.proxy_port})"
                ),
                (
                    f"selected_decode=D{selected_d.pool_id} "
                    f"({selected_d.proxy_ip_address}:{selected_d.proxy_port})"
                ),
                f"selected_fitness: {_format_float(selected_components['fitness'])}",
                f"selected_ability_loss: {_format_float(selected_components['ability_loss'])}",
                f"selected_load_loss: {_format_float(selected_components['load_loss'])}",
                f"selected_prefill_time_ms: {_format_float(selected_components['prefill_time_ms'])}",
                f"selected_prefill_time_source: {selected_components['prefill_time_source']}",
                f"selected_decode_time_ms: {_format_float(selected_components['decode_time_ms'])}",
                f"selected_decode_time_source: {selected_components['decode_time_source']}",
                "+" * 100,
                "",
            ]
        )
        _emit_debug_log("\n".join(lines))

    def schedule(
        self,
        token_length: int,
        max_tokens: int,
        slo_ttft: float,
        slo_tpot: float,
        compress_factor: float,
        prefill_pools: List[PrefillPool],
        decode_pools: List[DecodePool],
        network_map: Dict[Tuple[int, int], Network],
        request_id: Optional[int] = None,
        max_tokens_fallback: bool = False,
    ) -> Tuple[str, int, str, int]:
        num_p = len(prefill_pools)
        num_d = len(decode_pools)
        if num_p == 0 or num_d == 0:
            raise ValueError("Empty pools passed to adaptive_variance schedule")

        safe_token_length = max(1, int(token_length or 0))
        safe_max_tokens = max(1, int(max_tokens or safe_token_length))
        self.prompt_ranker.update(safe_token_length)
        self.decode_ranker.update(safe_max_tokens)
        prompt_rank = self.prompt_ranker.get_rank(safe_token_length)
        decode_rank = self.decode_ranker.get_rank(safe_max_tokens)
        p_rank_map = self._calculate_pool_ranks(prefill_pools)
        d_rank_map = self._calculate_pool_ranks(decode_pools)
        min_net_time_ms, max_net_time_ms = self._network_time_bounds(
            safe_token_length,
            compress_factor,
            prefill_pools,
            decode_pools,
            network_map,
        )

        positions = np.random.rand(self.num_particles, 2)
        positions[:, 0] *= num_p
        positions[:, 1] *= num_d
        velocities = np.random.rand(self.num_particles, 2) * 0.1
        p_best_pos = positions.copy()
        p_best_scores = np.full(self.num_particles, np.inf)
        g_best_pos = positions[0].copy()
        g_best_score = np.inf

        for _ in range(self.max_iterations):
            for i in range(self.num_particles):
                score = self._calculate_fitness(
                    positions[i],
                    safe_token_length,
                    safe_max_tokens,
                    prompt_rank,
                    decode_rank,
                    compress_factor,
                    prefill_pools,
                    decode_pools,
                    network_map,
                    p_rank_map,
                    d_rank_map,
                    min_net_time_ms,
                    max_net_time_ms,
                )
                if score < p_best_scores[i]:
                    p_best_scores[i] = score
                    p_best_pos[i] = positions[i].copy()
                if score < g_best_score:
                    g_best_score = score
                    g_best_pos = positions[i].copy()

            r1 = np.random.rand(self.num_particles, 2)
            r2 = np.random.rand(self.num_particles, 2)
            velocities = (
                self.w_inertia * velocities
                + self.c1 * r1 * (p_best_pos - positions)
                + self.c2 * r2 * (g_best_pos - positions)
            )
            positions += velocities
            positions[:, 0] = np.clip(positions[:, 0], 0, num_p - 1e-6)
            positions[:, 1] = np.clip(positions[:, 1], 0, num_d - 1e-6)

        best_p_idx = int(np.floor(g_best_pos[0])) % num_p
        best_d_idx = int(np.floor(g_best_pos[1])) % num_d
        selected_p = prefill_pools[best_p_idx]
        selected_d = decode_pools[best_d_idx]
        selected_components = self._adaptive_variance_components(
            safe_token_length,
            safe_max_tokens,
            prompt_rank,
            decode_rank,
            compress_factor,
            selected_p,
            selected_d,
            prefill_pools,
            decode_pools,
            network_map,
            p_rank_map,
            d_rank_map,
            min_net_time_ms,
            max_net_time_ms,
        )
        self._emit_debug_info(
            request_id,
            safe_token_length,
            safe_max_tokens,
            max_tokens_fallback,
            slo_ttft,
            slo_tpot,
            compress_factor,
            prompt_rank,
            decode_rank,
            prefill_pools,
            decode_pools,
            network_map,
            p_rank_map,
            d_rank_map,
            min_net_time_ms,
            max_net_time_ms,
            selected_p,
            selected_d,
            selected_components,
        )
        return (
            selected_p.proxy_ip_address,
            selected_p.proxy_port,
            selected_d.proxy_ip_address,
            selected_d.proxy_port,
        )
