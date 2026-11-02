# SLO calibration and operation

Start with the real [NIXL multi-Pool deployment](multi_pool.md). This guide keeps
its four vLLM engines, model, ports, and GPU assignments. It adds local TTFT/TPOT
calibration, Gateway SLO policies, and optional competing Instances within each
Pool. No fitted model is supplied by the repository or downloaded by these steps.

Exact startup defaults, environment exceptions, and HTTP surfaces are listed in
the [configuration/interface index](configuration-and-interfaces.md).

## 1. Know which layer makes each decision

| Layer | Selects | Policies | Predictor use |
| --- | --- | --- | --- |
| Controller | A compatible P Gateway and D Gateway | `round_robin`, `least_load`, `demand_aware` | `demand_aware` optionally loads Controller-local coefficients for cost estimates. SLO values are logged, not hard admission constraints in its fitness. |
| P Gateway | A local P Instance and request dispatch plan | `round_robin`, `jsq`, `slo_aware_prefill` | `slo_aware_prefill` predicts Prefill completion against TTFT deadlines and plans dispatch using outstanding work. |
| D Gateway | A local D Instance after Prefill | `round_robin`, `lowest_generation_throughput`, `slo_aware_decode` | `slo_aware_decode` evaluates the active batch plus the new request against TPOT budgets, preferring safe candidates. |

Exactly 1P/1D **Pool** bypasses the Controller policy, not the Gateway policies.
One Instance per Pool leaves only one local destination; it cannot demonstrate
better local placement. Section 8 adds a second Instance to every Pool.
Gateway planning does not replace vLLM continuous batching, and a predicted
feasible placement is not a hard deadline or rejection guarantee.

The P planner currently sets its deadline from arrival at the P Gateway, not
from the original client's send time. D prediction evaluates each active request
at `prompt_tokens + max_tokens`; its batch view is Gateway-tracked work, not the
engine's exact future continuous-batching membership. Keep these boundaries in
mind when comparing plans with end-to-end SLO traces.

### SLO source and units

`Service.mapping_slo_info()` uses the TCP client address seen by Controller:

| Client source | PD enabled | TTFT | TPOT | E2E |
| --- | --- | --- | --- | --- |
| `10.0.*` | No | 5000 ms | 50 ms | 55000 ms |
| `192.168.*` | No | 10000 ms | 150 ms | 160000 ms |
| Other, including `127.0.0.1` | Yes | 20000 ms | 40 ms | 25336 ms |

These are the mapping values, not the standalone `Service` dataclass defaults.
Send requests from inside the container to Controller's loopback endpoint.
Do not add arbitrary `SLO_TTFT`, `SLO_TPOT`, or `Enable_PD_Disaggregation` fields
to a normal OpenAI payload: the current ingress does not use them as overrides.
`Enable_Affinity=False` keeps repeated requests on the general policy path.

Service SLO fields and displayed budgets use **milliseconds**. Predictor APIs and
saved coefficients use **seconds**; scheduling code converts units. E2E is
reported separately, not a fourth selectable policy. Existing compression flags
and the Controller's KV-size/network estimate are not proof that NIXL compresses
the bytes transmitted by this deployment.

## 2. Calibrate against the actual engines

Leave the four vLLMs running and stop client traffic during calibration. Run the
following blocks sequentially from a check terminal. They directly request the
vLLM API ports **8001–8004**, not Proxy ports 7001–7004 or Gateway ports.
Keep the baseline Gateways on `--no-ttft-warmup` / `--no-tpot-warmup` for now.

```bash
cd /workspace/llm-stack/PDSchedule
export PDSCHEDULE_PROFILE_DIR=/workspace/llm-stack/PDSchedule/profile
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
```

These examples use the installed/source-compatible `pdschedule.profiling`
namespace and override the warmup grids **only in the calibration process**.
TTFT prompt lengths stop at 3072 to leave room for the chat template, UUID prefix,
and output within `--max-model-len 4096`. TPOT uses batch sizes 1/2/4 and generation
budgets 128/256/512. Increase sample coverage for your real workload; the example
grid is not a universal performance model. Warmup produces real inference load.

### TTFT: measure P-A and P-B separately

