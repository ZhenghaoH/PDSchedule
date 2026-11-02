# Controller scheduler

## Overview

`global_scheduler.py` builds the routing decision from `resource_state.py` snapshots and `manager.py`; `strategy.py` defines inputs/results, and `strategies/` implements the policies. Controller selects Gateway Pools, not GPUs or individual Instances.

## Quick start

Run the complete [1P/1D guide](../../doc/quickstart_v1.md); use the [2P/2D guide](../../doc/multi_pool.md) to exercise global choices. With only one active compatible P/D pair the scheduler selects it directly rather than invoking a specific global policy.

## Controller CLI

At `scheduler-cli>`, use `show pool_id`, `show scheduler`, `switch scheduler -m demand_aware`, `switch scheduler -m least_load`, `switch scheduler -m round_robin`, or `quit`. These affect the current process. Controller currently requires its interactive console.

## Scheduling workflow

Registration/update/unregister events refresh the versioned resource snapshot. The snapshot filters compatible P/D Connector pairs. For a multi-Pool request the manager passes token length, output limit, SLO and the current snapshot to a strategy. No compatible pair means routing cannot proceed. Affinity reuse, when enabled, is validated against the current snapshot.

## Strategies

| Name | Meaning |
| --- | --- |
| `demand_aware` | Default cost-based choice using request, Pool and network estimates; local profiles may affect estimates and have fallbacks. |
| `least_load` | Select from compatible pairs by load. |
| `round_robin` | Rotate among compatible candidates. |

See [strategy source](strategies/) and [SLO guide](../../doc/slo.md) for actual inputs and calibration.

## Runtime options

Controller host/port and exact environment behavior are indexed in [configuration](../../doc/developer-handbook/configuration-and-interfaces.md#3-service-cli). Strategy switching is interactive; there is no controller startup policy option or remote policy-switch API documented here.

## Strategy validation

Use the multi-Pool guide's real registration, policy switching and all-four-path transfer checks. CPU-only policy/snapshot checks: `python -m pytest -q test/test_controller_scheduler_components.py test/test_single_gateway_routing.py`.

## Resource Pool maintenance

Controller receives Pool and network updates. Active Pool filtering defaults to 3000 seconds and link filtering to 60 seconds; the periodic reconciliation does not replace actual heartbeat validation. Reported capacity is configuration, while load comes from role-specific aggregation. See [resource state](../../doc/resource_state_metrics.md).

## Notes

Selected compatible descriptors are necessary but not sufficient for actual KV movement. No knowledge-routing service or runtime profile is provided by this scheduler.
