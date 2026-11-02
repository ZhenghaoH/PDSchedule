# NIXL over TCP

The complete deployment procedure is documented in:

- [Environment setup](../../env/README.md): NVIDIA GPU, vLLM/NIXL, container and dependencies.
- [NIXL 1P/1D quick start](../../doc/quickstart_v1.md): all seven services, topology, requests and expected output.

Follow the documented Python commands; no additional toy proxy is needed.

## TCP configuration

Both vLLM processes use:

```bash
export UCX_TLS=tcp,cuda_copy
export UCX_NET_DEVICES=all
```

NIXL uses UCX; TCP still needs a UCX/NIXL build with CUDA memory support. The
side-channel host/port, model/layout and Instance metadata must correspond.
Setting environment variables alone does not add missing UCX transport support.

## Validation boundaries

The [vLLM NIXL guide](https://docs.vllm.ai/en/v0.13.0/features/nixl_connector_usage/)
can help check the underlying Connector separately; its toy proxy does not run
PDSchedule services. Validate the Controller PD request and Decode vLLM transfer
metrics using the [quick start](../../doc/quickstart_v1.md#10-send-a-real-request-through-controller).
