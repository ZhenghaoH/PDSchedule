import bisect
import math
import pickle
import warnings
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from pdschedule.paths import controller_log_file, profile_root, runtime_root

from store.controller.network import Network
from store.controller.pool_base import DecodePool, PrefillPool
from utils.kv_transfer import (
    estimate_qwen25_7b_kv_transfer_bytes,
    estimate_transfer_time_ms,
)
from scheduler.controller.resource_state import ResourceSnapshot
from scheduler.controller.strategy import (
    GlobalScheduleContext,
    GlobalScheduleDecision,
)


PROJECT_ROOT = runtime_root()
GLOBAL_SCHEDULE_LOG_PATH = controller_log_file(PROJECT_ROOT / "global_schedule.log")
PROFILE_ROOT = profile_root() / "controller"

def _emit_debug_log(text: str) -> None:
    print(text)
    try:
        with GLOBAL_SCHEDULE_LOG_PATH.open("a", encoding="utf-8") as log_file:
            log_file.write(text)
            if not text.endswith("\n"):
                log_file.write("\n")
    except Exception as exc:
        print(f"[DemandAwareStrategy] failed to write debug log: {exc}")


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


def _finite_or_none(value) -> Optional[float]:
    """把缺失或非有限的观测归一成 None，供感知日志标 N/A。"""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


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


