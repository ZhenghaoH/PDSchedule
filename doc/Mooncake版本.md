# 基于MooncakeP2PConnector的PD分离方案的旧版本说明

## 环境说明

旧版本的docker环境可以完全参考 [此文档](./docker/vLLM-Docker环境安装笔记.md) 中的描述进行环境安装，包含：

- Pytorch-2.7.0
- vLLM-0.9.2
- Mooncake
- LMCache

此环境与当前实际版本的docker是有区别的，使用的是P2P的方式而不是XPYD的方式，因而项目代码中也有一部分需要修改：

- 将 `scheduler/gateway`目录下所有文件中import导入部分xxx_xpyd的导入改为xxx_base，从而启用P2P分离方式下的调度算法
- 在`config/instanceP.py`和`config/instanceD.py`中将`KV_CONNECTOR`参数改为 mooncake

## 快速开始

### 1 逐一启动指令

```bash
# 在config.ADDRESS_CONFIG里修改IP地址等

# 在5090机器上创建docker容器
sudo docker run --gpus all --shm-size=16g -it --network host -v /home/zkgy/vLLM/workspace:/workspace/workspace --name test vllm-5090:latest

# 在3090机器上创建docker容器
sudo docker run --gpus all -it --network host -v /home/jtdx/vLLM/workspace:/workspace/workspace --name test vllm-cuda128:latest

# 启动容器
sudo docker exec -it test /bin/bash

# 修改源码
cp /workspace/workspace/FlexPD/instance/vLLM/mooncake_v0/mooncake_pipe.py /workspace/vllm/vllm/distributed/kv_transfer/kv_pipe/mooncake_pipe.py 

cp /workspace/workspace/FlexPD/instance/vLLM/mooncake_v0/simple_buffer.py /workspace/vllm/vllm/distributed/kv_transfer/kv_lookup_buffer/simple_buffer.py

# 启动metadata server
cd /workspace/workspace/FlexPD/entrypoints
python3 http_server.py

# 启动控制器
cd /workspace/workspace/FlexPD/entrypoints
python3 controller.py

# 启动P池
cd /workspace/workspace/FlexPD/entrypoints
python3 gatewayP.py

# 启动D池
cd /workspace/workspace/FlexPD/entrypoints
python3 gatewayD.py

# 启动vLLM P实例
# 单卡
cd /workspace/vllm
FORCE_STOP_ON_UPDATE=0 HTTP_IP=172.18.0.169:8101 CUDA_VISIBLE_DEVICES=0 VLLM_USE_V1=0 MOONCAKE_CONFIG_PATH=/workspace/workspace/mooncake2.json VLLM_USE_MODELSCOPE=True python3 -m vllm.entrypoints.openai.api_server --model /workspace/workspace/models/deepseek-ai/DeepSeek-R1-Distill-Qwen-1.5B --port 8001 --max-model-len 10000 --gpu-memory-utilization 0.8 --kv-transfer-config '{"kv_connector":"MooncakeConnector","kv_role":"kv_producer","kv_rank":0,"kv_parallel_size":2,"kv_buffer_size":2e9}' --host 172.18.0.169

# 多卡
FORCE_STOP_ON_UPDATE=0 HTTP_IP=172.18.0.169:8101 CUDA_VISIBLE_DEVICES=0,1 VLLM_USE_V1=0 MOONCAKE_CONFIG_PATH=/workspace/workspace/mooncake2.json VLLM_USE_MODELSCOPE=True python3 -m vllm.entrypoints.openai.api_server --model /workspace/workspace/models/deepseek-ai/DeepSeek-R1-Distill-Qwen-14B --port 8001 --max-model-len 10000 --gpu-memory-utilization 0.8 --kv-transfer-config '{"kv_connector":"MooncakeConnector","kv_role":"kv_producer","kv_rank":0,"kv_parallel_size":2,"kv_buffer_size":2e9}' --host 172.18.0.169 -tp 2

# 启动P实例代理
cd /workspace/workspace/FlexPD/entrypoints
python3 instanceP.py

# 启动vLLM D实例
# 单卡
cd /workspace/vllm
FORCE_STOP_ON_UPDATE=0 HTTP_IP=172.18.0.169:8102 CUDA_VISIBLE_DEVICES=1 VLLM_USE_V1=0 MOONCAKE_CONFIG_PATH=/workspace/workspace/mooncake2.json VLLM_USE_MODELSCOPE=True python3 -m vllm.entrypoints.openai.api_server --model /workspace/workspace/models/deepseek-ai/DeepSeek-R1-Distill-Qwen-1.5B --port 8002 --max-model-len 10000 --gpu-memory-utilization 0.8 --kv-transfer-config '{"kv_connector":"MooncakeConnector","kv_role":"kv_consumer","kv_rank":1,"kv_parallel_size":2,"kv_buffer_size":2e9}' --host 172.18.0.169

# 多卡
FORCE_STOP_ON_UPDATE=0 HTTP_IP=172.18.0.169:8102 CUDA_VISIBLE_DEVICES=2,3 VLLM_USE_V1=0 MOONCAKE_CONFIG_PATH=/workspace/workspace/mooncake2.json VLLM_USE_MODELSCOPE=True python3 -m vllm.entrypoints.openai.api_server --model /workspace/workspace/models/deepseek-ai/DeepSeek-R1-Distill-Qwen-14B --port 8002 --max-model-len 10000 --gpu-memory-utilization 0.8 --kv-transfer-config '{"kv_connector":"MooncakeConnector","kv_role":"kv_consumer","kv_rank":1,"kv_parallel_size":2,"kv_buffer_size":2e9}' --host 172.18.0.169 -tp 2

# 启动D实例代理
cd /workspace/workspace/FlexPD/entrypoints
python3 instanceD.py

# 启动用户脚本
cd /workspace/workspace/FlexPD/examples/clientUI
python3 client.py

# 在controller中输入指令查看ID
show pool_id
# 在controller中输入指令配对实例
pair pd -p 202 -d 201

# 输入指令，发送请求
http://127.0.0.1:5000/v1/chat/completions -H "Content-Type: application/json" -d '{"model": "/workspace/workspace/models/deepseek-ai/DeepSeek-R1-Distill-Qwen-1.5B","messages": [{"role": "user", "content": "What is DeepSeek"}],"max_tokens": 64,"stream":"True"}' 

http://127.0.0.1:5000/v1/chat/completions -H "Content-Type: application/json" -d '{"model": "/workspace/workspace/models/deepseek-ai/DeepSeek-R1-Distill-Qwen-1.5B","messages": [{"role": "user", "content": "What is DeepSeek"}],"max_tokens": 64,"stream":"False"}'
```



