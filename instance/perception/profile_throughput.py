import argparse
import asyncio
import math
import os
import time
import uuid
from typing import Dict, List, Optional

import numpy as np


DEFAULT_CONCURRENCY = [1, 2, 4, 8, 16, 32]
PHASE_CONFIGS = [
    {"name": "prefill", "prompt_len": 1024, "output_len": 10},
    {"name": "decode", "prompt_len": 10, "output_len": 1024},
]


def generate_prompt(
    approx_tokens: int,
    model_path: str = None,
    prompt_uuid: str = None,
) -> str:
    """
    生成指定长度的测试 prompt，并保留 UUID 前缀用于避免 KV Cache 前缀命中。
    """
    try:
        from transformers import AutoTokenizer

        if model_path:
            try:
                tokenizer = AutoTokenizer.from_pretrained(model_path)
            except Exception:
                tokenizer = None
                for fallback_model in ["gpt2", "facebook/opt-125m", "meta-llama/Llama-2-7b-hf"]:
                    try:
                        tokenizer = AutoTokenizer.from_pretrained(fallback_model)
                        print(f"   使用默认 tokenizer: {fallback_model}")
                        break
                    except Exception:
                        continue
                if tokenizer is None:
                    tokenizer = AutoTokenizer.from_pretrained("gpt2")
                    print("   使用默认 tokenizer: gpt2")
        else:
            tokenizer = AutoTokenizer.from_pretrained("gpt2")
            print("   使用默认 tokenizer: gpt2")

        prompt = ""
        while True:
            prompt += "test "
            token_count = len(tokenizer.tokenize(prompt))
            if token_count >= approx_tokens:
                break

        if prompt_uuid:
            prompt = f"[{prompt_uuid}] {prompt}"
        return prompt
    except Exception as exc:
        print(f"Warning: tokenizer 加载失败，使用简化 prompt 生成方式: {exc}")
        prompt = ("test " * approx_tokens).strip()
        if prompt_uuid:
            prompt = f"[{prompt_uuid}] {prompt}"
        return prompt


