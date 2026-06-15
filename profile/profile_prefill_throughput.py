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
PROMPT_TOKENS = 4096
OUTPUT_TOKENS = 1
REPEATS = 8
REQUEST_INTERVAL_SECONDS = 2.0


@dataclass
class PrefillRunResult:
    repeat_index: int
    request_id: str
    prompt_tokens: int
    ttft_seconds: float
    prefill_throughput: float


def load_tokenizer(model_path: str):
    from transformers import AutoTokenizer

    return AutoTokenizer.from_pretrained(model_path, trust_remote_code=True)


def generate_exact_token_prompt(tokenizer, target_tokens: int, marker: str) -> str:
    base_text = (
        f"[{marker}] "
        "Prefill throughput validation request with deterministic filler. "
    )
    filler_text = (
        "The quick validation sentence carries stable prefill-only tokens. "
    )

    base_ids = tokenizer.encode(base_text, add_special_tokens=False)
    filler_ids = tokenizer.encode(filler_text, add_special_tokens=False)
    if not filler_ids:
        raise ValueError("Tokenizer produced no filler tokens.")

    if len(base_ids) >= target_tokens:
        token_ids = base_ids[:target_tokens]
    else:
        missing = target_tokens - len(base_ids)
        token_ids = base_ids + (
            filler_ids * ((missing // len(filler_ids)) + 1)
        )[:missing]

    prompt = tokenizer.decode(token_ids, clean_up_tokenization_spaces=False)
    for _ in range(16):
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

    final_len = len(tokenizer.encode(prompt, add_special_tokens=False))
    raise ValueError(
        f"Failed to generate exact prompt length: expected={target_tokens}, "
        f"actual={final_len}"
    )


async def send_prefill_request(
    url: str,
    model: str,
    prompt: str,
    repeat_index: int,
) -> Optional[PrefillRunResult]:
    import aiohttp

    request_id = f"prefill-throughput-{uuid.uuid4().hex[:12]}"
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

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url=url, json=body, headers=headers) as response:
                if not (200 <= response.status < 300):
                    error_content = await response.text()
                    print(
                        f"repeat={repeat_index} request failed "
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

                    if "data: [DONE]" in chunk_text:
                        break
                    for line in chunk_text.splitlines():
                        if not line.startswith("data: "):
                            continue
                        payload = line[6:]
                        if payload == "[DONE]":
                            break
                        try:
                            json.loads(payload)
                        except json.JSONDecodeError:
                            continue
    except Exception as exc:
        print(f"repeat={repeat_index} request failed: {exc}")
        return None

    if first_chunk_time is None:
        print(f"repeat={repeat_index} request produced no streaming chunk.")
        return None

    ttft_seconds = first_chunk_time - start_time
    if ttft_seconds <= 0:
        print(f"repeat={repeat_index} invalid TTFT: {ttft_seconds}")
        return None

    return PrefillRunResult(
        repeat_index=repeat_index,
        request_id=request_id,
        prompt_tokens=PROMPT_TOKENS,
        ttft_seconds=ttft_seconds,
        prefill_throughput=PROMPT_TOKENS / ttft_seconds,
    )


async def benchmark(args: argparse.Namespace) -> None:
    print(f"开始 Prefill 吞吐测试: host={args.host}")
    print(f"model={args.model}")
    print(f"tokenizer_path={MODEL_PATH}")
    print(
        f"prompt_tokens={PROMPT_TOKENS}, output_tokens={OUTPUT_TOKENS}, "
        f"repeats={REPEATS}, interval={REQUEST_INTERVAL_SECONDS}s"
    )

    tokenizer = load_tokenizer(MODEL_PATH)
    url = f"http://{args.host}/v1/chat/completions"
    results: list[PrefillRunResult] = []

    for repeat_index in range(1, REPEATS + 1):
        marker = f"{uuid.uuid4().hex}:repeat-{repeat_index}"
        prompt = generate_exact_token_prompt(tokenizer, PROMPT_TOKENS, marker)
        actual_tokens = len(tokenizer.encode(prompt, add_special_tokens=False))
        if actual_tokens != PROMPT_TOKENS:
            raise RuntimeError(
                f"Prompt token validation failed: expected={PROMPT_TOKENS}, "
                f"actual={actual_tokens}"
            )

        print(f"\n[{repeat_index}/{REPEATS}] sending one request, tokens={actual_tokens}")
        result = await send_prefill_request(
            url=url,
            model=args.model,
            prompt=prompt,
            repeat_index=repeat_index,
        )
        if result is not None:
            results.append(result)
            print(
                f"repeat={repeat_index}, request_id={result.request_id}, "
                f"ttft={result.ttft_seconds * 1000.0:.3f} ms, "
                f"prefill_throughput={result.prefill_throughput:.3f} tokens/s"
            )

        if repeat_index < REPEATS:
            print(f"waiting {REQUEST_INTERVAL_SECONDS:.1f}s before next request...")
            await asyncio.sleep(REQUEST_INTERVAL_SECONDS)

    print("\n" + "=" * 80)
    print("Prefill Throughput Summary")
    print("=" * 80)
    print(f"success_count={len(results)} / {REPEATS}")
    if not results:
        print("prefill_throughput=n/a")
        return

    ttft_ms_values = [item.ttft_seconds * 1000.0 for item in results]
    throughput_values = [item.prefill_throughput for item in results]
    print(f"avg_ttft_ms={statistics.mean(ttft_ms_values):.3f}")
    print(f"min_ttft_ms={min(ttft_ms_values):.3f}")
    print(f"max_ttft_ms={max(ttft_ms_values):.3f}")
    print(
        "single_prefill_throughput="
        + ", ".join(f"{value:.3f}" for value in throughput_values)
    )
    print(f"prefill_throughput={statistics.mean(throughput_values):.3f}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="vLLM Prefill Throughput Profiler")
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