```bash
python3 - <<'PY'
import asyncio
import hashlib
import math
from pdschedule.paths import profile_root
from pdschedule.profiling.TTFT_predictor import prefill_predictor as ttft

MODEL = "/workspace/llm-stack/models/deepseek-ai/DeepSeek-R1-Distill-Qwen-7B"
ttft.WARM_UP_CONFIGS_DEFAULT = [(1, length) for length in (128, 256, 512, 1024, 2048, 3072)]

async def main():
    for gateway_port, engine_port in ((8081, 8001), (8083, 8002)):
        path = profile_root() / "deploy" / f"p{gateway_port}" / "artifacts" / "prefill_ttft_regressor.pkl"
        await ttft.perform_detailed_warmup(
            vllm_config={"host": "127.0.0.1", "port": engine_port,
                         "model_id": MODEL, "tokenizer_path": MODEL},
            repeats=3, model_file=str(path),
        )
        regressor = await ttft.get_regressor()
        sampled_lengths = {length for _, length, _ in regressor._training_data}
        expected_lengths = {length for _, length in ttft.WARM_UP_CONFIGS_DEFAULT}
        assert expected_lengths <= sampled_lengths, (engine_port, sampled_lengths)
        assert len(regressor._training_data) == 3 * len(ttft.WARM_UP_CONFIGS_DEFAULT)
        assert regressor._is_fitted and path.is_file(), path
        assert all(math.isfinite(float(value)) for value in regressor.get_coefficients().values())
        fitted_coeffs, fitted_samples = dict(regressor.get_coefficients()), list(regressor._training_data)
        assert await ttft.load_ttft_model(str(path)), path
        assert regressor.get_coefficients() == fitted_coeffs and regressor._training_data == fitted_samples
        print(f"P{gateway_port}: real_samples={len(regressor._training_data)}; TTFT(1,512)={await ttft.predict_ttft(1,512):.6f}s")
        print(f"saved={path}; sha256={hashlib.sha256(path.read_bytes()).hexdigest()}")

asyncio.run(main())
PY
```

### TPOT: measure D-A and D-B separately

```bash
python3 - <<'PY'
import asyncio
import hashlib
import math
from pdschedule.paths import profile_root
from pdschedule.profiling.TPOT_predictor import TPOT_predictor as tpot

MODEL = "/workspace/llm-stack/models/deepseek-ai/DeepSeek-R1-Distill-Qwen-7B"
tpot.TPOT_WARM_UP_CONFIGS_DEFAULT = [
    {"bs": batch, "gen": length}
    for batch in (1, 2, 4) for length in (128, 256, 512)
]

async def main():
    for gateway_port, engine_port in ((8082, 8003), (8084, 8004)):
        path = profile_root() / "deploy" / f"d{gateway_port}" / "artifacts" / "decode_tpot_regressor.pkl"
        await tpot.perform_detailed_warmup(
            vllm_config={"host": "127.0.0.1", "port": engine_port,
                         "model_id": MODEL, "tokenizer_path": MODEL,
                         "prompt": "Write a long, detailed story about a team exploring a distant planet."},
            repeats=2, model_file=str(path),
        )
        regressor = await tpot.get_regressor()
        assert len(regressor._training_data) == 2 * len(tpot.TPOT_WARM_UP_CONFIGS_DEFAULT)
        assert {point["bs"] for point in regressor._training_data} == {1, 2, 4}
        assert regressor._is_fitted and path.is_file(), path
        assert all(math.isfinite(float(value)) for value in regressor.get_coefficients().values())
        fitted_coeffs, fitted_samples = dict(regressor.get_coefficients()), list(regressor._training_data)
        assert await tpot.load_tpot_model(str(path)), path
        assert regressor.get_coefficients() == fitted_coeffs and regressor._training_data == fitted_samples
        print(f"D{gateway_port}: real_samples={len(regressor._training_data)}; TPOT(1,512)={await tpot.predict_tpot(1,512):.6f}s")
        print(f"saved={path}; sha256={hashlib.sha256(path.read_bytes()).hexdigest()}")

asyncio.run(main())
PY
```

Both routines clear collected data before measuring. The checks deliberately
reject empty/incomplete sample coverage even if a warmup function returns or a
previous model file exists, and check that loading the saved file restores the
new coefficients and samples. Examine errors, sample counts, and fitted coefficients;
`Warmup finished` alone is not sufficient. TPOT stores **actual** generated token
counts, which may be shorter than the requested budget. Its fixed-prompt grid
needs additional validation for workloads with different context distributions.

