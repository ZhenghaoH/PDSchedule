# TPOT Predictor

This directory provides measurement, fitting, prediction and local persistence
code, not pretrained or experiment-machine models. Generated `artifacts/` stay
local and are ignored by Git. See [SLO operation](../../doc/slo.md) for real NIXL
calibration, validation and Gateway loading using `pdschedule.profiling`.

## Files and models

- [TPOT_regressor.py](TPOT_regressor.py): fit four coefficients from generation times, update and persist state.
- [TPOT_predictor.py](TPOT_predictor.py): async prediction, detailed warmup and local load/save.
- [TPOT_measure.py](TPOT_measure.py): measure real vLLM generation.
- [TPOT_warmup.py](TPOT_warmup.py): warmup wrapper.
- [TPOT_prediction_server.py](TPOT_prediction_server.py): HTTP prediction service.
- [validate_decode_predictor.py](validate_decode_predictor.py): validate predictions against real requests.

Single-step and total generation-time models:

```text
step_seconds = d1 * batch_size * context_length
             + d2 * batch_size + d3 * context_length + d4

sum_context = generation_length * prompt_length
            + generation_length * (generation_length - 1) / 2
generation_seconds = d1 * batch_size * sum_context
                   + d2 * batch_size * generation_length
                   + d3 * sum_context + d4 * generation_length
```

Real samples include batch size, prompt and actual generated token counts, and
generation time. Remeasure and validate after changing model, GPU, parallelism,
runtime or batching configuration.

## Cold-start prediction

From the repository root, enter `profile/` and run:

```python
import asyncio
from TPOT_predictor.TPOT_predictor import predict_tpot, predict_generation_time

async def main():
    print(await predict_tpot(batch_size=1, context_length=128))
    print(await predict_generation_time(
        batch_size=1, prompt_length=128, generation_length=32,
    ))

asyncio.run(main())
```

Returns are in seconds. Initialization includes synthetic samples; until real
calibration, predictions support functional checks, not performance claims.

## Measure, fit and save locally

Prepare a real Decode vLLM service and tokenizer, then run in `profile/`:

```python
import asyncio
from TPOT_predictor.TPOT_predictor import perform_detailed_warmup

config = {
    "host": "127.0.0.1",
    "port": 8002,
    "model_id": "your-served-model",
    "tokenizer_path": "/path/to/your/model",
    "prompt": "Write a short story.",
}

asyncio.run(perform_detailed_warmup(
    vllm_config=config,
    repeats=2,
    model_file="TPOT_predictor/artifacts/decode_tpot_regressor.pkl",
))
```

Replace the model and tokenizer paths and set `port` to the actual Decode vLLM
port (8003 in the NIXL quick start); check batch size and generation length.
Warmup sends real requests and saves fitted state after valid samples. Inspect
measurement logs and validation; synthetic cold-start output is not calibration
evidence. Omitting `model_file` retains state only in the process.

## Loading and Decode Gateway integration

`load_tpot_model(path)` returns `False` for a missing file; `save_tpot_model(path)`
saves locally fitted state. Load only trusted pickle files. With warmup enabled,
D Gateway first loads its file and otherwise attempts real warmup. Inspect actual
readiness and fallback if calibration is missing. Controller uses [separate
local profiles](../controller/README.md).

`--no-tpot-warmup` skips both loading and fitting, even with `--tpot-model-file`.
With warmup enabled inspect the persisted-model path in logs. Restart Gateway
after replacing a model. If prediction is unavailable, `slo_aware_decode` falls
back to lowest generation throughput; without a safe candidate it can choose a
predicted least-violating candidate rather than reject the request.
