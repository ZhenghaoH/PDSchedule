import argparse
import asyncio
import json
import os
import random
import statistics
import sys
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Optional

import aiohttp


DEFAULT_MODEL_FILE = (
    "/workspace/llm-stack/workspace_HZH/FlexPD/entrypoints/profile/"
    "TTFT_predictor/artifacts/prefill_ttft_regressor.pkl"
)
DEFAULT_MODEL = "/workspace/llm-stack/models/deepseek-ai/DeepSeek-R1-Distill-Qwen-7B"


@dataclass
class RequestSpec:
    batch_index: int
    request_index: int
    prompt_tokens: int
    output_tokens: int
    request_id: str
    prompt: str


@dataclass
class RequestResult:
    batch_index: int
    request_index: int
    prompt_tokens: int
    output_tokens: int
    request_id: str
    send_offset_ms: float
    ttft_ms: Optional[float]
    end_offset_ms: float
    status: Optional[int]
    error: str = ""

    @property
    def ttft_offset_ms(self) -> Optional[float]:
        if self.ttft_ms is None:
            return None
        return self.send_offset_ms + self.ttft_ms


@dataclass
class ClusterReport:
    outer_batch_index: int
    cluster_index: int
    request_indices: list[int]
    prompt_tokens: list[int]
    total_tokens: int
    predicted_ms: Optional[float]
    actual_ms: Optional[float]
    error_ms: Optional[float]
    abs_error_ms: Optional[float]
    ape_percent: Optional[float]
    ttft_offset_range_ms: Optional[float]


@dataclass
class BatchReport:
    batch_index: int
    prompt_tokens: list[int]
    clusters: list[ClusterReport]
    failed_requests: int
    results: list[RequestResult]


def positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("value must be positive")
    return parsed


def load_regressor(model_file: str) -> Any:
    if not Path(model_file).exists():
        raise FileNotFoundError(f"Model file does not exist: {model_file}")

    if __package__:
        from .prefill_regressor import PrefillTimeRegressor
    else:
        sys.path.append(str(Path(__file__).resolve().parent))
        from prefill_regressor import PrefillTimeRegressor

    regressor = PrefillTimeRegressor()
    if not regressor.load_from_file(model_file):
        raise RuntimeError(
            "Failed to load fitted one-feature prefill regressor. "
            "The file may be an old batch-size-aware model; please rerun warmup."
        )
    return regressor


def split_total_tokens(total_tokens: int, request_count: int, rng: random.Random) -> list[int]:
    if request_count > total_tokens:
        raise ValueError("request_count cannot exceed total_tokens")
    cuts = sorted(rng.sample(range(1, total_tokens), request_count - 1))
    parts = []
    previous = 0
    for cut in [*cuts, total_tokens]:
        parts.append(cut - previous)
        previous = cut
    rng.shuffle(parts)
    return parts


