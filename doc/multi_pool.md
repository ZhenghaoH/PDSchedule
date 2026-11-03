# NIXL multi-Pool deployment

This guide extends the [single-Pool deployment](quickstart_v1.md) to **two Prefill
Pools and two Decode Pools** on one machine. It runs real vLLM services, not
simulated Instances. Prepare the [NIXL environment](../env/README.md) first.
Use a prepared, dedicated container (called `pdschedule-nixl` below; substitute
your actual name, such as `new_env`) with at least **four physical GPUs**,
each with enough memory for one TP=1 model and its KV cache.

Look up exact defaults and payload fields in the
[configuration/interface index](developer-handbook/configuration-and-interfaces.md); troubleshooting
for this topology stays at the end of this guide.

Stop the single-Pool example before reusing its ports and GPUs. All commands run
inside the same container, from `/workspace/llm-stack/PDSchedule`. Open a separate
terminal for every long-running service:

```bash
docker exec -it pdschedule-nixl /bin/bash
```

## 1. What this topology exercises

| Pool label | Gateway | Instance Proxy | vLLM | NIXL side channel | Physical GPU |
| --- | --- | --- | --- | --- | --- |
| P-A | 127.0.0.1:8081 | 127.0.0.1:7001 | 127.0.0.1:8001 | 127.0.0.1:5557 | 0 |
| P-B | 127.0.0.1:8083 | 127.0.0.1:7002 | 127.0.0.1:8002 | 127.0.0.1:5558 | 1 |
| D-A | 127.0.0.1:8082 | 127.0.0.1:7003 | 127.0.0.1:8003 | 127.0.0.1:5559 | 2 |
| D-B | 127.0.0.1:8084 | 127.0.0.1:7004 | 127.0.0.1:8004 | 127.0.0.1:5560 | 3 |

The Controller listens on `127.0.0.1:5000`. Pool labels above are explanatory;
numeric Pool IDs are assigned during registration, not fixed by this table.
There are 13 services: four vLLMs, one Controller, four Gateways, and four Proxies.

All Pools use `nixl`, version `1`, and compatible model/layout settings. The
Controller can select P-A→D-A, P-A→D-B, P-B→D-A, or P-B→D-B. D Instance selection
takes place at the selected D Gateway after Prefill; KV data bypasses Controller.

Exactly one active P Pool and one active D Pool uses `single_gateway_direct`,
which bypasses both global policy selection and affinity history. This topology
instead uses `multi_gateway_scheduled`. One Instance per Pool demonstrates
**global Pool choice**, not competing local Instance choices. The
[SLO guide](slo.md) explains calibration, local policies, and an eight-GPU extension.

## 2. Start four vLLM services

Run each block in its own terminal. Model, dtype, TP, context size, and attention
backend are intentionally identical. Side-channel ports must not overlap.

### Terminal 1: P-A engine

```bash
cd /workspace/llm-stack/PDSchedule
export CUDA_VISIBLE_DEVICES=0
export PYTHONHASHSEED=0 VLLM_USE_FLASHINFER_SAMPLER=0
export UCX_TLS=tcp,cuda_copy UCX_NET_DEVICES=all
export VLLM_NIXL_SIDE_CHANNEL_HOST=127.0.0.1
export VLLM_NIXL_SIDE_CHANNEL_PORT=5557
python3 -m vllm.entrypoints.openai.api_server \
  --model /workspace/llm-stack/models/deepseek-ai/DeepSeek-R1-Distill-Qwen-7B \
  --host 127.0.0.1 --port 8001 --tensor-parallel-size 1 --dtype half \
  --gpu-memory-utilization 0.8 --max-model-len 4096 \
  --max-num-batched-tokens 4096 --enforce-eager --attention-backend TRITON_ATTN \
  --no-enable-prefix-caching --disable-hybrid-kv-cache-manager --kv-cache-metrics \
  --kv-transfer-config '{"kv_connector":"NixlConnector","kv_role":"kv_producer","kv_load_failure_policy":"fail","kv_connector_extra_config":{"backends":["UCX"]}}'
```

### Terminal 2: P-B engine

