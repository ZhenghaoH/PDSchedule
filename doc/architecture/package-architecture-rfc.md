# PDSchedule package architecture

## Decision

Document the existing explicit package layout and its responsibilities. A full namespace reorganization or new versioned domain contracts are not approved or implemented by this document.

## Repository layout

The current checkout separates `core`, `config`, `scheduler`, `store`, `coord`, `connectors`, `proxy`, `instance`, `utils`, `entrypoints` and `pdschedule`. See the [module map](../developer-handbook/package-and-module-map.md) for every explicitly packaged module.

## Package responsibilities

`core` owns HTTP routing and request handling; `scheduler` owns selection logic; `store` and `coord` own local state and reporting; `connectors` owns descriptor validation and handoff; `proxy` implements forwarding and batching; `instance` collects serving-resource observations; `entrypoints` assemble services. `pdschedule` supplies package identity, paths and mapped profiling/resources. Boundaries describe current ownership, not enforced isolation.

## Integration boundaries

vLLM serves model inference. NIXL supplies request-scoped transfer, and LMCache can supply a shared store when independently configured. Controller cannot establish physical KV success from a descriptor alone. See [NIXL](../integrations/nixl.md) and [LMCache](../integrations/lmcache.md).

## Dependency direction

Entrypoints configure and assemble core lifecycles; handlers invoke scheduling, state, Connector and forwarding helpers. Gateway and Instance coordinate through HTTP. Package imports are not a stable plug-in API and the code does not guarantee a perfectly acyclic domain layering.

## Public API policy

The documented HTTP and CLI surfaces support deployment of this version. Internal Python modules and dataclasses are implementation details unless explicitly designated otherwise; see [API and models](../developer-handbook/public-api-and-data-models.md).

## Packaging policy

`pyproject.toml` lists packages and console scripts explicitly. The wheel includes mapped resources but excludes tests and root-only tools; `MANIFEST.in` governs the source distribution. [Installation](../installation.md) explains resource and artifact paths. Dependencies come from `requirements.txt`.

## Versioning policy

The package version comes from `pdschedule.__version__`. Connector descriptor `version` is a compatibility string, not a serving-library version. Documentation filenames containing `v1` do not introduce a separate runtime version.

## Current layout versus ownership

| Current path | Current role | Owner | Status | Rule |
| --- | --- | --- | --- | --- |
| `core/`, `entrypoints/` | HTTP and service assembly | service layer | Current | Keep CLI and HTTP docs in sync. |
| `scheduler/`, `store/`, `coord/` | placement, local state, reporting | scheduling | Current | Check snapshots, units and tests together. |
| `connectors/` | protocol, identity and request handoff | transfer boundary | Current | Validate serving data plane separately. |
| `profile/` mapped as `pdschedule.profiling` | calibration and predictor code | prediction | Current | Store fitted files outside installed packages. |

## Change checklist

Review module mapping, imports, installation modes, source distribution and wheel, documented interfaces and focused tests. Introduce separate design approval before any broad package migration.

## Non-goals

This document does not add a runtime profile, backend, Dashboard, unified exporter or namespace migration; it does not establish performance or GPU compatibility.
