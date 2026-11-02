# Instance Proxy

## Role and directory structure

An Instance Proxy represents a P or D serving engine to its Gateway. The role entrypoints are `entrypoints/instanceP.py` and `instanceD.py`; `instance/perception/` samples vLLM, GPU and network data. `core/handlers/instance/` and `coord/instance/` implement HTTP and reporting. The Proxy does not load the model itself.

## Runtime planes and service plane

Instance serves `/v1/chat/completions` and `/v1/completions` to the Gateway and exposes `/metadata` and `/connectors` for management. Its port and local vLLM port must be supplied explicitly. [HTTP index](../doc/developer-handbook/configuration-and-interfaces.md#instance-proxies-and-optional-client-ui) lists methods and fields.

## Startup lifecycle and quick start

Start the real vLLM engine, then the corresponding Gateway and Instance; the [NIXL quick start](../doc/quickstart_v1.md) contains P/D commands. The Instance registers with its Gateway and periodically reports metrics. `--host` is used for advertised identity and for reaching its local vLLM host; `--gpu-indices` selects NVML physical device indices to observe and does not allocate the engine's GPUs.

## Remote and container access

Gateway must reach the advertised Instance address, and Instance must reach its engine at its own host plus `--vllm_port`. For multiple hosts, check routing/firewall and host/container port visibility independently; `0.0.0.0` is a bind address, not a usable advertised destination. The complete example uses host networking.

## Configuration and CLI

| Option | Purpose |
| --- | --- |
| `--host`, `--port`, `--vllm_port`, `--gateway` | Proxy/engine/parent addresses; the vLLM flag has an underscore. |
| `--gpu-indices` | Comma-separated NVML-visible device indices. |
| `--connector-name`, `--connector-version`, `--connector-metadata` | Instance role's Connector descriptor. |

Defaults and validation are in the [configuration index](../doc/developer-handbook/configuration-and-interfaces.md#3-service-cli).

## Gateway registration and resource reporting

Role and Connector must match the parent Pool. Initial registration is followed by metrics updates; an unknown label returns 404 and prompts a registration attempt. vLLM gauges, throughput, NVML and interface samples have different failure/zero semantics. See [resource state](../doc/resource_state_metrics.md).

## Connector and predictor boundaries

NIXL and LMCache are described in [Connectors](../connectors/README.md). Instance Connector metadata is an application declaration; engines perform the actual KV operation. TTFT/TPOT fitting and Gateway loading are described under [profile](../profile/README.md) and [SLO](../doc/slo.md).

## Tests and troubleshooting

Run `python -m pytest -q test/test_runtime_instance_registration.py test/test_gateway_observability.py` for CPU checks. For registration failures inspect role, advertised host, Gateway reachability and descriptor; for empty GPU observations inspect NVML device visibility and `metric_source`. Validate `/v1/models` on the actual engine and compare real KV evidence. No integrated Instance Dashboard or Rust resource agent is provided.
