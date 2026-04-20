# 执行此代码前，先执行：
# 启动metadata服务
# python3 ./http_server.py
# 启动mooncake_master
# mooncake_master --port 50001

# 修改  vllm/vllm/distributed/kv_transfer/kv_lookup_buffer/mooncake_store.py 代码，以获取 DEBUG.txt 输出

# TC设置延时：sudo tc qdisc add dev lo root netem delay 10ms
# TC删除延时：sudo tc qdisc del dev lo root

import os
import time
import re
from multiprocessing import Event, Process, Queue

import pynvml  # 用于GPU监控
from transformers import AutoTokenizer

from vllm import LLM, SamplingParams
from vllm.config import KVTransferConfig

# --- 配置参数 ---
MODEL_DIR = "./models"  # 模型位置
MODEL_NAME = "deepseek-ai/DeepSeek-R1-Distill-Qwen-1.5B"  # 模型名称
NUM_CONCURRENT_REQUESTS = 1  # 并发请求数 (批处理大小)
TARGET_PROMPT_TOKENS = 256 - 1  # 每个请求的目标输入Token数
MAX_OUTPUT_TOKENS = 256 + 1  # 最大输出Token数
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
        print(f"  Max GPU utilization: {max(valid_utils):.2f}%")
        print(f"  Max GPU Memory Usage: {max(valid_mems):.2f} MB")
        print(f"  Average GPU utilization: {avg_util:.2f}%")
        print(f"  Average GPU Memory Usage: {avg_mem:.2f} MB")

    except Exception as e:
        print(f"[GPU Monitor] Error: {e}")
    finally:
        try:
            pynvml.nvmlShutdown()
        except:
            pass


def process_log_file(filename, clear=False):
    """
    从指定日志文件中提取数据并计算统计结果
        filename (str): 日志文件路径
        clear: 是否在读取后清除文件内容
    日志文件参考内容：
    L DEBUG P1: 1750130353.1963909
    L DEBUG P2: 1750130353.1984763
    L DEBUG D1: 1750130353.2414515
    L DEBUG D2: 1750130353.2681594
    """
    # 读取文件内容
    with open(filename, 'r') as f:
        logs = f.read()

    # --- 提取时间戳 ---
    def parse_logs(logs):
        """从日志字符串中提取阶段（P/D）、序号（1/2）和时间戳"""
        pattern = r"L DEBUG ([PD])(\d): ([\d.]+)"
        matches = re.findall(pattern, logs)
        results = {"P1": [], "P2": [], "D1": [], "D2": []}
        for stage, num, ts in matches:
            key = f"{stage}{num}"
            try:
                results[key].append(float(ts))
            except ValueError:
                print(f"Warning: Timestamp {ts} cannot be converted to float")
        return results

    data = parse_logs(logs)

    # --- 计算每个阶段的时延 ---
    def calculate_delays(data, stage_prefix):
        """计算指定阶段（如P或D）的时延"""
        p1 = data.get(f"{stage_prefix}1", [])
        p2 = data.get(f"{stage_prefix}2", [])
        delays = []
        for t1, t2 in zip(p1, p2):
            if t2 < t1:
                print(f"Warning: Timestamp inversion {t1} -> {t2}")
            delays.append(t2 - t1)
        return delays

    prefill_delays = calculate_delays(data, "P")
    decode_delays = calculate_delays(data, "D")

    # --- 统计结果 ---
    def summarize_delays(delays, stage_name):
        """计算总时延、平均时延和总次数"""
        total = sum(delays)
        avg = total / len(delays) if delays else 0
        return {
            "total_delay": total,
            "avg_delay": avg,
            "count": len(delays)
        }

    prefill_summary = summarize_delays(prefill_delays, "Prefill")
    decode_summary = summarize_delays(decode_delays, "Decode")

    # --- 合并总时延 ---
    total_summary = {
        "total_delay": prefill_summary["total_delay"] + decode_summary["total_delay"],
        "avg_delay": (prefill_summary["avg_delay"] + decode_summary["avg_delay"]) / 2,
        "total_count": prefill_summary["count"] + decode_summary["count"]
    }

    if clear:
        with open(filename, "w") as f:
            f.write("")

    return {
        "prefill": prefill_summary,
        "decode": decode_summary,
        "total": total_summary
    }


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
            filler = tokenizer.encode(" ".join(str(i * 100 + j) for j in range(remaining_tokens)),
                                      add_special_tokens=False)
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

    global NUM_CONCURRENT_REQUESTS

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
    print("-" * 60)
    for output in outputs:
        if SHOW_TOKENS:
            # print(f"Prompt: {output.prompt!r}, Generated text: {output.outputs[0].text!r}")
            print("prompt tokens:{}".format(len(output.prompt_token_ids)))
            print("output tokens:{}".format(len(output.outputs[0].token_ids)))
            print("-" * 60)
        total_input_token += len(output.prompt_token_ids)
        total_output_token += len(output.outputs[0].token_ids)

    result = process_log_file("./DEBUG.txt", True)

    print(
        f"Prefill Put: Total {result['prefill']['total_delay']:.3f} s, Avg {result['prefill']['avg_delay']:.3f} s, Count {result['prefill']['count']}")
    print(
        f"Decode Get: Total {result['decode']['total_delay']:.3f} s, Avg {result['decode']['avg_delay']:.3f} s, Count {result['decode']['count']}")

    print(f"  Prefill generation time: {p_time - result['prefill']['total_delay']} seconds.")
    print(f"  KV Cache transmit time: {result['total']['total_delay']} seconds")
    print(f"  Decode generation time:  {d_time - result['decode']['total_delay']} seconds.")
    print(f"  Prefill input tokens: {total_input_token} tokens.")
    print(f"  Decode output tokens: {total_output_token - 1 * NUM_CONCURRENT_REQUESTS} tokens.")
    print(
        f"  Prefill input Throughput: {total_input_token / (p_time - result['prefill']['total_delay'])} tokens/second")
    print(
        f"  Decode output Throughput: {(total_output_token - 1 * NUM_CONCURRENT_REQUESTS) / (d_time - result['decode']['total_delay'])} tokens/second")

    with open('RESULT.txt', 'a') as f:
        print(f"{NUM_CONCURRENT_REQUESTS}", file=f)
        print(f"Prefill generation time: {p_time - result['prefill']['total_delay']}", file=f)
        print(f"KV Cache transmit time: {result['total']['total_delay']}", file=f)
        print(f"Decode generation time:  {d_time - result['decode']['total_delay']}", file=f)
        print(f"Input throughput: {total_input_token / (p_time - result['prefill']['total_delay'])}", file=f)
        print(
            f"Output throughput: {(total_output_token - 1 * NUM_CONCURRENT_REQUESTS) / (d_time - result['decode']['total_delay'])}",
            file=f)


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
