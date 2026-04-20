import requests
import json
import time
import sys
import re


def estimate_tokens(text):
    """简单估算文本的token数量（基于近似值：1个token≈4个英文字符或0.75个中文字符）"""
    # 计算中文字符数量
    chinese_chars = len(re.findall(r'[\u4e00-\u9fff]', text))
    # 计算非中文字符数量
    non_chinese_chars = len(text) - chinese_chars
    # 估算token数
    return int(chinese_chars * 0.75 + non_chinese_chars / 4)


def chat_completion(url, model, messages, request_id, stream=False):
    headers = {
        "Content-Type": "application/json"
    }

    data = {
        "model": model,
        "messages": messages,
        "stream": stream,
        "request_id": request_id
    }

    start_time = time.time()
    response = requests.post(url, headers=headers, json=data, stream=stream)
    first_token_time = None
    completion_tokens = 0
    content = ""

    if stream:
        print("Assistant: ", end="")
        for line in response.iter_lines():
            if line:
                line_str = line.decode('utf-8')
                if line_str.startswith('data: '):
                    chunk = line_str[6:]  # Remove 'data: ' prefix
                    if chunk == '[DONE]':
                        break

                    try:
                        chunk_data = json.loads(chunk)
                        if 'choices' in chunk_data and len(chunk_data['choices']) > 0:
                            delta = chunk_data['choices'][0].get('delta', {})
                            if 'content' in delta:
                                token_content = delta['content']
                                content += token_content
                                sys.stdout.write(token_content)
                                sys.stdout.flush()

                                # 估算每个块的token数并累加
                                completion_tokens += estimate_tokens(token_content)

                                # 记录首令牌时间
                                if first_token_time is None:
                                    first_token_time = time.time() - start_time
                    except json.JSONDecodeError:
                        continue

        # 计算总时间和性能指标
        total_time = time.time() - start_time

        # 估算输入token数（基于消息内容）
        prompt_text = " ".join([msg["content"] for msg in messages])
        prompt_tokens = estimate_tokens(prompt_text)
        total_tokens = prompt_tokens + completion_tokens

        # 打印统计信息
        print(f"\n\nPrompt tokens: {prompt_tokens}")
        print(f"Completion tokens: {completion_tokens}")
        print(f"Total tokens: {total_tokens}")

        if first_token_time is not None:
            print(f"TTFT: {first_token_time:.4f}s")
            # 计算TPOT (Time Per Output Token)
            if completion_tokens > 0:
                tpot = (total_time - first_token_time) / completion_tokens
                print(f"TPOT: {tpot:.4f}s/token")

    else:
        # 非流式响应
        response_data = response.json()

        if 'choices' in response_data and len(response_data['choices']) > 0:
            content = response_data['choices'][0]['message']['content']
            print(f"Assistant: {content}")

        if 'usage' in response_data:
            usage = response_data['usage']
            total_time = time.time() - start_time

            print(f"\nPrompt tokens: {usage.get('prompt_tokens', 'N/A')}")
            print(f"Completion tokens: {usage.get('completion_tokens', 'N/A')}")
            print(f"Total tokens: {usage.get('total_tokens', 'N/A')}")
            print(f"TTFT: {total_time:.4f}s")  # 非流式响应中TTFT≈总时间


def query_prompt(ip, prompt):
    try:
        headers = {
            "Content-Type": "application/json"
        }
        data = {
            "prompt": prompt
        }
        scheduler_url = f"http://{ip}/user/prompt"
        response = requests.get(scheduler_url, headers=headers, json=data)
        if response.status_code == 200:
            response_data = response.json()
            prefill_proxy = response_data.get("prefill")
            request_id = response_data.get("request_id")
            return prefill_proxy, request_id
        else:
            return "", ""
    except Exception:
        exc_info = sys.exc_info()
        error_messages = [str(e) for e in exc_info if e]
        print("Error occurred in function query_prompt")
        print(error_messages)
        return "", ""


if __name__ == "__main__":
    scheduler_ip = "172.18.0.169:8000"
    messages = [
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": "Who are you?"}
    ]
    proxy_ip, request_id = query_prompt(scheduler_ip, prompt=messages)
    url = f"http://{proxy_ip}/v1/chat/completions"
    model = "/workspace/workspace/models/deepseek-ai/DeepSeek-R1-Distill-Qwen-1.5B"

    # 对于流式响应
    chat_completion(url, model, messages, request_id, stream=True)

    # 对于非流式响应
    # chat_completion(url, model, messages, request_id, stream=False)