```bash
cd /workspace/llm-stack/PDSchedule
export CUDA_VISIBLE_DEVICES=1
export PYTHONHASHSEED=0 VLLM_USE_FLASHINFER_SAMPLER=0
export UCX_TLS=tcp,cuda_copy UCX_NET_DEVICES=all
export VLLM_NIXL_SIDE_CHANNEL_HOST=127.0.0.1
export VLLM_NIXL_SIDE_CHANNEL_PORT=5558
python3 -m vllm.entrypoints.openai.api_server \
  --model /workspace/llm-stack/models/deepseek-ai/DeepSeek-R1-Distill-Qwen-7B \
  --host 127.0.0.1 --port 8002 --tensor-parallel-size 1 --dtype half \
  --gpu-memory-utilization 0.8 --max-model-len 4096 \
  --max-num-batched-tokens 4096 --enforce-eager --attention-backend TRITON_ATTN \
  --no-enable-prefix-caching --disable-hybrid-kv-cache-manager --kv-cache-metrics \
  --kv-transfer-config '{"kv_connector":"NixlConnector","kv_role":"kv_producer","kv_load_failure_policy":"fail","kv_connector_extra_config":{"backends":["UCX"]}}'
```

### Terminal 3: D-A engine

```bash
cd /workspace/llm-stack/PDSchedule
export CUDA_VISIBLE_DEVICES=2
export PYTHONHASHSEED=0 VLLM_USE_FLASHINFER_SAMPLER=0
export UCX_TLS=tcp,cuda_copy UCX_NET_DEVICES=all
export VLLM_NIXL_SIDE_CHANNEL_HOST=127.0.0.1
export VLLM_NIXL_SIDE_CHANNEL_PORT=5559
python3 -m vllm.entrypoints.openai.api_server \
  --model /workspace/llm-stack/models/deepseek-ai/DeepSeek-R1-Distill-Qwen-7B \
  --host 127.0.0.1 --port 8003 --tensor-parallel-size 1 --dtype half \
  --gpu-memory-utilization 0.8 --max-model-len 4096 \
  --max-num-batched-tokens 4096 --enforce-eager --attention-backend TRITON_ATTN \
  --no-enable-prefix-caching --disable-hybrid-kv-cache-manager --kv-cache-metrics \
  --kv-transfer-config '{"kv_connector":"NixlConnector","kv_role":"kv_consumer","kv_load_failure_policy":"fail","kv_connector_extra_config":{"backends":["UCX"]}}'
```

### Terminal 4: D-B engine

```bash
cd /workspace/llm-stack/PDSchedule
export CUDA_VISIBLE_DEVICES=3
export PYTHONHASHSEED=0 VLLM_USE_FLASHINFER_SAMPLER=0
export UCX_TLS=tcp,cuda_copy UCX_NET_DEVICES=all
export VLLM_NIXL_SIDE_CHANNEL_HOST=127.0.0.1
export VLLM_NIXL_SIDE_CHANNEL_PORT=5560
python3 -m vllm.entrypoints.openai.api_server \
  --model /workspace/llm-stack/models/deepseek-ai/DeepSeek-R1-Distill-Qwen-7B \
  --host 127.0.0.1 --port 8004 --tensor-parallel-size 1 --dtype half \
  --gpu-memory-utilization 0.8 --max-model-len 4096 \
  --max-num-batched-tokens 4096 --enforce-eager --attention-backend TRITON_ATTN \
  --no-enable-prefix-caching --disable-hybrid-kv-cache-manager --kv-cache-metrics \
  --kv-transfer-config '{"kv_connector":"NixlConnector","kv_role":"kv_consumer","kv_load_failure_policy":"fail","kv_connector_extra_config":{"backends":["UCX"]}}'
```

In a check terminal, wait for all four model endpoints:

```bash
python3 - <<'PY'
import json
import urllib.request
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
model = "/workspace/llm-stack/models/deepseek-ai/DeepSeek-R1-Distill-Qwen-7B"
for port in (8001, 8002, 8003, 8004):
    with opener.open(f"http://127.0.0.1:{port}/v1/models", timeout=10) as response:
        body = json.load(response)
    assert model in [entry["id"] for entry in body["data"]], body
    print(f"vLLM {port}: ready")
PY
```

Expected: four `ready` lines. Keep the four engines running throughout the guide.

## 3. Start Controller and baseline Gateways

### Terminal 5: Controller

```bash
cd /workspace/llm-stack/PDSchedule
export CUDA_VISIBLE_DEVICES=""
export PDSCHEDULE_PROFILE_DIR=/workspace/llm-stack/PDSchedule/profile
python3 entrypoints/controller.py --host 127.0.0.1 --port 5000
```

