# 环境准备：NVIDIA CUDA 与 NIXL

本指南配合[真实部署流程](quickstart.md)，在同一 Linux 主机、同一容器内运行
Controller、P/D Gateway、P/D Instance Proxy 和两个真实 vLLM 服务。KV 经 NIXL/UCX 传输，
不需要 Redis、LMCache、Mooncake 元数据服务或额外 toy proxy。

## 1. 环境与资源

| 项目 | 本指南使用的配置 |
| --- | --- |
| 主机 | Linux x86_64，Docker Engine、NVIDIA Container Toolkit |
| GPU | 两张可见 NVIDIA GPU；Prefill 使用物理 GPU 0，Decode 使用物理 GPU 1 |
| 模型 | 本地 `DeepSeek-R1-Distill-Qwen-7B`，P/D 使用完全相同的模型和 tokenizer |
| 实例并行度 | P/D 各 TP=1，各占一张 GPU |
| Python | 3.12 |
| vLLM | 0.13.0 |
| PyTorch | 2.9.0，由 vLLM 0.13.0 的依赖约束管理 |
| 容器 CUDA | 官方 vLLM 0.13.0 默认镜像构建采用 CUDA 12.9.1 |
| NIXL | `nixl==0.7.1` 与 `nixl-cu12==0.7.1` |
| KV 通信 | UCX 后端；`UCX_TLS=tcp,cuda_copy`，TCP 路线 |
| API | Controller 5000；P/D Gateway 8081/8082；Instance 7001/7003；vLLM 8001/8003 |

示例上下文长度为 4096、dtype 为 float16。每张卡必须容纳模型权重、运行时开销和 KV cache；
显存不足时选择更小的兼容模型，或增加 TP 并同步修改 GPU、NIXL metadata 和拓扑。
两张 GPU 是这个 TP=1 示例的进程分配，不代表所有模型均可在任意两张 GPU 上运行。

主机驱动须支持容器 CUDA。`nvidia-smi` 显示的 CUDA Version 是驱动支持能力，
不是当前 Python 中 Torch 实际使用的 CUDA；后面同时检查两者。
已自行构建的 CUDA 12.8 / Torch 2.9.1 镜像属于另一条构建路线，不与下面的官方镜像依赖混装。

