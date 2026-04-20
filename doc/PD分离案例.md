# PD分离基础案例

## 启动实例

### LMCache+MooncakeMaster

配置lmcache_mooncake.yaml

```yaml
chunk_size: 256
local_cpu: False
max_local_cpu_size: 100  # 本地 CPU 缓存大小 (GB)

# [关键] 指向 Mooncake Store
# 注意：这里连接的是 Mooncake Master 的端口 (50051)
remote_url: "mooncakestore://172.18.0.169:50051/"

# [关键] 开启 CacheGen 压缩
remote_serde: "naive"

# === Mooncake 底层配置 ===
# 通过 extra_config 传递参数给底层的 Mooncake SDK
extra_config:
  # 确保这个地址能被 Decode 节点访问
  metadata_server: "http://172.18.0.169:8080/metadata"
  protocol: "tcp"  # 如果你有 RDMA 环境，这里改成 "rdma"
  master_server_address: "172.18.0.169:50051"
  local_hostname: "worker-node-1"
  global_segment_size: 32212254720 # 30GB
  local_buffer_size: 1073741824 # 1GB
  transfer_timeout: 1
  save_chunk_meta: False

```

配置lmcache_mooncake_decode.yaml

```yaml
chunk_size: 256
local_cpu: False
max_local_cpu_size: 100  # 本地 CPU 缓存大小 (GB)

# [关键] 指向 Mooncake Store
# 注意：这里连接的是 Mooncake Master 的端口 (50051)
remote_url: "mooncakestore://172.18.0.169:50051/"

# [关键] 开启 CacheGen 压缩
remote_serde: "naive"

# === Mooncake 底层配置 ===
# 通过 extra_config 传递参数给底层的 Mooncake SDK
extra_config:
  # 确保这个地址能被 Decode 节点访问
  metadata_server: "http://172.18.0.169:8080/metadata"
  protocol: "tcp"  # 如果你有 RDMA 环境，这里改成 "rdma"
  master_server_address: "172.18.0.169:50051"
  local_hostname: "worker-node-2"
  global_segment_size: 32212254720 # 30GB
  local_buffer_size: 1073741824 # 1GB
  transfer_timeout: 1
  save_chunk_meta: False

```

执行指令

```bash
# 启动 mooncake master
mooncake_master -port 50051 -max_threads 64 -metrics_port 9004 \
  --enable_http_metadata_server=true \
  --http_metadata_server_host=0.0.0.0 \
  --http_metadata_server_port=8080
  
# 启动 P 实例
UCX_TLS=cuda_ipc,cuda_copy,tcp CUDA_VISIBLE_DEVICES=0 VLLM_USE_V1=1 PYTHONHASHSEED=0 VLLM_ENABLE_V1_MULTIPROCESSING=1 VLLM_WORKER_MULTIPROC_METHOD=spawn LMCACHE_CONFIG_FILE="./lmcache_mooncake.yaml" LMCACHE_USE_EXPERIMENTAL=True python3 -m vllm.entrypoints.openai.api_server     --model /workspace/llm-stack/models/deepseek-ai/DeepSeek-R1-Distill-Qwen-1.5B     --port 8001     --max-model-len 10000     --gpu-memory-utilization 0.8  --enforce-eager   --kv-transfer-config '{"kv_connector":"LMCacheConnectorV1", "kv_role":"kv_producer","kv_connector_extra_config": {"discard_partial_chunks": false, "lmcache_rpc_port": "producer1"}}'


# 启动 D 实例
UCX_TLS=cuda_ipc,cuda_copy,tcp NCCL_SOCKET_IFNAME=eno5 GLOO_SOCKET_IFNAME=eno5 CUDA_VISIBLE_DEVICES=1 VLLM_USE_V1=1 PYTHONHASHSEED=0 VLLM_ENABLE_V1_MULTIPROCESSING=1 VLLM_WORKER_MULTIPROC_METHOD=spawn LMCACHE_CONFIG_FILE="./lmcache_mooncake_decode.yaml" LMCACHE_USE_EXPERIMENTAL=True python3 -m vllm.entrypoints.openai.api_server     --model /workspace/llm-stack/models/deepseek-ai/DeepSeek-R1-Distill-Qwen-1.5B     --port 8002     --max-model-len 10000  --enforce-eager   --gpu-memory-utilization 0.8     --kv-transfer-config '{"kv_connector":"LMCacheConnectorV1", "kv_role":"kv_consumer","kv_connector_extra_config": {"discard_partial_chunks": false, "lmcache_rpc_port": "consumer1"}}'


# 执行测试请求脚本
python3 pd_test.py
```

