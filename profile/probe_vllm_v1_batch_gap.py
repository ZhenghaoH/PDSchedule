import argparse
import asyncio
import json
import os
import time
import uuid
from dataclasses import dataclass
from typing import Any, List, Optional, Sequence


DEFAULT_BATCH1_SHAPE = "128,512,2048,4096"
DEFAULT_BATCH2_SHAPE = "256,768,1536,3072"
DEFAULT_BATCH3_SHAPE = "256,768,1536,3072"
OUTPUT_TOKENS = 1


@dataclass
class RequestSpec:
    batch_name: str
    index: int
    prompt_tokens: int
    output_tokens: int
    marker: str
    prompt: str = ""


@dataclass
class RequestResult:
    marker: str
    batch_name: str
    index: int
    prompt_tokens: int
    output_tokens: int
    request_id: str
    response_header_request_id: str
    response_id: str
    global_start_perf: float
    send_start_perf: float
    first_chunk_perf: Optional[float]
    end_perf: float
    status: Optional[int]
    error: str = ""

    @property
    def ttft_ms(self) -> Optional[float]:
        if self.first_chunk_perf is None:
            return None
        return (self.first_chunk_perf - self.send_start_perf) * 1000.0

    @property
    def send_offset_ms(self) -> float:
        return (self.send_start_perf - self.global_start_perf) * 1000.0

    @property
    def ttft_offset_ms(self) -> Optional[float]:
        if self.first_chunk_perf is None:
            return None
        return (self.first_chunk_perf - self.global_start_perf) * 1000.0


def parse_prompt_lengths(shape: str) -> List[int]:
    lengths: List[int] = []
    for raw_part in shape.split(","):
        part = raw_part.strip()
        if not part:
            continue
        try:
            prompt_tokens = int(part)
        except ValueError as exc:
            raise argparse.ArgumentTypeError(
                f"Invalid shape item '{part}', expected prompt token count"
            ) from exc
        if prompt_tokens <= 0:
            raise argparse.ArgumentTypeError("Prompt token counts must be positive")
        lengths.append(prompt_tokens)
    if not lengths:
        raise argparse.ArgumentTypeError("Shape must contain at least one prompt length")
    return lengths


