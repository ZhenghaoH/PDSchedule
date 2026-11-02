# Profiling tools

## Source-only predictor distribution

The repository ships TTFT/TPOT measurement, fitting, prediction, and local
save/load code. It does not distribute fitted models from experiment machines.
Generated artifacts and pickle/joblib files under profile are ignored by Git.

Installed wheels expose the two predictor packages as
`pdschedule.profiling.TTFT_predictor` and `pdschedule.profiling.TPOT_predictor`,
without installing a top-level `profile` package that shadows Python's standard
library. Root offline profiler scripts remain source-checkout tools. See the
[installation guide](../docs/installation.md) for writable profile paths and
installed entrypoints; fitted models are never written into site-packages by default.

- [TTFT predictor](TTFT_predictor/README.md): fit and use a local prefill predictor.
- [TPOT predictor](TPOT_predictor/README.md): fit and use local decode predictors.
- [Controller profiles](controller/README.md): optional local calibration files
  used by demand_aware, including the behavior when files are missing.
- [SLO operation guide](../docs/slo.md): real NIXL engine calibration, held-out
  validation commands, per-Gateway model loading, Controller placement, and
  multi-Pool SLO policy checks using explicit deployment paths.

Cold-start estimates and synthetic initialization samples are not calibrated
hardware measurements. Refit and validate in the actual deployment environment.
Do not load untrusted pickle files. Locally generated models are not release
assets and should not be force-added to Git.

Predictor modules currently depend on NumPy, aiohttp, scikit-learn, and
transformers, even for a cold-start import. HTTP services additionally use
FastAPI, Pydantic, and Uvicorn. Prepare compatible application dependencies in
the serving environment before running the examples; no model/tokenizer is
downloaded by the numerical cold-start prediction itself.

## DistServe `L_m` offline profiler

`profile_distserve_lm.py` finds the shortest prompt length that saturates a
Prefill GPU for a specific model, GPU set, parallel configuration, and vLLM
configuration. It combines the Prefill throughput plateau with NVML GPU
compute utilization; it does not modify gateway configuration.

### Prerequisites

- Run the profiler on the same GPU host as the target Prefill vLLM process.
- Keep that Prefill instance free of other traffic during profiling.
- Install `requirements.txt`; GPU monitoring uses the official
  `nvidia-ml-py` package (`import pynvml`).
- Disable prefix caching for the profiling run. Every prompt is unique, but
  disabling the cache removes implementation-specific cache effects.
- Pass the NVML-visible indices used by the Prefill process. When tensor or
  pipeline parallelism uses multiple GPUs, list all of them.

GPU numbering may be remapped by a container or device visibility settings.
Use `nvidia-smi` in the profiler's runtime environment and verify the UUIDs
recorded in the JSON output against the GPUs assigned to vLLM.

### Example

```bash
python profile/profile_distserve_lm.py \
  --host 127.0.0.1:8001 \
  --gpu-indices 0,1 \
  --prompt-lengths 128,256,512,1024,2048,4096,8192 \
  --warmup-repeats 2 \
  --measure-repeats 8 \
  --gpu-sample-interval-ms 20 \
  --gpu-util-threshold 90
```

The script turns `--host IP:PORT` into `http://<IP:PORT>/v1`; do not include a
protocol, URL path, or endpoint. Use `[IPv6]:port` for IPv6. The default served
model and tokenizer are both:

```text
/workspace/llm-stack/models/deepseek-ai/DeepSeek-R1-Distill-Qwen-7B
```

Override `--model` when vLLM exposes a different served-model name, and
override `--tokenizer` when the tokenizer is stored elsewhere. The tokenizer
is used to construct and verify exact-token prompts.

### Selection rule

For every prompt length the tool sends isolated, serial requests with
`max_tokens=1` and measures TTFT. Prefill throughput is
`prompt_tokens / TTFT_seconds`. During each measured request, a background
thread samples every selected GPU through NVML at a default 20 ms interval.

The default `L_m` is the first of two consecutive prompt lengths for which:

- median Prefill throughput reaches 95% of the plateau estimate;
- the P95 GPU compute utilization of every selected GPU reaches 90%;
- enough requests and GPU samples succeeded; and
- throughput CV does not exceed 10%.

The plateau is the median throughput of the three longest stable points. Those
points must themselves be within the configured saturation ratio; otherwise
the scan is still rising and the tool refuses to guess. All thresholds and
scan lengths can be overridden from the command line.

### Output

The output directory defaults to `profile/artifacts/distserve_lm` and contains:

- timestamped JSON with raw request and GPU samples, device UUIDs, parameters,
  summaries, and the recommendation;
- timestamped CSV with one row per prompt length and GPU;
- `latest.json` and `latest.csv` copies for deployment tooling.

On success the command prints:

```text
recommended_lm_tokens=<value>
recommended_vllm_max_num_batched_tokens=<value>
```

The second value is a vLLM instance tuning recommendation. The P gateway JSQ
strategy does not accept a gateway batch-token parameter.

Exit code `2` means the measurements completed but no reliable `L_m` could be
identified. Exit code `1` means setup or execution failed. Unsupported optional
NVML readings such as power or clock are recorded as null; missing GPU compute
utilization is a hard measurement failure.

The result is not portable across model, GPU type, TP/PP degree, vLLM batching
settings, or software configuration. Re-run profiling whenever those change.