Models and reports below live under ignored `profile/**/artifacts/`. Keep them
local, with their hardware, model, runtime, launch configuration, sample coverage,
and hash. Only load trusted locally generated pickle files.

## 3. Validate with held-out real requests

Use the existing source-checkout validation tools, with every deployment-specific
path/endpoint explicit. These tools send more real requests and do not retrain the
saved files. The ranges differ from the fitting grid; inspect measured vs predicted
errors and failed-request counts, not just exit status.

```bash
cd /workspace/llm-stack/PDSchedule
python3 profile/TTFT_predictor/validate_prefill_predictor.py \
  --host 127.0.0.1 --port 8001 \
  --model /workspace/llm-stack/models/deepseek-ai/DeepSeek-R1-Distill-Qwen-7B \
  --model-file profile/deploy/p8081/artifacts/prefill_ttft_regressor.pkl \
  --num-batches 4 --min-total-tokens 384 --max-total-tokens 1536 \
  --min-requests 2 --max-requests 3 --max-output-tokens 1 --seed 7 \
  --output-json profile/deploy/p8081/artifacts/validation.json
```

```bash
python3 profile/TTFT_predictor/validate_prefill_predictor.py \
  --host 127.0.0.1 --port 8002 \
  --model /workspace/llm-stack/models/deepseek-ai/DeepSeek-R1-Distill-Qwen-7B \
  --model-file profile/deploy/p8083/artifacts/prefill_ttft_regressor.pkl \
  --num-batches 4 --min-total-tokens 384 --max-total-tokens 1536 \
  --min-requests 2 --max-requests 3 --max-output-tokens 1 --seed 7 \
  --output-json profile/deploy/p8083/artifacts/validation.json
```

```bash
python3 profile/TPOT_predictor/validate_decode_predictor.py \
  --host 127.0.0.1 --port 8003 \
  --model /workspace/llm-stack/models/deepseek-ai/DeepSeek-R1-Distill-Qwen-7B \
  --tokenizer-path /workspace/llm-stack/models/deepseek-ai/DeepSeek-R1-Distill-Qwen-7B \
  --model-file profile/deploy/d8082/artifacts/decode_tpot_regressor.pkl \
  --num-batches 4 --min-requests 1 --max-requests 3 \
  --min-output-tokens 160 --max-output-tokens 384 --seed 7 \
  --output-json profile/deploy/d8082/artifacts/validation.json
```

```bash
python3 profile/TPOT_predictor/validate_decode_predictor.py \
  --host 127.0.0.1 --port 8004 \
  --model /workspace/llm-stack/models/deepseek-ai/DeepSeek-R1-Distill-Qwen-7B \
  --tokenizer-path /workspace/llm-stack/models/deepseek-ai/DeepSeek-R1-Distill-Qwen-7B \
  --model-file profile/deploy/d8084/artifacts/decode_tpot_regressor.pkl \
  --num-batches 4 --min-requests 1 --max-requests 3 \
  --min-output-tokens 160 --max-output-tokens 384 --seed 7 \
  --output-json profile/deploy/d8084/artifacts/validation.json
```

A usable result has no unexplained failed requests, finite predictions, and error
small enough for the intended SLO margin across the actual workload range. There
is no repository-wide accuracy threshold. If a model is inaccurate, expand real
measurement coverage or change the deployment before treating its decisions as
calibrated. Direct-engine TTFT includes that measurement path and is not the same
as end-to-end client TTFT through Controller and both Gateways.

## 4. Install Controller profiles separately

Gateway model files are **not** automatically distributed to Controller.
`demand_aware` resolves `profile/controller/profile_<id>/...`, where `<id>` comes
from `machine` / `machine_id` / `hostname` if present, otherwise the final IP
segment. Normal Gateway registration does not expose a CLI machine-ID override.
The port and numeric Pool ID are not part of this key.

All four loopback Pools therefore use `profile_1`. Only when P-A/P-B are equivalent
in GPU/model/TP/runtime configuration and their validation results justify one
representative P profile, and likewise for D-A/D-B, place the representatives:

