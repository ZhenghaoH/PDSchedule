# 执行此代码前，先执行：
# 启动metadata服务
# python3 ./http_server.py


import os
import time
from multiprocessing import Event, Process, Queue

from vllm import LLM, SamplingParams
from vllm.config import KVTransferConfig

prompts = [
        "你好，请问你能生成一个500字以上的关于安装nvidia显卡驱动的小故事吗?",
        "描述一个包含5个角色的科幻短篇，包含星际旅行、量子计算机和时间悖论等",
        "构思一个18世纪海盗船长的故事，包含沉船宝藏、伪造地图和双面间谍等情节",
        "编写一段发生在22世纪垂直城市的职场剧情节，需涉及AI上司、全息投影会议",
        "设计一个维多利亚时代蒸汽朋克风格侦探故事，包含会说话的机械狗、炼金术",
        "叙述一个关于未来海洋城邦的生态危机事件，涉及基因改造珊瑚、海底火山爆发",
    ]
#prompts = prompts*10

p_time = 0

def run_prefill(prefill_done, queue):
    os.environ["CUDA_VISIBLE_DEVICES"] = "0"
    os.environ["VLLM_USE_V1"] = "0"
    os.environ["MOONCAKE_CONFIG_PATH"] = "./mooncake2.json"

    global prompts
    global p_time

    sampling_params = SamplingParams(temperature=0, top_p=0.95, max_tokens=1)

    ktc = KVTransferConfig(
        kv_connector="MooncakeConnector",
        kv_role="kv_producer",
        kv_rank=0,
        kv_parallel_size=2,  # 固定为2
        kv_buffer_size=2e9,
    )

    llm = LLM(
        model="./models/deepseek-ai/DeepSeek-R1-Distill-Qwen-1.5B",
        kv_transfer_config=ktc,
        max_model_len=2000,
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


def run_decode(prefill_done, queue):
    os.environ["CUDA_VISIBLE_DEVICES"] = "1"
    os.environ["VLLM_USE_V1"] = "0"
    os.environ["MOONCAKE_CONFIG_PATH"] = "./mooncake2.json"

    global prompts
    global p_time

    sampling_params = SamplingParams(temperature=0, top_p=0.95, max_tokens=500)

    ktc = KVTransferConfig(
        kv_connector="MooncakeConnector",
        kv_role="kv_consumer",
        kv_rank=1,
        kv_parallel_size=2,  # 固定为2
        kv_buffer_size=2e9,
    )

    llm = LLM(
        model="./models/deepseek-ai/DeepSeek-R1-Distill-Qwen-1.5B",
        kv_transfer_config=ktc,
        max_model_len=2000,
        gpu_memory_utilization=0.8,
    )

    print("Waiting for prefill node to finish...")
    prefill_done.wait()

    start_time = time.time()
    outputs = llm.generate(prompts, sampling_params)
    d_time = time.time() - start_time
    p_time = queue.get()

    for output in outputs:
        prompt = output.prompt
        generated_text = output.outputs[0].text
        # print(f"Prompt: {prompt!r}, Generated text: {generated_text!r}")
        print("prompt tokens:{}".format(len(output.prompt_token_ids)))
        print("output tokens:{}".format(len(output.outputs[0].token_ids)))
        print("-" * 60)
    print(f"Prefill generation time is {p_time} seconds.")
    print(f"Decode generation time is {d_time} seconds.")


def main():
    prefill_done = Event()
    q = Queue()
    prefill_process = Process(target=run_prefill, args=(prefill_done,q,))
    decode_process = Process(target=run_decode, args=(prefill_done,q,))

    # Start prefill node
    prefill_process.start()

    # Start decode node
    decode_process.start()

    # Terminate the prefill node when decode is finished
    decode_process.join()
    prefill_process.terminate()


if __name__ == "__main__":
    main()