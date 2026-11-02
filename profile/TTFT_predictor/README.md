# TTFT Predictor

This directory provides measurement, fitting, prediction and local persistence
code, not fitted experiment models. Generated `artifacts/` and profile pickle/joblib
files are ignored by Git. See [SLO operation](../../doc/slo.md) for real NIXL
calibration, validation and Gateway loading using `pdschedule.profiling`.

## Files and inputs

- [prefill_regressor.py](prefill_regressor.py): collect warmup samples, fit and persist a linear model.
- [prefill_predictor.py](prefill_predictor.py): async prediction, detailed warmup and local load/save.
- [request_generator.py](request_generator.py): construct requests with a tokenizer and measure TTFT.
- [prefill_prediction_server.py](prefill_prediction_server.py): HTTP prediction service.
- [validate_prefill_predictor.py](validate_prefill_predictor.py): check predictions against real requests.

The current model is:

```text
total_tokens = max(0, batch_size) * max(0, prompt_length)
TTFT_seconds = tokens_coefficient * total_tokens + intercept_seconds
```

The regressor normalizes total tokens before fitting and restores coefficients.
Detailed warmup sends real requests; batching and filtering are implemented in
the regressor. Results apply to the measured model, GPU, TP/PP, vLLM settings and
measurement path, not automatically to another deployment.

## Cold-start prediction

From the repository root, enter `profile/` and run:

```python
import asyncio
from TTFT_predictor.prefill_predictor import predict_ttft

print(asyncio.run(predict_ttft(batch_size=1, prompt_length=128)))
```

The return value is in seconds. Without a local model, built-in synthetic samples
or a rough estimate support functional checks, not measured hardware accuracy.

## Measure, fit and save locally

Prepare a compatible environment, real vLLM service and local tokenizer; run in `profile/`:

```python
import asyncio
from TTFT_predictor.prefill_predictor import perform_detailed_warmup

config = {
    "host": "127.0.0.1",
    "port": 8001,
    "model_id": "your-served-model",
    "tokenizer_path": "/path/to/your/model",
}

asyncio.run(perform_detailed_warmup(
    vllm_config=config,
    repeats=3,
    model_file="TTFT_predictor/artifacts/prefill_ttft_regressor.pkl",
))
```

Replace the model and tokenizer paths and check the service, batch size and prompt
length range. Warmup sends real requests. The file is local and is not saved when
`model_file` is omitted. Check sample validity, coefficients, logs and validation
results; a function return alone does not prove calibration.

## Loading and Gateway integration

`load_ttft_model(path)` returns `False` for a missing file; `save_ttft_model(path)`
saves locally fitted state. Load only trusted pickle files. With warmup enabled,
P Gateway first loads its local file and otherwise attempts real warmup. If the
target or calibration is unavailable, inspect readiness and policy fallback.
Controller uses [separate local profiles](../controller/README.md).

`--no-ttft-warmup` skips both loading and fitting, even if `--ttft-model-file` is
specified. With warmup enabled check the path in `[TTFT Predictor] Using persisted
model: ...`. Restart Gateway after replacing a model; there is no automatic reload.