```bash
python3 - <<'PY'
import hashlib
import shutil
from pdschedule.paths import profile_root
root = profile_root()
for source, target in (
    (root / "deploy/p8081/artifacts/prefill_ttft_regressor.pkl",
     root / "controller/profile_1/TTFT_predictor/artifacts/prefill_ttft_regressor.pkl"),
    (root / "deploy/d8082/artifacts/decode_tpot_regressor.pkl",
     root / "controller/profile_1/TPOT_predictor/artifacts/decode_tpot_regressor.pkl"),
):
    assert source.is_file(), source
    if target.exists() and target.read_bytes() != source.read_bytes():
        raise SystemExit(f"Refusing to overwrite a different Controller model: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)
    print(f"{target}: sha256={hashlib.sha256(target.read_bytes()).hexdigest()}")
PY
```

This collision guard protects existing calibration. Review an intentional model
replacement separately if it stops. For heterogeneous Pools sharing an IP, the
current Controller key cannot distinguish different same-role calibrations.
Keep using its documented fallback estimates or use appropriately separated
deployment identities; do not overwrite one Pool's profile with another and
claim per-Pool calibration. Distinct addresses ending in the same segment can
also collide. See [Controller profile details](../profile/controller/README.md).

Loaded profiles and missing-profile lookups are cached. Restart Controller after
adding/replacing files; switching away from `demand_aware` and back does not
recreate its strategy or clear these caches.

## 5. Restart application services with calibrated SLO policies

Stop client traffic. Stop all four Instance Proxies with Ctrl+C, enter `quit` in
each Gateway CLI, then `quit` in Controller. Leave the four vLLMs running.
Restart Controller with the command in the multi-Pool guide (including the same
`PDSCHEDULE_PROFILE_DIR`), then start these Gateways in four separate terminals.
Finally restart all four Proxies using their original commands. A Controller or
Gateway restart loses its in-memory registrations; do not assume surviving
children will automatically rebuild all registration state.

### P-A

```bash
cd /workspace/llm-stack/PDSchedule
export CUDA_VISIBLE_DEVICES=""
export FLEXPD_GATEWAY_LOG_DIR=/workspace/llm-stack/PDSchedule/gateway_logs/p8081
python3 entrypoints/gatewayP.py \
  --host 127.0.0.1 --port 8081 --controller 127.0.0.1:5000 \
  --prefill 127.0.0.1:7001 --decode 127.0.0.1:8082 127.0.0.1:8084 \
  --prefill-scheduler-strategy slo_aware_prefill --ttft-warmup \
  --ttft-target 127.0.0.1:8001 \
  --ttft-model-id /workspace/llm-stack/models/deepseek-ai/DeepSeek-R1-Distill-Qwen-7B \
  --ttft-tokenizer-path /workspace/llm-stack/models/deepseek-ai/DeepSeek-R1-Distill-Qwen-7B \
  --ttft-model-file /workspace/llm-stack/PDSchedule/profile/deploy/p8081/artifacts/prefill_ttft_regressor.pkl \
  --connector-name nixl --connector-version 1 --connector-metadata '{}'
```

### P-B

```bash
cd /workspace/llm-stack/PDSchedule
export CUDA_VISIBLE_DEVICES=""
export FLEXPD_GATEWAY_LOG_DIR=/workspace/llm-stack/PDSchedule/gateway_logs/p8083
python3 entrypoints/gatewayP.py \
  --host 127.0.0.1 --port 8083 --controller 127.0.0.1:5000 \
  --prefill 127.0.0.1:7002 --decode 127.0.0.1:8082 127.0.0.1:8084 \
  --prefill-scheduler-strategy slo_aware_prefill --ttft-warmup \
  --ttft-target 127.0.0.1:8002 \
  --ttft-model-id /workspace/llm-stack/models/deepseek-ai/DeepSeek-R1-Distill-Qwen-7B \
  --ttft-tokenizer-path /workspace/llm-stack/models/deepseek-ai/DeepSeek-R1-Distill-Qwen-7B \
  --ttft-model-file /workspace/llm-stack/PDSchedule/profile/deploy/p8083/artifacts/prefill_ttft_regressor.pkl \
  --connector-name nixl --connector-version 1 --connector-metadata '{}'
```

### D-A