Keep `scheduler-cli>` interactive. The default global policy is `demand_aware`;
there is no Controller `--scheduler` startup flag. Use the CLI below to switch.

### Terminal 6: P-A Gateway

```bash
cd /workspace/llm-stack/PDSchedule
export CUDA_VISIBLE_DEVICES=""
export FLEXPD_GATEWAY_LOG_DIR=/workspace/llm-stack/PDSchedule/gateway_logs/p8081
python3 entrypoints/gatewayP.py \
  --host 127.0.0.1 --port 8081 --controller 127.0.0.1:5000 \
  --prefill 127.0.0.1:7001 --decode 127.0.0.1:8082 127.0.0.1:8084 \
  --prefill-scheduler-strategy jsq --no-ttft-warmup \
  --connector-name nixl --connector-version 1 --connector-metadata '{}'
```

### Terminal 7: P-B Gateway

```bash
cd /workspace/llm-stack/PDSchedule
export CUDA_VISIBLE_DEVICES=""
export FLEXPD_GATEWAY_LOG_DIR=/workspace/llm-stack/PDSchedule/gateway_logs/p8083
python3 entrypoints/gatewayP.py \
  --host 127.0.0.1 --port 8083 --controller 127.0.0.1:5000 \
  --prefill 127.0.0.1:7002 --decode 127.0.0.1:8082 127.0.0.1:8084 \
  --prefill-scheduler-strategy jsq --no-ttft-warmup \
  --connector-name nixl --connector-version 1 --connector-metadata '{}'
```

### Terminal 8: D-A Gateway

```bash
cd /workspace/llm-stack/PDSchedule
export CUDA_VISIBLE_DEVICES=""
export FLEXPD_GATEWAY_LOG_DIR=/workspace/llm-stack/PDSchedule/gateway_logs/d8082
python3 entrypoints/gatewayD.py \
  --host 127.0.0.1 --port 8082 --controller 127.0.0.1:5000 \
  --decode 127.0.0.1:7003 --decode-scheduler-strategy round_robin \
  --no-tpot-warmup --connector-name nixl --connector-version 1 --connector-metadata '{}'
```

### Terminal 9: D-B Gateway

```bash
cd /workspace/llm-stack/PDSchedule
export CUDA_VISIBLE_DEVICES=""
export FLEXPD_GATEWAY_LOG_DIR=/workspace/llm-stack/PDSchedule/gateway_logs/d8084
python3 entrypoints/gatewayD.py \
  --host 127.0.0.1 --port 8084 --controller 127.0.0.1:5000 \
  --decode 127.0.0.1:7004 --decode-scheduler-strategy round_robin \
  --no-tpot-warmup --connector-name nixl --connector-version 1 --connector-metadata '{}'
```

Keep all four Gateway CLIs open; omitting `--no-cli` is intentional for policy
inspection and switching. `FLEXPD_GATEWAY_LOG_DIR` is the existing environment
variable, despite its historical prefix. Separate directories prevent different
Gateways from appending indistinguishable records to the same log.

The initial Gateway registration occurs before Instances register. Capacity
metadata therefore comes from `MY_GPU_TYPE`, `MY_FLOPS`, `MY_MEM_BANDWIDTH`, and
`MY_MAX_PROMPT_THROUGHPUT`, or their current defaults (`NVIDIA RTX5090`, `17.9`,
`800.0`, `37829.0`). `MY_MAX_DECODE_REQ_NUM` defaults to `128`. These are not
automatically measured properties of your GPUs. Set deployment-specific values
in each Gateway terminal **before starting it** when comparing heterogeneous
Pools. Existing heartbeats update loads, not these registered capacities. Changing
capacity metadata requires re-registration; nominal defaults suffice to inspect
routing, not to claim calibrated hardware ranking or performance improvements.

## 4. Start four Instance Proxies

Run each block in its corresponding new terminal. Unset the Gateway's empty CUDA
visibility if reusing an environment. `--gpu-indices` refers to NVML physical GPU
indices, not each engine's remapped local CUDA index 0.

### Terminal 10: P-A Proxy

```bash
cd /workspace/llm-stack/PDSchedule
unset CUDA_VISIBLE_DEVICES
python3 entrypoints/instanceP.py \
  --host 127.0.0.1 --port 7001 --vllm_port 8001 --gateway 127.0.0.1:8081 \
  --connector-name nixl --connector-version 1 \
  --connector-metadata '{"side_channel_host":"127.0.0.1","side_channel_port":5557,"tp_size":1}' \
  --gpu-indices 0
```

