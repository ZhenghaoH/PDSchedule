# PDSchedule + NIXL integration

## Why NIXL?

NIXL provides the transfer mechanism used by the documented separated Prefill/Decode vLLM example. KV movement occurs between serving engines, not through Controller.

## What PDSchedule adds

Controller filters Pool descriptors, Gateways select Instances and coordinate a Connector session, and the NIXL Adapter constructs request-scoped Prefill and Decode payloads. It extracts `kv_transfer_params` from the Prefill response and passes them through `Task.connector_context`; missing params cause an error rather than being silently fabricated.

## Environment and compatibility

The target is Python 3.12, vLLM `0.23.1rc1.dev256+gcec2ec117.cu128`, Torch `2.11.0+cu128` and NIXL/NIXL CUDA 12 `1.3.0`; see the [environment guide](../../env/README.md). The vLLM version is a development build, and these versions do not prove a particular build or data-plane run. Real engine, UCX and device compatibility depend on deployment. The documented example is TP=1 on each engine; descriptor compatibility alone does not check model/layout or transport.

## Configuration

Gateway and Instance use `--connector-name nixl --connector-version 1`. Gateway descriptor metadata may be `{}`; each Instance requires `side_channel_host`, positive `side_channel_port` and positive `tp_size`. Host/port must correspond to the actual NIXL engine side channel. Name and descriptor version must match across Pools and Instances. Descriptor version is not the NIXL package release. See [exact CLI and descriptor rules](../developer-handbook/configuration-and-interfaces.md#6-connector-descriptor-boundary).

## Deployment and request flow

Follow all seven service commands and their order in the [NIXL quick start](../quickstart_v1.md), including engine-side `VLLM_NIXL_SIDE_CHANNEL_HOST`, `VLLM_NIXL_SIDE_CHANNEL_PORT`, UCX and `--kv-transfer-config`. Use `kv_producer` for Prefill and `kv_consumer` for Decode; `kv_both` is deprecated at the fixed revision. `NixlConnector` is the pull Connector alias. The Prefill response includes `remote_request_id`, remote engine, block, host/port and TP fields; PDSchedule forwards the returned transfer dictionary without reconstructing it. The upstream scheduler requires `remote_request_id` when using remote blocks. The `X-Request-Id` request header supports correlation, while the engine-generated `remote_request_id` in the transfer payload identifies the remote KV request; do not substitute one for the other. See the [fixed scheduler source](https://github.com/vllm-project/vllm/blob/cec2ec11760f9f3beabd4c90451936078bf91533/vllm/distributed/kv_transfer/kv_connector/v1/nixl/pull_scheduler.py). Do not send an internally constructed Task as a substitute for the Controller request.

## Validation

Check the matching model and `/v1/models`, registrations, `/connectors` and session/selection logs. The [fixed metrics source](https://github.com/vllm-project/vllm/blob/cec2ec11760f9f3beabd4c90451936078bf91533/vllm/distributed/kv_transfer/kv_connector/v1/nixl/stats.py) defines the NIXL bytes histogram and failed-transfer counter. Send a real request and compare the byte sum, count and failures before and after; missing samples are not a successful check. An HTTP 200, matching metadata or a CPU test is not proof of actual transfer. The [quick start](../quickstart_v1.md) supplies exact commands and evidence.

## Troubleshooting and limitations

Wrong side-channel address/port, TP or UCX transport may pass descriptor shape validation while failing at runtime. A missing Prefill `kv_transfer_params` is a failure; diagnose the engine response and Connector version. SLO and performance depend on actual hardware and model; do not use diagnostic timing as network-only latency. Other TP, host and version combinations need their own validation.