```bash
cd /workspace/llm-stack/PDSchedule
export CUDA_VISIBLE_DEVICES=""
export FLEXPD_GATEWAY_LOG_DIR=/workspace/llm-stack/PDSchedule/gateway_logs/d8082
python3 entrypoints/gatewayD.py \
  --host 127.0.0.1 --port 8082 --controller 127.0.0.1:5000 --decode 127.0.0.1:7003 \
  --decode-scheduler-strategy slo_aware_decode --tpot-warmup \
  --tpot-target 127.0.0.1:8003 \
  --tpot-model-id /workspace/llm-stack/models/deepseek-ai/DeepSeek-R1-Distill-Qwen-7B \
  --tpot-tokenizer-path /workspace/llm-stack/models/deepseek-ai/DeepSeek-R1-Distill-Qwen-7B \
  --tpot-model-file /workspace/llm-stack/PDSchedule/profile/deploy/d8082/artifacts/decode_tpot_regressor.pkl \
  --connector-name nixl --connector-version 1 --connector-metadata '{}'
```

### D-B

```bash
cd /workspace/llm-stack/PDSchedule
export CUDA_VISIBLE_DEVICES=""
export FLEXPD_GATEWAY_LOG_DIR=/workspace/llm-stack/PDSchedule/gateway_logs/d8084
python3 entrypoints/gatewayD.py \
  --host 127.0.0.1 --port 8084 --controller 127.0.0.1:5000 --decode 127.0.0.1:7004 \
  --decode-scheduler-strategy slo_aware_decode --tpot-warmup \
  --tpot-target 127.0.0.1:8004 \
  --tpot-model-id /workspace/llm-stack/models/deepseek-ai/DeepSeek-R1-Distill-Qwen-7B \
  --tpot-tokenizer-path /workspace/llm-stack/models/deepseek-ai/DeepSeek-R1-Distill-Qwen-7B \
  --tpot-model-file /workspace/llm-stack/PDSchedule/profile/deploy/d8084/artifacts/decode_tpot_regressor.pkl \
  --connector-name nixl --connector-version 1 --connector-metadata '{}'
```

`--ttft-warmup` / `--tpot-warmup` enables the **load-first background task**, not
unconditional retraining. Expect `[TTFT Predictor] Using persisted model: ...` in
both P Gateways and `[TPOT Predictor] Using persisted model: ...` in both D Gateways.
Wait for these exact file paths before sending evaluation traffic. If a file is
absent or unloadable, the task attempts live warmup on the explicit **vLLM** target
instead, using the implementation's default grid, not Section 2's process-local
grid. Stop and correct the file when following the pre-calibrated flow.

`--no-ttft-warmup` / `--no-tpot-warmup` skips both loading and background fitting.
Providing a model filename alone while disabling warmup does not load it.
There is no predictor-ready HTTP endpoint. `/connectors` cannot establish readiness.

## 6. Inspect policies, dispatch, and fallbacks

Repeat the multi-Pool registration checks. In Controller enter `show scheduler`;
expect `multi_gateway_scheduled`, `p=2,d=2,network=4`. In each P Gateway CLI:

```text
show scheduler
```

Expect `role=prefill, scheduler=slo_aware_prefill`. Each D Gateway should show
`role=decode, scheduler=slo_aware_decode`. To compare policies without changing
predictor readiness, enter these commands in the appropriate **Gateway** CLI:

```text
switch scheduler -m jsq
show scheduler
switch scheduler -m round_robin
switch scheduler -m slo_aware_prefill
```

```text
switch scheduler -m lowest_generation_throughput
show scheduler
switch scheduler -m round_robin
switch scheduler -m slo_aware_decode
```

The first block is P-only; the second is D-only. Each change affects that Gateway,
not all Pools. Prefill switches reschedule pending work; already dispatched work
continues. Start a fresh observation interval after switching and compare the
same traffic. CLI switches do not retrain/load predictors, clear Controller profile
caches, or update the startup default for the next process restart.