### Terminal 11: P-B Proxy

```bash
cd /workspace/llm-stack/PDSchedule
unset CUDA_VISIBLE_DEVICES
python3 entrypoints/instanceP.py \
  --host 127.0.0.1 --port 7002 --vllm_port 8002 --gateway 127.0.0.1:8083 \
  --connector-name nixl --connector-version 1 \
  --connector-metadata '{"side_channel_host":"127.0.0.1","side_channel_port":5558,"tp_size":1}' \
  --gpu-indices 1
```

### Terminal 12: D-A Proxy

```bash
cd /workspace/llm-stack/PDSchedule
unset CUDA_VISIBLE_DEVICES
python3 entrypoints/instanceD.py \
  --host 127.0.0.1 --port 7003 --vllm_port 8003 --gateway 127.0.0.1:8082 \
  --connector-name nixl --connector-version 1 \
  --connector-metadata '{"side_channel_host":"127.0.0.1","side_channel_port":5559,"tp_size":1}' \
  --gpu-indices 2
```

### Terminal 13: D-B Proxy

```bash
cd /workspace/llm-stack/PDSchedule
unset CUDA_VISIBLE_DEVICES
python3 entrypoints/instanceD.py \
  --host 127.0.0.1 --port 7004 --vllm_port 8004 --gateway 127.0.0.1:8084 \
  --connector-name nixl --connector-version 1 \
  --connector-metadata '{"side_channel_host":"127.0.0.1","side_channel_port":5560,"tp_size":1}' \
  --gpu-indices 3
```

Member addresses in Gateway arguments do not replace dynamic registration. Wait
for all Instance startups and metrics updates before sending requests.

## 5. Inspect resources and switch the global policy

In a check terminal:

```bash
python3 - <<'PY'
import json
import urllib.request
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
for port in (8081, 8083, 8082, 8084, 7001, 7002, 7003, 7004):
    with opener.open(f"http://127.0.0.1:{port}/connectors", timeout=10) as response:
        body = json.load(response)
    assert body["connector"]["name"] == "nixl", body
    assert body["connector"]["version"] == "1", body
    assert body["mode"] == "request_scoped", body
    print(f"{port}: nixl/1 request_scoped")
PY
```

Expected: eight identity lines. This is a configuration check, not proof of
Instance registration or KV transfer. In the Controller CLI enter:

```text
show pool_id
show scheduler
switch scheduler -m round_robin
show scheduler
```

The Pool table must show exactly the four Gateways above. Expected status shape:

```text
mode=global, topology=multi_gateway_scheduled, scheduler=round_robin, resources=v...(p=2,d=2,network=4), available=demand_aware,least_load,round_robin
```

Snapshot version and Pool IDs vary. If the status still shows `resource_wait`,
`single_gateway_direct`, or fewer resources, resolve registration/startup first.
With no other workloads, run the four-request block in the next section. Then
switch policies at the **Controller** prompt and rerun it after each switch:

```text
switch scheduler -m least_load
show scheduler
```

```text
switch scheduler -m demand_aware
show scheduler
```

| Global policy | Current behavior | What to inspect |
| --- | --- | --- |
| `round_robin` | Rotates through Connector-compatible P/D pairs. It does not require link records to select a compatible pair. | `[RoundRobin] pair_index=...` and selected Gateway addresses. With a stable 2×2 topology, four requests visit all four pairs. |
| `least_load` | Minimizes the tuple `(Prefill current_load, Decode current_load)` over compatible pairs; Prefill load has priority, not a sum of the two. | `[LeastLoad] prefill_loads=... decode_loads=...`. Equal idle loads can repeatedly select the same pair. |
| `demand_aware` | Searches compatible pairs with active link records, using request-size ranks, capacity matching, and post-placement load variance. | `[DemandAwareStrategy]`, candidate costs, `selected_prefill`, `selected_decode`, and `*_time_source=profile` or `fallback`. |

`demand_aware` receives/logs SLO values but its current fitness is a capacity/load
objective, not hard TTFT/TPOT admission control. Gateway SLO policies are separate.
Do not interpret a successful global selection as an SLO guarantee.