版本依据：[vLLM CUDA 依赖](https://github.com/vllm-project/vllm/blob/v0.13.0/requirements/cuda.txt)、
[官方 Dockerfile](https://github.com/vllm-project/vllm/blob/v0.13.0/docker/Dockerfile)、
[NIXL 要求](https://github.com/vllm-project/vllm/blob/v0.13.0/requirements/kv_connectors.txt)、
[NIXL CUDA 12 包](https://pypi.org/project/nixl-cu12/0.7.1/)。

## 2. 准备主机目录与容器

安装 Docker 和 [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html)，
确认宿主机可以执行：

```bash
nvidia-smi
docker version
```

准备宿主机目录，仓库位于 `/llm-stack/PDSchedule`，模型位于
`/llm-stack/models/deepseek-ai/DeepSeek-R1-Distill-Qwen-7B`。
模型需由用户准备，包含权重、config 和 tokenizer 文件。

尚未克隆时，在宿主机执行（当前仓库为 private，需要有效 GitHub 访问权限）：

```bash
mkdir -p /llm-stack/models
cd /llm-stack
git clone --branch PDSchedule https://github.com/ZhenghaoH/PDSchedule.git
```

使用官方 vLLM 镜像。第一次创建容器：

```bash
docker pull vllm/vllm-openai:v0.13.0
docker run --name pdschedule-nixl --gpus all -it \
  --network host --ipc=host --shm-size=16g \
  -v /llm-stack:/workspace/llm-stack \
  -w /workspace/llm-stack/PDSchedule \
  --entrypoint /bin/bash \
  vllm/vllm-openai:v0.13.0
```

默认 API 入口被覆盖为 Bash，便于在同一容器中分别启动多个服务。不要同时再运行镜像自带的
默认 8000 端口服务。主机使用 `--network host`，无需另加 `-p` 映射。
`--ipc=host` 使用主机共享内存；这里的 `--shm-size` 不是独立的容器共享内存配额。

若容器已经存在，不重复创建。启动并进入：

```bash
docker start pdschedule-nixl
docker exec -it pdschedule-nixl /bin/bash
```

后续每个服务使用一个独立宿主机终端执行上述 `docker exec`，再运行对应 Python 命令。
七个服务均在同一个容器内，共享模型挂载、包环境和 loopback 网络。

仓库根 `Dockerfile` 仅准备基础系统环境，不等同于上述完整 vLLM 镜像。
本地名为 `cacheroute:...` 的实验镜像不是本项目公开分发的镜像，启动本指南不需要该镜像。

## 3. 安装 NIXL 和应用依赖

以下命令在容器内执行。固定 Torch 约束是为了避免安装应用时替换 serving 栈：

```bash
cd /workspace/llm-stack/PDSchedule
python3 -m pip install \
  'torch==2.9.0' 'nixl==0.7.1' 'nixl-cu12==0.7.1' \
  'fastapi~=0.124.0' 'uvicorn~=0.38.0' 'aiohttp~=3.13.2' \
  'requests~=2.32.5' 'pydantic~=2.12.5' 'jinja2~=3.1.6' \
  'pyyaml~=6.0.3' 'psutil~=7.0.0' 'nvidia-ml-py>=12.535.108' \
  'numpy~=1.26.4' 'scipy~=1.16.3' 'scikit-learn~=1.7.2' \
  'transformers~=4.57.3' 'prometheus-client>=0.18.0'
python3 -m pip check
```

`nixl` 是元包；同时固定 `nixl-cu12`，避免元包的宽松依赖选择另一版 CUDA 实现。
这里的显式依赖集合用于主服务，Gateway 即使关闭 predictor warmup 仍需导入 NumPy、
scikit-learn 和 transformers。根 `requirements.txt` 另外包含历史研究/数据处理工具，
不是本条最小部署路径的安装清单。不要在可用 GPU 环境中盲目升级 Torch、vLLM 或 CUDA 库。

检查安装版本和 GPU：

```bash
python3 - <<'PY'
import sys
from importlib.metadata import version
import torch
import nixl
import fastapi, uvicorn, aiohttp, requests, jinja2, yaml, psutil, sklearn
from prometheus_client.parser import text_string_to_metric_families
print("Python:", sys.version.split()[0])
for package in ("vllm", "torch", "nixl", "nixl-cu12", "transformers"):
    print(package + ":", version(package))
print("Torch CUDA:", torch.version.cuda)
print("CUDA available:", torch.cuda.is_available())
print("Visible GPUs:", torch.cuda.device_count())
assert torch.cuda.is_available(), "Container cannot access CUDA"
assert torch.cuda.device_count() >= 2, "This topology needs two visible GPUs"
PY
```

预期：vLLM 为 0.13.0，Torch 为 2.9.0（可带 CUDA 后缀），两个 NIXL 包为 0.7.1，
`CUDA available: True`，可见 GPU 数量至少为 2。CUDA/UCX 共享库加载失败需先修复镜像环境，
不能用仅安装成功代替 NIXL 后端就绪。

检查 Connector 可用及参数：

```bash
python3 - <<'PY'
from vllm.distributed.kv_transfer.kv_connector.v1.nixl_connector import NixlConnector
print("NixlConnector import: OK")
PY
python3 -m vllm.entrypoints.openai.api_server --help
```

`UCX_TLS=tcp,cuda_copy` 要求 NIXL 所用 UCX 构建包含这些 transport。
若出现 UCX transport/device 或 NIXL agent 初始化错误，按
[NIXL 官方安装说明](https://github.com/ai-dynamo/nixl)检查二进制/UCX 配套关系。
安装 `nixl` 不意味着任意驱动和 UCX 环境都会自动兼容。

## 4. 检查模型与端口

模型绝对路径在 P/D vLLM、Controller 和请求中保持一致，直接使用本地路径作为模型名，
不依赖旧的模型别名映射。检查模型与 tokenizer：

```bash
python3 - <<'PY'
from pathlib import Path
from transformers import AutoTokenizer
model = Path("/workspace/llm-stack/models/deepseek-ai/DeepSeek-R1-Distill-Qwen-7B")
assert (model / "config.json").is_file(), model
assert any(model.glob("*.safetensors")), "Model weights are missing"
tokenizer = AutoTokenizer.from_pretrained(str(model), local_files_only=True)
print("Model directory:", model)
print("Tokenizer:", type(tokenizer).__name__)
print("Prompt tokens:", len(tokenizer.encode("Explain prefill and decode.")))
PY
```

停止旧服务后，在启动新服务之前检查本指南使用的 TCP 端口：

```bash
python3 - <<'PY'
import socket
for port in (5000, 8081, 8082, 7001, 7003, 8001, 8003, 5557, 5559):
    with socket.socket() as sock:
        try:
            sock.bind(("127.0.0.1", port))
        except OSError as exc:
            raise SystemExit(f"Port {port} is unavailable: {exc}")
    print(f"Port {port}: available")
PY
```

预期为九行 `Port ...: available`。检查只说明当时可绑定，不预占端口；
启动期间若出现 `Address already in use`，仍需检查冲突进程。

然后进入[公开启动流程](quickstart.md)。无需修改 Python 地址配置、准备拟合模型或运行任何本地 Shell 启动器。

## 5. 升级与更换机器

更换 vLLM/NIXL/CUDA 版本时，先重新核对其 Connector API、参数和二进制依赖，
不要把本指南的版本组合与其他 release 的命令混搭。
多机时，Instance Proxy 当前使用自己的 `--host` 加 `--vllm_port` 连接 vLLM，
因此二者应位于同一主机并使用一致可达地址；不能只修改一个端口就把 vLLM 移到远端。
具体多机地址和并行度调整见启动文档末尾。
