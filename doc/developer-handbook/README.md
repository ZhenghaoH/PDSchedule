# PDSchedule Developer and Maintenance Handbook

## Intended readers

Maintainers working on scheduling, service interfaces, Connectors, deployment and validation. Operators can begin with the [quick start](../quickstart_v1.md) and [environment guide](../../env/README.md).

## How to use this handbook

Start with the architecture and runtime flow, then consult the interface index and focused tests before changing a boundary. Code defines actual behavior; this handbook records the boundaries that must be checked with it.

## Current development context

PDSchedule 0.1.0 has explicit installation metadata and six console entrypoints. Runtime code currently resides in several root-level packages alongside `pdschedule`; those packages are installed, not a promise of a stable external Python API. No full package reorganization is approved by this documentation.

## Chapter index

| Chapter | Use |
| --- | --- |
| [Architecture and evolution](architecture-and-evolution.md) | Current components and design boundaries |
| [Package and module map](package-and-module-map.md) | Responsibilities, installation and extension points |
| [Runtime flows](runtime-flows.md) | Request, registration, transfer and profiling paths |
| [Configuration and interfaces](configuration-and-interfaces.md) | Configuration resolution, CLI and HTTP index |
| [Public API and data models](public-api-and-data-models.md) | Supported surfaces and internal contracts |
| [Development and validation](development-and-validation.md) | Test environments and evidence |
| [Compatibility and migrations](compatibility-and-migrations.md) | Source, editable and distribution boundaries |
| [Documentation governance](documentation-governance.md) | Keeping documentation consistent with changes |
| [Glossary](glossary.md) | Shared terminology |

## Status legend

| Status | Meaning |
| --- | --- |
| Current | Present in this source tree; behavior still depends on deployment. |
| Internal | Implementation contract, not a stable third-party API. |
| Configuration | Declared compatibility or operator-supplied value, not a measurement. |
| Not implemented | No current behavior is promised. |

## Source-of-truth matrix

| Source | Scope | Authority |
| --- | --- | --- |
| Source code and tests | Current behavior and validation | Primary for the exact revision |
| `pyproject.toml`, `MANIFEST.in`, `requirements.txt` | Package contents and dependency metadata | Primary for builds |
| [Configuration index](configuration-and-interfaces.md) | Operator-facing defaults, CLI and HTTP | Navigational index; verify against code |
| [Environment](../../env/README.md), [quick start](../quickstart_v1.md) | Example serving setup and evidence | Deployment procedure; not proof of a successful run |

## Quick links for common maintenance tasks

| Task | Read first |
| --- | --- |
| Change P/D route selection | [Runtime flows](runtime-flows.md), [Controller scheduler](../../scheduler/controller/README.md) |
| Change Instance selection | [Gateway scheduler](../../scheduler/gateway/README.md), [SLO guide](../slo.md) |
| Change transfer compatibility | [Connectors](../../connectors/README.md), [NIXL](../integrations/nixl.md), [LMCache](../integrations/lmcache.md) |
| Change resource reporting | [Resource metrics](../resource_state_metrics.md), [observability](../architecture/observability-v1.md) |
| Change a CLI or HTTP field | [Configuration index](configuration-and-interfaces.md) and the linked source |
| Change packaging | [Installation](../installation.md), [module map](package-and-module-map.md) |

## Update expectations

Keep the responsible guide, interface index and relevant tests in step with code changes. Link to a single owner for complete command sequences and parameter tables; report what was actually validated.
