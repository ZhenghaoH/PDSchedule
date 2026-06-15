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


DEFAULT_MODEL_FILE = "/workspace/llm-stack/workspace_HZH/FlexPD/entrypoints/profile/TPOT_predictor/artifacts/decode_tpot_regressor.pkl"
DEFAULT_MODEL = "/workspace/llm-stack/models/deepseek-ai/DeepSeek-R1-Distill-Qwen-7B"
DEFAULT_PROMPT = "编写一个两万字的故事"


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
    requested_output_tokens: int
    actual_output_tokens: int
    request_id: str
    send_offset_ms: float
    ttft_ms: Optional[float]
    generation_time_ms: Optional[float]
    total_time_ms: Optional[float]
    end_offset_ms: float
    token_offsets_ms: list[float]
    status: Optional[int]
    error: str = ""


@dataclass
class RequestReport:
    batch_index: int
    request_index: int
    batch_size: int
    prompt_tokens: int
    requested_output_tokens: int
    actual_output_tokens: int
    predicted_generation_ms: Optional[float]
    actual_generation_ms: Optional[float]
    predicted_avg_tpot_ms: Optional[float]
    actual_avg_tpot_ms: Optional[float]
    error_ms: Optional[float]
    abs_error_ms: Optional[float]
    ape_percent: Optional[float]
    dynamic_predicted_generation_ms: Optional[float]
    dynamic_error_ms: Optional[float]
    dynamic_abs_error_ms: Optional[float]
    dynamic_ape_percent: Optional[float]
    dynamic_segments: int
    status: Optional[int]
    error: str = ""


@dataclass
class SegmentReport:
    batch_index: int
    request_index: int
    peer_request_index: int
    peer_finish_ms: float
    active_bs_before: int
    active_bs_after: int
    before_peer_finish_tpot_ms: Optional[float]
    after_peer_finish_tpot_ms: Optional[float]
    tpot_delta_ms: Optional[float]
    tpot_dropped: Optional[bool]
    pred_avg_tpot_before_ms: Optional[float]
    pred_avg_tpot_after_ms: Optional[float]
    after_vs_reduced_bs_error_ms: Optional[float]
    after_vs_reduced_bs_abs_error_ms: Optional[float]
    after_vs_reduced_bs_ape_percent: Optional[float]
    before_intervals: int
    after_intervals: int


@dataclass
class BatchReport:
    batch_index: int
    batch_size: int
    prompt_tokens: list[int]
    output_tokens: list[int]
    failed_requests: int
    reports: list[RequestReport]
    segment_reports: list[SegmentReport]
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
        from .TPOT_regressor import OnlineTPOTRegressor
    else:
        sys.path.append(str(Path(__file__).resolve().parent))
        from TPOT_regressor import OnlineTPOTRegressor

    regressor = OnlineTPOTRegressor()
    if not regressor.load_from_file(model_file):
        raise RuntimeError(
            "Failed to load fitted decode TPOT regressor. "
            "The file may be missing or incompatible; please rerun TPOT warmup."
        )
    return regressor


def build_random_batches(args: argparse.Namespace) -> list[list[int]]:
    rng = random.Random(args.seed)
    batches = []
    for _ in range(args.num_batches):
        request_count = rng.randint(args.min_requests, args.max_requests)
        batches.append(
            [
                rng.randint(args.min_output_tokens, args.max_output_tokens)
                for _ in range(request_count)
            ]
        )
    return batches


