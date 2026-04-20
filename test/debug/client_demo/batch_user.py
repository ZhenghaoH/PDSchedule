import requests
import json
import time
import sys
import re
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import defaultdict


class ConcurrentChatManager:
    def __init__(self, max_concurrent=3, show_output=True):
        self.max_concurrent = max_concurrent
        self.show_output = show_output
        self.lock = threading.Lock()
        self.results = {}

    def estimate_tokens(self, text):
        """简单估算文本的token数量（基于近似值：1个token≈4个英文字符或0.75个中文字符）"""
        chinese_chars = len(re.findall(r'[\u4e00-\u9fff]', text))
        non_chinese_chars = len(text) - chinese_chars
        return int(chinese_chars * 0.75 + non_chinese_chars / 4)

    def chat_completion(self, url, model, messages, sch_id, stream=False, request_id=0):
        """发送聊天补全请求，支持并发和流式响应"""
        headers = {"Content-Type": "application/json"}
        data = {
            "model": model,
            "messages": messages,
            "stream": stream,
            "request_id": sch_id
        }

        start_time = time.time()

        try:
            response = requests.post(url, headers=headers, json=data, stream=stream, timeout=60)
            response.raise_for_status()
        except requests.exceptions.RequestException as e:
            return {
                "request_id": request_id,
                "success": False,
                "error": str(e),
                "content": "",
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
                "ttft": 0,
                "tpot": 0,
                "total_time": 0
            }

        first_token_time = None
        completion_tokens = 0
        content = ""

        if stream:
            # 流式响应处理 - 收集内容但不显示
            for line in response.iter_lines():
                if line:
                    line_str = line.decode('utf-8')
                    if line_str.startswith('data: '):
                        chunk = line_str[6:]
                        if chunk == '[DONE]':
                            break

                        try:
                            chunk_data = json.loads(chunk)
                            if 'choices' in chunk_data and len(chunk_data['choices']) > 0:
                                delta = chunk_data['choices'][0].get('delta', {})
                                if 'content' in delta:
                                    token_content = delta['content']
                                    content += token_content

                                    # 估算每个块的token数并累加
                                    completion_tokens += self.estimate_tokens(token_content)

                                    # 记录首令牌时间
                                    if first_token_time is None:
                                        first_token_time = time.time() - start_time
                        except json.JSONDecodeError:
                            continue

            # 计算统计信息
            total_time = time.time() - start_time
            prompt_text = " ".join([msg["content"] for msg in messages])
            prompt_tokens = self.estimate_tokens(prompt_text)
            total_tokens = prompt_tokens + completion_tokens

            # 计算TPOT (Time Per Output Token)
            tpot = 0
            if first_token_time is not None and completion_tokens > 0:
                tpot = (total_time - first_token_time) / completion_tokens

            return {
                "request_id": request_id,
                "success": True,
                "error": None,
                "content": content,
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": total_tokens,
                "ttft": first_token_time if first_token_time is not None else total_time,
                "tpot": tpot,
                "total_time": total_time
            }

        else:
            # 非流式响应处理
            try:
                response_data = response.json()

                if 'choices' in response_data and len(response_data['choices']) > 0:
                    content = response_data['choices'][0]['message']['content']

                # 计算统计信息
                total_time = time.time() - start_time

                if 'usage' in response_data:
                    usage = response_data['usage']
                    prompt_tokens = usage.get('prompt_tokens', 0)
                    completion_tokens = usage.get('completion_tokens', 0)
                    total_tokens = usage.get('total_tokens', 0)
                else:
                    # 如果没有使用情况数据，估算一下
                    prompt_text = " ".join([msg["content"] for msg in messages])
                    prompt_tokens = self.estimate_tokens(prompt_text)
                    completion_tokens = self.estimate_tokens(content)
                    total_tokens = prompt_tokens + completion_tokens

                return {
                    "request_id": request_id,
                    "success": True,
                    "error": None,
                    "content": content,
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": completion_tokens,
                    "total_tokens": total_tokens,
                    "ttft": total_time,  # 非流式响应中TTFT≈总时间
                    "tpot": 0,  # 非流式无法计算TPOT
                    "total_time": total_time
                }

            except json.JSONDecodeError as e:
                return {
                    "request_id": request_id,
                    "success": False,
                    "error": f"JSON解析错误: {e}",
                    "content": "",
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                    "total_tokens": 0,
                    "ttft": 0,
                    "tpot": 0,
                    "total_time": 0
                }

    def query_prompt(self, ip, prompt):
        """查询提示词代理"""
        try:
            headers = {"Content-Type": "application/json"}
            data = {"prompt": prompt}
            scheduler_url = f"http://{ip}/user/prompt"
            response = requests.get(scheduler_url, headers=headers, json=data, timeout=10)
            if response.status_code == 200:
                response_data = response.json()
                return response_data.get("prefill", ""), response_data.get("request_id", "")
            else:
                return "", ""
        except Exception as e:
            return "", ""

    def send_concurrent_requests(self, scheduler_ip, model, messages_list, stream=False):
        """发送并发请求"""
        total_requests = len(messages_list)

        print(f"开始发送 {total_requests} 个请求，最大并发数: {self.max_concurrent}")
        print("=" * 60)

        start_time = time.time()

        with ThreadPoolExecutor(max_workers=self.max_concurrent) as executor:
            # 提交所有任务
            future_to_id = {}
            for i, messages in enumerate(messages_list):
                # 为每个请求查询代理IP
                proxy_ip, sch_id = self.query_prompt(scheduler_ip, messages)
                if not proxy_ip:
                    self.results[i] = {
                        "request_id": i,
                        "success": False,
                        "error": "无法获取代理IP",
                        "content": "",
                        "prompt_tokens": 0,
                        "completion_tokens": 0,
                        "total_tokens": 0,
                        "ttft": 0,
                        "tpot": 0,
                        "total_time": 0
                    }
                    continue

                url = f"http://{proxy_ip}/v1/chat/completions"
                future = executor.submit(self.chat_completion, url, model, messages, sch_id, stream, i)
                future_to_id[future] = i

            # 等待所有任务完成并收集结果
            for future in as_completed(future_to_id):
                request_id = future_to_id[future]
                try:
                    result = future.result()
                    self.results[request_id] = result
                except Exception as e:
                    self.results[request_id] = {
                        "request_id": request_id,
                        "success": False,
                        "error": f"执行错误: {e}",
                        "content": "",
                        "prompt_tokens": 0,
                        "completion_tokens": 0,
                        "total_tokens": 0,
                        "ttft": 0,
                        "tpot": 0,
                        "total_time": 0
                    }

        total_time = time.time() - start_time

        # 所有请求完成后统一显示结果
        self.display_results(total_time, stream)

    def display_results(self, total_time, stream):
        """统一显示所有请求的结果"""
        print("\n" + "=" * 60)
        print("所有请求完成! 汇总结果:")
        print("=" * 60)

        # 按请求ID排序显示
        sorted_results = sorted(self.results.items(), key=lambda x: x[0])

        successful_requests = 0
        total_prompt_tokens = 0
        total_completion_tokens = 0
        total_tokens = 0
        total_ttft = 0
        total_tpot = 0

        for request_id, result in sorted_results:
            print(f"\n[请求{request_id}]")

            if not result["success"]:
                print(f"  状态: 失败")
                print(f"  错误: {result['error']}")
                continue

            successful_requests += 1

            # 显示内容（如果启用）
            if self.show_output:
                print(f"  Assistant: {result['content']}")

            # 显示统计信息
            print(f"  状态: 成功")
            print(f"  总耗时: {result['total_time']:.4f}s")
            print(f"  Prompt tokens: {result['prompt_tokens']}")
            print(f"  Completion tokens: {result['completion_tokens']}")
            print(f"  Total tokens: {result['total_tokens']}")
            print(f"  TTFT: {result['ttft']:.4f}s")

            if stream and result['tpot'] > 0:
                print(f"  TPOT: {result['tpot']:.4f}s/token")

            # 累加统计信息
            total_prompt_tokens += result['prompt_tokens']
            total_completion_tokens += result['completion_tokens']
            total_tokens += result['total_tokens']
            total_ttft += result['ttft']
            total_tpot += result['tpot']

        # 显示总体统计信息
        print("\n" + "=" * 60)
        print("总体统计:")
        print(f"  总请求数: {len(self.results)}")
        print(f"  成功请求数: {successful_requests}")
        print(f"  失败请求数: {len(self.results) - successful_requests}")
        print(f"  总耗时: {total_time:.2f}秒")

        if successful_requests > 0:
            print(f"  平均TTFT: {total_ttft / successful_requests:.4f}s")
            if stream and total_tpot > 0:
                print(f"  平均TPOT: {total_tpot / successful_requests:.4f}s/token")

            print(f"  总Prompt tokens: {total_prompt_tokens}")
            print(f"  总Completion tokens: {total_completion_tokens}")
            print(f"  总Tokens: {total_tokens}")
            print(f"  平均Tokens/请求: {total_tokens / successful_requests:.2f}")

        print("=" * 60)


def main():
    # 配置参数 - 在这里直接设置
    scheduler_ip = "172.18.0.169:8000"
    model = "/workspace/workspace/models/deepseek-ai/DeepSeek-R1-Distill-Qwen-1.5B"
    max_concurrent = 3  # 最大并发请求数
    stream_mode = True  # 设置为True使用流式响应，False使用非流式响应
    total_requests = 3  # 总请求数量
    show_output = True  # 设置为True显示输出内容，False只显示统计信息

    # 创建相同的消息用于所有请求
    base_messages = [
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": "请用简短的话介绍你自己。"}
    ]

    # 创建多个相同的消息
    messages_list = [base_messages.copy() for _ in range(total_requests)]

    # 创建并发管理器
    manager = ConcurrentChatManager(max_concurrent=max_concurrent, show_output=show_output)

    # 发送并发请求
    manager.send_concurrent_requests(scheduler_ip, model, messages_list, stream=stream_mode)


if __name__ == "__main__":
    main()