| Observation | Meaning |
| --- | --- |
| P schedule record: `strategy=slo_aware_prefill`, planned start/finish, selected local Instance | Predictor-assisted planning/dispatch. Look at outstanding requests and timing records as well as the destination. |
| D log: `[SLOAwareDecodeStrategy] Selected ... safe=True violations=0 ...` | No predicted TPOT violation in that evaluated candidate batch. Not a measured guarantee. |
| D `safe=False` / `no_safe_candidate_minimum_slo_violations` | No fully safe candidate; still dispatches, prioritizing fewer predicted violations and margin. Not rejection or admission control. |
| D `tpot_prediction_unavailable_fallback_minimum_generation_throughput` | Predictions unavailable; uses lowest reported generation throughput, not SLO-based placement. |
| Controller `selected_prefill_time_source: profile` / `selected_decode_time_source: profile` | Loaded Controller-local coefficients. `fallback` means throughput/capacity estimates, not Gateway model reuse. |

P prediction tries the registered in-process predictor, then the legacy optional
`http://localhost:9000/predict`, then `0.0002 * total_tokens` seconds. D uses a
registered TPOT predictor; unusable predictions cause the fallback above. Cold
predictor APIs can initialize from synthetic seed data, so finite outputs or
`_is_fitted` alone do not establish real calibration. The sample checks, held-out
validation, and exact persisted-model load logs are required together.

Model replacement is not hot reload. Drain traffic, recalibrate, validate, and
restart the affected Gateway and its Proxies to rebuild registrations and clear
prediction caches. Restart Controller and application children as in Section 5
when replacing its profiles. A new model/GPU/TP/runtime/batching configuration
requires new measurement; one Gateway has one predictor model for its local Pool.

## 7. Issue a small streaming workload and inspect timing

First use Controller `round_robin` to cover all Pools deterministically. Then
repeat with `demand_aware` and check its candidate costs and profile sources.
The following sends eight concurrent real chat requests, with different prompt
sizes/output budgets. It parses SSE events instead of treating TCP chunks as
tokens. Run it from Controller's loopback namespace:

```bash
python3 - <<'PY'
import concurrent.futures
import json
import time
import urllib.request

def run(index):
    payload = {
        "model": "/workspace/llm-stack/models/deepseek-ai/DeepSeek-R1-Distill-Qwen-7B",
        "messages": [{"role": "user", "content":
            ("Explain how prefill and decode cooperate. " * (8, 32, 64, 96)[index % 4])
            + "Give a concise summary."}],
        "max_tokens": (64, 128, 192, 256)[index % 4],
        "temperature": 0.7, "stream": True, "Enable_Affinity": False,
    }
    request = urllib.request.Request(
        "http://127.0.0.1:5000/v1/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST",
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    start = time.perf_counter()
    content_times = []
    done = False
    with opener.open(request, timeout=600) as response:
        assert response.status == 200, response.status
        for raw_line in response:
            line = raw_line.decode("utf-8").strip()
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                done = True
                break
            event = json.loads(data)
            assert not event.get("error"), event
            for choice in event.get("choices", []):
                assert choice.get("finish_reason") != "error", event
                delta = choice.get("delta", {})
                if delta.get("content") or delta.get("reasoning_content"):
                    content_times.append(time.perf_counter())
    assert done and content_times, (index, done, len(content_times))
    elapsed_ms = (time.perf_counter() - start) * 1000
    first_ms = (content_times[0] - start) * 1000
    return f"request {index + 1}: HTTP 200; first_content_ms={first_ms:.1f}; e2e_ms={elapsed_ms:.1f}; content_events={len(content_times)}"

with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
    for result in executor.map(run, range(8)):
        print(result)
PY
```

Expected: eight success lines with actual, varying timings. Exceptions stop the
check. Reasoning-capable models may emit reasoning before visible answer text.
Neither one SSE event nor one HTTP chunk is necessarily one generated token, so
this block does not report a token-accurate TPOT or assert SLO attainment.

Inspect Controller request IDs and `[Controller-SLO]` traces, plus the four
separate Gateway schedule/timing/resource directories from the multi-Pool guide.
Current Controller streaming traces time **non-empty transport chunks**, including
possible non-content events; their TPOT is an inter-chunk estimate. Non-stream
traces use full-response time as TTFT and divide E2E by completion tokens for TPOT.
These proxies are not interchangeable with token-level TTFT/TPOT from an instrumented
serving engine/client. Compare only metrics with the same timing boundary.

Finally repeat the actual NIXL transfer-metric check for both Decode engines.
Successful generation, calibrated predictors, selected policies, and compatibility
logs are separate checks; none alone proves KV transfer or an SLO improvement.

## 8. Optional: two Instances per Pool on eight GPUs

