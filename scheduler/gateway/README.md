# Gateway scheduling

## Runtime planes and directory structure

P and D Gateways are separate HTTP processes, each serving inference and internal management routes. This directory contains `prefill/` and `decode/` strategy managers; HTTP handlers and lifecycles are in `core/`. Gateway default configured ports are 8081 (P) and 8082 (D), with explicit host and Controller address required when not configured.

## Quick start and lifecycle

Use [1P/1D startup](../../doc/quickstart_v1.md) or [multi-Pool startup](../../doc/multi_pool.md). Each Gateway registers a Pool with Controller and accepts role-matched Instance registration and updates. A new Instance becomes online after an update. Gateway termination unregisters its Pool when lifecycle cleanup completes.

## Control-plane APIs

Gateways expose `/instance/register`, `/instance/update`, `/instance/unregister`, `/pd/select_instance`, `/connectors` and `/connector/sessions` management routes. P additionally exposes a network-analysis route. Methods, parameters and limits are in the [interface index](../../doc/developer-handbook/configuration-and-interfaces.md#prefill-and-decode-gateways). These are internal control surfaces, not external client inference contracts.

## Request workflow and instance selection

The P Gateway chooses a Prefill Instance and runs Prefill; its transfer context is sent toward the selected D Gateway. D selects a Decode Instance and forwards the request and response stream. Only eligible online Instances enter the relevant local choices. See [P strategies](prefill/README.md) and [D strategies](decode/README.md).

## Queues and reservations

P scheduling can batch logical requests and keep local reservations. `jsq` makes an arrival-triggered decision using unfinished prompt tokens; the SLO policy plans batches and available times. Reservation cleanup and heartbeat accounting are local state, not a hardware KV allocation or a guarantee that work completed. D maintains active request information. Read [metrics](../../doc/resource_state_metrics.md) for units and fallbacks.

## Gateway CLI and runtime options

Use `show scheduler`, `switch scheduler -m <name>`, and `quit` in the interactive Gateway console. Startup may disable it with `--no-cli`. P and D flags and predictor warmup options are listed once in [configuration](../../doc/developer-handbook/configuration-and-interfaces.md#3-service-cli).

## Validation

`python -m pytest -q test/test_gateway_scheduler_strategy.py test/test_runtime_instance_registration.py test/test_gateway_observability.py` checks local behavior without a real model. Use the [real deployment](../../doc/quickstart_v1.md) to validate actual KV movement, addresses and response streaming.

## Instance capability contract and notes

An Instance must match its Pool role and Connector descriptor. NIXL requires Instance side-channel metadata; LMCache requires shared identity. A matching record does not verify engine readiness or backend contents. There is no separate Gateway browser Dashboard.
