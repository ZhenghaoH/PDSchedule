# Runtime compatibility

The `v1` filename follows the documentation structure. PDSchedule does not provide a CacheRoute-style runtime-profile switch or imply a new versioned runtime API.

## Startup interfaces

Source entrypoints and installed console commands accept the same service options. `--connector-name` selects the registered `nixl` or `lmcache` Adapter; descriptor `--connector-version` is an application compatibility string, not a vLLM/NIXL/LMCache release. See [configuration](developer-handbook/configuration-and-interfaces.md) and [installation](installation.md).

## Deployment combinations

| Combination | Current boundary | Evidence required |
| --- | --- | --- |
| vLLM `0.23.1rc1.dev256+gcec2ec117.cu128` + Torch `2.11.0+cu128` + NIXL `1.3.0` | Target development stack; host build and data plane not verified here | Readiness, compatible descriptors and actual transfer counters; see [quick start](quickstart_v1.md). |
| vLLM + LMCache shared backend | Adapter checks identity; serving integration and version compatibility must be prepared independently | Real backend write/load/hit and correct output; see [LMCache](integrations/lmcache.md). |
| Other CUDA, vLLM, NIXL, LMCache, multi-host or TP combinations | Depend on actual libraries, devices, model layout and network | Validate that exact stack; no blanket compatibility claim. |

## Cache identity compatibility

NIXL requires matching Connector name/version and per-Instance side-channel metadata. LMCache requires matching name/version and `backend`, `namespace`, `config_fingerprint`. These are application compatibility checks, not proof that two engines share model, tokenizer, KV layout or an accessible store. Match all serving-side requirements separately.

## Validation boundary

CPU Connector tests check logic without a GPU or external service. The real NIXL guide defines transfer evidence; a successful registration or import is insufficient. LMCache reuse must be verified using its engine/backend metrics and logs. This document does not assert an unperformed deployment run.

## Metrics contract

[Resource state](resource_state_metrics.md) describes units, scope, zero fallbacks and estimates. Timing is Gateway-observed and not a universal network-only metric.

## Future compatibility work

Evaluate new serving stacks and TP/multi-host layouts against their actual releases and document tested results before advertising support. No separate profile CLI is currently provided.