### LMCache+MooncakeMaster+CacheGen

配置lmcache_mooncake.yaml

```yaml
# === LMCache 核心配置 ===
chunk_size: 256
local_device: "cpu"
max_local_cpu_size: 5  # 本地 CPU 缓存大小 (GB)

# [关键] 指向 Mooncake Store
# 注意：这里连接的是 Mooncake Master 的端口 (50051)
remote_url: "mooncakestore://172.18.0.169:50051/"

# [关键] 开启 CacheGen 压缩
remote_serde: "cachegen"

# === Mooncake 底层配置 ===
# 通过 extra_config 传递参数给底层的 Mooncake SDK
extra_config:
  # 确保这个地址能被 Decode 节点访问
  metadata_server: "http://172.18.0.169:8080/metadata"
  protocol: "tcp"  # 如果你有 RDMA 环境，这里改成 "rdma"
  master_server_address: "172.18.0.169:50051"
  local_hostname: "worker-node-1"
```

配置lmcache_mooncake_decode.yaml

```yaml
chunk_size: 256
local_device: "cpu"
max_local_cpu_size: 5  # 本地 CPU 缓存大小 (GB)

# [关键] 指向 Mooncake Store
# 注意：这里连接的是 Mooncake Master 的端口 (50051)
remote_url: "mooncakestore://172.18.0.169:50051/"

# [关键] 开启 CacheGen 压缩
remote_serde: "cachegen"

# === Mooncake 底层配置 ===
# 通过 extra_config 传递参数给底层的 Mooncake SDK
extra_config:
  # 确保这个地址能被 Decode 节点访问
  metadata_server: "http://172.18.0.169:8080/metadata"
  protocol: "tcp"  # 如果你有 RDMA 环境，这里改成 "rdma"
  master_server_address: "172.18.0.169:50051"
  local_hostname: "worker-node-2"
```

执行指令

```bash
# 创建docker容器
sudo docker run --gpus all --shm-size=16g -it --network host -v /llm-stack:/workspace/llm-stack --name test cacheroute:vllm0.13-lmcache3.11-pytorch2.9.1

# 快速临时安装mooncake
pip install mooncake-transfer-engine -i https://pypi.tuna.tsinghua.edu.cn/simple/

# 打开docker容器
sudo docker exec -it test /bin/bash

# 启动 mooncake master
mooncake_master --rpc_address 172.18.0.169 --rpc_port 50051 --metrics_port 9002 --enable_http_metadata_server=true --http_metadata_server_host=172.18.0.169 --http_metadata_server_port=8080

# 启动 P 实例
CUDA_VISIBLE_DEVICES=0 VLLM_USE_V1=1 PYTHONHASHSEED=0 LMCACHE_CONFIG_FILE="./lmcache_mooncake.yaml" LMCACHE_USE_EXPERIMENTAL=True python3 -m vllm.entrypoints.openai.api_server     --model /workspace/llm-stack/models/deepseek-ai/DeepSeek-R1-Distill-Qwen-1.5B     --port 8001     --max-model-len 4096 --max-num-seqs 8     --gpu-memory-utilization 0.8     --kv-transfer-config '{"kv_connector":"LMCacheConnectorV1", "kv_role":"kv_producer"}'

# 启动 D 实例
NCCL_SOCKET_IFNAME=eno5 GLOO_SOCKET_IFNAME=eno5 CUDA_VISIBLE_DEVICES=1 VLLM_USE_V1=1 PYTHONHASHSEED=0 LMCACHE_CONFIG_FILE="./lmcache_mooncake_decode.yaml" LMCACHE_USE_EXPERIMENTAL=True python3 -m vllm.entrypoints.openai.api_server     --model /workspace/llm-stack/models/deepseek-ai/DeepSeek-R1-Distill-Qwen-1.5B     --port 8002     --max-model-len 4096 --max-num-seqs 8     --gpu-memory-utilization 0.8     --kv-transfer-config '{"kv_connector":"LMCacheConnectorV1", "kv_role":"kv_consumer"}'

# 执行测试请求脚本
python3 pd_test.py
```



