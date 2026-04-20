import time
import uuid

import aiohttp


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

    # Decode can slightly change the effective token count, so we re-tokenize and
    # iteratively trim/pad to minimize the gap to the target.
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


def add_uuid_prefix(prompt: str) -> str:
    """Prefix prompt with a random UUID to reduce prefix matching effects."""
    return f"[{uuid.uuid4()}] {prompt}"


async def send_test_request(
    session: aiohttp.ClientSession,
    host: str,
    port: int,
    model: str,
    prompt: str,
) -> bool:
    """
    Send one request to vLLM to trigger prefill load.
    Success means we received the first streamed chunk.
    """
    api_url = f"http://{host}:{port}/v1/chat/completions"
    headers = {"Content-Type": "application/json"}
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": add_uuid_prefix(prompt)}],
        "stream": True,
        "max_tokens": 1,
        "temperature": 0.01,
    }

    try:
        async with session.post(api_url, headers=headers, json=payload) as resp:
            if resp.status != 200:
                return False

            async for chunk in resp.content.iter_any():
                if chunk:
                    return True

            return False

    except Exception:
        return False


async def measure_ttft(
    session: aiohttp.ClientSession,
    host: str,
    port: int,
    model: str,
    prompt: str,
):
    """Send one request to vLLM and measure TTFT locally."""
    api_url = f"http://{host}:{port}/v1/chat/completions"
    headers = {"Content-Type": "application/json"}
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": add_uuid_prefix(prompt)}],
        "stream": True,
        "max_tokens": 1,
        "temperature": 0.01,
    }

    start_time = time.perf_counter()
    try:
        async with session.post(api_url, headers=headers, json=payload) as resp:
            if resp.status != 200:
                return None

            async for chunk in resp.content.iter_any():
                if chunk:
                    return time.perf_counter() - start_time

            return None
    except Exception:
        return None