Successful Pool registration, load updates, link updates, and unregister handlers
immediately refresh the Controller snapshot. A separate 1-second reconciliation
loop removes expired Controller state. Gateway reporting reads its configured
10-second interval at startup. If Controller forgets a Pool, the Gateway detects
the 404/409 on its next update, re-registers with the same address, and immediately
reports compute load. Prefill also refreshes network state; repeated reports fill
in links when a counterpart registers later. Management requests use 2-second
connect and 3-second read timeouts with bounded retry backoff. Actual reporting
cadence includes request processing and HTTP time.

Instance Proxies sleep 20 ms between metric collection/report cycles by default;
the actual arrival interval also includes sampling and HTTP time. Gateway local
scheduling expires a registered Instance after 30 seconds without a valid metric
heartbeat, with a 1-second scan and freshness checks at selection and dispatch.
The Controller's Pool and link timeouts remain 3000 and 60 seconds. A Gateway
heartbeat reports Proxy management state, not vLLM health. Expiry removes an
Instance from future local dispatch but does not replay requests already sent.
Graceful Gateway exit unregisters its Pool.

All Gateways here share an IP. The existing network updater treats them as
same-machine links and reports a PCIe-derived estimate; it is **not a measured
per-pair NIXL TCP bandwidth or RTT**. Initial links use nominal defaults. This
guide verifies routing/transfer, not WAN link characterization or policy speedup.

## 6. Send requests and verify all four transfer paths

At the Controller, first select `round_robin`. Run this block from a check
terminal inside the container; leave affinity disabled so every request exercises
the global policy. It sends four real requests sequentially:

```bash
python3 - <<'PY'
import json
import urllib.error
import urllib.request
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
for index in range(4):
    payload = {
        "model": "/workspace/llm-stack/models/deepseek-ai/DeepSeek-R1-Distill-Qwen-7B",
        "messages": [{"role": "user", "content": f"Request {index + 1}: explain prefill and decode in two sentences."}],
        "max_tokens": 128, "temperature": 0.7, "stream": False,
        "Enable_Affinity": False,
    }
    request = urllib.request.Request(
        "http://127.0.0.1:5000/v1/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST",
    )
    try:
        with opener.open(request, timeout=300) as response:
            status, body = response.status, json.load(response)
    except urllib.error.HTTPError as exc:
        raise SystemExit(f"HTTP {exc.code}: {exc.read().decode('utf-8', errors='replace')}")
    assert status == 200 and body.get("choices") and not body.get("error"), body
    assert body["choices"][0].get("finish_reason") != "error", body
    print(f"request {index + 1}: HTTP {status}; finish={body['choices'][0].get('finish_reason')}")
PY
```

Expected: four `HTTP 200` lines. Correlate Controller request IDs, selected P/D
Gateway addresses, `[Prefill Schedule] Selected instance`, and the D Gateway's
`[Decode-Connector] validation succeeded` line. With stable resources and no
other requests, all four Gateway pairs must appear; their order depends on
registration and the current round-robin cursor. Each Instance address must
belong to the selected Pool, not a manually preselected remote Instance.

Read metrics immediately before and after the four requests, then allow for the
vLLM statistics refresh interval and read again. Missing samples cause this check to fail and cannot prove transfer:

```bash
python3 - <<'PY'
import urllib.request
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
prefixes = (
    "vllm:nixl_bytes_transferred_sum",
    "vllm:nixl_bytes_transferred_count",
    "vllm:nixl_num_failed_transfers_total",
)
for port in (8003, 8004):
    with opener.open(f"http://127.0.0.1:{port}/metrics", timeout=10) as response:
        text = response.read().decode("utf-8")
    print(f"Decode vLLM {port}")
    found = set()
    for line in text.splitlines():
        if line.startswith(prefixes):
            name = line.partition("{")[0].split()[0]
            if name in prefixes:
                found.add(name)
                print(line)
    missing = set(prefixes) - found
    if missing:
        raise SystemExit(f"port {port}: missing NIXL metrics: {sorted(missing)}")
PY
```

Both Decode engines should show increases in transferred-byte sums and transfer
counts, without increasing failures. Log identity/compatibility alone does not
prove KV movement. Do not require every pair to win under `least_load` or
`demand_aware`: repeated winners are valid when their inputs favor that choice.