### LMCache+Redis+CacheGen

配置lmcache.yaml

```yaml
 chunk_size: 256

 local_cpu: true
 max_local_cpu_size: 80.0

 remote_url: "redis://127.0.0.1:6378"
 remote_serde: "cachegen"

 local_disk: null
 max_local_disk_size: 0

 save_decode_cache: false
 cache_policy: "LRU"
 numa_mode: null
```

执行指令

```bash
# 创建docker容器
sudo docker run --gpus all --shm-size=16g -it --network host -v /llm-stack:/workspace/llm-stack --name test cacheroute:vllm0.13-lmcache3.11-pytorch2.9.1

# 打开docker容器
sudo docker exec -it test /bin/bash

# 创建redis容器
sudo docker run -d --name lmcache-redis --network host redis:7 redis-server --save "" --appendonly no --maxmemory 200gb --maxmemory-policy allkeys-lru

# 打开redis容器
sudo docker exec -it lmcache-redis /bin/bash

# 清理redis缓存
redis-cli flushall

# 查看redis缓存大小
redis-cli info memory | grep used_memory_human


cd /workspace/llm-stack/workspace_LLL/cachegen

# 启动 P 实例
export CUDA_VISIBLE_DEVICES=0
export PYTHONHASHSEED=0
export LMCACHE_REMOTE_URL="redis://127.0.0.1:6379"
export LMCACHE_REMOTE_SERDE="cachegen"
export LMCACHE_CONFIG_FILE=/workspace/llm-stack/workspace_LLL/cachegen/lmcache.yaml
python3 -m vllm.entrypoints.openai.api_server \
--model /workspace/llm-stack/models/deepseek-ai/DeepSeek-R1-Distill-Qwen-1.5B \
--host 0.0.0.0 --port 8001 \
--kv-transfer-config '{"kv_connector":"LMCacheConnectorV1","kv_role":"kv_producer"}' \
--gpu-memory-utilization 0.8 \
--max-model-len 4096 \
--max-num-seqs 8 \
--dtype auto \
--max-num-batched-tokens 8192 \
--kv-offloading-backend lmcache \
--kv-offloading-size 64 \
--disable-hybrid-kv-cache-manager \
--kv-cache-metrics

# 启动 D 实例
export CUDA_VISIBLE_DEVICES=1
export PYTHONHASHSEED=0
export LMCACHE_REMOTE_URL="redis://127.0.0.1:6379"
export LMCACHE_REMOTE_SERDE="cachegen"
export LMCACHE_CONFIG_FILE=/workspace/llm-stack/workspace_LLL/cachegen/lmcache.yaml
python3 -m vllm.entrypoints.openai.api_server \
--model /workspace/llm-stack/models/deepseek-ai/DeepSeek-R1-Distill-Qwen-1.5B \
--host 0.0.0.0 --port 8002 \
--kv-transfer-config '{"kv_connector":"LMCacheConnectorV1","kv_role":"kv_consumer"}' \
--gpu-memory-utilization 0.8 \
--max-model-len 4096 \
--max-num-seqs 8 \
--dtype auto \
--max-num-batched-tokens 8192 \
--kv-offloading-backend lmcache \
--kv-offloading-size 64 \
--disable-hybrid-kv-cache-manager \
--kv-cache-metrics

# 执行测试请求脚本
python3 pd_test.py
```



## 测试脚本

pd_test.py