def approximate_token_count(text: str, model_name: Optional[str]) -> int:
    """
    对无法直接从 usage 获取的文本做近似 token 计数。
    """
    if not text:
        return 0

    try:
        from transformers import AutoTokenizer

        if model_name:
            try:
                tokenizer = AutoTokenizer.from_pretrained(model_name)
                return len(tokenizer.tokenize(text))
            except Exception:
                pass

        tokenizer = AutoTokenizer.from_pretrained("gpt2")
        return len(tokenizer.tokenize(text))
    except Exception:
        return max(1, len(text) // 4)


async def run_worker(
    url: str,
    model: str,
    prompt: str,
    max_tokens: int,
    request_id: int,
) -> Optional[Dict[str, float]]:
    """
    发送单个流式请求，统计 TTFT、总时延、配置输出长度和实际生成 token 数。
    """
    try:
        import aiohttp
        import json

        start_time = time.perf_counter()
        ttft = 0.0
        prompt_tokens = 0
        completion_tokens = 0
        generated_text_parts: List[str] = []
        per_second_generated_tokens: Dict[int, int] = {}

        body = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "stream": True,
            "max_tokens": max_tokens,
            "temperature": 0,
        }
        headers = {
            "Authorization": f"Bearer {os.environ.get('OPENAI_API_KEY', 'EMPTY')}",
            "Content-Type": "application/json",
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(url=url, json=body, headers=headers) as response:
                if not (200 <= response.status < 300):
                    error_content = await response.text()
                    print(f"Request {request_id} failed with status {response.status}: {error_content}")
                    return None

                first_chunk = True
                async for chunk_bytes in response.content.iter_chunked(1024):
                    if first_chunk:
                        ttft = time.perf_counter() - start_time
                        first_chunk = False

                    try:
                        chunk_text = chunk_bytes.decode("utf-8")
                    except Exception:
                        continue

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

                        usage = data.get("usage")
                        if usage:
                            prompt_tokens = usage.get("prompt_tokens", prompt_tokens)
                            completion_tokens = usage.get("completion_tokens", completion_tokens)

                        for choice in data.get("choices", []):
                            delta = choice.get("delta", {})
                            content = delta.get("content")
                            if content:
                                generated_text_parts.append(content)
                                token_count = approximate_token_count(content, model)
                                elapsed_seconds = max(0.0, time.perf_counter() - start_time)
                                second_bucket = int(math.floor(elapsed_seconds))
                                per_second_generated_tokens[second_bucket] = (
                                    per_second_generated_tokens.get(second_bucket, 0) + token_count
                                )

        latency = time.perf_counter() - start_time
        generated_text = "".join(generated_text_parts)
        counted_completion_tokens = approximate_token_count(generated_text, model)

        if prompt_tokens == 0:
            prompt_tokens = approximate_token_count(prompt, model)
        if completion_tokens == 0:
            completion_tokens = counted_completion_tokens if counted_completion_tokens > 0 else max_tokens

        return {
            "req_id": request_id,
            "ttft": ttft,
            "latency": latency,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "configured_output_tokens": max_tokens,
            "counted_completion_tokens": counted_completion_tokens,
            "per_second_generated_tokens": per_second_generated_tokens,
        }
    except Exception as exc:
        print(f"Request {request_id} failed: {exc}")
        return None


async def detect_model(base_url: str) -> Optional[str]:
    import aiohttp

    async with aiohttp.ClientSession() as session:
        async with session.get(f"{base_url}/models") as response:
            if response.status != 200:
                print(f"获取模型失败: {response.status}")
                return None
            data = await response.json()
            return data.get("data", [{}])[0].get("id")


async def benchmark_once(
    host: str,
    model_name: str,
    phase_name: str,
    prompt_len: int,
    output_len: int,
    concurrency: int,
) -> Optional[Dict[str, float]]:
    base_url = f"http://{host}/v1"
    chat_url = f"{base_url}/chat/completions"
    prompts = []
    tasks = []

    for _ in range(concurrency):
        request_uuid = str(uuid.uuid4())
        prompts.append(generate_prompt(prompt_len, model_name, request_uuid))

    benchmark_start_time = time.perf_counter()
    for request_id in range(concurrency):
        tasks.append(run_worker(chat_url, model_name, prompts[request_id], output_len, request_id))

    results = await asyncio.gather(*tasks)
    benchmark_duration = time.perf_counter() - benchmark_start_time
    valid_results = [result for result in results if result is not None]

    if not valid_results:
        return None

    total_prompt_tokens = sum(result["prompt_tokens"] for result in valid_results)
    total_completion_tokens = sum(result["completion_tokens"] for result in valid_results)
    total_counted_completion_tokens = sum(result["counted_completion_tokens"] for result in valid_results)
    total_configured_output_tokens = sum(result["configured_output_tokens"] for result in valid_results)
    merged_per_second_generated_tokens: Dict[int, int] = {}
    ttft_ms_list = [result["ttft"] * 1000 for result in valid_results]
    ttft_seconds_list = [result["ttft"] for result in valid_results]

    for result in valid_results:
        for second_bucket, token_count in result["per_second_generated_tokens"].items():
            merged_per_second_generated_tokens[second_bucket] = (
                merged_per_second_generated_tokens.get(second_bucket, 0) + token_count
            )

    max_ttft_s = max(ttft_seconds_list) if ttft_seconds_list else 0.0
    prompt_throughput = (total_prompt_tokens / max_ttft_s) if max_ttft_s > 0 else 0.0
    gen_throughput = max(merged_per_second_generated_tokens.values(), default=0)

    return {
        "phase_name": phase_name,
        "prompt_len": prompt_len,
        "output_len": output_len,
        "concurrency": concurrency,
        "success_count": len(valid_results),
        "benchmark_duration": benchmark_duration,
        "total_prompt_tokens": total_prompt_tokens,
        "total_completion_tokens": total_completion_tokens,
        "total_counted_completion_tokens": total_counted_completion_tokens,
        "total_configured_output_tokens": total_configured_output_tokens,
        "max_ttft_s": max_ttft_s,
        "avg_ttft_ms": float(np.mean(ttft_ms_list)),
        "p99_ttft_ms": float(np.percentile(ttft_ms_list, 99)),
        "prompt_throughput": prompt_throughput,
        "gen_throughput": gen_throughput,
        "peak_gen_tokens_per_s": gen_throughput,
    }


async def run_phase_benchmark(
    host: str,
    model_name: str,
    concurrency_list: List[int],
    phase_config: Dict[str, int],
) -> List[Dict[str, float]]:
    phase_results = []

    print("\n" + "=" * 60)
    print(
        f"阶段 {phase_config['name']} 开始: "
        f"prompt_len={phase_config['prompt_len']}, output_len={phase_config['output_len']}"
    )
    print("=" * 60)

    for concurrency in concurrency_list:
        print("\n" + "-" * 60)
        print(f"开始测试阶段={phase_config['name']} 并发={concurrency}")
        result = await benchmark_once(
            host=host,
            model_name=model_name,
            phase_name=phase_config["name"],
            prompt_len=phase_config["prompt_len"],
            output_len=phase_config["output_len"],
            concurrency=concurrency,
        )
        if result is None:
            print(f"阶段={phase_config['name']} 并发={concurrency} 测试失败")
            continue

        phase_results.append(result)
        print(
            f"阶段={result['phase_name']}, 并发={result['concurrency']}, "
            f"成功={result['success_count']}/{result['concurrency']}, "
            f"Prompt TPS={result['prompt_throughput']:.2f}, "
            f"Gen TPS={result['gen_throughput']:.2f}, "
            f"实际生成tokens={result['total_completion_tokens']}, "
            f"计数生成tokens={result['total_counted_completion_tokens']}, "
            f"TTFT(avg/p99/max)={result['avg_ttft_ms']:.2f}/{result['p99_ttft_ms']:.2f}/{result['max_ttft_s'] * 1000:.2f} ms"
        )

    return phase_results


def print_phase_summary(phase_results: List[Dict[str, float]]) -> None:
    if not phase_results:
        print("该阶段没有可用结果。")
        return

    best_result = max(phase_results, key=lambda item: item["gen_throughput"])

    print("\n" + "=" * 60)
    print(f"{best_result['phase_name']} 阶段结果汇总")
    print("=" * 60)
    for result in phase_results:
        print(
            f"并发={result['concurrency']:<4} "
            f"Gen TPS={result['gen_throughput']:.2f} "
            f"Prompt TPS={result['prompt_throughput']:.2f} "
            f"最长TTFT={result['max_ttft_s'] * 1000:.2f} ms "
            f"实际生成tokens={result['total_completion_tokens']} "
            f"计数生成tokens={result['total_counted_completion_tokens']}"
        )

    print("\n" + "-" * 60)
    print(f"{best_result['phase_name']} 阶段最大生成吞吐量总结")
    print("-" * 60)
    print(f"BEST_CONCURRENCY = {best_result['concurrency']}")
    print(f"MAX_GEN_TPS = {best_result['gen_throughput']:.2f}")
    print(f"MAX_TTFT_MS = {best_result['max_ttft_s'] * 1000:.2f}")
    print(f"PROMPT_LEN = {best_result['prompt_len']}")
    print(f"OUTPUT_LEN = {best_result['output_len']}")
    print(f"TOTAL_ACTUAL_COMPLETION_TOKENS = {best_result['total_completion_tokens']}")
    print(f"TOTAL_COUNTED_COMPLETION_TOKENS = {best_result['total_counted_completion_tokens']}")
    print(f"SUCCESS_COUNT = {best_result['success_count']}")
    print(f"BENCHMARK_DURATION = {best_result['benchmark_duration']:.2f}s")


async def benchmark(args):
    print(f"开始测试: {args.host}")
    print(f"并发列表: {args.concurrency}")
    print("将按两个固定阶段进行压测：prefill(1024->10) 与 decode(10->1024)")

    base_url = f"http://{args.host}/v1"
    model_name = await detect_model(base_url)
    if not model_name:
        print("无法自动获取模型名称，测试终止。")
        return

    print(f"检测到模型: {model_name}")

    all_phase_results = {}
    for phase_config in PHASE_CONFIGS:
        phase_results = await run_phase_benchmark(
            host=args.host,
            model_name=model_name,
            concurrency_list=args.concurrency,
            phase_config=phase_config,
        )
        all_phase_results[phase_config["name"]] = phase_results

    print("\n" + "#" * 60)
    print("分阶段最大生成吞吐量总结")
    print("#" * 60)
    for phase_name in ["prefill", "decode"]:
        print_phase_summary(all_phase_results.get(phase_name, []))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="vLLM Phase Throughput Profiler")
    parser.add_argument("--host", type=str, required=True, help="vLLM IP:PORT")
    parser.add_argument(
        "--concurrency",
        type=int,
        nargs="+",
        default=DEFAULT_CONCURRENCY,
        help="并发请求列表，例如: --concurrency 1 2 4 8 16 32",
    )

    args = parser.parse_args()
    asyncio.run(benchmark(args))
