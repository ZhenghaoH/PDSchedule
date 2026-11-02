# NIXL end-to-end quick start: 1P/1D

This guide applies to PDSchedule 0.1.0, vLLM 0.13.0 and NIXL. Its `v1`
filename does not imply a separate runtime profile or LMCache MP protocol.

Complete [environment setup](../env/README.md) first, then open seven service
terminals in the same `pdschedule-nixl` container. Run all Python commands from
`/workspace/llm-stack/PDSchedule`. See [configuration and interfaces](developer-handbook/configuration-and-interfaces.md),
[multi-Pool deployment](multi_pool.md), and [SLO operation](slo.md).

## 1. Topology and startup order

| Terminal | Process | Address | Peer / downstream | GPU / TP |
| --- | --- | --- | --- | --- |
| 1 | Prefill vLLM API | 127.0.0.1:8001 | NIXL side channel 127.0.0.1:5557 | Physical GPU 0 / TP=1 |
| 2 | Decode vLLM API | 127.0.0.1:8003 | NIXL side channel 127.0.0.1:5559 | Physical GPU 1 / TP=1 |
| 3 | `entrypoints/controller.py` | 127.0.0.1:5000 | P/D Gateways | None |
| 4 | `entrypoints/gatewayP.py` | 127.0.0.1:8081 | Controller 5000, P Instance 7001, D Gateway 8082 | None |
| 5 | `entrypoints/gatewayD.py` | 127.0.0.1:8082 | Controller 5000, D Instance 7003 | None |
| 6 | `entrypoints/instanceP.py` | 127.0.0.1:7001 | Gateway 8081, P vLLM 8001 | Observe physical GPU 0 |
| 7 | `entrypoints/instanceD.py` | 127.0.0.1:7003 | Gateway 8082, D vLLM 8003 | Observe physical GPU 1 |

Start terminals 1–7 in order and keep each service in the foreground. Open an
additional check/request terminal in the same container:

```bash
docker exec -it pdschedule-nixl /bin/bash
```

The request travels Controller → P Gateway → P Instance → P vLLM. The P Gateway
obtains `kv_transfer_params` and forwards through D Gateway → D Instance → D
vLLM. Decode pulls KV through NIXL; Controller does not carry KV. Both Gateways
still register dynamically. With one compatible P/D Pool pair, the global
scheduler selects that pair directly without requiring an extra network record.

## 2. Terminal 1: Prefill vLLM

```bash
cd /workspace/llm-stack/PDSchedule
export CUDA_VISIBLE_DEVICES=0
export PYTHONHASHSEED=0
export VLLM_USE_FLASHINFER_SAMPLER=0
export UCX_TLS=tcp,cuda_copy
export UCX_NET_DEVICES=all
export VLLM_NIXL_SIDE_CHANNEL_HOST=127.0.0.1
export VLLM_NIXL_SIDE_CHANNEL_PORT=5557
python3 -m vllm.entrypoints.openai.api_server \
  --model /workspace/llm-stack/models/deepseek-ai/DeepSeek-R1-Distill-Qwen-7B \
  --host 127.0.0.1 --port 8001 \
  --tensor-parallel-size 1 --dtype half \
  --gpu-memory-utilization 0.8 --max-model-len 4096 \
  --max-num-batched-tokens 4096 \
  --enforce-eager --attention-backend TRITON_ATTN \
  --no-enable-prefix-caching --disable-hybrid-kv-cache-manager \
  --kv-cache-metrics \
  --kv-transfer-config '{"kv_connector":"NixlConnector","kv_role":"kv_both","kv_load_failure_policy":"fail","kv_connector_extra_config":{"backends":["UCX"]}}'
```

## 3. Terminal 2: Decode vLLM

```bash
cd /workspace/llm-stack/PDSchedule
export CUDA_VISIBLE_DEVICES=1
export PYTHONHASHSEED=0
export VLLM_USE_FLASHINFER_SAMPLER=0
export UCX_TLS=tcp,cuda_copy
export UCX_NET_DEVICES=all
export VLLM_NIXL_SIDE_CHANNEL_HOST=127.0.0.1
export VLLM_NIXL_SIDE_CHANNEL_PORT=5559
python3 -m vllm.entrypoints.openai.api_server \
  --model /workspace/llm-stack/models/deepseek-ai/DeepSeek-R1-Distill-Qwen-7B \
  --host 127.0.0.1 --port 8003 \
  --tensor-parallel-size 1 --dtype half \
  --gpu-memory-utilization 0.8 --max-model-len 4096 \
  --max-num-batched-tokens 4096 \
  --enforce-eager --attention-backend TRITON_ATTN \
  --no-enable-prefix-caching --disable-hybrid-kv-cache-manager \
  --kv-cache-metrics \
  --kv-transfer-config '{"kv_connector":"NixlConnector","kv_role":"kv_both","kv_load_failure_policy":"fail","kv_connector_extra_config":{"backends":["UCX"]}}'
```

