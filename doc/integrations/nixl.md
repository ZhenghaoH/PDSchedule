# PDSchedule + NIXL integration

## Why NIXL?

NIXL provides the transfer mechanism used by the documented separated Prefill/Decode vLLM example. KV movement occurs between serving engines, not through Controller.

## What PDSchedule adds

Controller filters Pool descriptors, Gateways select Instances and coordinate a Connector session, and the NIXL Adapter constructs request-scoped Prefill and Decode payloads. It extracts `kv_transfer_params` from the Prefill response and passes them through `Task.connector_context`; missing params cause an error rather than being silently fabricated.

## Environment and compatibility

The [environment guide](../../env/README.md) gives the example Python, vLLM, Torch, CUDA and NIXL versions. Real engine, UCX and device compatibility depend on the deployed build. The documented example is TP=1 on each engine; descriptor compatibility alone does not check model/layout or transport.

## Configuration

Gateway and Instance use `--connector-name nixl --connector-version 1`. Gateway descriptor metadata may be `{}`; each Instance requires `side_channel_host`, positive `side_channel_port` and positive `tp_size`. Host/port must correspond to the actual NIXL engine side channel. Name and descriptor version must match across Pools and Instances. Descriptor version is not the NIXL package release. See [exact CLI and descriptor rules](../developer-handbook/configuration-and-interfaces.md#6-connector-descriptor-boundary).

## Deployment and request flow

Follow all seven service commands and their order in the [NIXL quick start](../quickstart_v1.md), including engine-side `VLLM_NIXL_SIDE_CHANNEL_HOST`, `VLLM_NIXL_SIDE_CHANNEL_PORT`, UCX and `--kv-transfer-config`. P engine returns transfer parameters; the D engine uses them for the request. Do not send an internally constructed Task as a substitute for the Controller request.

## Validation

Check the matching model and `/v1/models`, registrations, `/connectors` and session/selection logs. Then send a real request and compare NIXL successful transfer count and byte counters before and after. An HTTP 200, matching metadata or a CPU test is not proof of actual transfer. The [quick start](../quickstart_v1.md) supplies exact commands and evidence.

## Troubleshooting and limitations

Wrong side-channel address/port, TP or UCX transport may pass descriptor shape validation while failing at runtime. A missing Prefill `kv_transfer_params` is a failure; diagnose the engine response and Connector version. SLO and performance depend on actual hardware and model; do not use diagnostic timing as network-only latency. Other TP, host and version combinations need their own validation.
