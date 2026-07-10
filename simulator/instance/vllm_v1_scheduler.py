from __future__ import annotations

import argparse
import csv
import json
from collections import deque
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Deque, Iterable, Literal, Optional, Union

try:
    from .ttft_model import DEFAULT_PREFILL_TTFT_MODEL_PATH, LinearTTFTModel, load_prefill_ttft_model
except ImportError:
    from ttft_model import DEFAULT_PREFILL_TTFT_MODEL_PATH, LinearTTFTModel, load_prefill_ttft_model


IdleBatchingPolicy = Literal["first", "same_time", "window"]


@dataclass(frozen=True)
class VllmRequest:
    """Input request for the vLLM V1 prefill scheduler simulator."""

    request_id: str
    arrival_ms: float
    prompt_tokens: int

    def __post_init__(self) -> None:
        if self.prompt_tokens <= 0:
            raise ValueError("prompt_tokens must be positive")
        if self.arrival_ms < 0:
            raise ValueError("arrival_ms must be non-negative")


@dataclass(frozen=True)
class BatchChunk:
    request_id: str
    token_start: int
    token_end: int
    tokens: int
    completed: bool


@dataclass(frozen=True)
class BatchRecord:
    batch_id: int
    start_ms: float
    end_ms: float
    scheduled_tokens: int
    chunks: list[BatchChunk]


@dataclass(frozen=True)
class RequestRecord:
    request_id: str
    arrival_ms: float
    prompt_tokens: int
    first_batch_id: int
    final_batch_id: int
    complete_time_ms: float
    ttft_ms: float
    chunk_count: int


@dataclass(frozen=True)
class SimulationResult:
    batches: list[BatchRecord]
    requests: list[RequestRecord]

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class _RuntimeRequest:
    source: VllmRequest
    remaining_tokens: int
    processed_tokens: int = 0
    first_batch_id: Optional[int] = None
    chunk_count: int = 0


class VllmV1SchedulerSimulator:
    """Discrete-event simulator for the vLLM V1 chunked-prefill rules.

    The model follows the rules in `vllm v1调度规则.docx`:
    - FCFS ordering.
    - One batch has a `max_num_batched_tokens` prefill budget.
    - Over-budget requests are split and the unfinished suffix stays at the
      front of the next batch.
    - Arrivals during a running batch wait until that batch finishes.
    - Finished prefill requests emit a KV-ready notification at batch end.
    """

    def __init__(
        self,
        max_num_batched_tokens: int = 10240,
        arrival_window_ms: float = 20.0,
        token_time_ms: float = 0.01,
        batch_overhead_ms: float = 0.0,
        idle_batching: IdleBatchingPolicy = "first",
        ttft_model_path: Optional[Union[Path, str]] = DEFAULT_PREFILL_TTFT_MODEL_PATH,
    ) -> None:
        if max_num_batched_tokens <= 0:
            raise ValueError("max_num_batched_tokens must be positive")
        if arrival_window_ms < 0:
            raise ValueError("arrival_window_ms must be non-negative")
        if token_time_ms < 0:
            raise ValueError("token_time_ms must be non-negative")
        if batch_overhead_ms < 0:
            raise ValueError("batch_overhead_ms must be non-negative")
        if idle_batching not in ("first", "same_time", "window"):
            raise ValueError("idle_batching must be one of: first, same_time, window")

        self.max_num_batched_tokens = max_num_batched_tokens
        self.arrival_window_ms = arrival_window_ms
        self.token_time_ms = token_time_ms
        self.batch_overhead_ms = batch_overhead_ms
        self.idle_batching = idle_batching
        self.ttft_model: Optional[LinearTTFTModel] = load_prefill_ttft_model(ttft_model_path)

    def simulate(self, requests: Iterable[VllmRequest]) -> SimulationResult:
        incoming = [
            _RuntimeRequest(source=req, remaining_tokens=req.prompt_tokens)
            for req in sorted(requests, key=lambda item: item.arrival_ms)
        ]
        pending: Deque[_RuntimeRequest] = deque()
        batches: list[BatchRecord] = []
        finished: dict[str, RequestRecord] = {}
        now_ms = 0.0
        next_index = 0
        batch_id = 0

        while next_index < len(incoming) or pending:
            if not pending:
                now_ms = max(now_ms, incoming[next_index].source.arrival_ms)
                first_arrival_ms = incoming[next_index].source.arrival_ms
                pending.append(incoming[next_index])
                next_index += 1

                if self.idle_batching != "first":
                    cutoff_ms = first_arrival_ms
                    if self.idle_batching == "window":
                        cutoff_ms += self.arrival_window_ms
                        now_ms = cutoff_ms
                    while (
                        next_index < len(incoming)
                        and incoming[next_index].source.arrival_ms <= cutoff_ms
                    ):
                        pending.append(incoming[next_index])
                        next_index += 1
            else:
                while (
                    next_index < len(incoming)
                    and incoming[next_index].source.arrival_ms <= now_ms
                ):
                    pending.append(incoming[next_index])
                    next_index += 1

            batch_id += 1
            start_ms = now_ms
            budget = self.max_num_batched_tokens
            chunks: list[BatchChunk] = []
            completed_in_batch: list[_RuntimeRequest] = []

            while pending and budget > 0:
                req = pending[0]
                if req.first_batch_id is None:
                    req.first_batch_id = batch_id

                tokens = min(req.remaining_tokens, budget)
                token_start = req.processed_tokens
                req.processed_tokens += tokens
                req.remaining_tokens -= tokens
                req.chunk_count += 1
                budget -= tokens

                completed = req.remaining_tokens == 0
                chunks.append(
                    BatchChunk(
                        request_id=req.source.request_id,
                        token_start=token_start,
                        token_end=req.processed_tokens,
                        tokens=tokens,
                        completed=completed,
                    )
                )
                if completed:
                    completed_in_batch.append(pending.popleft())
                else:
                    break

            scheduled_tokens = sum(chunk.tokens for chunk in chunks)
            end_ms = start_ms + self._predict_duration_ms(scheduled_tokens)
            batches.append(
                BatchRecord(
                    batch_id=batch_id,
                    start_ms=start_ms,
                    end_ms=end_ms,
                    scheduled_tokens=scheduled_tokens,
                    chunks=chunks,
                )
            )

            for req in completed_in_batch:
                source = req.source
                finished[source.request_id] = RequestRecord(
                    request_id=source.request_id,
                    arrival_ms=source.arrival_ms,
                    prompt_tokens=source.prompt_tokens,
                    first_batch_id=req.first_batch_id or batch_id,
                    final_batch_id=batch_id,
                    complete_time_ms=end_ms,
                    ttft_ms=end_ms - source.arrival_ms,
                    chunk_count=req.chunk_count,
                )

            now_ms = end_ms
            while next_index < len(incoming) and incoming[next_index].source.arrival_ms <= now_ms:
                pending.append(incoming[next_index])
                next_index += 1

        ordered_request_records = [
            finished[req.source.request_id]
            for req in sorted(incoming, key=lambda item: item.source.arrival_ms)
        ]
        return SimulationResult(batches=batches, requests=ordered_request_records)

    def _predict_duration_ms(self, total_tokens: int) -> float:
        if self.ttft_model is not None:
            return self.ttft_model.predict_ms(total_tokens)
        return self.batch_overhead_ms + total_tokens * self.token_time_ms