For Gateway records, inspect `gateway_logs/p8081/`, `p8083/`, `d8082/`, and
`d8084/`. Each contains role-specific schedule, timing, and resource logs.
The Controller console reports all global policies; `global_schedule.log`
contains detailed demand-aware costs and Controller SLO traces, not a persistent
record of every round-robin/least-load selection.

## 7. Continue to SLO policies, or stop

Continue with [SLO calibration and operation](slo.md) to generate local predictor
files, enable `slo_aware_prefill` / `slo_aware_decode`, and inspect prediction
fallbacks. Do not simply enable a strategy name and treat an uncalibrated
prediction as a measurement.

To stop, finish requests, stop the four Proxies with Ctrl+C, and enter `quit` in
each Gateway CLI before quitting Controller. Stop the four vLLMs with Ctrl+C
when no longer needed. For calibration-related application restarts, leave
vLLMs running and follow the restart order in the SLO guide.

For multiple machines, replace loopback addresses with reachable advertised
addresses, keep each Proxy with its vLLM, and assign distinct side-channel port
ranges for each TP engine. Do not advertise `0.0.0.0`. Client ingress also needs
attention: the current source-IP mapping disables PD for `10.0.*` and
`192.168.*`. Sending from an external host on those ranges is not equivalent to
this loopback example. Keep the client on Controller's loopback for this flow;
there is no documented per-request flag that overrides this mapping.

## 8. Troubleshooting multi-Pool operation

| Symptom | Check / action | What establishes recovery |
| --- | --- | --- |
| `single_gateway_direct` instead of `multi_gateway_scheduled` | Use Controller `show pool_id` and `show scheduler`. Check both P and both D Gateway startup/registration targets, not only their `/connectors` identities. | Exactly the expected four Gateways are present and the active snapshot shows `p=2,d=2`. |
| `demand_aware` has no compatible pair with a network, or `network` drops below 4 | Check P Gateway network-update errors and current Pool IDs. Link records expire after 60 seconds; `/network/register` alone is only an acknowledgement, not a link refresh. Inspect `/network/update` reporting and current source estimates. | The active snapshot again shows four link records and requests have compatible candidates. Do not inject invented bandwidth just to increase the count. |
| `least_load` or `demand_aware` repeatedly chooses one pair | Compare the logged actual load/capacity/candidate costs. Equal idle loads or the cost objective can legitimately produce the same winner. Disable affinity for policy observations. | The choice is explained by current inputs; do not require round-robin-like alternation from another policy. |
| Round robin does not visit all four pairs | Confirm stable 2P/2D registration, compatible descriptors, affinity off, and no other request sender advancing the cursor. Use the four sequential requests in Section 6. | Four distinct compatible Gateway pairs occur during that isolated observation interval. |
| A killed Instance remains a candidate, or traffic selects a dead endpoint | A Proxy with no valid metric heartbeat is excluded at the 30-second TTL (plus up to the 1-second scan interval). Confirm its `/instance/update` traffic and Gateway expiry logs. | Resource/schedule records no longer list the expired Instance. The TTL tests Proxy reporting, not vLLM engine health. Requests already sent are not replayed. |
| Controller restarted but existing Gateways do not recover | Check Gateway logs for the old Pool ID's 404/409 and the new registration. Gateways retry with a 1/2/4/8/10-second capped backoff; Prefill also retries network updates until both ends are registered. | New valid Pool IDs, refreshed compute loads and links, and the expected snapshot topology are present. Controller Pool timeout remains 3000 seconds. |
| Gateway logs mix multiple Pools or appear empty | Set a different absolute `FLEXPD_GATEWAY_LOG_DIR` before each Gateway starts. Look at the console for write failures and `record_state=logging_paused` for idle resource groups. This pause affects resource-log emission, not liveness. | Records in the correct directory identify actual local candidates/decisions; heartbeat and inference checks also pass. |
| Demand-aware timing still says `fallback` after fitting | Controller has a separate model root/cache. Check the [profile placement and key-collision rules](slo.md#4-install-controller-profiles-separately); a Gateway's loaded model is not automatically available to Controller. | Cost logs show the intended `profile` source after the correct application restart. |

For engine/forwarding failures use [single-Pool troubleshooting](quickstart_v1.md#13-troubleshooting).
For actual transfer, read metrics on the Decode **engine selected by that request**;
do not query only 8003 when the request used D-B/8004. Snapshot/network counts
and Connector compatibility logs are not data-plane measurements.
