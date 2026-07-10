from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Deque, Optional, Union

try:
    from .ttft_model import DEFAULT_PREFILL_TTFT_MODEL_PATH, LinearTTFTModel, load_prefill_ttft_model
    from .vllm_v1_scheduler import BatchChunk, BatchRecord, RequestRecord, SimulationResult, VllmRequest
except ImportError:
    from ttft_model import DEFAULT_PREFILL_TTFT_MODEL_PATH, LinearTTFTModel, load_prefill_ttft_model
    from vllm_v1_scheduler import BatchChunk, BatchRecord, RequestRecord, SimulationResult, VllmRequest


@dataclass
class _RuntimeRequest:
    source: VllmRequest
    remaining_tokens: int
    processed_tokens: int = 0
    first_batch_id: Optional[int] = None
    chunk_count: int = 0
    sequence: int = 0


@dataclass
class _RunningBatch:
    record: BatchRecord
    completed_requests: list[_RuntimeRequest]


@dataclass(frozen=True)
class VllmQueuedRequestSnapshot:
    request_id: str
    arrival_ms: float
    remaining_tokens: int
    sequence: int


@dataclass(frozen=True)
class VllmQueueSnapshot:
    current_time_ms: float
    running_end_ms: Optional[float]
    queued_requests: list[VllmQueuedRequestSnapshot]