def generate_prompt(target_tokens: int, marker: str, model: Optional[str]) -> str:
    prefix = f"[{uuid.uuid4()}] batch-probe:{marker}\n"
    body_target = max(1, target_tokens - 16)
    base_text = "This is a scheduler batch probing request with controlled token length. "

    try:
        from transformers import AutoTokenizer

        tokenizer = AutoTokenizer.from_pretrained(model or "gpt2")
        base_ids = tokenizer.encode(base_text, add_special_tokens=False) or [0]
        body_ids = (base_ids * ((body_target // len(base_ids)) + 2))[:body_target]
        body = tokenizer.decode(body_ids, clean_up_tokenization_spaces=False)
        return prefix + body
    except Exception:
        return prefix + ("probe " * body_target)


async def detect_model(base_url: str) -> Optional[str]:
    import aiohttp

    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(f"{base_url}/models") as response:
                if response.status != 200:
                    return None
                data = await response.json()
                return data.get("data", [{}])[0].get("id")
        except Exception:
            return None


async def send_request(
    session: Any,
    url: str,
    model: str,
    spec: RequestSpec,
    api_key: str,
    global_start_perf: float,
) -> RequestResult:
    request_id = f"batch-probe-{spec.marker}"
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": spec.prompt}],
        "stream": True,
        "max_tokens": spec.output_tokens,
        "temperature": 0,
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "X-Request-Id": request_id,
    }

    send_start = time.perf_counter()
    first_chunk: Optional[float] = None
    response_id = ""
    response_header_request_id = ""
    status: Optional[int] = None
    error = ""

    try:
        async with session.post(url, headers=headers, json=payload) as response:
            status = response.status
            response_header_request_id = str(response.headers.get("x-request-id", ""))
            if response.status < 200 or response.status >= 300:
                error = await response.text()
                return RequestResult(
                    spec.marker,
                    spec.batch_name,
                    spec.index,
                    spec.prompt_tokens,
                    spec.output_tokens,
                    request_id,
                    response_header_request_id,
                    response_id,
                    global_start_perf,
                    send_start,
                    first_chunk,
                    time.perf_counter(),
                    status,
                    error,
                )

            async for chunk_bytes in response.content.iter_chunked(4096):
                if chunk_bytes and first_chunk is None:
                    first_chunk = time.perf_counter()
                try:
                    chunk_text = chunk_bytes.decode("utf-8")
                except UnicodeDecodeError:
                    continue
                for line in chunk_text.splitlines():
                    if not line.startswith("data: "):
                        continue
                    payload_text = line[6:].strip()
                    if payload_text == "[DONE]":
                        continue
                    try:
                        data = json.loads(payload_text)
                    except json.JSONDecodeError:
                        continue
                    if not response_id:
                        response_id = str(data.get("id") or "")
    except Exception as exc:
        error = repr(exc)

    return RequestResult(
        spec.marker,
        spec.batch_name,
        spec.index,
        spec.prompt_tokens,
        spec.output_tokens,
        request_id,
        response_header_request_id,
        response_id,
        global_start_perf,
        send_start,
        first_chunk,
        time.perf_counter(),
        status,
        error,
    )


def build_specs(
    batch_name: str,
    prompt_lengths: Sequence[int],
    trial_id: str,
    model: str,
) -> List[RequestSpec]:
    return [
        RequestSpec(
            batch_name=batch_name,
            index=index,
            prompt_tokens=prompt_tokens,
            output_tokens=OUTPUT_TOKENS,
            marker=f"{trial_id}:{batch_name}-{index}",
            prompt=generate_prompt(prompt_tokens, f"{trial_id}:{batch_name}-{index}", model),
        )
        for index, prompt_tokens in enumerate(prompt_lengths)
    ]


async def run_triple_batch(args: argparse.Namespace, model: str) -> List[RequestResult]:
    import aiohttp

    trial_id = str(uuid.uuid4())
    url = f"http://{args.host}:{args.port}/v1/chat/completions"
    api_key = os.environ.get("OPENAI_API_KEY", "EMPTY")
    batch1_specs = build_specs("batch1", parse_prompt_lengths(args.batch1_shape), trial_id, model)
    batch2_specs = build_specs("batch2", parse_prompt_lengths(args.batch2_shape), trial_id, model)
    batch3_specs = build_specs("batch3", parse_prompt_lengths(args.batch3_shape), trial_id, model)

    async with aiohttp.ClientSession() as session:
        global_start_perf = time.perf_counter()
        batch1_tasks = [
            asyncio.create_task(send_request(session, url, model, spec, api_key, global_start_perf))
            for spec in batch1_specs
        ]
        await asyncio.sleep(args.gap_ms / 1000.0)
        batch2_tasks = [
            asyncio.create_task(send_request(session, url, model, spec, api_key, global_start_perf))
            for spec in batch2_specs
        ]
        await asyncio.sleep(args.gap_ms / 1000.0)
        batch3_tasks = [
            asyncio.create_task(send_request(session, url, model, spec, api_key, global_start_perf))
            for spec in batch3_specs
        ]

        batch1_results = await asyncio.gather(*batch1_tasks)
        batch2_results = await asyncio.gather(*batch2_tasks)
        batch3_results = await asyncio.gather(*batch3_tasks)

    return [*batch1_results, *batch2_results, *batch3_results]


def _fmt(value: Any, width: int) -> str:
    text = "" if value is None else str(value)
    if len(text) > width:
        return text[: max(0, width - 3)] + "..."
    return text


def print_batch_table(batch_name: str, results: Sequence[RequestResult]) -> None:
    rows = [result for result in results if result.batch_name == batch_name]
    print("")
    print(f"{batch_name.upper()} REQUEST TIMES")
    print("-" * 112)
    headers = [
        ("idx", 5),
        ("prompt", 8),
        ("out", 5),
        ("status", 8),
        ("ttft_ms", 12),
        ("send_gap_ms", 12),
        ("ttft_gap_ms", 12),
        ("error", 28),
    ]
    print(" ".join(name.ljust(width) for name, width in headers))
    print("-" * 112)
    for result in rows:
        ttft_text = "" if result.ttft_ms is None else f"{result.ttft_ms:.3f}"
        ttft_offset_text = "" if result.ttft_offset_ms is None else f"{result.ttft_offset_ms:.3f}"
        error_text = result.error.replace("\n", " ") if result.error else ""
        values = [
            result.index,
            result.prompt_tokens,
            result.output_tokens,
            result.status if result.status is not None else "",
            ttft_text,
            f"{result.send_offset_ms:.3f}",
            ttft_offset_text,
            error_text,
        ]
        print(
            " ".join(
                _fmt(value, width).ljust(width)
                for value, (_, width) in zip(values, headers)
            )
        )


def _batch_values(
    batch_name: str,
    results: Sequence[RequestResult],
    attr: str,
) -> List[float]:
    values: List[float] = []
    for result in results:
        if result.batch_name != batch_name:
            continue
        value = getattr(result, attr)
        if value is not None:
            values.append(float(value))
    return values


def _format_range(values: Sequence[float]) -> str:
    if not values:
        return "n/a"
    return f"{min(values):.3f}..{max(values):.3f}"


def print_merge_analysis(results: Sequence[RequestResult], merge_tolerance_ms: float) -> None:
    batch1_ttft_offsets = _batch_values("batch1", results, "ttft_offset_ms")
    batch2_send_offsets = _batch_values("batch2", results, "send_offset_ms")
    batch3_send_offsets = _batch_values("batch3", results, "send_offset_ms")
    batch2_ttft_offsets = _batch_values("batch2", results, "ttft_offset_ms")
    batch3_ttft_offsets = _batch_values("batch3", results, "ttft_offset_ms")

    batch1_first_done_ms = min(batch1_ttft_offsets) if batch1_ttft_offsets else None
    batch2_first_send_ms = min(batch2_send_offsets) if batch2_send_offsets else None
    batch3_first_send_ms = min(batch3_send_offsets) if batch3_send_offsets else None

    b2_sent_before_b1_done = (
        batch1_first_done_ms is not None
        and batch2_first_send_ms is not None
        and batch2_first_send_ms < batch1_first_done_ms
    )
    b3_sent_before_b1_done = (
        batch1_first_done_ms is not None
        and batch3_first_send_ms is not None
        and batch3_first_send_ms < batch1_first_done_ms
    )

    combined_b23_offsets = [*batch2_ttft_offsets, *batch3_ttft_offsets]
    b23_ttft_span_ms = (
        max(combined_b23_offsets) - min(combined_b23_offsets)
        if combined_b23_offsets
        else None
    )
    likely_merged = (
        b23_ttft_span_ms is not None
        and b23_ttft_span_ms <= merge_tolerance_ms
    )

    print("")
    print("MERGE ANALYSIS")
    print("-" * 112)
    print(f"batch1 ttft_gap_ms range: {_format_range(batch1_ttft_offsets)}")
    print(f"batch2 send_gap_ms range: {_format_range(batch2_send_offsets)}")
    print(f"batch3 send_gap_ms range: {_format_range(batch3_send_offsets)}")
    print(f"batch2 ttft_gap_ms range: {_format_range(batch2_ttft_offsets)}")
    print(f"batch3 ttft_gap_ms range: {_format_range(batch3_ttft_offsets)}")
    print(
        "batch2 sent before batch1 first TTFT: "
        f"{'yes' if b2_sent_before_b1_done else 'no/unknown'}"
    )
    print(
        "batch3 sent before batch1 first TTFT: "
        f"{'yes' if b3_sent_before_b1_done else 'no/unknown'}"
    )
    if b23_ttft_span_ms is None:
        print("batch2+batch3 TTFT span: n/a")
        print("likely batch2+batch3 merged after batch1: unknown")
    else:
        print(f"batch2+batch3 TTFT span: {b23_ttft_span_ms:.3f} ms")
        print(
            "likely batch2+batch3 merged after batch1: "
            f"{'yes' if likely_merged else 'no'} "
            f"(tolerance={merge_tolerance_ms:g} ms)"
        )


async def main_async(args: argparse.Namespace) -> int:
    base_url = f"http://{args.host}:{args.port}/v1"
    model = args.model or await detect_model(base_url)
    if not model:
        print("Failed to detect model. Please pass --model.")
        return 2

    results = await run_triple_batch(args, model)
    print(f"Target: http://{args.host}:{args.port}/v1/chat/completions")
    print(f"Model: {model}")
    print(f"Inter-batch gap: {args.gap_ms:g} ms")
    print_batch_table("batch1", results)
    print_batch_table("batch2", results)
    print_batch_table("batch3", results)
    print_merge_analysis(results, args.merge_tolerance_ms)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Send three heterogeneous prompt-length batches to vLLM with a fixed "
            "inter-batch gap and print per-request prefill TTFT metrics."
        )
    )
    parser.add_argument("--host", default="172.18.0.170")
    parser.add_argument("--port", type=int, default=8001)
    parser.add_argument("--model", default="/workspace/llm-stack/models/deepseek-ai/DeepSeek-R1-Distill-Qwen-7B")
    parser.add_argument("--batch1-shape", default=DEFAULT_BATCH1_SHAPE)
    parser.add_argument("--batch2-shape", default=DEFAULT_BATCH2_SHAPE)
    parser.add_argument("--batch3-shape", default=DEFAULT_BATCH3_SHAPE)
    parser.add_argument("--gap-ms", type=float, default=100.0)
    parser.add_argument("--merge-tolerance-ms", type=float, default=20.0)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if args.gap_ms < 0:
        parser.error("--gap-ms must be non-negative")
    if args.merge_tolerance_ms < 0:
        parser.error("--merge-tolerance-ms must be non-negative")
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
