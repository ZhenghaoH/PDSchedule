# Environment setup: NVIDIA CUDA and NIXL

This guide prepares the single-host [NIXL 1P/1D quick start](../doc/quickstart_v1.md): Controller, P/D Gateways, P/D Instance Proxies and two real vLLM engines in one Linux container. KV moves over NIXL/UCX. Redis, LMCache, a separate metadata service and an additional toy proxy are not required for this example.

## 1. Environment and resources

| Component | Example configuration |
| --- | --- |
| Host | Linux x86_64, Docker Engine and NVIDIA Container Toolkit |
| GPU | Two visible NVIDIA GPUs; Prefill physical GPU 0 and Decode physical GPU 1 |
| Model | Local `DeepSeek-R1-Distill-Qwen-7B`; identical model and tokenizer at both engines |
| Parallelism | TP=1 per engine, one GPU each |
| Python / vLLM | Python 3.12 / vLLM 0.13.0 |
| PyTorch / container CUDA | Torch 2.9.0 under vLLM 0.13.0 constraints / official image build CUDA 12.9.1 |
| NIXL | `nixl==0.7.1` and `nixl-cu12==0.7.1` |
| KV transport | UCX backend, `UCX_TLS=tcp,cuda_copy` |
| API ports | Controller 5000; Gateways 8081/8082; Instances 7001/7003; vLLM 8001/8003 |

The example uses context length 4096 and float16. Each GPU must fit weights, runtime overhead and KV cache. Select a smaller compatible model or increase TP and update GPU allocation, NIXL metadata and topology if memory is insufficient. Two GPUs do not guarantee that every model fits.

The host driver must support the container CUDA version. The CUDA Version in `nvidia-smi` describes driver support, not the CUDA version used by Python's Torch. A separately built CUDA 12.8 / Torch 2.9.1 image has different dependencies and must be validated separately. References: [vLLM CUDA constraints](https://github.com/vllm-project/vllm/blob/v0.13.0/requirements/cuda.txt), [vLLM Dockerfile](https://github.com/vllm-project/vllm/blob/v0.13.0/docker/Dockerfile), [connector requirements](https://github.com/vllm-project/vllm/blob/v0.13.0/requirements/kv_connectors.txt), [NIXL CUDA 12 package](https://pypi.org/project/nixl-cu12/0.7.1/).

## 2. Prepare the host, workspace and container

Install Docker and the [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html), and check the host:

```bash
nvidia-smi
docker version
```

Prepare `/llm-stack/PDSchedule` and the deployment-owned model at `/llm-stack/models/deepseek-ai/DeepSeek-R1-Distill-Qwen-7B`, including weights, config and tokenizer. If needed, clone with authorized access:

```bash
mkdir -p /llm-stack/models
cd /llm-stack
git clone --branch PDSchedule https://github.com/ZhenghaoH/PDSchedule.git
```

Create the container using the official vLLM image:

```bash
docker pull vllm/vllm-openai:v0.13.0
docker run --name pdschedule-nixl --gpus all -it \
  --network host --ipc=host --shm-size=16g \
  -v /llm-stack:/workspace/llm-stack \
  -w /workspace/llm-stack/PDSchedule \
  --entrypoint /bin/bash \
  vllm/vllm-openai:v0.13.0
```

The API entrypoint is overridden with Bash to run separate services. Do not also start the image's default port-8000 service. Host networking does not need `-p`; with `--ipc=host`, the host shared-memory namespace is used and `--shm-size` is not an independent container allocation. If the container already exists, start and enter it:

```bash
docker start pdschedule-nixl
docker exec -it pdschedule-nixl /bin/bash
```

Open a separate host terminal and execute `docker exec` for each service. All seven services share the container, model mount, packages and loopback network. The repository's root `Dockerfile` is a base system image, not the complete vLLM image above.

## 3. Install NIXL and project dependencies