class VllmExecutionCalculator:
    """Online vLLM V1 prefill executor used by the external QueueManager.

    The offline `VllmV1SchedulerSimulator.simulate()` API replays a complete
    request list after the QueueManager is done. This calculator exposes the
    same chunked-prefill rules as an online engine so QueueManager events and
    vLLM batches share one execution timeline.
    """

    def __init__(
        self,
        max_num_batched_tokens: int = 10240,
        token_time_ms: float = 0.01,
        batch_overhead_ms: float = 0.0,
        ttft_model_path: Optional[Union[Path, str]] = DEFAULT_PREFILL_TTFT_MODEL_PATH,
    ) -> None:
        if max_num_batched_tokens <= 0:
            raise ValueError("max_num_batched_tokens must be positive")
        if token_time_ms < 0:
            raise ValueError("token_time_ms must be non-negative")
        if batch_overhead_ms < 0:
            raise ValueError("batch_overhead_ms must be non-negative")

        self.max_num_batched_tokens = max_num_batched_tokens
        self.token_time_ms = token_time_ms
        self.batch_overhead_ms = batch_overhead_ms
        self.ttft_model: Optional[LinearTTFTModel] = load_prefill_ttft_model(ttft_model_path)
        self.now_ms = 0.0
        self._incoming: list[_RuntimeRequest] = []
        self._pending: Deque[_RuntimeRequest] = deque()
        self._running: Optional[_RunningBatch] = None
        self._batches: list[BatchRecord] = []
        self._finished: dict[str, RequestRecord] = {}
        self._batch_id = 0
        self._sequence = 0

    @property
    def running_end_ms(self) -> Optional[float]:
        if self._running is None:
            return None
        return self._running.record.end_ms

    @property
    def has_pending_work(self) -> bool:
        return bool(self._pending or self._incoming)

    @property
    def has_waiting_work(self) -> bool:
        return bool(self._pending)

    @property
    def waiting_tokens(self) -> int:
        return sum(req.remaining_tokens for req in self._pending) + sum(
            req.remaining_tokens for req in self._incoming
        )

    @property
    def next_arrival_ms(self) -> Optional[float]:
        if not self._incoming:
            return None
        return self._incoming[0].source.arrival_ms

    def add_requests(self, requests: list[VllmRequest], now_ms: float) -> None:
        self.now_ms = max(self.now_ms, now_ms)
        for request in requests:
            self._sequence += 1
            runtime = _RuntimeRequest(
                source=request,
                remaining_tokens=request.prompt_tokens,
                sequence=self._sequence,
            )
            if request.arrival_ms <= self.now_ms:
                self._pending.append(runtime)
            else:
                self._incoming.append(runtime)
        self._incoming.sort(key=lambda item: (item.source.arrival_ms, item.sequence))

    def snapshot(self, current_time_ms: float) -> VllmQueueSnapshot:
        queued = [
            VllmQueuedRequestSnapshot(
                request_id=req.source.request_id,
                arrival_ms=req.source.arrival_ms,
                remaining_tokens=req.remaining_tokens,
                sequence=req.sequence,
            )
            for req in self._pending
        ]
        queued.extend(
            VllmQueuedRequestSnapshot(
                request_id=req.source.request_id,
                arrival_ms=req.source.arrival_ms,
                remaining_tokens=req.remaining_tokens,
                sequence=req.sequence,
            )
            for req in self._incoming
        )
        return VllmQueueSnapshot(
            current_time_ms=current_time_ms,
            running_end_ms=self.running_end_ms,
            queued_requests=queued,
        )

    def complete_running_if_due(self, now_ms: float) -> bool:
        if self._running is None or now_ms < self._running.record.end_ms:
            return False

        self.now_ms = self._running.record.end_ms
        self._batches.append(self._running.record)
        for req in self._running.completed_requests:
            source = req.source
            self._finished[source.request_id] = RequestRecord(
                request_id=source.request_id,
                arrival_ms=source.arrival_ms,
                prompt_tokens=source.prompt_tokens,
                first_batch_id=req.first_batch_id or self._running.record.batch_id,
                final_batch_id=self._running.record.batch_id,
                complete_time_ms=self._running.record.end_ms,
                ttft_ms=self._running.record.end_ms - source.arrival_ms,
                chunk_count=req.chunk_count,
            )
        self._running = None
        self._move_arrived_to_pending(self.now_ms)
        return True

    def start_next_batch_if_idle(self, now_ms: float) -> Optional[BatchRecord]:
        if self._running is not None:
            return None

        self.now_ms = max(self.now_ms, now_ms)
        self._move_arrived_to_pending(self.now_ms)
        if not self._pending:
            return None

        self._batch_id += 1
        start_ms = self.now_ms
        budget = self.max_num_batched_tokens
        chunks: list[BatchChunk] = []
        completed_in_batch: list[_RuntimeRequest] = []

        while self._pending and budget > 0:
            req = self._pending[0]
            if req.first_batch_id is None:
                req.first_batch_id = self._batch_id

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
                completed_in_batch.append(self._pending.popleft())
            else:
                break

        scheduled_tokens = sum(chunk.tokens for chunk in chunks)
        end_ms = start_ms + self._predict_duration_ms(scheduled_tokens)
        record = BatchRecord(
            batch_id=self._batch_id,
            start_ms=start_ms,
            end_ms=end_ms,
            scheduled_tokens=scheduled_tokens,
            chunks=chunks,
        )
        self._running = _RunningBatch(record=record, completed_requests=completed_in_batch)
        return record

    def finish_all(self) -> SimulationResult:
        while self._running is not None or self._pending or self._incoming:
            if self._running is not None:
                self.complete_running_if_due(self._running.record.end_ms)
                continue
            if not self._pending and self._incoming:
                self.now_ms = max(self.now_ms, self._incoming[0].source.arrival_ms)
            self.start_next_batch_if_idle(self.now_ms)
        return self.result()

    def result(self) -> SimulationResult:
        request_records = sorted(
            self._finished.values(),
            key=lambda item: (item.arrival_ms, item.request_id),
        )
        return SimulationResult(batches=list(self._batches), requests=request_records)

    def _move_arrived_to_pending(self, now_ms: float) -> None:
        ready = []
        remaining = []
        for request in self._incoming:
            if request.source.arrival_ms <= now_ms:
                ready.append(request)
            else:
                remaining.append(request)
        self._incoming = remaining
        for request in ready:
            self._pending.append(request)

    def _predict_duration_ms(self, total_tokens: int) -> float:
        if self.ttft_model is not None:
            return self.ttft_model.predict_ms(total_tokens)
        return self.batch_overhead_ms + total_tokens * self.token_time_ms
