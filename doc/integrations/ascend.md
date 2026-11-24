# PDSchedule + Ascend 910B Mooncake P2P integration

## Why Mooncake P2P on Ascend

vLLM-Ascend ships `MooncakeConnectorV1`, which moves KV cache between the
prefill and decode engines using the Mooncake transport. This example uses it
for a two-node Ascend deployment without Redis or LMCache.

## What PDSchedule adds

PDSchedule does not touch KV data. It filters pool descriptors, pairs a prefill
pool with a decode pool, selects instances, and shapes the HTTP requests that
carry the transfer parameters:

| Stage | Behaviour |
| --- | --- |
| Registration | Pool and instance descriptors under one connector name and version |
| Pairing | Same name, same version; TP is not part of the pairing check |
| Prefill request | Non-streaming, one token, `kv_transfer_params` with `do_remote_decode` |
| Prefill response | `kv_transfer_params` located recursively and stored in `Task.connector_context` |
| Decode request | The same dictionary is forwarded; block ids, host and port are not rewritten |

The registration name on Ascend is `mooncake_p2p_ascend`. It is a signaling
identity only: `--connector-name` never changes which connector vLLM uses, and
the engine side stays `MooncakeConnectorV1`.

## Topology

Two nodes, one prefill instance and one decode instance, both TP1.

| Component | Node | Port | Notes |
| --- | --- | --- | --- |
| Controller | s1 | 8000 | request entry point |
| Gateway-P | s1 | 8100 | management API |
| Instance-P | s1 | 7100 | management API |
| vLLM-P | s1 | 7101 | OpenAI API |
| Gateway-D | s2 | 8200 | management API |
| Instance-D | s2 | 7200 | management API |
| vLLM-D | s2 | 7201 | OpenAI API |
| Mooncake KV signaling | s1 | 36000 | Prefill engine `kv_port`, not the RoCE data endpoint |
| Mooncake KV signaling | s2 | 36200 | Decode engine `kv_port`, not the RoCE data endpoint |

Keep the KV ports above 28000: `AscendDirectTransport` allocates a random port
in `[20000, 20000 + cards*1000)`, which reaches 28000 on an eight-card node.
Reaching the management ports over TCP says nothing about the RoCE data path.

## Versions to record

Fill this table from the runtime that actually serves the request. This guide
does not assert that a particular combination has been validated.

| Item | What to record |
| --- | --- |
| PDSchedule | commit hash of the running checkout |
| Image | tag or digest |
| Driver / CANN | version reported by the node |
| PyTorch / torch-npu | package versions |
| vLLM / vLLM-Ascend | version, or source commit for a development build |
| Mooncake | version, or source commit |
| Model | Qwen3-8B checkpoint id |

Changing startup flags or adapter behaviour after a run means that run no
longer describes the new behaviour.

## Prerequisites

- Both nodes use the same runtime versions.
- The container or host has host networking, or publishes every port above to
  the peer node. P must advertise an address the D node can reach; a loopback
  or hostname-only advertisement will fail at transfer time.
- NPU visibility is explicit: `ASCEND_RT_VISIBLE_DEVICES` selects the card, and
  the Instance proxy advertises that card through `--gpu-indices`.
- The model path is mounted in the runtime, not only on the host.
- The NPU RoCE interface carries no background traffic and no port shaping.

## Configuration

All four entry points must pass the same name and version:

```bash
--connector-name mooncake_p2p_ascend --connector-version 1
```

Gateway descriptor metadata may be empty. Each Instance requires
`side_channel_host`, a positive `side_channel_port` and a positive `tp_size`:

```bash
--connector-metadata '{"side_channel_host":"<node IP>","side_channel_port":<kv port>,"tp_size":1}'
```

`side_channel_port` is the engine KV port used by `MooncakeConnectorV1` on that
node, not a separate service. A gateway registered as `mooncake_p2p_ascend`
pairs only with another `mooncake_p2p_ascend` descriptor; it will not pair with
`nixl`. See
[connector descriptor rules](../developer-handbook/configuration-and-interfaces.md#6-connector-descriptor-boundary).

## Start order

The example scripts under `examples/ascend/` implement exactly this order and
wait for each dependency:

1. Both vLLM engines (`kv_producer` on s1, `kv_consumer` on s2). Confirm
   `/v1/models` answers on both.
2. Controller on s1.
3. Gateway-P on s1 and Gateway-D on s2.
4. Instance-P on s1 and Instance-D on s2.
5. Send a request to the Controller, not to a Gateway or engine. The current
   service mapping disables PD for source addresses starting with `10.0.` or
   `192.168.`. Use a source address that enables PD and verify
   `PD分离: True` in `controller.log`.

```bash
bash examples/ascend/start.sh s1 vllm-p
bash examples/ascend/start.sh s2 vllm-d
bash examples/ascend/start.sh s1 controller
bash examples/ascend/start.sh s1 gateway-p
bash examples/ascend/start.sh s2 gateway-d
bash examples/ascend/start.sh s1 instance-p
bash examples/ascend/start.sh s2 instance-d
bash examples/ascend/request.sh "Explain what a KV cache is in one sentence." 64
```

## Evidence

Keep the three layers separate. A stronger claim needs the stronger layer.

| Layer | Acceptable record | Not sufficient |
| --- | --- | --- |
| Backend transfer | vLLM P/D pair transferring KV, decode side logging the byte count | HTTP 200 from an engine |
| System run | One request visible end to end: Controller selection, pool pair, chosen instances, decode byte count, client answer | Direct calls to a Gateway or engine |
| Data plane | Transfer-backend record for the same request, or NPU MAC/RoCE counters that can be attributed to it | Port counters alone, or the configured connector name |

A streamed client response does not by itself prove PD routing or KV
transfer. Check the selected pools and instances and a positive Decode
transfer byte count for the same request. If component IDs differ, correlate
them using the logged mapping; do not combine records from different runs.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| `connector metadata requires side_channel_port` | Instance metadata must contain all three keys |
| Instance or pool rejected as incompatible | Name and version must match on both sides |
| Prefill returns 502 with no `kv_transfer_params` | Engine connector version; the response shape must contain the dictionary |
| Decode returns 503 missing `kv_transfer_params` | The prefill hop did not bind a result, or the session context was lost |
| Request hangs after prefill | RoCE reachability between the nodes, KV port differences, and whether P advertised a routable address |
| Transfer succeeds but no bytes in decode log | Confirm `MooncakeConnectorV1` on both engines and check the decode log for the request id |

## Scope and limitations

This path covers two nodes, one P and one D, TP1, one connector, with the
existing gateway strategy and no predictor warmup. It does not establish
multi-pool policies, asymmetric TP, other Ascend models, or LMCache-Ascend on
Ascend. SVD compression, background traffic and port shaping are experiment
settings and are not part of this deployment.