`kv_both` allows NIXL to handle send and receive; PDSchedule's entrypoints and
request context assign the P/D roles. The engines use matching model, dtype,
attention backend and TP. `fail` prevents silent recomputation when KV loading
fails. Eager execution and disabled prefix caching aid transfer observation;
they are not performance tuning recommendations.

### Verify both engines are ready

In the check terminal, wait for both `/v1/models` endpoints rather than using a fixed delay:

```bash
python3 - <<'PY'
import json
import urllib.request
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
expected = "/workspace/llm-stack/models/deepseek-ai/DeepSeek-R1-Distill-Qwen-7B"
for port in (8001, 8003):
    url = f"http://127.0.0.1:{port}/v1/models"
    with opener.open(url, timeout=10) as response:
        models = json.load(response)
    ids = [item["id"] for item in models["data"]]
    assert expected in ids, (url, ids)
    print(f"vLLM {port}: ready; model={expected}")
PY
```

Expect one ready line each for ports 8001 and 8003. On failure inspect the
corresponding engine terminal and retry after model loading completes.

## 4. Terminal 3: Controller

```bash
cd /workspace/llm-stack/PDSchedule
export CUDA_VISIBLE_DEVICES=""
python3 entrypoints/controller.py --host 127.0.0.1 --port 5000
```

Keep the terminal interactive: Controller has no `--no-cli` option. Its prompt
is `scheduler-cli>` and its request page is `http://127.0.0.1:5000/requests`.

## 5. Terminal 4: Prefill Gateway

```bash
cd /workspace/llm-stack/PDSchedule
export CUDA_VISIBLE_DEVICES=""
python3 entrypoints/gatewayP.py \
  --host 127.0.0.1 --port 8081 \
  --controller 127.0.0.1:5000 \
  --prefill 127.0.0.1:7001 --decode 127.0.0.1:8082 \
  --prefill-scheduler-strategy jsq \
  --connector-name nixl --connector-version 1 --connector-metadata '{}' \
  --no-ttft-warmup --no-cli
```

## 6. Terminal 5: Decode Gateway

```bash
cd /workspace/llm-stack/PDSchedule
export CUDA_VISIBLE_DEVICES=""
python3 entrypoints/gatewayD.py \
  --host 127.0.0.1 --port 8082 \
  --controller 127.0.0.1:5000 --decode 127.0.0.1:7003 \
  --decode-scheduler-strategy round_robin \
  --connector-name nixl --connector-version 1 --connector-metadata '{}' \
  --no-tpot-warmup --no-cli
```

Gateway NIXL metadata can be empty; Instance side-channel metadata must be
complete. The member/downstream flags do not replace registration. `jsq` and
`round_robin` do not require fitted models; warmup is explicitly disabled here.

## 7. Terminal 6: Prefill Instance Proxy

```bash
cd /workspace/llm-stack/PDSchedule
unset CUDA_VISIBLE_DEVICES
python3 entrypoints/instanceP.py \
  --host 127.0.0.1 --port 7001 --vllm_port 8001 \
  --gateway 127.0.0.1:8081 \
  --connector-name nixl --connector-version 1 \
  --connector-metadata '{"side_channel_host":"127.0.0.1","side_channel_port":5557,"tp_size":1}' \
  --gpu-indices 0
```

## 8. Terminal 7: Decode Instance Proxy

```bash
cd /workspace/llm-stack/PDSchedule
unset CUDA_VISIBLE_DEVICES
python3 entrypoints/instanceD.py \
  --host 127.0.0.1 --port 7003 --vllm_port 8003 \
  --gateway 127.0.0.1:8082 \
  --connector-name nixl --connector-version 1 \
  --connector-metadata '{"side_channel_host":"127.0.0.1","side_channel_port":5559,"tp_size":1}' \
  --gpu-indices 1
```

Instance Proxy observes GPU metrics and does not load the model. `--gpu-indices`
uses physical NVML indices, not indices remapped by vLLM's `CUDA_VISIBLE_DEVICES`;
D observes physical GPU 1. Instance `--host` must also reach its local vLLM.

## 9. Check registration and Connectors

In the check terminal:

```bash
python3 - <<'PY'
import json
import urllib.request
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
for port in (8081, 8082, 7001, 7003):
    with opener.open(f"http://127.0.0.1:{port}/connectors", timeout=10) as response:
        body = json.load(response)
    assert body["connector"]["name"] == "nixl", body
    assert body["connector"]["version"] == "1", body
    print(f"{port}: connector=nixl version=1 mode={body['mode']}")
PY
```

