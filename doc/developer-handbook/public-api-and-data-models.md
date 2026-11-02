# Public API and data-model catalog

## Common rules

| Area | Current rule |
| --- | --- |
| Installation | Six console commands are documented in [installation](../installation.md); source entrypoints remain available. |
| HTTP | The [interface index](configuration-and-interfaces.md#4-http-interfaces) describes current routes and fields. A client uses Controller inference endpoints; Gateway management routes are internal deployment surfaces. |
| Python imports | `pdschedule.__version__`, paths, root packages, Connector and scheduler models describe this version's implementation; no separately versioned general-purpose SDK contract is declared. |
| Models and secrets | Deployment provides model weights, tokenizers, predictors and serving configuration. Packages do not provide them. |

## Module export index

| Import path | Owner | Stability | Intended use | Non-goals | Exported public objects | Focused tests |
| --- | --- | --- | --- | --- | --- | --- |
| `pdschedule` | package | Current version | Package identity and paths | Universal stable SDK | `__version__` | [packaging](../../test/test_package_installation.py) |
| `core.schemas.request` | core | Internal | Carry request, SLO and selected route | Complete OpenAI schema | `Prompt`, `Service`, `Task`, `BaseRequest` | [single route](../../test/test_single_gateway_routing.py) |
| `connectors.models`, `connectors.protocol` | Connector | Internal | Descriptor, session, adapter contract | Real transfer implementation | `ConnectorDescriptor`, `ConnectorSession`, `ConnectorAdapter`, enums | [protocol](../../test/test_connector_protocol.py) |
| `scheduler.controller.strategy`, `.resource_state` | Controller | Internal | Immutable scheduling inputs, decisions and snapshots | Public remote API | `GlobalScheduleContext`, `GlobalScheduleDecision`, `ResourceSnapshot` | [controller](../../test/test_controller_scheduler_components.py) |
| `scheduler.gateway.prefill.strategy`, `.decode.strategy` | Gateway | Internal | Instance selection and result metadata | Wire contract | P/D strategy classes and result models | [Gateway](../../test/test_gateway_scheduler_strategy.py) |

## Core data models and wire values

| Module | Important fields, defaults, validation, enums | Minimal Python 3 example |
| --- | --- | --- |
| `connectors.models` | Descriptor `name`, string `version` default `1`, metadata mapping; names normalized by `from_payload`. Modes: `request_scoped`, `shared_backend`. | `ConnectorDescriptor.from_payload({"name": "nixl"})` |
| `connectors.models` | Session has ID, descriptor, P/D endpoints, context, timestamps and `created`, `active`, `closed`, `failed` states. Adapter determines actual state transitions. | Use `ConnectorManager.create_session(...)`; do not construct remote endpoints from assumptions. |
| `core.schemas.request` | `BaseRequest` wraps `Request_ID`, `Request_type`, `Prompt`, `Service`, `Task`. `Task` includes selected addresses, Connector context and scheduler metadata. | See `BaseRequest.build_request` for construction and validation. |
| `scheduler.controller.strategy` | Context includes token length, output limit, SLOs and compression factor; decision contains P/D addresses and descriptor. | `GlobalScheduleContext(10, 5, 2000, 20, 0.3)` |
| `scheduler.controller.resource_state` | Snapshot contains versioned Pool, network and compatible-pair data. Read the source for exact types. | `resource_state.snapshot()` |

## Current observability propagation and startup exports

`Request_ID` and `Task` context carry selection and transfer information across internal hops; NIXL also handles `X-Request-Id`. This is not a universal distributed trace ID guarantee. See [observability](../architecture/observability-v1.md). Entrypoint mappings and package resource paths are in [installation](../installation.md).

## Dedicated public-module sections

### Request and service models

`Prompt` describes model and tokenized request, `Service` holds service parameters and `Task` holds route state. Their exact serialized fields and endpoint behavior are implementation-specific; client requests are summarized in [configuration and interfaces](configuration-and-interfaces.md#5-client-request-fields-and-forwarding-limits).

### Connector descriptor and session

`ConnectorDescriptor.from_payload` accepts an object or name string; metadata must be a mapping. NIXL and LMCache impose different validation rules; see [Connector contracts](../../connectors/README.md). Session creation is an in-memory compatibility/lifecycle operation, not a cache-transfer receipt.

### Scheduling and resources

Global strategy inputs use `GlobalScheduleContext` and `ResourceSnapshot`; local strategies return P/D scheduling results. Their fields may change with implementation; inspect [source](../../scheduler/controller/strategy.py) and [strategy tests](../../test/test_gateway_scheduler_strategy.py) before integrating.