To exercise competing local placements, allocate **four more independent GPUs**
and add these engines/Proxies to the existing four Pools:

| Pool | Extra physical GPU | Extra vLLM | Side channel | Extra Proxy |
| --- | --- | --- | --- | --- |
| P-A / 8081 | 4 | 8005 | 5561 | 7005 |
| P-B / 8083 | 5 | 8006 | 5562 | 7006 |
| D-A / 8082 | 6 | 8007 | 5563 | 7007 |
| D-B / 8084 | 7 | 8008 | 5564 | 7008 |

Use the same model/layout settings and equivalent hardware within each Pool.
Validate the Pool's calibration on its added Instance too; a single Gateway model
is not an independent per-Instance calibrator. Do not share one engine between
multiple Proxies to imitate independent capacity.

Open four new engine terminals. In each terminal choose **one** corresponding
assignment block, then run the common full engine command below it:

```bash
# Extra P-A engine terminal
export CUDA_VISIBLE_DEVICES=4 ENGINE_PORT=8005 VLLM_NIXL_SIDE_CHANNEL_PORT=5561
```

```bash
# Extra P-B engine terminal
export CUDA_VISIBLE_DEVICES=5 ENGINE_PORT=8006 VLLM_NIXL_SIDE_CHANNEL_PORT=5562
```

```bash
# Extra D-A engine terminal
export CUDA_VISIBLE_DEVICES=6 ENGINE_PORT=8007 VLLM_NIXL_SIDE_CHANNEL_PORT=5563
```

```bash
# Extra D-B engine terminal
export CUDA_VISIBLE_DEVICES=7 ENGINE_PORT=8008 VLLM_NIXL_SIDE_CHANNEL_PORT=5564
```

```bash
cd /workspace/llm-stack/PDSchedule
export PYTHONHASHSEED=0 VLLM_USE_FLASHINFER_SAMPLER=0
export UCX_TLS=tcp,cuda_copy UCX_NET_DEVICES=all
export VLLM_NIXL_SIDE_CHANNEL_HOST=127.0.0.1
python3 -m vllm.entrypoints.openai.api_server \
  --model /workspace/llm-stack/models/deepseek-ai/DeepSeek-R1-Distill-Qwen-7B \
  --host 127.0.0.1 --port "${ENGINE_PORT:?Choose one engine assignment first}" \
  --tensor-parallel-size 1 --dtype half \
  --gpu-memory-utilization 0.8 --max-model-len 4096 --max-num-batched-tokens 4096 \
  --enforce-eager --attention-backend TRITON_ATTN \
  --no-enable-prefix-caching --disable-hybrid-kv-cache-manager --kv-cache-metrics \
  --kv-transfer-config '{"kv_connector":"NixlConnector","kv_role":"kv_both","kv_load_failure_policy":"fail","kv_connector_extra_config":{"backends":["UCX"]}}'
```

Check `/v1/models` on ports 8005–8008 as in the multi-Pool guide, then start each
extra Proxy in its own terminal:

```bash
cd /workspace/llm-stack/PDSchedule
unset CUDA_VISIBLE_DEVICES
python3 entrypoints/instanceP.py \
  --host 127.0.0.1 --port 7005 --vllm_port 8005 --gateway 127.0.0.1:8081 \
  --connector-name nixl --connector-version 1 \
  --connector-metadata '{"side_channel_host":"127.0.0.1","side_channel_port":5561,"tp_size":1}' \
  --gpu-indices 4
```

```bash
cd /workspace/llm-stack/PDSchedule
unset CUDA_VISIBLE_DEVICES
python3 entrypoints/instanceP.py \
  --host 127.0.0.1 --port 7006 --vllm_port 8006 --gateway 127.0.0.1:8083 \
  --connector-name nixl --connector-version 1 \
  --connector-metadata '{"side_channel_host":"127.0.0.1","side_channel_port":5562,"tp_size":1}' \
  --gpu-indices 5
```

```bash
cd /workspace/llm-stack/PDSchedule
unset CUDA_VISIBLE_DEVICES
python3 entrypoints/instanceD.py \
  --host 127.0.0.1 --port 7007 --vllm_port 8007 --gateway 127.0.0.1:8082 \
  --connector-name nixl --connector-version 1 \
  --connector-metadata '{"side_channel_host":"127.0.0.1","side_channel_port":5563,"tp_size":1}' \
  --gpu-indices 6
```

