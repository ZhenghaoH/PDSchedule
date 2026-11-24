# Ascend 910B Mooncake P2P (1P/1D, TP1)

Two-node deployment: one prefill instance on `s1`, one decode instance on `s2`,
each TP1, with vLLM-Ascend's `MooncakeConnectorV1` moving KV cache and
PDSchedule carrying only the HTTP signaling.

```text
s1  (prefill + Controller)                 s2  (decode)
  vLLM-P       :7101  KV signaling :36000   vLLM-D      :7201  KV signaling :36200
  Instance-P   :7100                         Instance-D  :7200
  Gateway-P    :8100                         Gateway-D   :8200
  Controller   :8000   <-- request entry
```

Both nodes register with `--connector-name mooncake_p2p_ascend --connector-version 1`.
The vLLM `kv_connector` stays `MooncakeConnectorV1`; the PDSchedule registration
name is a signaling identity and does not change the data plane.

## Prerequisites

- Two Ascend 910B nodes with the same runtime: driver and CANN, PyTorch and
  torch-npu, vLLM and vLLM-Ascend, Mooncake.
- A Qwen3-8B checkpoint visible at the path you configure.
- The runtime environment (container or host) has host networking, or publishes
  every port above to the peer node. The two nodes must reach each other on
  these addresses.
- No process is listening on the ports above, and no background traffic or
  port shaping is applied to the NPU RoCE interface.

Record the exact versions you use; a run record is only meaningful together
with them.

## Configure

```bash
cp examples/ascend/config.example.env examples/ascend/config.env
$EDITOR examples/ascend/config.env
```

`config.env` is the single place for repository path, container, model path,
node addresses, NPU indices and ports. `CONTAINER_NAME` empty runs the
components in the current shell; set it to run them through `docker exec` in
an existing container. Replace the example IP addresses and use the same
configuration on both nodes.

Keep `KV_PORT_P` and `KV_PORT_D` above 28000: `AscendDirectTransport` picks a
random port in `[20000, 20000 + cards*1000)`, which reaches 28000 on an
8-card node.

## Start

Run each command in its own terminal, in this order. Every role waits for the
dependency it needs, so a later step may be started while an earlier one is
still loading.

```bash
# 1. engines, one per node
bash examples/ascend/start.sh s1 vllm-p
bash examples/ascend/start.sh s2 vllm-d

# 2. Controller on s1
bash examples/ascend/start.sh s1 controller

# 3. Gateways
bash examples/ascend/start.sh s1 gateway-p
bash examples/ascend/start.sh s2 gateway-d

# 4. Instance proxies
bash examples/ascend/start.sh s1 instance-p
bash examples/ascend/start.sh s2 instance-d
```

An 8B model takes a few minutes to load. Wait for both engines before sending
a request:

```bash
(
  . examples/ascend/config.env
  until curl -sf "http://${S1_HOST}:${VLLM_P_PORT}/v1/models" >/dev/null \
     && curl -sf "http://${S2_HOST}:${VLLM_D_PORT}/v1/models" >/dev/null; do sleep 5; done
  echo "Both engines are ready; check Gateway and Instance registration before sending."
)
```

## Send a request

```bash
bash examples/ascend/request.sh "Explain what a KV cache is in one sentence." 64
```

The request goes to the Controller only. With the current service mapping,
client addresses starting with `10.0.` or `192.168.` select a non-PD path.
Send from a client whose address, as seen by the Controller, enables PD and
check for `PD分离: True` in `controller.log`. A streamed answer alone does
not prove KV transfer; look for a positive transferred byte count on Decode
for the same request.

## What to look for

| Stage | Where | Evidence |
| --- | --- | --- |
| Pool and instance registration | `controller.log`, Gateway and Instance logs | P/D pools and their instances registered under `mooncake_p2p_ascend` |
| Controller routing | Controller output and schedule log | PD enabled for the request and the expected P/D pool pair selected |
| Prefill selection | `gateway_p.log` | chosen prefill instance for the request |
| Decode selection | `gateway_d.log` | chosen decode instance for the same request |
| KV transfer | `vllm_d.log` | a completed transfer and a positive transferred byte count for the same request |
| Answer | client output | generated text in the streamed response |

Correlate the request IDs across components using the logged mapping when
IDs differ. Do not combine records from different runs.

## Stop

Each component runs in the foreground of its own terminal: press `Ctrl+C`
there. If a process remains (including inside the configured container),
identify its PID by the configured listening port before stopping it. Do not
kill all processes that share the module name.

Stop the Instance proxies first, then the Gateways, then the Controller, and
the engines last. Confirm the ports are released before restarting.

## Scope

This example covers one P and one D, TP1, one connector. It does not configure
multi-pool layouts, asymmetric TP, SVD compression, background traffic, port
shaping, warmup or SLO profiles. Only claim a validated deployment for the
exact versions and topology you actually ran.
