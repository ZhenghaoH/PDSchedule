import json
import time
from typing import Dict, Optional

import aiohttp
from transformers import AutoTokenizer


TOKENIZER_CACHE = {}


def get_tokenizer(tokenizer_path: str):
    """Load and cache tokenizer."""
    if tokenizer_path not in TOKENIZER_CACHE:
        print(f"[INFO] Loading tokenizer from: {tokenizer_path}")
        try:
            TOKENIZER_CACHE[tokenizer_path] = AutoTokenizer.from_pretrained(tokenizer_path)
        except Exception as exc:
            print(f"[ERROR] Failed to load tokenizer: {exc}")
            return None
    return TOKENIZER_CACHE.get(tokenizer_path)


def generate_prompt_with_tokens(tokenizer, target_token_count: int) -> str:
    """Generate a prompt whose token length is as close as possible to the target."""
    if target_token_count <= 0:
        return ""

    base_text = "This is a long context test to measure the performance of the system. "
    base_token_ids = tokenizer.encode(base_text, add_special_tokens=False)
    if not base_token_ids:
        return "Error"

    estimated_repeats = (target_token_count // len(base_token_ids)) + 2
    prompt_ids = (base_token_ids * estimated_repeats)[:target_token_count]
    prompt = tokenizer.decode(prompt_ids, clean_up_tokenization_spaces=False)

    for _ in range(8):
        encoded_ids = tokenizer.encode(prompt, add_special_tokens=False)
        current_len = len(encoded_ids)
        if current_len == target_token_count:
            return prompt

        if current_len > target_token_count:
            encoded_ids = encoded_ids[:target_token_count]
        else:
            missing = target_token_count - current_len
            extra_ids = (base_token_ids * ((missing // len(base_token_ids)) + 1))[:missing]
            encoded_ids = encoded_ids + extra_ids

        prompt = tokenizer.decode(encoded_ids, clean_up_tokenization_spaces=False)

    return prompt


async def measure_generation_performance(
    session: aiohttp.ClientSession,
    prompt: str,
    vllm_config: Dict,
    max_new_tokens: int,
) -> Optional[Dict]:
    """
    Send a streamed request to vLLM and measure TTFT, output token count, TPOT,
    and total request time.
    """
    api_url = f"http://{vllm_config['host']}:{vllm_config['port']}/v1/chat/completions"
    payload = {
        "model": vllm_config["model_id"],
        "messages": [{"role": "user", "content": prompt}],
        "stream": True,
        "max_tokens": max_new_tokens,
        "temperature": 0.0,
    }

    tokenizer_path = "/home/dell/vLLM/workspace/models/deepseek-ai/DeepSeek-R1-Distill-Qwen-1.5B/"

    start_time = time.perf_counter()
    ttft = 0.0
    tpot = 0.0
    full_response_text = ""
    num_output_tokens = 0

    try:
        async with session.post(api_url, json=payload, headers={"Content-Type": "application/json"}) as resp:
            if resp.status != 200:
                error_msg = await resp.text()
                print(f"Error from vLLM: {resp.status}, {error_msg}")
                return None

            buffer = b""
            first_chunk_received = False

            async for chunk in resp.content.iter_any():
                if not first_chunk_received and chunk:
                    ttft = time.perf_counter() - start_time
                    first_chunk_received = True

                buffer += chunk
                while b"\n" in buffer:
                    line, buffer = buffer.split(b"\n", 1)
                    line = line.strip()
                    if line.startswith(b"data: "):
                        data = line[len(b"data: "):]
                        if data == b"[DONE]":
                            break
                        try:
                            if data:
                                chunk_data = json.loads(data.decode("utf-8"))
                                choices = chunk_data.get("choices", [])
                                if choices:
                                    delta = choices[0].get("delta", {})
                                    content = delta.get("content", "")
                                    if content:
                                        full_response_text += content
                        except json.JSONDecodeError:
                            continue

        end_time = time.perf_counter()
        total_request_time = end_time - start_time

        if full_response_text:
            tokenizer = get_tokenizer(tokenizer_path)
            if tokenizer:
                try:
                    num_output_tokens = len(tokenizer.encode(full_response_text))
                except Exception as exc:
                    print(f"[ERROR] Failed to tokenize output: {exc}")
                    num_output_tokens = 0

        if num_output_tokens > 1 and first_chunk_received:
            tpot = (total_request_time - ttft) / (num_output_tokens - 1)
        elif num_output_tokens == 1 and first_chunk_received:
            tpot = total_request_time - ttft

        if num_output_tokens > 0:
            return {
                "ttft": ttft,
                "output_tokens": num_output_tokens,
                "tpot": tpot,
                "total_time": total_request_time,
            }

    except Exception as exc:
        print(f"Error during generation measurement: {exc}")

    return None
