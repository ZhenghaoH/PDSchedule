# Compute-Network Resource State and Perception Metrics

## Perception levels and data flow

vLLM exposes counters and request/KV gauges; Instance Proxy samples them with GPU/NVML and host-interface data and posts to its Gateway. The Gateway stores Instance records, maintains local scheduling reservations, and reports aggregate Pool load and a separate network estimate to Controller. Controller creates a versioned snapshot of active Pools, links and compatible pairs. See [Instance reporting](../coord/instance/up_store.py), [Gateway reporting](../coord/gateway/up_store.py) and [resource snapshots](../scheduler/controller/resource_state.py). This describes current paths, not a new collection or eviction mechanism.

## Compute resources

GPU device utilization and memory come from NVML. `gpu_devices` contains per-device indices, byte counters, percentages and `metric_source`; aggregate utilization is the arithmetic mean across reported devices. vLLM supplies KV usage and request counts. A windowed token-counter delta produces prompt and generation throughput; a short/empty window returns zero. These samples describe different scopes and are not an automatically calibrated capacity.

## Network resources

Host interface counters provide rates and reported link speed; available bandwidth is an estimate derived from speed and traffic. A Gateway reports its own NIC/PCIe estimates for P/D links. NIC RTT is configured as 10 ms in the current reporter and PCIe RTT defaults to 0.1 ms; neither is a per-request measured network RTT. The Controller uses PCIe values for same-IP Pools. A zero/missing available NIC bandwidth falls back to 1000 Mbps in the Gateway reporter. These estimates do not prove NIXL transfer speed.

## Instance and Pool state

An Instance is registered by role, label and Connector; a successful metrics update marks it online. Gateway aggregates P prompt throughput and D running plus waiting requests for Pool reporting. Controller normalizes P throughput by `max_prompt_throughput` and D count by `max_decode_req_num`. The Pool hardware labels and capacity parameters are startup configuration, not automatic sums of hardware. Controller active-Pool and network snapshot filters use their existing timeout logic; no new recovery or eviction behavior is implied.

## Request and scheduling state

Gateway reservation accounting and predicted availability are local scheduling values, not direct GPU counters. Controller `demand_aware` consumes resource/link and request estimates; P JSQ selects by unfinished prompt-token load, and D `lowest_generation_throughput` uses generation throughput. SLO-aware decisions depend on the corresponding predictor and fallback logic. Candidate logs may contain additional values that the selected strategy does not use.

## Metric semantics and source

### Granularity

| Level | Scope and update | Caution |
| --- | --- | --- |
| GPU | Each reported physical NVML index, sampled during Instance update | Device metrics are not per-request allocations. |
| Instance | vLLM process/Proxy and selected device average; update/heartbeat | `report_label` identifies the Proxy, not the GPU. |
| Pool | Gateway aggregate and registered capacity | A Pool may contain multiple Instances; P and D use different load units. |
| Link / Controller snapshot | Gateway estimates, then active P/D link records and versioned snapshot | Not independently measured bandwidth between each engine pair. |
| Request | Gateway timing/selection and local reservations | Not a cross-host synchronized trace or guaranteed network-only duration. |

### Null vs zero

| Situation | Actual representation | Interpretation |
| --- | --- | --- |
| Missing vLLM fetch | request counts and KV usage may become `0`; throughput may be zero | Zero can mean unavailable, not idle. A missing individual required gauge in an otherwise populated response can abort that update. |
| NVML failure | zero-valued device metrics with `metric_source` beginning `nvml_error:` | Inspect provenance; zero is not a valid utilization assertion. |
| No interface sample | `{}` | Unavailable; not a zero-speed link. Gateway reporting has its own fallback. |
| Missing timing endpoints | `null` timing values | Unknown duration, not zero; negative measured durations are clamped. |
| Too few throughput samples | `0.0` | Not evidence of zero model capacity. |

### Field audit

| Field | Level / unit | Source, update and classification | Scheduling use / boundary |
| --- | --- | --- | --- |
| `gpu_devices[].gpu_util_percent`, `gpu_memory_util_percent`, `gpu_memory_used_bytes`, `gpu_memory_total_bytes`, `metric_source` | Device / %, bytes | NVML on Instance update; measured or error fallback | Resource observation; not generally Controller strategy inputs. |
| `gpu_util_percent`, `gpu_memory_util_percent` | Instance / % | Arithmetic device means on Instance update; derived | Gateway state/log observation; not Pool capacity. |
| `kv_cache_usage_percent` | Instance / % | vLLM `vllm:kv_cache_usage_perc` or `vllm:gpu_cache_usage_perc`, ratio scaled when ≤1; update | Appears in D candidate snapshot; presence in a log does not mean every D strategy uses it. |
| `running_reqs`, `waiting_reqs` | Instance / requests | vLLM gauges sampled per update; fallback can be zero | D Pool total includes both for active Instances; also local state and candidate observations. |
| `prompt_throughput`, `generate_throughput` | Instance / tokens/s | Windowed deltas of vLLM token counters; sampled monitor and update | P throughput contributes to Pool load; D lowest-throughput policy uses generation rate. |
| `max_prompt_throughput`, `max_decode_req_num`, `flops`, `mem_bandwidth` | Pool / tokens/s, requests, nominal capacity | Gateway startup configuration registered at Controller | Load normalization and demand-aware capacity; not hardware auto-detection. |
| `network_metric` (`tx_mbps`, `rx_mbps`, `available_mbps`, etc.) | Instance host interface / Mbps | psutil counter/rate sample cached by Instance; observation | Gateway resource logs; not the Controller's independently reported Gateway link. |
| `bandwidth`, `rtt`, `pcie_bandwidth`, `pcie_rtt` | Gateway/Controller link / Mbps, ms | Gateway host/PCIe estimates and configured RTT, periodically reported | Controller network link input for multi-Pool policies; not direct per-request measurements. |
| `predicted_available_time`, `real_available_time` | Instance / epoch seconds | Gateway local predicted and completion state; updated by scheduling | P placement timing, not an externally measured GPU ready timestamp. |
| `unfinished_prompt_tokens` / local reservations | Instance / tokens | Gateway accounting on selection, release and heartbeat | JSQ and relevant local scheduling; may differ from vLLM running count. |
| `prefill_ms`, `decode_stream_ms`, `observed_network_transfer_ms`, `gateway_e2e_ms` | Request / ms | Gateway timing timestamps; derived at request completion | Diagnostics, not raw network transfer alone; see [timing](architecture/observability-v1.md#timing-and-correlation). |

### Provenance

Per-GPU `metric_source` distinguishes NVML and error samples. Network dictionary and log timestamps identify the reported context but do not establish synchronized remote measurements. The existing interfaces do not provide a universal provenance/status field for every zero or aggregate. Verify the exporter, device, collection window, sample freshness and serving logs before using a number as evidence.

## Scheduling-use boundary

Only the fields read by a specific strategy affect that decision. The Controller filters Connector-compatible pairs before selection; the single-pair route bypasses policy computation. Consult [Controller strategy source](../scheduler/controller/strategies/demand_aware.py) and [Gateway strategy guides](../scheduler/gateway/README.md) before inferring a metric's effect. CPU tests validate computations, not measurement accuracy on a GPU.
