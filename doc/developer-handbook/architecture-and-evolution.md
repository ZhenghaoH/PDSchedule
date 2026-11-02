# Architecture and evolution

## Current component architecture

Controller receives inference requests and chooses a compatible Prefill/Decode Gateway pair. A P Gateway selects a P Instance Proxy; after Prefill, the selected D Gateway selects a D Instance Proxy. Instance Proxies forward to their local vLLM engines. NIXL carries request-scoped transfer parameters; LMCache uses a shared-backend identity. The Python `proxy/` directory is forwarding machinery, not an independent Gateway process. See [runtime flows](runtime-flows.md).

## Current repository layout

`config/` defines service settings; `core/` provides schemas, routers, handlers, lifecycles and consoles; `coord/` handles registration and reporting. `scheduler/` contains Controller and Gateway managers and strategies. `store/` maintains state; `connectors/` owns compatibility and handoff. `instance/perception/`, `utils/`, `profile/`, and `examples/` supply measurements, supporting tools, predictors, and the browser client. [Module map](package-and-module-map.md) lists installed packages and tests.

## Design boundaries

Controller chooses Pools from an atomic resource snapshot, not individual GPUs. Gateways choose online Instances within a Pool. Pool descriptors are compared before route selection; an Instance must match its parent Pool. Model execution and KV movement belong to the serving engines and their Connector, not to Controller. Service source addresses influence current SLO mapping; see [interfaces](configuration-and-interfaces.md).

## Implemented modules

Each scheduler has a manager and strategy implementations. Controller offers `demand_aware`, `least_load`, and `round_robin`; the single compatible P/D pair takes a direct route. Gateway P and D strategies and their fallbacks are indexed in the [Gateway guide](../../scheduler/gateway/README.md). Descriptor/session validation is in [connectors](../../connectors/README.md).

## Current observability foundation

Controller offers a request page. Gateways write separate schedule, timing, and resource logs; Instances report metrics to their Gateway. Those are separate local observations, not a unified distributed trace or a guarantee that KV was transferred. See [observability](../architecture/observability-v1.md) and [resource state](../resource_state_metrics.md).

## Compatibility boundaries

The source tree, editable installation and wheel share entrypoints but may differ in writable paths. Package exports, dynamic imports, predictor files and templates are covered in [compatibility](compatibility-and-migrations.md) and [installation](../installation.md).

## Capabilities not implemented

No knowledge-selection service, standalone resource Dashboard, Rust Agent, unified trace exporter, or CacheRoute runtime-profile CLI is provided. A descriptor match does not create a serving backend or validate actual data-plane transfer.

## Future direction

Changes to package layout, externally stable Python contracts, deployment combinations or monitoring require their own design and verification. This document does not approve an implementation or claim a future release.
