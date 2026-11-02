# Environment setup: NVIDIA CUDA and NIXL

This guide prepares the single-host [NIXL 1P/1D quick start](../doc/quickstart_v1.md): Controller, P/D Gateways, P/D Instance Proxies and two real vLLM engines in one Linux container. KV moves over NIXL/UCX. Redis, LMCache, a separate metadata service and an additional toy proxy are not required for this example.

## 1. Environment and resources

| Component | Example configuration |
| --- | --- |
| Host | Linux x86_64, Docker Engine and NVIDIA Container Toolkit |
| GPU | Two visible NVIDIA GPUs; Prefill physical GPU 0 and Decode physical GPU 1 |
| Model | Local `DeepSeek-R1-Distill-Qwen-7B`; identical model and tokenizer at both engines |
| Parallelism | TP=1 per engine, one GPU each |
| Python / vLLM | Python 3.12 / `0.23.1rc1.dev256+gcec2ec117.cu128` (development build) |
| PyTorch / CUDA build | `2.11.0+cu128` / CUDA 12.8; driver and system toolkit are separate |
| NIXL | `nixl==1.3.0` and `nixl-cu12==1.3.0` |
| KV transport | UCX backend, `UCX_TLS=tcp,cuda_copy` |
| API ports | Controller 5000; Gateways 8081/8082; Instances 7001/7003; vLLM 8001/8003 |

The example uses context length 4096 and float16. Each GPU must fit weights, runtime overhead and KV cache. Select a smaller compatible model or increase TP and update GPU allocation, NIXL metadata and topology if memory is insufficient. Two GPUs do not guarantee that every model fits.

The host driver must support the serving stack. The CUDA Version shown by `nvidia-smi` describes driver capability, not the CUDA version compiled into Torch; `nvcc --version`, the base image and `torch.version.cuda` also describe different things. The supplied vLLM version is a development build, not an official `0.23.1` release. Its version string identifies upstream commit `cec2ec11760f9f3beabd4c90451936078bf91533`, but does not establish that a local source tree is clean or that its extensions match.