def build_heterogeneous_prompt(
    base_prompt: str,
    batch_index: int,
    request_index: int,
    rng: random.Random,
) -> str:
    themes = [
        "星际远航与失落文明",
        "近未来城市里的普通家庭",
        "古代边城中的医者和旅人",
        "深海科考站的长期任务",
        "雨季山村与一封迟到的信",
        "人工智能觉醒后的社会协商",
        "雪原铁路沿线的三代人",
        "沙漠商队寻找绿洲的旅程",
    ]
    genres = [
        "现实主义与科幻结合",
        "历史传奇",
        "悬疑冒险",
        "家族史诗",
        "成长小说",
        "心理描写细腻的文学故事",
        "多线叙事",
        "带有哲思的长篇故事",
    ]
    viewpoints = [
        "第一人称",
        "第三人称有限视角",
        "多主角交替视角",
        "书信体与旁白交织",
    ]
    structures = [
        "分为十二章，每章都有明确冲突和结尾悬念。",
        "采用起承转合结构，铺垫充分，结尾回收所有伏笔。",
        "包含序章、主体章节和尾声，人物关系逐步展开。",
        "每隔数章切换一次时间线，但保持故事连贯。",
    ]
    detail_blocks = [
        "请重点描写人物动机、环境细节、长期选择带来的后果。",
        "请加入丰富对白、日常场景、内心独白和关键转折。",
        "请让每个主要人物都有独立目标、误解、失败和成长。",
        "请保持长篇叙事节奏，不要写成提纲、摘要或短篇。",
        "请让世界观、社会规则、人物关系和情感线都充分展开。",
        "请包含至少三条副线，并在后半部分交汇。",
    ]
    extra_count = rng.randint(1, 5)
    extras = " ".join(rng.sample(detail_blocks, k=extra_count))
    return (
        f"{base_prompt}。"
        f"主题：{themes[(batch_index + request_index) % len(themes)]}。"
        f"体裁：{rng.choice(genres)}。"
        f"叙事视角：{rng.choice(viewpoints)}。"
        f"结构要求：{rng.choice(structures)}"
        f"{extras}"
        "必须写成长篇完整故事，内容细节丰富，避免只给大纲。"
    )


def build_specs(
    output_token_batches: list[list[int]],
    tokenizer: Any,
    prompt: str,
    heterogeneous_prompts: bool,
    prompt_variant_seed: Optional[int],
) -> list[list[RequestSpec]]:
    all_specs = []
    run_id = uuid.uuid4().hex[:10]
    rng = random.Random(prompt_variant_seed)

    for batch_index, output_tokens_list in enumerate(output_token_batches, start=1):
        specs = []
        for request_index, output_tokens in enumerate(output_tokens_list):
            request_id = f"decode-validate-{run_id}:b{batch_index}:r{request_index}"
            request_prompt = (
                build_heterogeneous_prompt(prompt, batch_index, request_index, rng)
                if heterogeneous_prompts
                else prompt
            )
            prompt_tokens = len(tokenizer.encode(request_prompt, add_special_tokens=False))
            if prompt_tokens <= 0:
                raise ValueError(
                    f"Prompt has zero tokens after tokenization: request={request_id}"
                )
            specs.append(
                RequestSpec(
                    batch_index=batch_index,
                    request_index=request_index,
                    prompt_tokens=prompt_tokens,
                    output_tokens=output_tokens,
                    request_id=request_id,
                    prompt=request_prompt,
                )
            )
        all_specs.append(specs)
    return all_specs


def _extract_stream_content(chunk_data: dict) -> str:
    choices = chunk_data.get("choices", [])
    if not choices:
        return ""
    choice = choices[0]
    delta = choice.get("delta")
    if isinstance(delta, dict):
        return delta.get("content") or ""
    text = choice.get("text")
    if isinstance(text, str):
        return text
    return ""


def _mean_intervals_between(
    token_offsets_ms: list[float],
    lower_ms: float,
    upper_ms: float,
) -> tuple[Optional[float], int]:
    intervals = []
    for previous, current in zip(token_offsets_ms, token_offsets_ms[1:]):
        if lower_ms <= previous and current <= upper_ms:
            intervals.append(current - previous)
    if not intervals:
        return None, 0
    return statistics.mean(intervals), len(intervals)