```bash
cd /workspace/llm-stack/PDSchedule
unset CUDA_VISIBLE_DEVICES
python3 entrypoints/instanceD.py \
  --host 127.0.0.1 --port 7008 --vllm_port 8008 --gateway 127.0.0.1:8084 \
  --connector-name nixl --connector-version 1 \
  --connector-metadata '{"side_channel_host":"127.0.0.1","side_channel_port":5564,"tp_size":1}' \
  --gpu-indices 7
```

Dynamic registration adds each Instance to its Pool's candidate list; no extra
Pool or Controller reconfiguration is needed. Startup member lists may also
contain both local addresses, but are not a substitute for these registrations.
Keep the same Connector descriptor throughout each Pool. The Controller still
reports **2P/2D**, not 4P/4D; there are now 21 services in total.

Repeat the concurrent workload and inspect `candidates` and `selected_instance`
in each Gateway schedule log. Expect two active local candidates per Pool;
selection need not alternate under SLO policies. Compare predicted safety/margins,
outstanding work, and observed timing under the same workload. Read NIXL metrics
on **all four Decode engines** (8003, 8004, 8007, 8008), and stop all eight Proxies
before Gateways when shutting down or restarting the application layer.

## 9. Troubleshooting SLO operation

| Symptom | Check / action | What establishes recovery |
| --- | --- | --- |
| SLO strategy selected but no persisted-model load log | Check the actual CLI: `--no-ttft-warmup` / `--no-tpot-warmup` also disables loading. A filename alone is insufficient. Use Section 5's explicit engine target, file, model ID, and tokenizer options. | Each relevant Gateway logs `Using persisted model` with its own expected file path before evaluation traffic. |
| Unexpected long live warmup at Gateway startup | The load-first task could not load its file and began real requests using the implementation's default grid. Stop evaluation traffic, inspect missing/unreadable/incompatible-model errors, and regenerate the local file using Section 2 rather than mistaking background traffic for the test workload. | Sample/persistence checks and held-out validation pass, then restart loads the intended file instead of fitting again. |
| Calibration returns but has no valid model or incomplete samples | Warmup functions can report failures and return without a usable new model. Check real vLLM readiness, target port, model/tokenizer identity, context budget, sample logs, and the explicit assertions in Section 2. Do not substitute synthetic seed predictions. | Expected real sample coverage and saved-state reload checks pass; validation errors are acceptable for the chosen margin. |
| D Gateway says TPOT prediction unavailable | Inspect predictor load/readiness and exceptions. The strategy falls back to lowest generation throughput; this can keep dispatching without TPOT-aware evaluation. | A loaded predictor produces finite evaluated batches and selection records show SLO-aware decisions, not the fallback reason. |
| `safe=False` or observed SLO violations | No predicted-safe candidate is not a rejection condition. Compare active-batch size, output/context range, load, model fit errors, and observed timings; lower real offered load or recalibrate as appropriate. | Measured results meet the intended objective under a stated workload; a policy name alone is not evidence. |
| Controller/Gateway still uses an old model after replacing files | No hot reload. Controller caches both profiles and missing keys; switching policies reuses them. Drain traffic and follow the documented restart order, including child registrations. | Correct file/hash provenance and new load/profile-source logs agree after restart. |
| Different same-IP Pools appear to share a calibration | Ports do not distinguish Controller `profile_<id>` keys. Only use a representative profile for equivalent validated Pools. Do not repeatedly overwrite `profile_1` with different hardware models. | Profile identity matches the actual deployment; otherwise keep the documented global fallback instead of claiming per-Pool calibration. |
| Client TTFT/TPOT disagrees with Controller traces | Compare the same request and timing boundary. Controller streaming uses non-empty chunks; non-stream uses full-response time. First content/SSE events are not guaranteed individual tokens, and P deadlines start at P Gateway arrival. | Measurements are labeled with the same start/end events and units; no cross-boundary comparison is presented as a prediction error or guarantee. |

Use [multi-Pool troubleshooting](multi_pool.md#8-troubleshooting-multi-pool-operation)
for registration/state problems and [deployment troubleshooting](quickstart.md#13-常见问题)
for NIXL transfer errors. SLO checks do not replace real KV-transfer evidence.