Expect four `connector=nixl version=1 mode=request_scoped` lines. This endpoint
describes Connector configuration, not Instance registration or KV transfer.
At the Controller `scheduler-cli>` prompt:

```text
show pool_id
show scheduler
```

The Pool list should contain P `127.0.0.1:8081` and D `127.0.0.1:8082`; Pool IDs
are assigned dynamically. Routing should report `single_gateway_direct`, not
`resource_wait`. Check the Instance and Gateway terminals for successful startup
and registration; request logs must show actual selection of 7001 and 7003.

## 10. Send a real request through Controller

Run the request in the container over loopback. Source IP affects current PD/SLO
mapping: `10.0.*` and `192.168.*` sources can select non-PD behavior. A request
from another host must not automatically count as this PD validation.

Send a non-streaming request and inspect the complete response:

```bash
cd /workspace/llm-stack/PDSchedule
python3 - <<'PY'
import json
import urllib.error
import urllib.request
payload = {
    "model": "/workspace/llm-stack/models/deepseek-ai/DeepSeek-R1-Distill-Qwen-7B",
    "messages": [{"role": "user", "content": "Explain the difference between prefill and decode in two sentences."}],
    "max_tokens": 128,
    "temperature": 0.7,
    "top_p": 1.0,
    "stream": False,
}
request = urllib.request.Request(
    "http://127.0.0.1:5000/v1/chat/completions",
    data=json.dumps(payload).encode("utf-8"),
    headers={"Content-Type": "application/json"},
    method="POST",
)
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
try:
    with opener.open(request, timeout=300) as response:
        status = response.status
        body = json.load(response)
except urllib.error.HTTPError as exc:
    raise SystemExit(f"HTTP {exc.code}: {exc.read().decode('utf-8', errors='replace')}")
assert status == 200, status
assert body.get("choices"), body
assert not body.get("error"), body
assert body["choices"][0].get("finish_reason") != "error", body
print("HTTP", status)
print(json.dumps(body, ensure_ascii=False, indent=2))
PY
```

Example response shape; generated text, ID, token counts and finish reason vary:

```json
{
  "id": "chatcmpl-<request-id>",
  "object": "chat.completion",
  "model": "/workspace/llm-stack/models/deepseek-ai/DeepSeek-R1-Distill-Qwen-7B",
  "choices": [
    {"index": 0, "message": {"role": "assistant", "content": "<model-generated text>"}, "finish_reason": "stop"}
  ],
  "usage": {"prompt_tokens": 32, "completion_tokens": 64, "total_tokens": 96}
}
```

`HTTP 200` precedes the JSON. Usage figures are illustrative; reasoning models
can emit reasoning first or finish with `length` when the budget is exhausted.

### Check that the request used P/D

Correlate these lines for the same request; request and session IDs vary:

```text
PD分离:  True
[Controller-Connector] ... connector=nixl session_id=nixl-...
[Prefill Schedule] Selected instance: 127.0.0.1:7001
[Decode-Connector] validation succeeded request_id=... connector=nixl prefill_instance=127.0.0.1:7001 decode_instance=127.0.0.1:7003 session_id=...
```

A text response without P/D selection logs does not validate the path.
`validation succeeded` confirms descriptor compatibility, not physical transfer.

### Check actual NIXL transfer metrics

Read Decode vLLM metrics before and after the request:

```bash
python3 - <<'PY'
import urllib.request
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
prefixes = (
    "vllm:nixl_bytes_transferred_sum",
    "vllm:nixl_bytes_transferred_count",
    "vllm:nixl_num_failed_transfers_total",
)
with opener.open("http://127.0.0.1:8003/metrics", timeout=10) as response:
    text = response.read().decode("utf-8")
for line in text.splitlines():
    if line.startswith(prefixes):
        print(line)
PY
```

Expect `vllm:nixl_bytes_transferred_sum` and `_count` to increase without an
increase in `vllm:nixl_num_failed_transfers_total`. Labels and values depend on
the engine; allow a metrics refresh cycle. If samples are absent, inspect version,
`--kv-cache-metrics` and D vLLM NIXL logs. No error or generic KV offload logs do
not establish transfer. The evidence chain is readiness → successful Controller
PD request → transfer bytes/count increase without failed-transfer growth.

## 11. Pages, logs and shutdown

- Controller request page: `http://127.0.0.1:5000/requests`.
- `global_schedule.log`: global selection and request timing.
- `gateway_logs/prefill_gateway_schedule.log`, `decode_gateway_schedule.log`: local selection.
- `gateway_logs/prefill_gateway_timing.log`, `decode_gateway_timing.log`: request lifecycle.
- `gateway_logs/prefill_gateway_resource.log`, `decode_gateway_resource.log`: sampled resources.