```python
import requests
import time
import sys
# === 配置 ===
PREFILL_URL = "http://172.18.0.169:8001/v1/chat/completions"
DECODE_URL  = "http://172.18.0.169:8002/v1/chat/completions"
MODEL = "/workspace/llm-stack/models/deepseek-ai/DeepSeek-R1-Distill-Qwen-1.5B"
# 一个足够长的 Prompt，让 Prefill 变得慢，这样才能看出 Decode 命中的效果
PROMPT = "what is deepseek" * 256
def run_prefill():
    print(f"\n[Step 1] 发送给 Prefill 节点 (Port 8100)...")
    payload = {
        "model": MODEL,
        "messages": [{"role": "user", "content": PROMPT}],
        "max_tokens": 1,  # <--- 关键！只生成 1 个 token 就停
        "stream": False
    }
    start = time.time()
    resp = requests.post(PREFILL_URL, json=payload)
    end = time.time()
    print(f"Prefill 完成! 耗时: {end - start:.4f}s")
    print(f"   (此时 KV Cache 已经通过 LMCache 推送到 Redis 了)")
def run_decode():
    print(f"\n[Step 2] 发送给 Decode 节点 (Port 8200)...")
    payload = {
        "model": MODEL,
        "messages": [{"role": "user", "content": PROMPT}], # 必须是完全一样的 Prompt
        "max_tokens": 100, # 继续生成后续内容
        "stream": False
    }
    start = time.time()
    # 第一次请求 Decode 节点
    resp = requests.post(DECODE_URL, json=payload)
    end = time.time()
    # 计算 TTFT (首字延迟)
    # 如果命中了 KV Cache，这个时间应该非常短（接近纯网络开销+Decode时间），而不是 Prefill 时间
    print(f"Decode 完成! 总耗时: {end - start:.4f}s")
    try:
        content = resp.json()['choices'][0]['message']['content']
        print(f"   生成内容预览: {content[:50]}...")
    except:
        print("   请求可能失败，请检查 Server 日志")
if __name__ == "__main__":
    # 1. 先做 Prefill
    run_prefill()
    print("Waiting for transport...")
    time.sleep(3) 
    run_decode()

```

benchmark.py

```python
import requests
import time
import json

# === 配置 ===
# 直接连接你的 Decode 节点 (或者 Prefill 节点也可以，只要它没存过这个 Prompt)
# 确保使用真实 IP 以避免防火墙问题
TARGET_URL = "http://172.18.0.169:8002/v1/chat/completions"

# 使用和之前完全一样的模型和 Prompt
MODEL = "/workspace/llm-stack/models/deepseek-ai/DeepSeek-R1-Distill-Qwen-1.5B"

# 为了确保缓存绝对不命中，我们在 Prompt 后面加一点随机时间戳，或者改一个词
# 这样 Hash 值变了，Mooncake 肯定找不到缓存
PROMPT = "Explain the detailed history of Quantum Mechanics, including key figures. " * 100 + str(time.time())


def run_standalone_benchmark():
    print(f"--- 开始基准测试 (Standalone Benchmark) ---")
    print(f"目标: {TARGET_URL}")
    print(f"说明: 这次将强制发生 Cache Miss，由单节点完成所有计算。")

    payload = {
        "model": MODEL,
        "messages": [{"role": "user", "content": PROMPT}],
        # 保持和之前 pd_test.py 一样的参数，公平对比
        "max_tokens": 20,
        "stream": False
    }

    start_time = time.time()

    try:
        response = requests.post(TARGET_URL, json=payload)
        response.raise_for_status()
        result = response.json()
    except Exception as e:
        print(f"❌ 请求失败: {e}")
        return

    end_time = time.time()
    total_time = end_time - start_time

    print(f"\n 完整推理完成!")
    print(f" 总耗时: {total_time:.4f} 秒")

    # 打印一些 Token 信息辅助分析
    if "usage" in result:
        prompt_tokens = result['usage']['prompt_tokens']
        total_tokens = result['usage']['total_tokens']
        print(f" 处理 Token 数: Prompt={prompt_tokens}, Total={total_tokens}")


if __name__ == "__main__":
    run_standalone_benchmark()
```