async def send_request(
    session: aiohttp.ClientSession,
    url: str,
    model: str,
    tokenizer: Any,
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
    full_response_text = ""
    token_offsets_ms: list[float] = []
    previous_token_count = 0

    try:
        async with session.post(url, headers=headers, json=payload) as response:
            status = response.status
            if response.status < 200 or response.status >= 300:
                error = await response.text()
            else:
                buffer = b""
                async for chunk in response.content.iter_any():
                    now = time.perf_counter()
                    if chunk and first_chunk is None:
                        first_chunk = now
                    buffer += chunk
                    while b"\n" in buffer:
                        line, buffer = buffer.split(b"\n", 1)
                        line = line.strip()
                        if not line.startswith(b"data: "):
                            continue
                        data = line[len(b"data: "):]
                        if data == b"[DONE]":
                            continue
                        try:
                            if data:
                                chunk_data = json.loads(data.decode("utf-8"))
                                content = _extract_stream_content(chunk_data)
                                if content:
                                    full_response_text += content
                                    current_token_count = len(
                                        tokenizer.encode(
                                            full_response_text,
                                            add_special_tokens=False,
                                        )
                                    )
                                    if current_token_count > previous_token_count:
                                        offset_ms = (now - send_start) * 1000.0
                                        token_offsets_ms.extend(
                                            [offset_ms]
                                            * (current_token_count - previous_token_count)
                                        )
                                        previous_token_count = current_token_count
                        except json.JSONDecodeError:
                            continue
    except Exception as exc:
        error = repr(exc)

    end_time = time.perf_counter()
    actual_output_tokens = previous_token_count
    ttft_ms = None if first_chunk is None else (first_chunk - send_start) * 1000.0
    total_time_ms = (end_time - send_start) * 1000.0
    generation_time_ms = None
    if ttft_ms is not None:
        generation_time_ms = max(0.0, total_time_ms - ttft_ms)

    return RequestResult(
        batch_index=spec.batch_index,
        request_index=spec.request_index,
        prompt_tokens=spec.prompt_tokens,
        requested_output_tokens=spec.output_tokens,
        actual_output_tokens=actual_output_tokens,
        request_id=spec.request_id,
        send_offset_ms=(send_start - global_start) * 1000.0,
        ttft_ms=ttft_ms,
        generation_time_ms=generation_time_ms,
        total_time_ms=total_time_ms,
        end_offset_ms=(end_time - global_start) * 1000.0,
        token_offsets_ms=token_offsets_ms,
        status=status,
        error=error,
    )


async def run_batches(
    args: argparse.Namespace,
    tokenizer: Any,
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
                f"prompt_tokens={[spec.prompt_tokens for spec in specs]}, "
                f"max_tokens={[spec.output_tokens for spec in specs]}"
            )
            tasks = [
                asyncio.create_task(
                    send_request(session, url, args.model, tokenizer, spec, api_key, global_start)
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


def make_segment_reports(
    regressor: Any,
    batch_index: int,
    results: list[RequestResult],
    min_segment_intervals: int,
) -> list[SegmentReport]:
    successful = [
        result
        for result in results
        if result.total_time_ms is not None and len(result.token_offsets_ms) >= 3
    ]
    if len(successful) < 2:
        return []

    segment_reports = []
    batch_start = min(result.send_offset_ms for result in successful)
    finish_events = sorted(
        (
            result.end_offset_ms - batch_start,
            result.request_index,
        )
        for result in successful
    )

    for peer_finish_ms, peer_idx in finish_events:
        active_before = [
            result
            for result in successful
            if result.send_offset_ms - batch_start <= peer_finish_ms <= result.end_offset_ms - batch_start
        ]
        active_bs_before = len(active_before)
        active_bs_after = max(0, active_bs_before - 1)
        if active_bs_after <= 0:
            continue

        previous_finish_ms = max(
            [
                finish_ms
                for finish_ms, _idx in finish_events
                if finish_ms < peer_finish_ms
            ],
            default=0.0,
        )
        next_finish_ms = min(
            [
                finish_ms
                for finish_ms, _idx in finish_events
                if finish_ms > peer_finish_ms
            ],
            default=float("inf"),
        )

        for result in active_before:
            if result.request_index == peer_idx:
                continue
            request_send_in_batch_ms = result.send_offset_ms - batch_start
            request_finish_in_batch_ms = result.end_offset_ms - batch_start
            if request_finish_in_batch_ms <= peer_finish_ms:
                continue

            local_peer_finish_ms = peer_finish_ms - request_send_in_batch_ms
            local_previous_finish_ms = max(0.0, previous_finish_ms - request_send_in_batch_ms)
            local_next_finish_ms = min(
                request_finish_in_batch_ms - request_send_in_batch_ms,
                next_finish_ms - request_send_in_batch_ms,
            )

            before_tpot, before_count = _mean_intervals_between(
                result.token_offsets_ms,
                local_previous_finish_ms,
                local_peer_finish_ms,
            )
            after_tpot, after_count = _mean_intervals_between(
                result.token_offsets_ms,
                local_peer_finish_ms,
                local_next_finish_ms,
            )
            if before_count < min_segment_intervals or after_count < min_segment_intervals:
                continue

            pred_before = (
                regressor.predict_generation_time(
                    active_bs_before,
                    result.prompt_tokens,
                    result.requested_output_tokens,
                )
                * 1000.0
                / max(result.requested_output_tokens, 1)
            )
            pred_after = (
                regressor.predict_generation_time(
                    active_bs_after,
                    result.prompt_tokens,
                    result.requested_output_tokens,
                )
                * 1000.0
                / max(result.requested_output_tokens, 1)
            )

            tpot_delta = None
            tpot_dropped = None
            after_error = None
            after_abs_error = None
            after_ape = None
            if before_tpot is not None and after_tpot is not None:
                tpot_delta = after_tpot - before_tpot
                tpot_dropped = tpot_delta < 0
                after_error = pred_after - after_tpot
                after_abs_error = abs(after_error)
                if after_tpot > 0:
                    after_ape = after_abs_error / after_tpot * 100.0

            segment_reports.append(
                SegmentReport(
                    batch_index=batch_index,
                    request_index=result.request_index,
                    peer_request_index=peer_idx,
                    peer_finish_ms=peer_finish_ms,
                    active_bs_before=active_bs_before,
                    active_bs_after=active_bs_after,
                    before_peer_finish_tpot_ms=before_tpot,
                    after_peer_finish_tpot_ms=after_tpot,
                    tpot_delta_ms=tpot_delta,
                    tpot_dropped=tpot_dropped,
                    pred_avg_tpot_before_ms=pred_before,
                    pred_avg_tpot_after_ms=pred_after,
                    after_vs_reduced_bs_error_ms=after_error,
                    after_vs_reduced_bs_abs_error_ms=after_abs_error,
                    after_vs_reduced_bs_ape_percent=after_ape,
                    before_intervals=before_count,
                    after_intervals=after_count,
                )
            )

    return segment_reports


def _tokens_generated_by_time(token_offsets_ms: list[float], cutoff_ms: float) -> int:
    return sum(1 for offset_ms in token_offsets_ms if offset_ms <= cutoff_ms)


def predict_generation_time_with_dynamic_bs(
    regressor: Any,
    request: RequestResult,
    batch_results: list[RequestResult],
) -> tuple[Optional[float], int]:
    if request.generation_time_ms is None:
        return None, 0

    if request.actual_output_tokens > 1:
        total_generation_tokens = request.actual_output_tokens - 1
    else:
        total_generation_tokens = max(request.requested_output_tokens, 1)
    token_offsets = request.token_offsets_ms
    if len(token_offsets) < 2:
        fixed_pred = (
            regressor.predict_generation_time(
                len(batch_results),
                request.prompt_tokens,
                total_generation_tokens,
            )
            * 1000.0
        )
        return fixed_pred, 1

    batch_start_ms = min(result.send_offset_ms for result in batch_results)
    request_send_ms = request.send_offset_ms - batch_start_ms
    request_finish_ms = request.end_offset_ms - batch_start_ms

    peer_finish_points = sorted(
        {
            result.end_offset_ms - batch_start_ms
            for result in batch_results
            if result.request_index != request.request_index
            and result.generation_time_ms is not None
            and request_send_ms < result.end_offset_ms - batch_start_ms < request_finish_ms
        }
    )
    boundaries = [request_send_ms, *peer_finish_points, request_finish_ms]
    if len(boundaries) < 2:
        fixed_pred = (
            regressor.predict_generation_time(
                len(batch_results),
                request.prompt_tokens,
                total_generation_tokens,
            )
            * 1000.0
        )
        return fixed_pred, 1

    predicted_ms = 0.0
    generated_before = 0
    segment_count = 0

    for start_ms, end_ms in zip(boundaries, boundaries[1:]):
        if end_ms <= start_ms:
            continue
        active_bs = sum(
            1
            for result in batch_results
            if result.generation_time_ms is not None
            and result.send_offset_ms - batch_start_ms <= start_ms
            and start_ms < result.end_offset_ms - batch_start_ms
        )
        if active_bs <= 0:
            active_bs = 1

        local_end_ms = end_ms - request_send_ms
        generated_until_end = _tokens_generated_by_time(token_offsets, local_end_ms)
        segment_tokens = generated_until_end - generated_before
        if end_ms == request_finish_ms:
            segment_tokens = max(segment_tokens, total_generation_tokens - generated_before)
        if segment_tokens <= 0:
            continue

        predicted_ms += (
            regressor.predict_generation_time(
                active_bs,
                request.prompt_tokens + generated_before,
                segment_tokens,
            )
            * 1000.0
        )
        generated_before += segment_tokens
        segment_count += 1

    if generated_before < total_generation_tokens:
        active_bs = 1
        predicted_ms += (
            regressor.predict_generation_time(
                active_bs,
                request.prompt_tokens + generated_before,
                total_generation_tokens - generated_before,
            )
            * 1000.0
        )
        segment_count += 1

    return predicted_ms, segment_count


def make_batch_report(
    regressor: Any,
    batch_index: int,
    results: list[RequestResult],
    min_segment_intervals: int,
) -> BatchReport:
    batch_size = len(results)
    reports = []

    for result in results:
        predicted_generation_ms = None
        predicted_avg_tpot_ms = None
        actual_avg_tpot_ms = None
        error_ms = None
        abs_error_ms = None
        ape_percent = None
        dynamic_predicted_generation_ms = None
        dynamic_error_ms = None
        dynamic_abs_error_ms = None
        dynamic_ape_percent = None
        dynamic_segments = 0

        if result.generation_time_ms is not None:
            predicted_generation_ms = (
                regressor.predict_generation_time(
                    batch_size,
                    result.prompt_tokens,
                    result.requested_output_tokens,
                )
                * 1000.0
            )
            predicted_avg_tpot_ms = predicted_generation_ms / max(result.requested_output_tokens, 1)
            actual_avg_tpot_ms = result.generation_time_ms / max(result.actual_output_tokens - 1, 1)
            error_ms = predicted_generation_ms - result.generation_time_ms
            abs_error_ms = abs(error_ms)
            if result.generation_time_ms > 0:
                ape_percent = abs_error_ms / result.generation_time_ms * 100.0
            dynamic_predicted_generation_ms, dynamic_segments = (
                predict_generation_time_with_dynamic_bs(regressor, result, results)
            )
            if dynamic_predicted_generation_ms is not None:
                dynamic_error_ms = dynamic_predicted_generation_ms - result.generation_time_ms
                dynamic_abs_error_ms = abs(dynamic_error_ms)
                if result.generation_time_ms > 0:
                    dynamic_ape_percent = dynamic_abs_error_ms / result.generation_time_ms * 100.0

        reports.append(
            RequestReport(
                batch_index=batch_index,
                request_index=result.request_index,
                batch_size=batch_size,
                prompt_tokens=result.prompt_tokens,
                requested_output_tokens=result.requested_output_tokens,
                actual_output_tokens=result.actual_output_tokens,
                predicted_generation_ms=predicted_generation_ms,
                actual_generation_ms=result.generation_time_ms,
                predicted_avg_tpot_ms=predicted_avg_tpot_ms,
                actual_avg_tpot_ms=actual_avg_tpot_ms,
                error_ms=error_ms,
                abs_error_ms=abs_error_ms,
                ape_percent=ape_percent,
                dynamic_predicted_generation_ms=dynamic_predicted_generation_ms,
                dynamic_error_ms=dynamic_error_ms,
                dynamic_abs_error_ms=dynamic_abs_error_ms,
                dynamic_ape_percent=dynamic_ape_percent,
                dynamic_segments=dynamic_segments,
                status=result.status,
                error=result.error,
            )
        )

    return BatchReport(
        batch_index=batch_index,
        batch_size=batch_size,
        prompt_tokens=[result.prompt_tokens for result in results],
        output_tokens=[result.requested_output_tokens for result in results],
        failed_requests=sum(1 for result in results if result.generation_time_ms is None),
        reports=reports,
        segment_reports=make_segment_reports(
            regressor,
            batch_index,
            results,
            min_segment_intervals,
        ),
        results=results,
    )


def fmt_float(value: Optional[float], digits: int = 3) -> str:
    if value is None:
        return ""
    return f"{value:.{digits}f}"


def print_reports(reports: list[BatchReport]) -> None:
    headers = [
        ("batch", 6),
        ("req", 5),
        ("bs", 4),
        ("prompt", 8),
        ("req_out", 8),
        ("act_out", 8),
        ("pred_gen", 10),
        ("act_gen", 10),
        ("pred_tpot", 11),
        ("act_tpot", 10),
        ("err_ms", 10),
        ("abs_ms", 10),
        ("ape_%", 9),
        ("dyn_pred", 10),
        ("dyn_err", 10),
        ("dyn_abs", 10),
        ("dyn_ape", 9),
        ("segs", 5),
    ]

    print("\nDECODE PREDICTION VALIDATION")
    print("-" * 140)
    print(" ".join(name.ljust(width) for name, width in headers))
    print("-" * 140)
    for report in reports:
        print(
            f"outer_batch={report.batch_index} "
            f"prompt_tokens={report.prompt_tokens} outputs={report.output_tokens}"
        )
        for item in report.reports:
            values = [
                item.batch_index,
                item.request_index,
                item.batch_size,
                item.prompt_tokens,
                item.requested_output_tokens,
                item.actual_output_tokens,
                fmt_float(item.predicted_generation_ms),
                fmt_float(item.actual_generation_ms),
                fmt_float(item.predicted_avg_tpot_ms),
                fmt_float(item.actual_avg_tpot_ms),
                fmt_float(item.error_ms),
                fmt_float(item.abs_error_ms),
                fmt_float(item.ape_percent),
                fmt_float(item.dynamic_predicted_generation_ms),
                fmt_float(item.dynamic_error_ms),
                fmt_float(item.dynamic_abs_error_ms),
                fmt_float(item.dynamic_ape_percent),
                item.dynamic_segments,
            ]
            print(
                " ".join(
                    str(value)[:width].ljust(width)
                    for value, (_, width) in zip(values, headers)
                )
            )

    print_segment_reports(reports)
    print_summary(reports)


def print_segment_reports(reports: list[BatchReport]) -> None:
    segment_reports = [
        segment
        for report in reports
        for segment in report.segment_reports
    ]
    if not segment_reports:
        print("\nSEGMENT TPOT AFTER PEER FINISH")
        print("-" * 140)
        print("No segment cases with enough token intervals.")
        return

    headers = [
        ("batch", 6),
        ("req", 5),
        ("peer", 6),
        ("bs_b", 5),
        ("bs_a", 5),
        ("before", 10),
        ("after", 10),
        ("delta", 10),
        ("drop", 6),
        ("pred_a", 10),
        ("err_a", 10),
        ("abs_a", 10),
        ("ape_a", 9),
    ]
    print("\nSEGMENT TPOT AFTER PEER FINISH")
    print("-" * 140)
    print(" ".join(name.ljust(width) for name, width in headers))
    print("-" * 140)
    for segment in segment_reports:
        values = [
            segment.batch_index,
            segment.request_index,
            segment.peer_request_index,
            segment.active_bs_before,
            segment.active_bs_after,
            fmt_float(segment.before_peer_finish_tpot_ms),
            fmt_float(segment.after_peer_finish_tpot_ms),
            fmt_float(segment.tpot_delta_ms),
            segment.tpot_dropped,
            fmt_float(segment.pred_avg_tpot_after_ms),
            fmt_float(segment.after_vs_reduced_bs_error_ms),
            fmt_float(segment.after_vs_reduced_bs_abs_error_ms),
            fmt_float(segment.after_vs_reduced_bs_ape_percent),
        ]
        print(
            " ".join(
                str(value)[:width].ljust(width)
                for value, (_, width) in zip(values, headers)
            )
        )


def print_summary(reports: list[BatchReport]) -> None:
    valid_abs_errors = [
        item.abs_error_ms
        for report in reports
        for item in report.reports
        if item.abs_error_ms is not None
    ]
    valid_apes = [
        item.ape_percent
        for report in reports
        for item in report.reports
        if item.ape_percent is not None
    ]
    valid_tpot_errors = [
        abs((item.predicted_avg_tpot_ms or 0.0) - (item.actual_avg_tpot_ms or 0.0))
        for report in reports
        for item in report.reports
        if item.predicted_avg_tpot_ms is not None and item.actual_avg_tpot_ms is not None
    ]
    dynamic_abs_errors = [
        item.dynamic_abs_error_ms
        for report in reports
        for item in report.reports
        if item.dynamic_abs_error_ms is not None
    ]
    dynamic_apes = [
        item.dynamic_ape_percent
        for report in reports
        for item in report.reports
        if item.dynamic_ape_percent is not None
    ]
    segment_reports = [
        segment
        for report in reports
        for segment in report.segment_reports
    ]
    segment_abs_errors = [
        segment.after_vs_reduced_bs_abs_error_ms
        for segment in segment_reports
        if segment.after_vs_reduced_bs_abs_error_ms is not None
    ]
    segment_apes = [
        segment.after_vs_reduced_bs_ape_percent
        for segment in segment_reports
        if segment.after_vs_reduced_bs_ape_percent is not None
    ]
    segment_drop_count = sum(1 for segment in segment_reports if segment.tpot_dropped)
    failed_requests = sum(report.failed_requests for report in reports)
    total_requests = sum(len(report.reports) for report in reports)

    print("\nSUMMARY")
    print("-" * 140)
    print(f"valid_requests={len(valid_abs_errors)} / {total_requests}")
    print(f"outer_batches={len(reports)}")
    print(f"failed_requests={failed_requests}")
    if valid_abs_errors:
        print(f"GenerationTime_MAE={statistics.mean(valid_abs_errors):.3f} ms")
        print(f"GenerationTime_MaxAbsError={max(valid_abs_errors):.3f} ms")
    else:
        print("GenerationTime_MAE=n/a")
        print("GenerationTime_MaxAbsError=n/a")
    if valid_tpot_errors:
        print(f"AvgTPOT_MAE={statistics.mean(valid_tpot_errors):.3f} ms/token")
    else:
        print("AvgTPOT_MAE=n/a")
    if valid_apes:
        print(f"GenerationTime_MAPE={statistics.mean(valid_apes):.3f}%")
    else:
        print("GenerationTime_MAPE=n/a")
    if dynamic_abs_errors:
        print(f"DynamicGenerationTime_MAE={statistics.mean(dynamic_abs_errors):.3f} ms")
        print(f"DynamicGenerationTime_MaxAbsError={max(dynamic_abs_errors):.3f} ms")
    else:
        print("DynamicGenerationTime_MAE=n/a")
        print("DynamicGenerationTime_MaxAbsError=n/a")
    if dynamic_apes:
        print(f"DynamicGenerationTime_MAPE={statistics.mean(dynamic_apes):.3f}%")
    else:
        print("DynamicGenerationTime_MAPE=n/a")

    print(f"segment_cases={len(segment_reports)}")
    print(f"segment_tpot_drop_count={segment_drop_count}")
    if segment_reports:
        print(f"segment_tpot_drop_ratio={segment_drop_count / len(segment_reports):.3f}")
    else:
        print("segment_tpot_drop_ratio=n/a")
    if segment_abs_errors:
        print(f"after_vs_reduced_bs_mae={statistics.mean(segment_abs_errors):.3f} ms/token")
    else:
        print("after_vs_reduced_bs_mae=n/a")
    if segment_apes:
        print(f"after_vs_reduced_bs_mape={statistics.mean(segment_apes):.3f}%")
    else:
        print("after_vs_reduced_bs_mape=n/a")


def write_json(path: str, reports: list[BatchReport]) -> None:
    payload = [asdict(report) for report in reports]
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nWrote JSON results to {output_path}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Validate the decode TPOT predictor by using a fixed prompt and "
            "varying max_tokens across concurrent requests."
        )
    )
    parser.add_argument("--host", default="172.18.0.170")
    parser.add_argument("--port", type=int, default=8003)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--tokenizer-path", default=DEFAULT_MODEL)
    parser.add_argument("--model-file", default=DEFAULT_MODEL_FILE)
    parser.add_argument("--prompt", default=DEFAULT_PROMPT)
    prompt_mode_group = parser.add_mutually_exclusive_group()
    prompt_mode_group.add_argument(
        "--heterogeneous-prompts",
        dest="heterogeneous_prompts",
        action="store_true",
        help="Use varied prompts that preserve the long-story request semantics.",
    )
    prompt_mode_group.add_argument(
        "--homogeneous-prompts",
        dest="heterogeneous_prompts",
        action="store_false",
        help="Use the same prompt for every request.",
    )
    parser.set_defaults(heterogeneous_prompts=True)
    parser.add_argument(
        "--prompt-variant-seed",
        type=int,
        default=None,
        help="Seed for heterogeneous prompt variants. Defaults to --seed.",
    )
    parser.add_argument("--num-batches", type=positive_int, default=10)
    parser.add_argument("--min-requests", type=positive_int, default=1)
    parser.add_argument("--max-requests", type=positive_int, default=8)
    parser.add_argument("--min-output-tokens", type=positive_int, default=64)
    parser.add_argument("--max-output-tokens", type=positive_int, default=1024)
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--timeout-seconds", type=float, default=900.0)
    parser.add_argument(
        "--batch-interval-seconds",
        type=float,
        default=0.0,
        help="Seconds to wait between completed outer batches.",
    )
    parser.add_argument(
        "--min-segment-intervals",
        type=positive_int,
        default=2,
        help="Minimum token intervals required before and after a peer finishes.",
    )
    parser.add_argument("--output-json", default="")
    return parser


def validate_args(args: argparse.Namespace) -> None:
    if args.min_requests > args.max_requests:
        raise ValueError("--min-requests cannot exceed --max-requests")
    if args.min_output_tokens > args.max_output_tokens:
        raise ValueError("--min-output-tokens cannot exceed --max-output-tokens")
    if args.timeout_seconds <= 0:
        raise ValueError("--timeout-seconds must be positive")
    if args.batch_interval_seconds < 0:
        raise ValueError("--batch-interval-seconds cannot be negative")


async def main_async(args: argparse.Namespace) -> int:
    validate_args(args)
    regressor = load_regressor(args.model_file)

    try:
        from transformers import AutoTokenizer

        tokenizer = AutoTokenizer.from_pretrained(args.tokenizer_path)
    except Exception as exc:
        raise RuntimeError(f"Tokenizer load failed: {exc}") from exc

    base_prompt_tokens = len(tokenizer.encode(args.prompt, add_special_tokens=False))
    print(f"Base prompt: {args.prompt}")
    print(f"Base prompt tokens: {base_prompt_tokens}")
    print(f"Heterogeneous prompts: {args.heterogeneous_prompts}")

    output_token_batches = build_random_batches(args)
    prompt_variant_seed = args.prompt_variant_seed
    if prompt_variant_seed is None:
        prompt_variant_seed = args.seed
    spec_batches = build_specs(
        output_token_batches,
        tokenizer,
        args.prompt,
        args.heterogeneous_prompts,
        prompt_variant_seed,
    )
    result_batches = await run_batches(args, tokenizer, spec_batches)

    reports = [
        make_batch_report(regressor, index, results, args.min_segment_intervals)
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
