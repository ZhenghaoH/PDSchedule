# Prefill Gateway scheduling

## Role and request path

The P Gateway receives the Controller's internal request, chooses an online Prefill Instance and executes Prefill before handing context to Decode. It has a local strategy manager and queue/dispatch machinery; it is not the vLLM engine. See [Gateway overview](../README.md) and [runtime flows](../../../doc/developer-handbook/runtime-flows.md).

## Strategies and capability contract

| Strategy | Behavior | Boundary |
| --- | --- | --- |
| `slo_aware_prefill` | Default; plans Prefill work using TTFT estimates and availability. | Requires appropriate predictor/calibration for meaningful estimates; see [SLO](../../../doc/slo.md). |
| `jsq` | Arrival-triggered selection by reserved unfinished prompt tokens. | Its capabilities disallow preemption and adaptive switch; local accounting is not GPU utilization. |
| `round_robin` | Rotates among eligible Instances. | Does not require a calibrated SLO model. |

Policies implement `PrefillStrategy` and return scheduling results; candidate snapshots are diagnostic and do not imply all fields influence all choices. Queue and reservation lifecycle is managed by Gateway scheduling/dispatch code.

## Startup, management and validation

Use `--prefill-scheduler-strategy` and optional predictor flags as documented in the [CLI index](../../../doc/developer-handbook/configuration-and-interfaces.md#3-service-cli). `show scheduler` and `switch scheduler -m <name>` require the interactive Gateway CLI. [Quick start](../../../doc/quickstart_v1.md) uses `jsq` and disables warmup; [SLO guide](../../../doc/slo.md) gives calibration and policy examples. Run `python -m pytest -q test/test_gateway_scheduler_strategy.py` for CPU checks. No standalone P-only cache-transfer guarantee is implied.
