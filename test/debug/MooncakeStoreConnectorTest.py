# 执行此代码前，先执行：
# 启动metadata服务
# python3 ./http_server.py
# 启动mooncake_master
# mooncake_master --port 50001

import os
import time
from multiprocessing import Event, Process, Queue

import pynvml  # 用于GPU监控
from transformers import AutoTokenizer

from vllm import LLM, SamplingParams
from vllm.config import KVTransferConfig


# --- 配置参数 ---
MODEL_DIR = "./models"  # 模型位置
MODEL_NAME = "deepseek-ai/DeepSeek-R1-Distill-Qwen-1.5B"  # 模型名称
NUM_CONCURRENT_REQUESTS = 25  # 并发请求数 (批处理大小)
TARGET_PROMPT_TOKENS = 512  # 每个请求的目标输入Token数
MAX_OUTPUT_TOKENS = 512  # 最大输出Token数
GPU_MONITOR_INTERVAL = 0.1  # GPU监控采样间隔（秒）
SHOW_TOKENS = False  # 展示输入token和输出token数


# --- GPU监控进程 ---
def gpu_monitor_process(stop_event: Event):
    global GPU_MONITOR_INTERVAL
    try:
        pynvml.nvmlInit()
        handle = pynvml.nvmlDeviceGetHandleByIndex(0)
        utilization_log = []
        memory_log = []

        while not stop_event.is_set():
            try:
                util = pynvml.nvmlDeviceGetUtilizationRates(handle)
                mem_info = pynvml.nvmlDeviceGetMemoryInfo(handle)
                utilization_log.append(util.gpu)
                memory_log.append(mem_info.used / (1024 ** 2))  # MB
            except Exception as e:
                utilization_log.append(-1)
                memory_log.append(-1)
            time.sleep(GPU_MONITOR_INTERVAL)

        # 计算平均值
        valid_utils = [u for u in utilization_log if u != -1]
        avg_util = sum(valid_utils) / len(valid_utils) if valid_utils else 0

        valid_mems = [m for m in memory_log if m != -1]
        avg_mem = sum(valid_mems) / len(valid_mems) if valid_mems else 0

        # 直接打印结果
        print(f"  Average GPU utilization: {avg_util:.2f}%")
        print(f"  Average GPU Memory Usage: {avg_mem:.2f} MB")

    except Exception as e:
        print(f"[GPU Monitor] Error: {e}")
    finally:
        try:
            pynvml.nvmlShutdown()
        except:
            pass


