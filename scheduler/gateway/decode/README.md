# Decode Gateway scheduling

## Role and request path

After Prefill context reaches D Gateway, it selects an online Decode Instance and forwards the request. It tracks response streaming and selected Instance state. See [Gateway overview](../README.md) and [runtime flows](../../../doc/developer-handbook/runtime-flows.md).

## Strategies and capability contract

| Strategy | Behavior | Boundary |
| --- | --- | --- |
| `slo_aware_decode` | Default; evaluates Decode placement with TPOT prediction and fallbacks. | Real predictor validity depends on serving calibration; see [SLO](../../../doc/slo.md). |
| `lowest_generation_throughput` | Selects minimum reported generation throughput, with address tie-break. | Missing/insufficient samples can appear as zero. |
| `round_robin` | Rotates among eligible Instances. | No calibrated SLO model required. |

Candidate snapshots can include running, waiting and KV usage; logging a field does not mean a strategy consumes it. Active request tracking is local to the Gateway.

## Startup, management and validation

Use `--decode-scheduler-strategy` and predictor flags in the [CLI index](../../../doc/developer-handbook/configuration-and-interfaces.md#3-service-cli). The optional interactive console supports `show scheduler` and `switch scheduler -m <name>`. [Quick start](../../../doc/quickstart_v1.md) uses `round_robin` with warmup disabled; [SLO guide](../../../doc/slo.md) covers calibrated operation. Run `python -m pytest -q test/test_gateway_scheduler_strategy.py test/test_decode_pool_load.py` for CPU checks; validate real KV delivery separately.
