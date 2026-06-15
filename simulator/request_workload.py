from __future__ import annotations

import argparse
import csv
import json
import random
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable, Literal, Optional, Union


ArrivalMode = Literal["all_at_once", "qps"]
PromptTokenSource = Literal["burstgpt", "synthetic"]
DEFAULT_BURSTGPT_CSV_PATH = Path(__file__).resolve().parent / "BurstGPT_1.csv"


@dataclass(frozen=True)
class GeneratedRequest:
    request_id: str
    arrival_ms: float
    prompt_tokens: int
    slo_ttft_ms: float

    def to_prefill_dict(self) -> dict:
        return asdict(self)


def generate_requests(
    num_requests: int = 50,
    seed: int = 0,
    arrival_mode: ArrivalMode = "qps",
    qps: int = 10,
    prompt_token_source: PromptTokenSource = "burstgpt",
    burstgpt_csv_path: Optional[Union[Path, str]] = DEFAULT_BURSTGPT_CSV_PATH,
    min_slo_ttft_ms: float = 100.0,
    max_slo_ttft_ms: float = 800.0,
) -> list[GeneratedRequest]:
    if num_requests <= 0:
        raise ValueError("num_requests must be positive")
    if arrival_mode not in ("all_at_once", "qps"):
        raise ValueError("arrival_mode must be 'all_at_once' or 'qps'")
    if prompt_token_source not in ("burstgpt", "synthetic"):
        raise ValueError("prompt_token_source must be 'burstgpt' or 'synthetic'")
    if qps <= 0:
        raise ValueError("qps must be positive")
    if min_slo_ttft_ms <= 0 or max_slo_ttft_ms < min_slo_ttft_ms:
        raise ValueError("SLO range is invalid")

    arrival_rng = random.Random(seed)
    payload_rng = random.Random(seed)
    arrivals = _generate_arrival_times(num_requests, arrival_rng, arrival_mode, qps)
    burstgpt_tokens = (
        load_burstgpt_request_tokens(num_requests, burstgpt_csv_path)
        if prompt_token_source == "burstgpt"
        else None
    )
    requests = []
    for index in range(num_requests):
        prompt_tokens = (
            burstgpt_tokens[index]
            if burstgpt_tokens is not None
            else _sample_prompt_tokens(payload_rng)
        )
        slo_ttft_ms = payload_rng.uniform(min_slo_ttft_ms, max_slo_ttft_ms)
        requests.append(
            GeneratedRequest(
                request_id=f"req-{index + 1:03d}",
                arrival_ms=arrivals[index],
                prompt_tokens=prompt_tokens,
                slo_ttft_ms=round(slo_ttft_ms, 6),
            )
        )
    return requests


def requests_to_dicts(requests: Iterable[GeneratedRequest]) -> list[dict]:
    return [asdict(req) for req in requests]


def load_burstgpt_request_tokens(
    num_tokens: int,
    csv_path: Optional[Union[Path, str]] = DEFAULT_BURSTGPT_CSV_PATH,
) -> list[int]:
    if num_tokens <= 0:
        raise ValueError("num_tokens must be positive")
    if csv_path is None:
        raise ValueError("csv_path is required for BurstGPT token loading")

    path = Path(csv_path)
    tokens = []
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)
        if "Request tokens" not in (reader.fieldnames or []):
            raise ValueError("BurstGPT CSV must contain 'Request tokens' column")
        for row in reader:
            try:
                value = int(float(row["Request tokens"]))
            except (TypeError, ValueError):
                continue
            if value <= 0:
                continue
            tokens.append(value)
            if len(tokens) >= num_tokens:
                break

    if len(tokens) < num_tokens:
        raise ValueError(
            f"BurstGPT CSV only contains {len(tokens)} valid Request tokens, "
            f"but {num_tokens} were requested"
        )
    return tokens


def _sample_prompt_tokens(rng: random.Random) -> int:
    bucket = rng.random()
    if bucket < 0.4:
        return rng.randint(256, 2048)
    if bucket < 0.8:
        return rng.randint(2049, 8192)
    return rng.randint(8193, 20000)


def _generate_arrival_times(
    num_requests: int,
    rng: random.Random,
    arrival_mode: ArrivalMode,
    qps: int,
) -> list[float]:
    if arrival_mode == "all_at_once":
        return [0.0 for _ in range(num_requests)]

    arrivals = []
    window_ms = 1000.0
    for window_index, start in enumerate(range(0, num_requests, qps)):
        count = min(qps, num_requests - start)
        window_start_ms = window_index * window_ms
        window_arrivals = sorted(rng.uniform(0.0, window_ms) for _ in range(count))
        arrivals.extend(round(window_start_ms + offset, 6) for offset in window_arrivals)
    return arrivals


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate reproducible heterogeneous simulator requests.")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--num-requests", type=int, default=50)
    parser.add_argument("--arrival-mode", choices=("all_at_once", "qps"), default="qps")
    parser.add_argument("--qps", type=int, default=10)
    parser.add_argument("--prompt-token-source", choices=("burstgpt", "synthetic"), default="burstgpt")
    parser.add_argument("--burstgpt-csv", type=Path, default=DEFAULT_BURSTGPT_CSV_PATH)
    parser.add_argument("--min-slo-ttft-ms", type=float, default=1500.0)
    parser.add_argument("--max-slo-ttft-ms", type=float, default=1500.0)
    parser.add_argument("--indent", type=int, default=2)
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    requests = generate_requests(
        num_requests=args.num_requests,
        seed=args.seed,
        arrival_mode=args.arrival_mode,
        qps=args.qps,
        prompt_token_source=args.prompt_token_source,
        burstgpt_csv_path=args.burstgpt_csv,
        min_slo_ttft_ms=args.min_slo_ttft_ms,
        max_slo_ttft_ms=args.max_slo_ttft_ms,
    )
    print(json.dumps({"requests": requests_to_dicts(requests)}, ensure_ascii=False, indent=args.indent))


if __name__ == "__main__":
    main()
