from __future__ import annotations

import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal, Optional, Union

try:
    from ..gateway.prefill_pso_scheduler import (
        PrefillScheduleResult,
    )
    from ..gateway.queue_manager import DispatchRecord, QueueManager, QueueManagerResult, SimRequestState
    from ..dataset.request_workload import DEFAULT_BURSTGPT_CSV_PATH, GeneratedRequest, generate_requests, requests_to_dicts
    from ..gateway.scheduling_strategies import PSOSchedulingStrategy
    from ..instance.ttft_model import DEFAULT_PREFILL_TTFT_MODEL_PATH
    from ..instance.vllm_v1_scheduler import SimulationResult, VllmRequest, VllmV1SchedulerSimulator
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from simulator.gateway.prefill_pso_scheduler import (
        PrefillScheduleResult,
    )
    from simulator.gateway.queue_manager import DispatchRecord, QueueManager, QueueManagerResult, SimRequestState
    from simulator.dataset.request_workload import DEFAULT_BURSTGPT_CSV_PATH, GeneratedRequest, generate_requests, requests_to_dicts
    from simulator.gateway.scheduling_strategies import PSOSchedulingStrategy
    from simulator.instance.ttft_model import DEFAULT_PREFILL_TTFT_MODEL_PATH
    from simulator.instance.vllm_v1_scheduler import SimulationResult, VllmRequest, VllmV1SchedulerSimulator


Strategy = Literal["direct", "pso", "both"]
DEFAULT_LATEST_RESULT_PATH = Path(__file__).resolve().parents[1] / "result" / "latest_result.json"


@dataclass(frozen=True)
class RequestGoodputRecord:
    request_id: str
    strategy: str
    original_arrival_ms: float
    scheduled_arrival_ms: float
    dispatch_time_ms: float
    prompt_tokens: int
    slo_ttft_ms: float
    deadline_ms: float
    complete_time_ms: float
    ttft_ms: float
    met_slo: bool
    batch_order: Optional[int]


@dataclass(frozen=True)
class GoodputSummary:
    strategy: str
    total_requests: int
    met_slo_requests: int
    missed_slo_requests: int
    slo_attainment_rate: float
    total_prompt_tokens: int
    goodput_tokens: int
    goodput_ratio: float
    avg_ttft_ms: float
    max_ttft_ms: float


@dataclass(frozen=True)
class StrategyRun:
    strategy: str
    summary: GoodputSummary
    requests: list[RequestGoodputRecord]
    vllm_result: SimulationResult
    queue_result: Optional[QueueManagerResult] = None
    pso_result: Optional[PrefillScheduleResult] = None

    def to_dict(self) -> dict:
        data = {
            "strategy": self.strategy,
            "summary": asdict(self.summary),
            "requests": [asdict(req) for req in self.requests],
            "vllm_result": self.vllm_result.to_dict(),
        }
        if self.queue_result is not None:
            data["queue_result"] = self.queue_result.to_dict()
        if self.pso_result is not None:
            data["pso_result"] = self.pso_result.to_dict()
        return data


def run_comparison(
    seed: int = 0,
    num_requests: int = 50,
    arrival_mode: str = "qps",
    qps: int = 10,
    prompt_token_source: str = "burstgpt",
    burstgpt_csv_path: Optional[Union[Path, str]] = DEFAULT_BURSTGPT_CSV_PATH,
    min_slo_ttft_ms: float = 100.0,
    max_slo_ttft_ms: float = 800.0,
    strategy: Strategy = "both",
    max_num_batched_tokens: int = 10240,
    arrival_window_ms: float = 20.0,
    token_time_ms: float = 0.01,
    batch_overhead_ms: float = 0.0,
    idle_batching: str = "first",
    ttft_model_path: Optional[Union[Path, str]] = DEFAULT_PREFILL_TTFT_MODEL_PATH,
    pso_particles: int = 30,
    pso_iterations: int = 20,
    pso_random_seed: Optional[int] = None,
) -> dict:
    requests = generate_requests(
        num_requests=num_requests,
        seed=seed,
        arrival_mode=arrival_mode,
        qps=qps,
        prompt_token_source=prompt_token_source,
        burstgpt_csv_path=burstgpt_csv_path,
        min_slo_ttft_ms=min_slo_ttft_ms,
        max_slo_ttft_ms=max_slo_ttft_ms,
    )
    vllm = VllmV1SchedulerSimulator(
        max_num_batched_tokens=max_num_batched_tokens,
        arrival_window_ms=arrival_window_ms,
        token_time_ms=token_time_ms,
        batch_overhead_ms=batch_overhead_ms,
        idle_batching=idle_batching,
        ttft_model_path=ttft_model_path,
    )

    runs = {}
    if strategy in ("direct", "both"):
        runs["direct"] = run_direct_strategy(requests, vllm)
    if strategy in ("pso", "both"):
        runs["pso"] = run_pso_strategy(
            requests,
            vllm,
            max_batch_tokens=max_num_batched_tokens,
            token_time_ms=token_time_ms,
            batch_overhead_ms=batch_overhead_ms,
            pso_particles=pso_particles,
            pso_iterations=pso_iterations,
            random_seed=seed if pso_random_seed is None else pso_random_seed,
            ttft_model_path=ttft_model_path,
        )

    output = {
        "seed": seed,
        "num_requests": num_requests,
        "arrival": {
            "mode": arrival_mode,
            "qps": qps,
        },
        "prompt_tokens": {
            "source": prompt_token_source,
            "csv_path": str(Path(burstgpt_csv_path)) if burstgpt_csv_path is not None else None,
            "loaded_rows": num_requests if prompt_token_source == "burstgpt" else None,
        },
        "slo": {
            "min_ttft_ms": min_slo_ttft_ms,
            "max_ttft_ms": max_slo_ttft_ms,
        },
        "workload": requests_to_dicts(requests),
        "runs": {name: run.to_dict() for name, run in runs.items()},
    }
    if "direct" in runs and "pso" in runs:
        output["comparison"] = compare_summaries(runs["direct"].summary, runs["pso"].summary)
    return output


