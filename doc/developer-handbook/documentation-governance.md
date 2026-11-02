# Documentation governance

## Same-change handbook maintenance rule

When behavior, configuration, package contents or supported deployment changes, update the owning reference and its tests in the same change. Describe current behavior and separately mark unimplemented or unverified combinations.

## Surfaces requiring same-change review

| Changed surface | Review |
| --- | --- |
| CLI, environment, HTTP or schema | [Configuration and interfaces](configuration-and-interfaces.md), examples and focused tests |
| Strategy, state or Connector | [Runtime flows](runtime-flows.md), [module map](package-and-module-map.md), integration and component guides |
| Metrics or logs | [Resource state](../resource_state_metrics.md), [observability](../architecture/observability-v1.md) and units |
| Packaging, paths or files | [Installation](../installation.md), manifest, package tests and relative links |
| Model or serving-version guidance | [Environment](../../env/README.md), [quick start](../quickstart_v1.md), explicit validation boundary |

## Maintenance rules

Give detailed commands and parameter tables one owning page and link there. Keep source symbols, names and defaults exact. Verify local file links and anchors, identify third-party requirements, and distinguish measured values from configured estimates. Do not turn design proposals or CPU results into evidence of a real GPU run.
