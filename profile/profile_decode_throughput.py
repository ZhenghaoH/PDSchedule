import argparse
import asyncio
import json
import os
import statistics
import time
import uuid
from dataclasses import dataclass
from typing import Optional


MODEL_PATH = "/workspace/llm-stack/models/deepseek-ai/DeepSeek-R1-Distill-Qwen-7B"
PROMPT_QUESTION = "帮我生成一个两万字的故事"
OUTPUT_TOKENS = 1024
CONCURRENCY_LEVELS = range(9, 17)
REQUEST_INTERVAL_SECONDS = 2.0


@dataclass
class DecodeRunResult:
    concurrency: int
    request_index: int
    request_id: str
    prompt_tokens: int
    actual_output_tokens: int
    ttft_seconds: float
    total_time_seconds: float
    decode_time_seconds: float


@dataclass
class DecodeGroupResult:
    concurrency: int
    success_count: int
    group_wall_time_seconds: float
    group_decode_tokens: int
    group_decode_throughput: float
    request_results: list[DecodeRunResult]


def load_tokenizer(model_path: str):
    from transformers import AutoTokenizer

    return AutoTokenizer.from_pretrained(model_path, trust_remote_code=True)


def build_prompt(tokenizer) -> tuple[str, int]:
    prompt = PROMPT_QUESTION
    prompt_tokens = len(tokenizer.encode(prompt, add_special_tokens=False))
    if prompt_tokens <= 0:
        raise ValueError("Prompt question produced no tokens.")
    return prompt, prompt_tokens


def parse_stream_content(chunk_text: str) -> list[str]:
    content_parts = []
    for line in chunk_text.splitlines():
        if not line.startswith("data: "):
            continue

        payload = line[6:]
        if payload == "[DONE]":
            continue

        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            continue

        for choice in data.get("choices", []):
            delta = choice.get("delta", {})
            content = delta.get("content")
            if content:
                content_parts.append(content)
    return content_parts


async def send_decode_request(
    url: str,
    model: str,
    tokenizer,
    prompt: str,
    prompt_tokens: int,
    concurrency: int,
    request_index: int,
) -> Optional[DecodeRunResult]:
    import aiohttp

    request_id = f"decode-throughput-{uuid.uuid4().hex[:12]}"
    headers = {
        "Authorization": f"Bearer {os.environ.get('OPENAI_API_KEY', 'EMPTY')}",
        "Content-Type": "application/json",
        "X-Request-Id": request_id,
    }
    body = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "stream": True,
        "max_tokens": OUTPUT_TOKENS,
        "temperature": 0,
    }

    start_time = time.perf_counter()
    first_chunk_time = None
    generated_text_parts: list[str] = []

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url=url, json=body, headers=headers) as response:
                if not (200 <= response.status < 300):
                    error_content = await response.text()
                    print(
                        f"concurrency={concurrency} request={request_index} failed "
                        f"status={response.status}: {error_content}"
                    )
                    return None

                async for chunk_bytes in response.content.iter_chunked(1024):
                    if chunk_bytes and first_chunk_time is None:
                        first_chunk_time = time.perf_counter()

                    try:
                        chunk_text = chunk_bytes.decode("utf-8")
                    except Exception:
                        continue

                    generated_text_parts.extend(parse_stream_content(chunk_text))
                    if "data: [DONE]" in chunk_text:
                        break
    except Exception as exc:
        print(f"concurrency={concurrency} request={request_index} failed: {exc}")
        return None

    end_time = time.perf_counter()
    if first_chunk_time is None:
        print(
            f"concurrency={concurrency} request={request_index} "
            "produced no streaming chunk."
        )
        return None

    generated_text = "".join(generated_text_parts)
    actual_output_tokens = len(
        tokenizer.encode(generated_text, add_special_tokens=False)
    )
    ttft_seconds = first_chunk_time - start_time
    total_time_seconds = end_time - start_time
    decode_time_seconds = total_time_seconds - ttft_seconds

    if actual_output_tokens <= 1:
        print(
            f"concurrency={concurrency} request={request_index} "
            f"invalid output tokens: {actual_output_tokens}"
        )
        return None
    if ttft_seconds <= 0 or decode_time_seconds <= 0:
        print(
            f"concurrency={concurrency} request={request_index} invalid timing: "
            f"ttft={ttft_seconds}, decode_time={decode_time_seconds}"
        )
        return None

    return DecodeRunResult(
        concurrency=concurrency,
        request_index=request_index,
        request_id=request_id,
        prompt_tokens=prompt_tokens,
        actual_output_tokens=actual_output_tokens,
        ttft_seconds=ttft_seconds,
        total_time_seconds=total_time_seconds,
        decode_time_seconds=decode_time_seconds,
    )