def run_direct_strategy(
    requests: list[GeneratedRequest],
    vllm: VllmV1SchedulerSimulator,
) -> StrategyRun:
    queue_result = _build_direct_queue_result(requests)
    result = vllm.simulate(queue_result.vllm_requests)
    dispatch_times = {
        state.request_id: state.dispatch_time_ms
        for state in queue_result.request_states
        if state.dispatch_time_ms is not None
    }
    batch_orders = {
        state.request_id: state.batch_order
        for state in queue_result.request_states
        if state.batch_order is not None
    }
    records, summary = calculate_goodput("direct", requests, result, batch_orders, dispatch_times)
    return StrategyRun(
        strategy="direct",
        summary=summary,
        requests=records,
        vllm_result=result,
        queue_result=queue_result,
    )


def _build_direct_queue_result(requests: list[GeneratedRequest]) -> QueueManagerResult:
    request_states = []
    dispatch_records = []
    vllm_requests = []
    for index, req in enumerate(sorted(requests, key=lambda item: (item.arrival_ms, item.request_id)), start=1):
        state = SimRequestState.from_request(req)
        state.status = "dispatched"
        state.batch_order = index
        state.prefill_instance = "prefill-0"
        state.planned_start_ms = req.arrival_ms
        state.planned_finish_ms = req.arrival_ms
        state.batch_tokens = req.prompt_tokens
        state.plan_position = index - 1
        state.dispatch_time_ms = req.arrival_ms
        request_states.append(state)
        dispatch_records.append(
            DispatchRecord(
                dispatch_order=index,
                strategy="direct",
                dispatch_time_ms=req.arrival_ms,
                request_ids=[req.request_id],
                total_tokens=req.prompt_tokens,
                prefill_instance="prefill-0",
                planned_start_ms=req.arrival_ms,
                planned_finish_ms=req.arrival_ms,
            )
        )
        vllm_requests.append(
            VllmRequest(req.request_id, req.arrival_ms, req.prompt_tokens)
        )
    return QueueManagerResult(
        strategy="direct",
        dispatch_records=dispatch_records,
        request_states=request_states,
        vllm_requests=vllm_requests,
        pso_results=[],
    )


def run_pso_strategy(
    requests: list[GeneratedRequest],
    vllm: VllmV1SchedulerSimulator,
    max_batch_tokens: int,
    token_time_ms: float,
    batch_overhead_ms: float,
    pso_particles: int,
    pso_iterations: int,
    random_seed: int,
    ttft_model_path: Optional[Union[Path, str]],
) -> StrategyRun:
    queue_result = QueueManager(
        PSOSchedulingStrategy(
            max_batch_tokens=max_batch_tokens,
            token_time_ms=token_time_ms,
            batch_overhead_ms=batch_overhead_ms,
            pso_particles=pso_particles,
            pso_iterations=pso_iterations,
            random_seed=random_seed,
            ttft_model_path=ttft_model_path,
        ),
        max_num_batched_tokens=max_batch_tokens,
        token_time_ms=token_time_ms,
        batch_overhead_ms=batch_overhead_ms,
        ttft_model_path=ttft_model_path,
    ).run(requests)
    result = queue_result.vllm_result or vllm.simulate(queue_result.vllm_requests)
    dispatch_times = {
        state.request_id: state.dispatch_time_ms
        for state in queue_result.request_states
        if state.dispatch_time_ms is not None
    }
    batch_orders = {
        state.request_id: state.batch_order
        for state in queue_result.request_states
        if state.batch_order is not None
    }
    records, summary = calculate_goodput("pso", requests, result, batch_orders, dispatch_times)
    pso_result = queue_result.pso_results[-1] if queue_result.pso_results else None
    return StrategyRun(
        strategy="pso",
        summary=summary,
        requests=records,
        vllm_result=result,
        queue_result=queue_result,
        pso_result=pso_result,
    )