References: [fixed vLLM requirements](https://github.com/vllm-project/vllm/blob/cec2ec11760f9f3beabd4c90451936078bf91533/requirements/common.txt), [CUDA requirements](https://github.com/vllm-project/vllm/blob/cec2ec11760f9f3beabd4c90451936078bf91533/requirements/cuda.txt) and [CUDA installation/build guidance](https://github.com/vllm-project/vllm/blob/cec2ec11760f9f3beabd4c90451936078bf91533/docs/getting_started/installation/gpu.cuda.inc.md).

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

Use an already prepared Linux NVIDIA container with this exact development stack, model mount and reachable network. The examples refer to a container named `pdschedule-nixl`; substitute your actual container name, such as `new_env`. Neither that name nor a particular image digest is a publicly supplied image. Enter an existing running container:

```bash
docker exec -it pdschedule-nixl /bin/bash
```

Open a separate host terminal for each service. Ensure the repository and models are mounted at the documented paths, the GPUs are visible, and loopback/host networking and shared memory are suitable for your deployment. The repository's root `Dockerfile` is not a complete vLLM serving image.

If preparing the development source separately, identify the exact upstream revision:

```bash
git clone https://github.com/vllm-project/vllm.git
cd vllm
git checkout --detach cec2ec11760f9f3beabd4c90451936078bf91533
git rev-parse HEAD
git status --short
```

This checkout alone does not provide compatible compiled extensions or recreate the supplied build. Use the fixed revision's upstream CUDA source-build guidance and a compatible CUDA 12.8/PyTorch 2.11 toolchain, or obtain an independently verified matching wheel/image. The upstream CUDA requirements at this commit also contain CUDA 13-specific extras; the exact CUDA 12.8 build inputs and adaptations are not established by the version string and must be verified for the actual build. Build parameters, wheel availability and image digest are not established here. Do not assume an automatically selected prebuilt wheel matches this commit and CUDA variant; verify the installed version, extensions and source state. Do not rebuild or replace an existing working container merely to follow this guide.

## 3. Install NIXL and project dependencies

[`requirements.txt`](../requirements.txt) includes application, CPU test, build and research dependencies and is also used for package metadata. See [installation](../doc/installation.md). CPU tests use Python 3.12 and do not need a GPU, Torch, vLLM, NIXL, LMCache or Redis; NumPy 1.26.4 is not a Python 3.13 baseline. A CPU test environment is not a serving image.

First verify that the prepared serving environment already contains the exact
vLLM/Torch/NIXL stack. Run this read-only check in the serving container before
installing application dependencies:

```bash
python3 - <<'PY'
from importlib.metadata import version
import sys
import torch
expected = {
    "vllm": "0.23.1rc1.dev256+gcec2ec117.cu128",
    "torch": "2.11.0+cu128",
    "nixl": "1.3.0",
    "nixl-cu12": "1.3.0",
}
print("Python:", sys.version.split()[0], "Torch CUDA:", torch.version.cuda)
assert sys.version_info[:2] == (3, 12), "Expected Python 3.12"
assert torch.version.cuda == "12.8", "Expected a CUDA 12.8 Torch build"
for package, required in expected.items():
    actual = version(package)
    print(package + ":", actual)
    assert actual == required, (package, actual, required)
PY
```

An application requirements installation must not silently replace that stack.
Preview dependency resolution and review the proposed changes:

```bash
cd /workspace/llm-stack/PDSchedule
python3 -m pip install --dry-run \
  'vllm==0.23.1rc1.dev256+gcec2ec117.cu128' 'torch==2.11.0+cu128' \
  'nixl==1.3.0' 'nixl-cu12==1.3.0' -r requirements.txt
```

Only if the preview succeeds and preserves the serving stack, install and check:

```bash
cd /workspace/llm-stack/PDSchedule
python3 -m pip install \
  'vllm==0.23.1rc1.dev256+gcec2ec117.cu128' 'torch==2.11.0+cu128' \
  'nixl==1.3.0' 'nixl-cu12==1.3.0' -r requirements.txt
python3 -m pip check
```

The dry run can fail if the exact development wheel is not available from configured indexes; stop and prepare the matching stack rather than resolving a different version. `nixl` is a metapackage; `nixl-cu12` provides the CUDA 12 backend. Preserve other backends already present unless independently justified; do not uninstall a CUDA 13 backend merely because it is installed. FastAPI, transformers, aiohttp and setuptools constraints intersect the fixed vLLM revision. Gateway imports NumPy, scikit-learn and transformers even with warmup disabled. `requirements.txt` includes CPU tests and research tools; they do not themselves install Torch, vLLM or NIXL.

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
expected = {"vllm": "0.23.1rc1.dev256+gcec2ec117.cu128",
            "torch": "2.11.0+cu128", "nixl": "1.3.0", "nixl-cu12": "1.3.0"}
for package, required in expected.items():
    actual = version(package)
    print(package + ":", actual)
    assert actual == required, (package, actual, required)
print("transformers:", version("transformers"))
print("Torch CUDA:", torch.version.cuda)
print("CUDA available:", torch.cuda.is_available())
print("Visible GPUs:", torch.cuda.device_count())
assert torch.version.cuda == "12.8", "Expected a CUDA 12.8 Torch build"
assert torch.cuda.is_available(), "Container cannot access CUDA"
assert torch.cuda.device_count() >= 2, "This topology needs two visible GPUs"
PY
```

Expect the exact supplied development version, Torch `2.11.0+cu128`, both NIXL packages at `1.3.0`, Torch CUDA `12.8`, `CUDA available: True`, and at least two visible GPUs. Resolve CUDA/UCX shared-library errors before proceeding; package installation alone does not establish backend readiness.

Check the Connector import and engine options:

```bash
python3 - <<'PY'
from vllm.distributed.kv_transfer.kv_connector.v1.nixl import NixlConnector
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
