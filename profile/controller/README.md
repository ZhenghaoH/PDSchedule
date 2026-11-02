# Controller local prediction models

The repository provides predictor and scheduling code, not machine-specific
fitted models. `demand_aware` can use rough capacity/throughput estimates when
models are missing; that preserves scheduling but is not hardware calibration.

Source checkout defaults to the repository `profile/`; wheel installation defaults
to `profile/` in the service working directory. Set `PDSCHEDULE_PROFILE_DIR`
before startup to a writable root. Controller uses its `controller/` subdirectory,
not site-packages. Restart after changing the path.

## Generate and place local models

Use [TTFT](../TTFT_predictor/README.md) or [TPOT](../TPOT_predictor/README.md)
tools to measure and fit on the target model, GPU, TP/PP and runtime, then place
trusted local files. The implementation derives an identifier from Pool machine
identity when present or the address suffix, and loads:

```text
profile/controller/profile_<id>/TTFT_predictor/artifacts/prefill_ttft_regressor.pkl
profile/controller/profile_<id>/TPOT_predictor/artifacts/decode_tpot_regressor.pkl
```

For example `10.0.0.7` defaults to `profile_7`; a matching suffix does not make
models transferable between machines. Gateway artifacts are not copied to Controller.
Port and numeric Pool ID do not distinguish these paths, so same-IP Pools of the
same role or addresses with matching suffixes can collide. The normal Gateway CLI
does not provide a separate machine-ID flag. For a loopback multi-Pool example,
use representative `profile_1` only when hardware, model and settings are equivalent
and validated. See [SLO operation](../../doc/slo.md).

Controller reads `coeffs`: TTFT uses `tokens` and `d`, TPOT uses `d1`–`d4`.
Save state through the regressors rather than fabricating production coefficients.

## Missing files, loading and updates

- Missing or failed files use the existing fallback; no model download is required.
- The strategy caches loaded and missing results. Rebuild the strategy or restart
  Controller after changing files. Switching away and back in the CLI reuses the
  same strategy instance and does not clear those caches.
- Load trusted pickle files only. Generated artifacts are ignored by Git.
- Operators retain measurement data, validation evidence, hardware/model versions and hashes.