def generate_prompt_with_exact_tokens(tokenizer: Any, target_tokens: int, marker: str) -> str:
    if target_tokens <= 0:
        return ""

    base_text = (
        f"[{marker}] "
        "This is a heterogeneous prefill predictor validation request. "
    )
    if tokenizer is None:
        return "probe " * target_tokens

    base_ids = tokenizer.encode(base_text, add_special_tokens=False)
    filler_ids = tokenizer.encode(
        "The quick validation sentence carries deterministic filler tokens. ",
        add_special_tokens=False,
    )
    if not filler_ids:
        filler_ids = base_ids or [0]

    if len(base_ids) >= target_tokens:
        token_ids = base_ids[:target_tokens]
    else:
        missing = target_tokens - len(base_ids)
        token_ids = base_ids + (filler_ids * ((missing // len(filler_ids)) + 1))[:missing]

    prompt = tokenizer.decode(token_ids, clean_up_tokenization_spaces=False)
    for _ in range(8):
        current_ids = tokenizer.encode(prompt, add_special_tokens=False)
        current_len = len(current_ids)
        if current_len == target_tokens:
            return prompt
        if current_len > target_tokens:
            current_ids = current_ids[:target_tokens]
        else:
            missing = target_tokens - current_len
            current_ids = current_ids + (
                filler_ids * ((missing // len(filler_ids)) + 1)
            )[:missing]
        prompt = tokenizer.decode(current_ids, clean_up_tokenization_spaces=False)
    return prompt


def build_random_batches(args: argparse.Namespace) -> list[list[int]]:
    rng = random.Random(args.seed)
    batches = []
    for _ in range(args.num_batches):
        total_tokens = rng.randint(args.min_total_tokens, args.max_total_tokens)
        max_requests = min(args.max_requests, total_tokens)
        min_requests = min(args.min_requests, max_requests)
        request_count = rng.randint(min_requests, max_requests)
        batches.append(split_total_tokens(total_tokens, request_count, rng))
    return batches


def build_specs(
    token_batches: list[list[int]],
    tokenizer: Any,
    output_tokens: int,
) -> list[list[RequestSpec]]:
    all_specs = []
    run_id = uuid.uuid4().hex[:10]
    for batch_index, prompt_lengths in enumerate(token_batches, start=1):
        specs = []
        for request_index, prompt_tokens in enumerate(prompt_lengths):
            marker = f"{run_id}:b{batch_index}:r{request_index}"
            request_id = f"prefill-validate-{marker}"
            prompt = generate_prompt_with_exact_tokens(tokenizer, prompt_tokens, marker)
            specs.append(
                RequestSpec(
                    batch_index=batch_index,
                    request_index=request_index,
                    prompt_tokens=prompt_tokens,
                    output_tokens=output_tokens,
                    request_id=request_id,
                    prompt=prompt,
                )
            )
        all_specs.append(specs)
    return all_specs


async def send_request(
    session: aiohttp.ClientSession,
    url: str,
    model: str,
    spec: RequestSpec,
    api_key: str,
    global_start: float,
) -> RequestResult:
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "X-Request-Id": spec.request_id,
    }
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": spec.prompt}],
        "stream": True,
        "max_tokens": spec.output_tokens,
        "temperature": 0,
    }

    send_start = time.perf_counter()
    first_chunk: Optional[float] = None
    status: Optional[int] = None
    error = ""

    try:
        async with session.post(url, headers=headers, json=payload) as response:
            status = response.status
            if response.status < 200 or response.status >= 300:
                error = await response.text()
            else:
                async for chunk in response.content.iter_any():
                    if chunk and first_chunk is None:
                        first_chunk = time.perf_counter()
    except Exception as exc:
        error = repr(exc)

    end_time = time.perf_counter()
    ttft_ms = None if first_chunk is None else (first_chunk - send_start) * 1000.0
    return RequestResult(
        batch_index=spec.batch_index,
        request_index=spec.request_index,
        prompt_tokens=spec.prompt_tokens,
        output_tokens=spec.output_tokens,
        request_id=spec.request_id,
        send_offset_ms=(send_start - global_start) * 1000.0,
        ttft_ms=ttft_ms,
        end_offset_ms=(end_time - global_start) * 1000.0,
        status=status,
        error=error,
    )


async def run_batches(
    args: argparse.Namespace,
    spec_batches: list[list[RequestSpec]],
) -> list[list[RequestResult]]:
    url = f"http://{args.host}:{args.port}/v1/chat/completions"
    api_key = os.environ.get("OPENAI_API_KEY", "EMPTY")
    timeout = aiohttp.ClientTimeout(total=args.timeout_seconds)
    all_results = []

    async with aiohttp.ClientSession(timeout=timeout) as session:
        global_start = time.perf_counter()
        for batch_offset, specs in enumerate(spec_batches):
            print(
                f"\nSending batch {specs[0].batch_index}: "
                f"tokens={[spec.prompt_tokens for spec in specs]}"
            )
            tasks = [
                asyncio.create_task(
                    send_request(session, url, args.model, spec, api_key, global_start)
                )
                for spec in specs
            ]
            results = await asyncio.gather(*tasks)
            all_results.append(sorted(results, key=lambda item: item.request_index))
            if (
                args.batch_interval_seconds > 0
                and batch_offset < len(spec_batches) - 1
            ):
                print(
                    f"Waiting {args.batch_interval_seconds:.3f}s "
                    "before next batch..."
                )
                await asyncio.sleep(args.batch_interval_seconds)
    return all_results


def make_batch_report(
    regressor: Any,
    batch_index: int,
    results: list[RequestResult],
    cluster_tolerance_ms: float,
) -> BatchReport:
    prompt_tokens = [result.prompt_tokens for result in results]
    failed_requests = sum(1 for result in results if result.ttft_ms is None)
    successful_results = [
        result for result in results if result.ttft_offset_ms is not None
    ]
    successful_results.sort(key=lambda result: result.ttft_offset_ms or 0.0)

    raw_clusters: list[list[RequestResult]] = []
    for result in successful_results:
        if not raw_clusters:
            raw_clusters.append([result])
            continue

        previous_offset = raw_clusters[-1][-1].ttft_offset_ms
        current_offset = result.ttft_offset_ms
        if (
            previous_offset is not None
            and current_offset is not None
            and current_offset - previous_offset <= cluster_tolerance_ms
        ):
            raw_clusters[-1].append(result)
        else:
            raw_clusters.append([result])

    clusters: list[ClusterReport] = []
    min_send_offset_ms = (
        min(result.send_offset_ms for result in successful_results)
        if successful_results
        else None
    )
    previous_cluster_mean_offset_ms = None

    for cluster_index, cluster_results in enumerate(raw_clusters, start=1):
        offsets = [
            result.ttft_offset_ms
            for result in cluster_results
            if result.ttft_offset_ms is not None
        ]
        cluster_mean_offset_ms = statistics.mean(offsets)
        if previous_cluster_mean_offset_ms is None:
            actual_ms = (
                cluster_mean_offset_ms - min_send_offset_ms
                if min_send_offset_ms is not None
                else None
            )
        else:
            actual_ms = cluster_mean_offset_ms - previous_cluster_mean_offset_ms
        previous_cluster_mean_offset_ms = cluster_mean_offset_ms

        cluster_prompt_tokens = [result.prompt_tokens for result in cluster_results]
        total_tokens = sum(cluster_prompt_tokens)
        predicted_ms = regressor.predict(1, total_tokens) * 1000.0
        error_ms = None
        abs_error_ms = None
        ape_percent = None
        if actual_ms is not None:
            error_ms = predicted_ms - actual_ms
            abs_error_ms = abs(error_ms)
            if actual_ms > 0:
                ape_percent = abs_error_ms / actual_ms * 100.0

        clusters.append(
            ClusterReport(
                outer_batch_index=batch_index,
                cluster_index=cluster_index,
                request_indices=[result.request_index for result in cluster_results],
                prompt_tokens=cluster_prompt_tokens,
                total_tokens=total_tokens,
                predicted_ms=predicted_ms,
                actual_ms=actual_ms,
                error_ms=error_ms,
                abs_error_ms=abs_error_ms,
                ape_percent=ape_percent,
                ttft_offset_range_ms=max(offsets) - min(offsets) if offsets else None,
            )
        )

    return BatchReport(
        batch_index=batch_index,
        prompt_tokens=prompt_tokens,
        clusters=clusters,
        failed_requests=failed_requests,
        results=results,
    )


def fmt_float(value: Optional[float], digits: int = 3) -> str:
    if value is None:
        return ""
    return f"{value:.{digits}f}"


def print_reports(reports: list[BatchReport]) -> None:
    headers = [
        ("batch", 6),
        ("cluster", 8),
        ("req_idx", 14),
        ("tokens", 28),
        ("total", 8),
        ("pred_ms", 10),
        ("actual_ms", 10),
        ("err_ms", 10),
        ("abs_ms", 10),
        ("ape_%", 9),
        ("span_ms", 9),
    ]

    print("\nPREDICTION VALIDATION")
    print("-" * 140)
    print(" ".join(name.ljust(width) for name, width in headers))
    print("-" * 140)
    for report in reports:
        print(f"outer_batch={report.batch_index} all_tokens={report.prompt_tokens}")
        if not report.clusters:
            print("  no successful requests to cluster")
            continue
        for cluster in report.clusters:
            values = [
                cluster.outer_batch_index,
                cluster.cluster_index,
                cluster.request_indices,
                cluster.prompt_tokens,
                cluster.total_tokens,
                fmt_float(cluster.predicted_ms),
                fmt_float(cluster.actual_ms),
                fmt_float(cluster.error_ms),
                fmt_float(cluster.abs_error_ms),
                fmt_float(cluster.ape_percent),
                fmt_float(cluster.ttft_offset_range_ms),
            ]
            print(
                " ".join(
                    str(value)[:width].ljust(width)
                    for value, (_, width) in zip(values, headers)
                )
            )

    valid_abs_errors = [
        cluster.abs_error_ms
        for report in reports
        for cluster in report.clusters
        if cluster.abs_error_ms is not None
    ]
    valid_apes = [
        cluster.ape_percent
        for report in reports
        for cluster in report.clusters
        if cluster.ape_percent is not None
    ]
    failed_requests = sum(report.failed_requests for report in reports)
    total_clusters = sum(len(report.clusters) for report in reports)

    print("\nSUMMARY")
    print("-" * 140)
    print(f"valid_clusters={len(valid_abs_errors)} / {total_clusters}")
    print(f"outer_batches={len(reports)}")
    print(f"failed_requests={failed_requests}")
    if valid_abs_errors:
        print(f"MAE={statistics.mean(valid_abs_errors):.3f} ms")
        print(f"MaxAbsError={max(valid_abs_errors):.3f} ms")
    else:
        print("MAE=n/a")
        print("MaxAbsError=n/a")
    if valid_apes:
        print(f"MAPE={statistics.mean(valid_apes):.3f}%")
    else:
        print("MAPE=n/a")


def write_json(path: str, reports: list[BatchReport]) -> None:
    payload = []
    for report in reports:
        item = asdict(report)
        payload.append(item)
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"\nWrote JSON results to {output_path}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Validate the prefill time predictor with random heterogeneous "
            "batches sent serially to a vLLM OpenAI-compatible endpoint."
        )
    )
    parser.add_argument("--host", default="172.18.0.170")
    parser.add_argument("--port", type=int, default=8007)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--model-file", default=DEFAULT_MODEL_FILE)
    parser.add_argument("--num-batches", type=positive_int, default=10)
    parser.add_argument("--min-total-tokens", type=positive_int, default=512)
    parser.add_argument("--max-total-tokens", type=positive_int, default=10000)
    parser.add_argument("--min-requests", type=positive_int, default=2)
    parser.add_argument("--max-requests", type=positive_int, default=8)
    parser.add_argument("--max-output-tokens", type=positive_int, default=1)
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--cluster-tolerance-ms", type=float, default=20.0)
    parser.add_argument(
        "--split-warning-ms",
        type=float,
        default=None,
        help=argparse.SUPPRESS,
    )
    parser.add_argument("--timeout-seconds", type=float, default=600.0)
    parser.add_argument(
        "--batch-interval-seconds",
        type=float,
        default=0.0,
        help="Seconds to wait between completed outer batches.",
    )
    parser.add_argument("--output-json", default="")
    return parser


def validate_args(args: argparse.Namespace) -> None:
    if args.min_total_tokens > args.max_total_tokens:
        raise ValueError("--min-total-tokens cannot exceed --max-total-tokens")
    if args.min_requests > args.max_requests:
        raise ValueError("--min-requests cannot exceed --max-requests")
    if args.min_requests < 2:
        raise ValueError("--min-requests must be at least 2")
    if args.min_total_tokens < args.max_requests:
        raise ValueError("--min-total-tokens must be >= --max-requests")
    if args.split_warning_ms is not None:
        args.cluster_tolerance_ms = args.split_warning_ms
    if args.cluster_tolerance_ms < 0:
        raise ValueError("--cluster-tolerance-ms cannot be negative")
    if args.timeout_seconds <= 0:
        raise ValueError("--timeout-seconds must be positive")
    if args.batch_interval_seconds < 0:
        raise ValueError("--batch-interval-seconds cannot be negative")


async def main_async(args: argparse.Namespace) -> int:
    validate_args(args)
    regressor = load_regressor(args.model_file)

    tokenizer = None
    try:
        from transformers import AutoTokenizer

        tokenizer = AutoTokenizer.from_pretrained(args.model)
    except Exception as exc:
        print(f"WARNING: tokenizer load failed: {exc}")
        print("WARNING: transformers is unavailable; prompt lengths are approximate.")

    token_batches = build_random_batches(args)
    spec_batches = build_specs(token_batches, tokenizer, args.max_output_tokens)
    result_batches = await run_batches(args, spec_batches)

    reports = [
        make_batch_report(regressor, index, results, args.cluster_tolerance_ms)
        for index, results in enumerate(result_batches, start=1)
    ]
    print_reports(reports)

    if args.output_json:
        write_json(args.output_json, reports)
    return 0


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        return asyncio.run(main_async(args))
    except KeyboardInterrupt:
        print("\nInterrupted.")
        return 130
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