### 2 单机快速启动指令

#### 

````bash
# 在config.ADDRESS_CONFIG里修改IP地址等

# 在5090机器上创建docker容器
sudo docker run --gpus all --shm-size=16g -it --network host -v /home/zkgy/vLLM/workspace:/workspace/workspace --name test vllm-5090:latest

# 在3090机器上创建docker容器
sudo docker run --gpus all -it --network host -v /home/jtdx/vLLM/workspace:/workspace/workspace --name test vllm-cuda128:latest

# 启动容器
sudo docker exec -it test /bin/bash

# 修改源码
cp /workspace/workspace/FlexPD/instance/vLLM/mooncake_v0/mooncake_pipe.py /workspace/vllm/vllm/distributed/kv_transfer/kv_pipe/mooncake_pipe.py 

cp /workspace/workspace/FlexPD/instance/vLLM/mooncake_v0/simple_buffer.py /workspace/vllm/vllm/distributed/kv_transfer/kv_lookup_buffer/simple_buffer.py

# 在docker容器外(确保容器处于start状态)，运行启动脚本
cd /workspace/workspace/FlexPD/examples/mooncake_v0/single
sudo sh ./run.sh

# 启动用户脚本
cd /workspace/workspace/FlexPD/examples/clientUI
python3 client.py

# 在controller中输入指令查看ID
show pool_id
# 在controller中输入指令配对实例
pair pd -p 202 -d 201

# 输入指令，发送请求
http://127.0.0.1:5000/v1/chat/completions -H "Content-Type: application/json" -d '{"model": "/workspace/workspace/models/deepseek-ai/DeepSeek-R1-Distill-Qwen-1.5B","messages": [{"role": "user", "content": "What is DeepSeek"}],"max_tokens": 64,"stream":"True"}' 

http://127.0.0.1:5000/v1/chat/completions -H "Content-Type: application/json" -d '{"model": "/workspace/workspace/models/deepseek-ai/DeepSeek-R1-Distill-Qwen-1.5B","messages": [{"role": "user", "content": "What is DeepSeek"}],"max_tokens": 64,"stream":"False"}'
````



### 3 多机快速启动指令

#### 

````bash
# 在config.ADDRESS_CONFIG里修改IP地址等

# 在5090*8机器上创建docker容器
sudo docker run --gpus all --shm-size=16g -it --network host -v /home/zkgy/vLLM/workspace:/workspace/workspace --name test vllm-5090:latest

# 在5090*7机器上创建docker容器
sudo docker run --gpus all --shm-size=16g -it --network host -v /llm-stack:/workspace/workspace --name test vllm-5090:latest

# 在3090机器上创建docker容器
sudo docker run --gpus all -it --network host -v /home/jtdx/vLLM/workspace:/workspace/workspace --name test vllm-cuda128:latest

# 启动容器
sudo docker exec -it test /bin/bash

# 修改源码
cp /workspace/workspace/FlexPD/instance/vLLM/mooncake_v0/mooncake_pipe.py /workspace/vllm/vllm/distributed/kv_transfer/kv_pipe/mooncake_pipe.py 

cp /workspace/workspace/FlexPD/instance/vLLM/mooncake_v0/simple_buffer.py /workspace/vllm/vllm/distributed/kv_transfer/kv_lookup_buffer/simple_buffer.py

# 在docker容器外(确保容器处于start状态)，运行启动脚本
# 在8卡5090上
cd /workspace/workspace/FlexPD/examples/mooncake_v0/multi
sudo sh ./5090a.sh

# 在7卡5090上
cd /workspace/workspace/FlexPD/examples/mooncake_v0/multi
sudo sh ./5090b.sh

# 在3090-166上
cd /workspace/workspace/FlexPD/examples/mooncake_v0/multi
sudo sh ./3090.sh

# 启动用户脚本
cd /workspace/workspace/FlexPD/examples/clientUI
python3 client.py

# 在controller中输入指令查看ID
show pool_id
# 在controller中输入指令配对实例
pair pd -p 1 -d 202
pair pd -p 1 -d 202 --force=true
pair pd -p 202 -d 201
pair pd -p 202 -d 201 --force=true

# 输入指令，发送请求
http://127.0.0.1:5000/v1/chat/completions -H "Content-Type: application/json" -d '{"model": "/workspace/workspace/models/deepseek-ai/DeepSeek-R1-Distill-Qwen-7B","messages": [{"role": "user", "content": "What is DeepSeek"}],"max_tokens": 64,"stream":"True"}' 

http://127.0.0.1:5000/v1/chat/completions -H "Content-Type: application/json" -d '{"model": "/workspace/workspace/models/deepseek-ai/DeepSeek-R1-Distill-Qwen-7B","messages": [{"role": "user", "content": "What is DeepSeek"}],"max_tokens": 64,"stream":"False"}'
````