def generate_prompt_batch(tokenizer, target_token_count, num_prompts):
    """生成一批prompt，每个prompt约有target_token_count个token，且内容不同"""
    # 定义多个基础文本片段（可扩展）
    base_texts = [
        "这是一个用于测试长文本的示例句子。",
        "我们通过重复它直到达到目标长度。",
        "为了生成多样化的prompt，我们可以改变文本结构。",
        "例如，使用不同的填充模式或数字序列。",
    ]
    base_tokens_list = [tokenizer.encode(text, add_special_tokens=False) for text in base_texts]
    
    # 确保每个基础文本片段至少有一个token
    for i, tokens in enumerate(base_tokens_list):
        if not tokens:
            base_texts[i] = "测试"
            base_tokens_list[i] = tokenizer.encode("测试", add_special_tokens=False)
    
    # 计算每个prompt的token分配策略
    prompts = []
    for i in range(num_prompts):
        # 选择不同的基础片段组合
        selected_tokens = []
        remaining_tokens = target_token_count
        for j in range(len(base_tokens_list)):
            # 按索引顺序选择片段，确保每次执行结果相同
            if remaining_tokens <= 0:
                break
            repeat_times = min(remaining_tokens // len(base_tokens_list[j]), 5)  # 最多重复5次
            selected_tokens.extend(base_tokens_list[j] * repeat_times)
            remaining_tokens -= len(base_tokens_list[j]) * repeat_times
        
        # 用数字序列填充剩余token（确保确定性）
        if remaining_tokens > 0:
            filler = tokenizer.encode(" ".join(str(i * 100 + j) for j in range(remaining_tokens)), add_special_tokens=False)
            selected_tokens.extend(filler[:remaining_tokens])
        
        # 截断到目标长度
        final_tokens = selected_tokens[:target_token_count]
        final_text = tokenizer.decode(final_tokens)
        prompts.append(final_text)
    
    return prompts


model_path = os.path.join(MODEL_DIR, MODEL_NAME)
os.environ["VLLM_USE_MODELSCOPE"] = "True"
tokenizer = AutoTokenizer.from_pretrained(model_path, trust_remote_code=True)
prompts = generate_prompt_batch(tokenizer, TARGET_PROMPT_TOKENS, NUM_CONCURRENT_REQUESTS)


# --- Prefill进程 ---
def run_prefill(prefill_done, queue):
    os.environ["CUDA_VISIBLE_DEVICES"] = "0"
    os.environ["VLLM_USE_V1"] = "0"
    os.environ["MOONCAKE_CONFIG_PATH"] = "./mooncake.json"

    global prompts
    global model_path

    sampling_params = SamplingParams(temperature=0, top_p=0.95, max_tokens=1)

    ktc = KVTransferConfig(
        kv_connector="MooncakeStoreConnector",
        kv_role="kv_producer",
    )

    llm = LLM(
        model=model_path,
        kv_transfer_config=ktc,
        max_model_len=20000,
        gpu_memory_utilization=0.8,
    )

    start_time = time.time()
    llm.generate(prompts, sampling_params)
    p_time = time.time() - start_time
    queue.put(p_time)

    print("Prefill node is finished.")
    prefill_done.set()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("Script stopped by user.")


# --- Decode进程 ---
def run_decode(prefill_done, queue):
    os.environ["CUDA_VISIBLE_DEVICES"] = "1"
    os.environ["VLLM_USE_V1"] = "0"
    os.environ["MOONCAKE_CONFIG_PATH"] = "./mooncake.json"

    global prompts
    global model_path
    global MAX_OUTPUT_TOKENS
    global NUM_CONCURRENT_REQUESTS
    global SHOW_TOKENS

    sampling_params = SamplingParams(temperature=0, top_p=0.95, max_tokens=MAX_OUTPUT_TOKENS)

    ktc = KVTransferConfig(
        kv_connector="MooncakeStoreConnector",
        kv_role="kv_consumer",
    )

    llm = LLM(
        model=model_path,
        kv_transfer_config=ktc,
        max_model_len=20000,
        gpu_memory_utilization=0.8,
    )

    print("Waiting for prefill node to finish...")
    prefill_done.wait()

    start_time = time.time()
    outputs = llm.generate(prompts, sampling_params)
    d_time = time.time() - start_time
    p_time = queue.get()

    total_input_token = 0
    total_output_token = 0
    for output in outputs:
        if SHOW_TOKENS:
            # print(f"Prompt: {output.prompt!r}, Generated text: {output.outputs[0].text!r}")
            print("prompt tokens:{}".format(len(output.prompt_token_ids)))
            print("output tokens:{}".format(len(output.outputs[0].token_ids)))
            print("-" * 60)
        total_input_token += len(output.prompt_token_ids)
        total_output_token += len(output.outputs[0].token_ids)
    print(f"  Prefill generation time: {p_time} seconds.")
    print(f"  Decode generation time: {d_time} seconds.")
    print(f"  Prefill input tokens: {total_input_token} tokens.")
    print(f"  Decode output tokens: {total_output_token - 1*NUM_CONCURRENT_REQUESTS} tokens.")
    print(f"  Prefill input Throughput: {total_input_token / p_time} tokens/second")
    print(f"  Decode output Throughput: {(total_output_token - 1*NUM_CONCURRENT_REQUESTS) / d_time} tokens/second")


def main():
    # 启动 GPU 监控进程
    stop_event = Event()
    gpu_monitor = Process(
        target=gpu_monitor_process,
        args=(stop_event,),
        name="GPU Monitor"
    )
    gpu_monitor.start()

    # 启动 prefill 和 decode 进程
    prefill_done = Event()
    q = Queue()
    prefill_process = Process(target=run_prefill, args=(prefill_done, q))
    decode_process = Process(target=run_decode, args=(prefill_done, q))

    # 开始 Prefill 结点
    prefill_process.start()

    # 开始 Decode 结点
    decode_process.start()

    # 当 Decode 结点结束时终止 Prefill结点
    decode_process.join()
    prefill_process.terminate()

    # 停止 GPU 监控进程
    stop_event.set()
    gpu_monitor.join(timeout=5)


if __name__ == "__main__":
    main()