def scheduled_arrivals_from_pso(
    pso_result: PrefillScheduleResult,
    original_requests: list[GeneratedRequest],
) -> dict[str, float]:
    original_arrivals = {req.request_id: req.arrival_ms for req in original_requests}
    scheduled = {}
    current_arrival_ms = 0.0
    for batch in sorted(pso_result.batches, key=lambda item: item.batch_order):
        for request_id in batch.request_ids:
            if request_id in scheduled:
                continue
            scheduled_arrival_ms = max(current_arrival_ms, original_arrivals[request_id])
            scheduled[request_id] = round(scheduled_arrival_ms, 6)
            current_arrival_ms = scheduled_arrival_ms + 0.001
    return scheduled


def calculate_goodput(
    strategy: str,
    original_requests: list[GeneratedRequest],
    vllm_result: SimulationResult,
    batch_orders: dict[str, int],
    scheduled_arrivals: Optional[dict[str, float]] = None,
) -> tuple[list[RequestGoodputRecord], GoodputSummary]:
    scheduled_arrivals = scheduled_arrivals or {
        req.request_id: req.arrival_ms for req in original_requests
    }
    original_by_id = {req.request_id: req for req in original_requests}
    vllm_by_id = {req.request_id: req for req in vllm_result.requests}

    records = []
    total_prompt_tokens = 0
    goodput_tokens = 0
    ttfts = []
    for request_id in [req.request_id for req in original_requests]:
        original = original_by_id[request_id]
        actual = vllm_by_id[request_id]
        deadline_ms = original.arrival_ms + original.slo_ttft_ms
        met_slo = actual.complete_time_ms <= deadline_ms
        total_prompt_tokens += original.prompt_tokens
        if met_slo:
            goodput_tokens += original.prompt_tokens
        ttfts.append(actual.complete_time_ms - original.arrival_ms)
        records.append(
            RequestGoodputRecord(
                request_id=request_id,
                strategy=strategy,
                original_arrival_ms=original.arrival_ms,
                scheduled_arrival_ms=scheduled_arrivals[request_id],
                dispatch_time_ms=scheduled_arrivals[request_id],
                prompt_tokens=original.prompt_tokens,
                slo_ttft_ms=original.slo_ttft_ms,
                deadline_ms=deadline_ms,
                complete_time_ms=actual.complete_time_ms,
                ttft_ms=actual.complete_time_ms - original.arrival_ms,
                met_slo=met_slo,
                batch_order=batch_orders.get(request_id),
            )
        )

    total_requests = len(records)
    met_slo_requests = sum(1 for record in records if record.met_slo)
    avg_ttft_ms = sum(ttfts) / len(ttfts) if ttfts else 0.0
    max_ttft_ms = max(ttfts) if ttfts else 0.0
    summary = GoodputSummary(
        strategy=strategy,
        total_requests=total_requests,
        met_slo_requests=met_slo_requests,
        missed_slo_requests=total_requests - met_slo_requests,
        slo_attainment_rate=met_slo_requests / total_requests if total_requests else 0.0,
        total_prompt_tokens=total_prompt_tokens,
        goodput_tokens=goodput_tokens,
        goodput_ratio=goodput_tokens / total_prompt_tokens if total_prompt_tokens else 0.0,
        avg_ttft_ms=avg_ttft_ms,
        max_ttft_ms=max_ttft_ms,
    )
    return records, summary


def compare_summaries(direct: GoodputSummary, pso: GoodputSummary) -> dict:
    return {
        "goodput_tokens_delta": pso.goodput_tokens - direct.goodput_tokens,
        "goodput_ratio_delta": pso.goodput_ratio - direct.goodput_ratio,
        "met_slo_requests_delta": pso.met_slo_requests - direct.met_slo_requests,
        "slo_attainment_rate_delta": pso.slo_attainment_rate - direct.slo_attainment_rate,
        "avg_ttft_ms_delta": pso.avg_ttft_ms - direct.avg_ttft_ms,
    }


def save_latest_result(
    result: dict,
    output_path: Optional[Union[Path, str]] = None,
    indent: int = 2,
) -> Path:
    path = Path(output_path) if output_path is not None else DEFAULT_LATEST_RESULT_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(result, ensure_ascii=False, indent=indent),
        encoding="utf-8",
    )
    return path


def load_latest_result(output_path: Optional[Union[Path, str]] = None) -> dict:
    path = Path(output_path) if output_path is not None else DEFAULT_LATEST_RESULT_PATH
    return json.loads(path.read_text(encoding="utf-8"))