class DemandAwareStrategy:
    name = "demand_aware"

    def __init__(
        self,
        num_particles: int = 20,
        max_iterations: int = 15,
        ability_weight: float = 0.8,
        load_weight: float = 0.2,
    ):
        # num_particles / max_iterations 仅为兼容既有调用方保留，选路不再使用。
        del num_particles, max_iterations
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

    def _compatible_network_time_bounds_ms(
        self,
        token_length: int,
        compress_factor: float,
        compatible_ids: Set[Tuple[int, int]],
        network_map: Dict[Tuple[int, int], Network],
    ) -> Tuple[float, float]:
        """本轮合法候选池对的基础网络估时 (T_min, T_max)，不含预约等待；无有效估时返回 (inf, inf)。"""
        times = [
            self._network_time_ms(token_length, compress_factor, network_map[pair])
            for pair in compatible_ids
        ]
        valid = [net_time for net_time in times if math.isfinite(net_time) and net_time >= 0.0]
        if not valid:
            return math.inf, math.inf
        return min(valid), max(valid)

    def _network_match_loss(self, selected_time: float, min_time: float, max_time: float) -> float:
        """式 (12)：候选网络估时在本次请求内的 min–max 归一化，0 为本次最快，1 为本次最慢。"""
        if not math.isfinite(selected_time) or selected_time < 0.0:
            return 1.0
        if not math.isfinite(min_time) or not math.isfinite(max_time):
            return 1.0
        span = max_time - min_time
        if span <= 0.0:
            return 0.0
        return _clamp((selected_time - min_time) / span)

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
        net_time_bounds_ms: Tuple[float, float],
    ) -> Dict[str, float]:
        net = network_map.get((p_node.pool_id, d_node.pool_id))
        net_time_ms = self._network_time_ms(token_length, compress_factor, net)
        net_min_ms, net_max_ms = net_time_bounds_ms

        # 三阶段动态权重：P、网络、D 估时各占端到端估时的比重。
        prefill_time_ms, prefill_time_source = self._estimate_prefill_time_ms(token_length, p_node)
        decode_time_ms, decode_time_source = self._estimate_decode_time_ms(
            max_tokens,
            token_length,
            d_node,
        )
        safe_net_time_ms = max(0.0, net_time_ms if math.isfinite(net_time_ms) else 1e9)
        e2e_est_ms = max(prefill_time_ms + safe_net_time_ms + decode_time_ms, 1e-9)
        alpha_p = prefill_time_ms / e2e_est_ms
        alpha_n = safe_net_time_ms / e2e_est_ms
        alpha_d = decode_time_ms / e2e_est_ms

        p_loss = _capacity_loss(p_rank_map.get(p_node.pool_id, 0.5), prompt_rank)
        d_loss = _capacity_loss(d_rank_map.get(d_node.pool_id, 0.5), decode_rank)
        n_loss = self._network_match_loss(net_time_ms, net_min_ms, net_max_ms)
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
            "network_time_min_ms": net_min_ms,
            "network_time_max_ms": net_max_ms,
            "prefill_time_ms": prefill_time_ms,
            "prefill_time_source": prefill_time_source,
            "decode_time_ms": decode_time_ms,
            "decode_time_source": decode_time_source,
            "prefill_var": prefill_var,
            "decode_var": decode_var,
            "network_var": network_var,
            "prefill_load_after": prefill_vector[prefill_pools.index(p_node)],
            "decode_load_after": decode_vector[decode_pools.index(d_node)],
            "network_load_after": _clamp(self._network_load(net) + n_delta),
            "p_rank": p_rank_map.get(p_node.pool_id, 0.5),
            "d_rank": d_rank_map.get(d_node.pool_id, 0.5),
        }

    def _emit_debug_info(
        self,
        request_id: Optional[int],
        resources: ResourceSnapshot,
    ) -> None:
        """只输出快照中的池与链路感知状态，不输出候选分数或预约量。"""
        captured_at = datetime.fromtimestamp(
            float(getattr(resources, "captured_at", 0.0) or 0.0)
        ).isoformat(timespec="seconds")
        lines = [
            "+" * 100,
            "[DemandAwareStrategy] 调度感知状态",
            (
                f"snapshot: v{getattr(resources, 'version', 0)} captured_at={captured_at} "
                f"pools={len(resources.prefill_pools)}P/"
                f"{len(resources.decode_pools)}D "
                f"pairs={len(resources.compatible_pairs)}"
            ),
            f"request_id: {request_id if request_id is not None else 'N/A'}",
            "-" * 100,
        ]

        for pool in resources.prefill_pools:
            capacity = _finite_or_none(getattr(pool, "max_prompt_throughput", None))
            load = _finite_or_none(getattr(pool, "current_load", None))
            prompt_tps = (
                load * capacity
                if load is not None and capacity is not None and capacity > 0.0
                else None
            )
            lines.append(
                f"P{pool.pool_id} {pool.proxy_ip_address}:{pool.proxy_port} "
                f"prompt_tps={_format_float(prompt_tps, 1)}"
            )

        for pool in resources.decode_pools:
            lines.append(
                f"D{pool.pool_id} {pool.proxy_ip_address}:{pool.proxy_port} "
                f"running_reqs={int(getattr(pool, 'running_reqs', 0) or 0)} "
                f"load={_format_float(_finite_or_none(getattr(pool, 'current_load', None)))} "
                f"gen_tps=N/A"
            )

        for prefill, decode in resources.compatible_pairs:
            network = resources.network_map.get((prefill.pool_id, decode.pool_id))
            if network is None:
                lines.append(
                    f"link P{prefill.pool_id}->D{decode.pool_id} "
                    "bw=N/A peak=N/A rtt=N/A"
                )
                continue
            lines.append(
                f"link P{prefill.pool_id}->D{decode.pool_id} "
                f"bw={_format_float(_finite_or_none(getattr(network, 'bandwidth', None)), 1)}Mbps "
                f"peak={_format_float(_finite_or_none(getattr(network, 'max_bandwidth', None)), 1)}Mbps "
                f"rtt={_format_float(_finite_or_none(getattr(network, 'rtt', None)), 2)}ms"
            )

        lines.extend(["-" * 100, ""])
        _emit_debug_log("\n".join(lines))


    def schedule(
        self,
        context: GlobalScheduleContext,
        resources: ResourceSnapshot,
    ) -> GlobalScheduleDecision:
        token_length = context.token_length
        max_tokens = context.max_tokens
        compress_factor = context.compress_factor
        request_id = context.request_id
        prefill_pools = resources.prefill_pools
        decode_pools = resources.decode_pools
        network_map = resources.network_map
        if not prefill_pools or not decode_pools:
            raise ValueError("Empty pools passed to demand_aware schedule")
        candidates = [
            (prefill, decode)
            for prefill, decode in resources.compatible_pairs
            if (prefill.pool_id, decode.pool_id) in network_map
        ]
        if not candidates:
            raise ValueError(
                "No connector-compatible P/D gateway pair with a network"
            )

        safe_token_length = max(1, int(token_length or 0))
        safe_max_tokens = max(1, int(max_tokens or safe_token_length))
        self.prompt_ranker.update(safe_token_length)
        self.decode_ranker.update(safe_max_tokens)
        prompt_rank = self.prompt_ranker.get_rank(safe_token_length)
        decode_rank = self.decode_ranker.get_rank(safe_max_tokens)
        p_rank_map = self._calculate_pool_ranks(prefill_pools)
        d_rank_map = self._calculate_pool_ranks(decode_pools)
        compatible_ids = {
            (prefill.pool_id, decode.pool_id) for prefill, decode in candidates
        }
        net_time_bounds_ms = self._compatible_network_time_bounds_ms(
            safe_token_length,
            compress_factor,
            compatible_ids,
            network_map,
        )

        # 全量遍历：每个合法池对只评一次，同分保留先出现的候选。
        selected_p = None
        selected_d = None
        best_fitness = None
        for prefill, decode in candidates:
            components = self._adaptive_variance_components(
                safe_token_length,
                safe_max_tokens,
                prompt_rank,
                decode_rank,
                compress_factor,
                prefill,
                decode,
                prefill_pools,
                decode_pools,
                network_map,
                p_rank_map,
                d_rank_map,
                net_time_bounds_ms,
            )
            fitness = components["fitness"]
            if best_fitness is None or fitness < best_fitness:
                best_fitness = fitness
                selected_p = prefill
                selected_d = decode

        self._emit_debug_info(request_id, resources)
        return GlobalScheduleDecision(
            prefill_address=selected_p.proxy_ip_address,
            prefill_port=selected_p.proxy_port,
            decode_address=selected_d.proxy_ip_address,
            decode_port=selected_d.proxy_port,
            strategy=self.name,
            connector=selected_p.connector["name"],
            connector_descriptor=selected_p.connector,
        )
