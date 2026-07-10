"""Lightweight vLLM continuous batching primitives for offline simulation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import List


@dataclass
class PrefillRuntimeRequest:
    request_id: str
    arrival_ms: float
    prompt_tokens: int
    remaining_tokens: int
    processed_tokens: int = 0


@dataclass(frozen=True)
class PrefillBatch:
    instance_id: str
    start_ms: float
    end_ms: float
    scheduled_tokens: int
    request_ids: List[str]
    completed_request_ids: List[str]


@dataclass
class DecodeRuntimeRequest:
    request_id: str
    kv_ready_ms: float
    prompt_tokens: int
    response_tokens: int
    generated_tokens: int = 0
    start_ms: float | None = None
    end_ms: float | None = None
    tpot_sum_ms: float = 0.0
    tpot_steps: int = 0


@dataclass(frozen=True)
class DecodeStep:
    instance_id: str
    start_ms: float
    end_ms: float
    batch_size: int
    request_ids: List[str]