[`requirements.txt`](../requirements.txt) includes application, CPU test, build and research dependencies and is also used for package metadata. See [installation](../doc/installation.md). CPU tests use Python 3.12 and do not need a GPU, Torch, vLLM, NIXL, LMCache or Redis; NumPy 1.26.4 is not a Python 3.13 baseline. A CPU test environment is not a serving image.

Inside the serving container, pin Torch and both NIXL packages while installing the project dependencies:

```bash
cd /workspace/llm-stack/PDSchedule
python3 -m pip install \
  'torch==2.9.0' 'nixl==0.7.1' 'nixl-cu12==0.7.1' \
  -r requirements.txt
python3 -m pip check
```

`nixl` is a metapackage; pin `nixl-cu12` as well to avoid resolving a different CUDA implementation. Gateway imports NumPy, scikit-learn and transformers even with predictor warmup disabled. `prometheus-client` parses Instance metrics and `nvidia-ml-py` supplies `pynvml`. Preserve the compatible Torch/vLLM/CUDA stack and run `pip check` after installing. Package metadata is in `pyproject.toml`; the version comes from `pdschedule.__version__`.

Check the installed versions and GPUs:

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

Expect vLLM 0.13.0, Torch 2.9.0 (possibly with a CUDA suffix), both NIXL packages at 0.7.1, `CUDA available: True`, and at least two visible GPUs. Resolve CUDA/UCX shared-library errors before proceeding; package installation alone does not establish backend readiness.

Check the Connector import and engine options:

```bash
python3 - <<'PY'
from vllm.distributed.kv_transfer.kv_connector.v1.nixl_connector import NixlConnector
print("NixlConnector import: OK")
PY
python3 -m vllm.entrypoints.openai.api_server --help
```

The UCX build used by NIXL must support `tcp,cuda_copy`. Diagnose initialization, device or transport errors using [NIXL installation guidance](https://github.com/ai-dynamo/nixl) and the actual container libraries.

## 4. Check the model and ports

Use the same absolute model path for both vLLM engines, Controller tokenizer access and requests. Check the locally prepared weights and tokenizer:

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

Before starting this topology, check its TCP ports while the relevant services are stopped:

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

Expect nine available ports. A successful bind check does not reserve the ports. Proceed to the [quick start](../doc/quickstart_v1.md); fitted predictor models and local shell launchers are not prerequisites for its baseline policies.

## 5. Version and host changes

Recheck Connector APIs, options, libraries and binary dependencies when changing vLLM, NIXL or CUDA. Instance Proxy connects to vLLM using its own `--host` and `--vllm_port`; place them on the same reachable host. The quick start explains model, TP and multi-host adjustments. Rust, Tkinter, an independent Dashboard and Redis are not required for this NIXL topology.

## 6. Environment troubleshooting

| Symptom | Check | Completion evidence |
| --- | --- | --- |
| Torch/vLLM replaced or dependency conflict | Run `python3 -m pip check` in the actual serving container; preserve the documented stack. | Dependencies consistent, driver compatible, Connector import works. |
| Unknown Connector, import or UCX error | Check actual interpreter, NIXL CUDA package, UCX libraries and transport. | The vLLM interpreter imports the Connector and accepts its options. |
| GPU out of memory or loading stalls | Check model footprint, GPU assignments, TP and existing processes; wait for `/v1/models`. | Both engines are ready with the intended model. |
| Tokenizer offline failure or `Fallback to char count` | Rerun the model/tokenizer check; inspect Controller configuration and request model path. | Correct tokenizer loads and token counts are not fallback character counts. |
| Port conflict or unexpected service | Check the specific process and port in the container; perform bind checks only before starting services. | Each port reaches the intended engine or component. |

See the [configuration index](../doc/developer-handbook/configuration-and-interfaces.md) and [quick-start troubleshooting](../doc/quickstart_v1.md#13-troubleshooting). Installation alone does not prove end-to-end inference or KV transfer.
