# Package and module map

The current implementation is authoritative. “Internal” means a Python implementation interface rather than a separately versioned public SDK. Console entrypoints and documented HTTP routes are deployment interfaces.

| Concept / responsibility | Canonical package/module | Current transitional implementation | Dependency direction | Public/internal | Design link | Relevant tests | Migration status | Status |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Version and paths | `pdschedule` | `pdschedule/__init__.py`, `paths.py` | Entrypoints → path helpers | Internal helpers | [installation](../installation.md) | [packaging](../../test/test_package_installation.py) | Current layout | Current |
| HTTP and request models | `core` | `schemas`, `routers`, `handlers`, `lifespans`, `consoles` | Entrypoints → core → scheduling/store | HTTP documented; models internal | [interfaces](configuration-and-interfaces.md) | [gateway](../../test/test_gateway_scheduler_strategy.py) | Current layout | Current |
| Runtime configuration | `config` | role config and `network/` | Entrypoints/core → config | CLI/environment documented | [configuration](configuration-and-interfaces.md) | [registration](../../test/test_runtime_instance_registration.py) | Current layout | Current |
| Global route and snapshot | `scheduler.controller` | manager, strategy, resource state, implementations | core → scheduler → store/connectors | Internal | [Controller](../../scheduler/controller/README.md) | [controller](../../test/test_controller_scheduler_components.py) | Current layout | Current |
| Pool placement | `scheduler.gateway` | P/D managers, strategies | handlers → strategies → Gateway store | Internal | [Gateway](../../scheduler/gateway/README.md) | [gateway](../../test/test_gateway_scheduler_strategy.py) | Current layout | Current |
| Compatibility and handoff | `connectors` | manager, registry, protocol, models, adapters | scheduler/handlers → connectors | Internal | [Connectors](../../connectors/README.md) | [protocol](../../test/test_connector_protocol.py) | Current layout | Current |
| State and registration | `store`, `coord` | Controller/Gateway/Instance stores and reporting | handlers/coord → stores | Internal | [metrics](../resource_state_metrics.md) | [registration](../../test/test_runtime_instance_registration.py) | Current layout | Current |
| Forwarding and batching | `proxy` | `forward.py`, `batcher.py`, `sync.py` | handlers → proxy → HTTP downstream | Internal library | [proxy](../../proxy/README.md) | [gateway](../../test/test_gateway_scheduler_strategy.py) | Current layout | Current |
| Engine observation | `instance.perception` | vLLM, NVML and throughput sampling | Instance reporting → perception | Internal | [Instance](../../instance/README.md) | [observability](../../test/test_gateway_observability.py) | Current layout | Current |
| Prediction and calibration | `pdschedule.profiling` | packaged code sourced from `profile/` | strategies → predictors; artifacts deployment-owned | Internal | [profile](../../profile/README.md) | [profiler](../../test/test_distserve_lm_profiler.py) | Explicit package mapping | Current |
| Service launch | `entrypoints`, `examples.clientUI` | Controller, P/D Gateway, P/D Instance and client | entrypoints → core/config | Six console commands | [installation](../installation.md#installed-service-commands) | [packaging](../../test/test_package_installation.py) | Current layout | Current |
| Support | `utils`, `pdschedule.resources` | networking, timing, tokenizer and packaged template | higher-level code → utilities/resources | Internal | [observability](../architecture/observability-v1.md) | [packaging](../../test/test_package_installation.py) | Current layout | Current |

## Explicitly configured package coverage

The following is the actual `tool.setuptools.packages` list in `pyproject.toml`. Predictor package directories are mapped to `profile/`; template resources map to `templates/`. `instance.simulator` is packaged; root `simulator/`, `plan/`, tests and documentation are not wheel packages. See the [distribution scope](../installation.md#namespaces-and-resources).

<!-- BEGIN EXPLICIT PACKAGES -->
- `pdschedule`
- `pdschedule.profiling`
- `pdschedule.resources`
- `pdschedule.profiling.TTFT_predictor`
- `pdschedule.profiling.TPOT_predictor`
- `pdschedule.resources.templates`
- `config`
- `config.models`
- `config.network`
- `connectors`
- `connectors.adapters`
- `coord`
- `coord.controller`
- `coord.gateway`
- `coord.instance`
- `core`
- `core.commands`
- `core.commands.controller`
- `core.consoles`
- `core.handlers`
- `core.handlers.controller`
- `core.handlers.gateway`
- `core.handlers.gateway.prefill`
- `core.handlers.gateway.decode`
- `core.handlers.instance`
- `core.lifespans`
- `core.routers`
- `core.schemas`
- `entrypoints`
- `examples`
- `examples.clientUI`
- `instance`
- `instance.perception`
- `instance.simulator`
- `proxy`
- `scheduler`
- `scheduler.controller`
- `scheduler.controller.strategies`
- `scheduler.gateway`
- `scheduler.gateway.prefill`
- `scheduler.gateway.decode`
- `store`
- `store.controller`
- `store.gateway`
- `store.instance`
- `utils`
<!-- END EXPLICIT PACKAGES -->

## Extension points

Add a global strategy under `scheduler/controller/strategies/` and register it with the manager; add role-specific strategies under `scheduler/gateway/prefill/` or `decode/`. Request models live in `core/schemas/request.py`; registration and updates in `core/handlers/gateway/` and `coord/instance/up_store.py`. Connector changes must cover adapters, registry, compatibility and actual serving validation. No other P2P Connector is registered by default.
