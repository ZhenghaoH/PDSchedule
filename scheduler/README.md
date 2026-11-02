# Scheduler

## Overview

PDSchedule makes decisions at two levels: Controller selects a compatible Prefill/Decode Pool pair; each Gateway selects an Instance in its own Pool. See [Controller scheduler](controller/README.md) and [Gateway schedulers](gateway/README.md).

## Quick start and CLI

Start services and inspect their registration with the [real NIXL guide](../doc/quickstart_v1.md). Controller's interactive CLI provides `show pool_id`, `show scheduler`, `switch scheduler -m <name>` and `quit`. Gateway consoles provide `show scheduler`, `switch scheduler -m <name>` and `quit` unless disabled with `--no-cli`.

## Workflow and strategy

Controller filters P/D pairs by Connector compatibility before global selection. One active compatible pair takes the direct path. For multiple Pools, the default is `demand_aware`; `least_load` and `round_robin` are alternatives. P and D Gateway policy sets are different; see the linked role guides. Predictors and fallback behavior are described in [SLO operation](../doc/slo.md).

## Options and validation

Controller global policy changes via its console; P/D Gateway startup flags and defaults are in [configuration](../doc/developer-handbook/configuration-and-interfaces.md#3-service-cli). Run `python -m pytest -q test/test_controller_scheduler_components.py test/test_gateway_scheduler_strategy.py test/test_single_gateway_routing.py` in the [CPU environment](../test/README.md). Physical transfer requires the real deployment checks.

## Resource maintenance and notes

Instance and Gateway reports update local and Controller state; snapshots contain currently active Pools and network records. Metrics, fallback values and freshness are detailed in [resource state](../doc/resource_state_metrics.md). An observed value is not necessarily used by every policy.