def load_requests_csv(path: Path) -> list[VllmRequest]:
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        rows = csv.DictReader(file)
        return [
            VllmRequest(
                request_id=str(row["request_id"]),
                arrival_ms=float(row["arrival_ms"]),
                prompt_tokens=int(row["prompt_tokens"]),
            )
            for row in rows
        ]


def load_requests_json(path: Path) -> list[VllmRequest]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict):
        data = data["requests"]
    return [
        VllmRequest(
            request_id=str(item["request_id"]),
            arrival_ms=float(item["arrival_ms"]),
            prompt_tokens=int(item["prompt_tokens"]),
        )
        for item in data
    ]


def _demo_requests() -> list[VllmRequest]:
    return [
        VllmRequest("id1", 0.0, 5120),
        VllmRequest("id2", 5.0, 5120),
        VllmRequest("id3", 8.0, 7680),
        VllmRequest("id4", 140.0, 4096),
        VllmRequest("id5", 240.0, 4096),
    ]


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Simulate vLLM V1 chunked-prefill scheduling.")
    parser.add_argument("--input", type=Path, help="CSV or JSON request file.")
    parser.add_argument("--max-num-batched-tokens", type=int, default=10240)
    parser.add_argument("--arrival-window-ms", type=float, default=20.0)
    parser.add_argument("--token-time-ms", type=float, default=0.01)
    parser.add_argument("--batch-overhead-ms", type=float, default=0.0)
    parser.add_argument("--ttft-model", type=Path, default=DEFAULT_PREFILL_TTFT_MODEL_PATH)
    parser.add_argument(
        "--idle-batching",
        choices=("first", "same_time", "window"),
        default="first",
        help="How to batch requests when the engine is idle.",
    )
    parser.add_argument("--indent", type=int, default=2)
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    if args.input is None:
        requests = _demo_requests()
    elif args.input.suffix.lower() == ".csv":
        requests = load_requests_csv(args.input)
    else:
        requests = load_requests_json(args.input)

    simulator = VllmV1SchedulerSimulator(
        max_num_batched_tokens=args.max_num_batched_tokens,
        arrival_window_ms=args.arrival_window_ms,
        token_time_ms=args.token_time_ms,
        batch_overhead_ms=args.batch_overhead_ms,
        idle_batching=args.idle_batching,
        ttft_model_path=args.ttft_model,
    )
    print(json.dumps(simulator.simulate(requests).to_dict(), ensure_ascii=False, indent=args.indent))


if __name__ == "__main__":
    main()
