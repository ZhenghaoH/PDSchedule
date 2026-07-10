# NixlConnector TCP fallback minimal check

This directory is a standalone smoke test for vLLM `NixlConnector` over TCP.
It does not change or depend on the current `P2P_FlexPD` controller/gateway path.

## Goal

Check whether the current container can run one Prefill vLLM, one Decode vLLM,
and one NIXL disaggregated prefill proxy without RDMA.

The scripts force UCX toward TCP with:

```bash
export UCX_TLS=tcp,cuda_copy
```

If this fails with NIXL/UCX backend errors, the current image probably cannot
use `NixlConnector` without RDMA or without rebuilding/installing the required
NIXL/UCX pieces.

## Files

- `run_prefill.sh`: start one Prefill vLLM using `NixlConnector`.
- `run_decode.sh`: start one Decode vLLM using `NixlConnector`.
- `run_proxy.sh`: start the official vLLM NIXL toy proxy.
- `request.sh`: send one OpenAI-compatible request to the proxy.

## Same-host test

Open three terminals inside the same container.

Terminal 1:

```bash
cd /workspace/llm-stack/workspace_HZH/P2P_FlexPD/examples/nixl_tcp_fallback
GPU=0 ./run_prefill.sh
```

Terminal 2:

```bash
cd /workspace/llm-stack/workspace_HZH/P2P_FlexPD/examples/nixl_tcp_fallback
GPU=1 ./run_decode.sh
```

Terminal 3:

```bash
cd /workspace/llm-stack/workspace_HZH/P2P_FlexPD/examples/nixl_tcp_fallback
./run_proxy.sh
```

Then send one request:

```bash
cd /workspace/llm-stack/workspace_HZH/P2P_FlexPD/examples/nixl_tcp_fallback
./request.sh
```

## Cross-host TCP check

After same-host succeeds, run Prefill on host A and Decode on host B.

On host A:

```bash
HOST_IP=172.18.0.170 GPU=0 ./run_prefill.sh
```

On host B:

```bash
HOST_IP=172.18.0.169 GPU=0 ./run_decode.sh
```

Run proxy on either side, pointing to both vLLM servers:

```bash
PREFILL_HOST=172.18.0.170 DECODE_HOST=172.18.0.169 ./run_proxy.sh
```

Then:

```bash
PROXY_HOST=172.18.0.170 ./request.sh
```

Adjust `PROXY_HOST` to the machine where `run_proxy.sh` is running.

## Common variables

All scripts can be configured with environment variables:

- `MODEL_PATH`: model path. Default is `/workspace/llm-stack/models/deepseek-ai/DeepSeek-R1-Distill-Qwen-7B`.
- `HOST_IP`: vLLM listen/bind host. Default is `0.0.0.0`.
- `PREFILL_HOST`: Prefill host seen by the proxy. Default is `127.0.0.1`.
- `DECODE_HOST`: Decode host seen by the proxy. Default is `127.0.0.1`.
- `PREFILL_PORT`: Prefill OpenAI API port. Default is `8100`.
- `DECODE_PORT`: Decode OpenAI API port. Default is `8200`.
- `PROXY_HOST`: proxy host for requests. Default is `127.0.0.1`.
- `PROXY_PORT`: proxy port. Default is `8000`.
- `PREFILL_SIDE_CHANNEL_PORT`: Prefill NIXL side-channel port exported to vLLM. Default is `5557`.
- `DECODE_SIDE_CHANNEL_PORT`: Decode NIXL side-channel port exported to vLLM. Default is `5558`.
- `GPU`: CUDA device list for one vLLM. Defaults are `0` for Prefill and `1` for Decode.
- `TP_SIZE`: tensor parallel size. Default is `1`.
- `MAX_MODEL_LEN`: default is `4096`.
- `GPU_MEMORY_UTILIZATION`: default is `0.8`.
- `VLLM_REPO`: vLLM source checkout. Default tries `/workspace/llm-stack/vllm`, `/workspace/vllm`, then the installed package location.
- `PROXY_SCRIPT`: explicit path to `toy_proxy_server.py`. The current official toy proxy only needs
  OpenAI API hosts/ports; side-channel ports are configured on the vLLM processes through env vars.

## Success criteria

The test is considered successful when:

- Prefill and Decode both start with `NixlConnector`.
- The proxy starts and sees both endpoints.
- `request.sh` returns normal generated text.
- Logs do not show NIXL/UCX backend init failure, RDMA device required/not found,
  connection timeout, or missing connector errors.

Useful failure signals:

- `Unknown kv connector NixlConnector`: current vLLM does not include this connector.
- `ModuleNotFoundError: nixl`: NIXL is not installed in the image.
- UCX transport/device errors: TCP fallback is not available in the current build or env.
- Proxy script not found: set `VLLM_REPO` or `PROXY_SCRIPT` to the vLLM checkout that contains
  `tests/v1/kv_connector/nixl_integration/toy_proxy_server.py`.