async def run_concurrency_group(
    url: str,
    model: str,
    tokenizer,
    concurrency: int,
) -> DecodeGroupResult:
    prompts = []
    prompt_token_counts = []
    for request_index in range(1, concurrency + 1):
        prompt, prompt_tokens = build_prompt(tokenizer)
        actual_prompt_tokens = len(tokenizer.encode(prompt, add_special_tokens=False))
        if actual_prompt_tokens != prompt_tokens:
            raise RuntimeError(
                f"Prompt token validation failed: expected={prompt_tokens}, "
                f"actual={actual_prompt_tokens}"
            )
        prompts.append(prompt)
        prompt_token_counts.append(prompt_tokens)

    tasks = [
        asyncio.create_task(
            send_decode_request(
                url=url,
                model=model,
                tokenizer=tokenizer,
                prompt=prompt,
                prompt_tokens=prompt_token_counts[request_index - 1],
                concurrency=concurrency,
                request_index=request_index,
            )
        )
        for request_index, prompt in enumerate(prompts, start=1)
    ]

    group_start = time.perf_counter()
    raw_results = await asyncio.gather(*tasks)
    group_wall_time_seconds = time.perf_counter() - group_start

    request_results = [result for result in raw_results if result is not None]
    group_decode_tokens = sum(
        max(result.actual_output_tokens - 1, 0)
        for result in request_results
    )
    group_decode_throughput = (
        group_decode_tokens / group_wall_time_seconds
        if group_wall_time_seconds > 0
        else 0.0
    )

    return DecodeGroupResult(
        concurrency=concurrency,
        success_count=len(request_results),
        group_wall_time_seconds=group_wall_time_seconds,
        group_decode_tokens=group_decode_tokens,
        group_decode_throughput=group_decode_throughput,
        request_results=request_results,
    )


async def benchmark(args: argparse.Namespace) -> None:
    concurrency_levels = list(CONCURRENCY_LEVELS)
    print(f"开始 Decode 并发吞吐测试: host={args.host}")
    print(f"model={args.model}")
    print(f"tokenizer_path={MODEL_PATH}")
    print(
        f"prompt_question={PROMPT_QUESTION}, output_tokens={OUTPUT_TOKENS}, "
        f"concurrency_levels={concurrency_levels}, "
        f"interval={REQUEST_INTERVAL_SECONDS}s"
    )

    tokenizer = load_tokenizer(MODEL_PATH)
    url = f"http://{args.host}/v1/chat/completions"
    group_results: list[DecodeGroupResult] = []

    for level_index, concurrency in enumerate(concurrency_levels, start=1):
        print(
            f"\n[{level_index}/{len(concurrency_levels)}] "
            f"sending concurrency={concurrency}"
        )
        group = await run_concurrency_group(
            url=url,
            model=args.model,
            tokenizer=tokenizer,
            concurrency=concurrency,
        )
        group_results.append(group)

        for result in group.request_results:
            print(
                f"concurrency={concurrency}, request={result.request_index}, "
                f"request_id={result.request_id}, "
                f"prompt_tokens={result.prompt_tokens}, "
                f"ttft={result.ttft_seconds * 1000.0:.3f} ms, "
                f"total_time={result.total_time_seconds:.3f} s, "
                f"actual_output_tokens={result.actual_output_tokens}"
            )
        print(
            f"group concurrency={concurrency}, "
            f"success={group.success_count}/{concurrency}, "
            f"group_wall_time={group.group_wall_time_seconds:.3f} s, "
            f"group_decode_tokens={group.group_decode_tokens}, "
            f"decode_throughput={group.group_decode_throughput:.3f} tokens/s"
        )

        if level_index < len(concurrency_levels):
            print(f"waiting {REQUEST_INTERVAL_SECONDS:.1f}s before next group...")
            await asyncio.sleep(REQUEST_INTERVAL_SECONDS)

    print("\n" + "=" * 80)
    print("Decode Concurrency Throughput Summary")
    print("=" * 80)
    for group in group_results:
        if group.request_results:
            avg_ttft_ms = statistics.mean(
                item.ttft_seconds * 1000.0 for item in group.request_results
            )
            avg_total_time_s = statistics.mean(
                item.total_time_seconds for item in group.request_results
            )
            avg_output_tokens = statistics.mean(
                item.actual_output_tokens for item in group.request_results
            )
            avg_prompt_tokens = statistics.mean(
                item.prompt_tokens for item in group.request_results
            )
            avg_ttft_text = f"{avg_ttft_ms:.3f}"
            avg_total_text = f"{avg_total_time_s:.3f}"
            avg_tokens_text = f"{avg_output_tokens:.3f}"
            avg_prompt_text = f"{avg_prompt_tokens:.3f}"
        else:
            avg_ttft_text = "n/a"
            avg_total_text = "n/a"
            avg_tokens_text = "n/a"
            avg_prompt_text = "n/a"

        print(
            f"concurrency={group.concurrency:<2} "
            f"success={group.success_count}/{group.concurrency:<2} "
            f"avg_prompt_tokens={avg_prompt_text:<10} "
            f"avg_ttft_ms={avg_ttft_text:<10} "
            f"avg_total_time_s={avg_total_text:<10} "
            f"avg_actual_output_tokens={avg_tokens_text:<10} "
            f"group_wall_time_s={group.group_wall_time_seconds:.3f} "
            f"group_decode_tokens={group.group_decode_tokens:<6} "
            f"decode_throughput={group.group_decode_throughput:.3f}"
        )

    print(
        "decode_throughput_by_concurrency="
        + ", ".join(
            f"{group.concurrency}:{group.group_decode_throughput:.3f}"
            for group in group_results
        )
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="vLLM Decode Throughput Profiler")
    parser.add_argument("--host", type=str, required=True, help="vLLM IP:PORT")
    parser.add_argument(
        "--model",
        type=str,
        default=MODEL_PATH,
        help="Model name/path sent to the OpenAI-compatible endpoint.",
    )
    return parser


if __name__ == "__main__":
    parser = build_parser()
    asyncio.run(benchmark(parser.parse_args()))