These observations have different scopes; `observed_network_transfer_ms` is not
pure NIXL network time. This example binds to loopback; remote access requires
appropriate reachable host and bind settings.

Finish requests, stop the two Instance Proxies, two Gateways and two vLLM
foreground processes with Ctrl+C, then enter `quit` in Controller. Verify the
intended GPU processes have exited. To stop the dedicated container when appropriate:

```bash
docker stop pdschedule-nixl
```

This stops every service in that container; do not use it on a shared container.

## 12. Change model, TP or host

- Change the model path in both engines and requests; make the tokenizer accessible to Controller.
- For TP>1 allocate disjoint GPU groups and update `--tensor-parallel-size`,
  Proxy `--gpu-indices` and descriptor `tp_size`. This example uses equal TP.
- For multiple hosts use reachable registration and side-channel addresses;
  do not advertise loopback or `0.0.0.0` to remote peers. Each vLLM engine and
  its Instance Proxy must share a host.
- Set `UCX_NET_DEVICES` to an appropriate real interface when needed.
- Different Pools can use different Connectors, but one P/D request cannot bridge
  incompatible Connectors. This NIXL example does not require LMCache or Redis.

## 13. Troubleshooting

| Symptom | Check and action | Do not infer |
| --- | --- | --- |
| `Missing required deployment option(s)` | Supply required host, Controller/Gateway and Instance/vLLM port options; see [address defaults](developer-handbook/configuration-and-interfaces.md#addresses-and-membership). | Missing options do not enable automatic discovery. |
| Unknown Connector, import or UCX error | Check the serving interpreter, versions, driver and UCX transport in [environment troubleshooting](../env/README.md#6-environment-troubleshooting). | Changing `--connector-name` does not install vLLM's Connector. |
| `resource_wait` or missing Pools | Run `show pool_id` and `show scheduler`; inspect Gateway registration target, advertised address and logs. | A request page alone does not mean P/D registered. |
| No active Instance or `Invalid report_label` | Verify parent Gateway, role, descriptor and successful `/instance/update`. A new Instance needs a valid update; a 404 prompts a registration attempt. | Startup logs or `/connectors` alone do not establish an eligible Instance. |
| Missing Prefill `kv_transfer_params` | Inspect P vLLM NIXL configuration and response, correlating the Controller request ID. | Do not fabricate empty context to replace a transfer result. |
| Missing NIXL context before Decode or Connector validation failure | Inspect `pd_connector`, session, P/D role and descriptor, and Prefill result along the route. | `/metadata` or a manually created session is not a NIXL handshake. |
| Response text but no P/D logs | Inspect source IP mapping and retry through container loopback; check the displayed PD flag. | Generated text alone does not prove P/D execution. |
| Transfer failures grow or bytes do not increase | Check selected D engine metrics/logs, side-channel address/port, TP metadata, model/layout and refresh delay. | Descriptor compatibility or generic offload logs do not prove NIXL transfer. |
| HTTP 200 with error, missing choices or broken SSE | Inspect response body, `[DONE]` and correlated logs; downstream errors can occur after streaming starts. | HTTP 200 or connection establishment does not prove successful completion. |
| Port conflict or GPU OOM | Identify the specific conflicting foreground process and GPU allocation; do not run the bind check against a running service. | A port number alone does not identify a process. |

### Read-only runtime check

In the same container, check existing endpoints without registration or inference:

```bash
python3 - <<'PY'
import json
import urllib.error
import urllib.request
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
for port, path in ((8001, "/v1/models"), (8003, "/v1/models"),
                   (8081, "/connectors"), (8082, "/connectors"),
                   (7001, "/connectors"), (7003, "/connectors")):
    url = f"http://127.0.0.1:{port}{path}"
    try:
        with opener.open(url, timeout=10) as response:
            body = json.load(response)
            print(url, response.status, body)
    except (urllib.error.URLError, ValueError) as exc:
        print(url, "CHECK FAILED:", exc)
PY
```

The script prints model or Connector data and reports individual failures; it is
not an automated readiness gate. `/v1/models` is on vLLM; `/connectors` is on
Gateways/Instances. This application has no generic `/healthz` or `/debug/status`.

### Using LMCache

This NIXL guide does not require Redis. For LMCache, compare all Pool/Instance
`backend`, `namespace` and `config_fingerprint` metadata with the actual serving
configuration, and check backend reads/writes/hits. The repository's YAML has a
fixed Redis address and is not a universal version-independent setup. See
[LMCache integration](integrations/lmcache.md). For NIXL engine details see the
[vLLM 0.13.0 guide](https://docs.vllm.ai/en/v0.13.0/features/nixl_connector_usage/).
