# Configuration and interface index

This is an index of the current application's deployment-facing configuration,
CLI, and HTTP surfaces, not another startup tutorial. Source definitions remain
authoritative. Internal optimizer weights and function-local tuning constants
are deliberately omitted. Follow [single-Pool deployment](../quickstart_v1.md),
[multi-Pool deployment](../multi_pool.md), and [SLO operation](../slo.md) for commands.

## 1. Configuration resolution

- Service CLI arguments override their `argparse` defaults. Address defaults
  come from [ADDRESS_CONFIG.py](../../config/ADDRESS_CONFIG.py) and the role's config
  module; the environment is read when those modules are imported.
- This is **not** a universal CLI > environment > config rule. Malformed imported
  environment values can fail before CLI overrides are parsed. Changing a shell
  variable after a service starts does not reconfigure that process.
- Controller [config](../../config/controller.py) assigns
  `SCHEDULER_MODEL_PATH`, `SCHEDULER_TOKENIZER_MAP`, `HF_HUB_OFFLINE`, and
  `TRANSFORMERS_OFFLINE` on import. Set `PDSCHEDULE_MODEL_PATH`,
  `PDSCHEDULE_MODEL_NAME`, `PDSCHEDULE_TOKENIZER_PATH`, or
  `PDSCHEDULE_TOKENIZER_MAP` before startup to provide local overrides. The
  Controller still forces offline Hugging Face access. A local absolute model
  path in a request is resolved directly by [TokenizerRegistry](../../utils/tokenizer_registry.py).
- No application `.env` loader or runtime configuration in `pyproject.toml` is
  provided. The latter describes package installation. NIXL/vLLM options and
  LMCache YAML belong to their serving processes, not this argument parser.
- The `FLEXPD_*` environment names below are current interfaces. There are no
  automatic `PDSCHEDULE_*` aliases except the explicit
  `PDSCHEDULE_PROFILE_DIR` option.

## 2. Environment variables and configured state

### Addresses and membership

Sources: [shared addresses](../../config/ADDRESS_CONFIG.py),
[P Instance config](../../config/instanceP.py), [D Instance config](../../config/instanceD.py).
Port environment values are integers: unset or empty uses the listed default;
other non-integer strings raise during import. No port-range validation is added
by the shared helper. Host strings are not reachability checks.

| Name | Owner / CLI default affected | Default | Resolution / behavior |
| --- | --- | --- | --- |
| `FLEXPD_CONTROLLER_HOST` | Controller `--host`; Gateways `--controller` host | Empty string | Controller requires a non-empty host. Gateways derive `host:port` only when this is set. |
| `FLEXPD_CONTROLLER_PORT` | Controller `--port`; derived Gateway Controller address | `5000` | `--controller` can override the whole derived address. |
| `FLEXPD_GATEWAY_P_HOST` / `FLEXPD_GATEWAY_D_HOST` | Respective Gateway `--host`; derived Instance parent | Empty string | Each Gateway requires a non-empty host. |
| `FLEXPD_GATEWAY_P_PORT` / `FLEXPD_GATEWAY_D_PORT` | Respective Gateway `--port`; derived Instance parent | `8081` / `8082` | A port is not a Pool ID. |
| `FLEXPD_PREFILL_INSTANCES` | P Gateway `--prefill`; initial TTFT target default | Empty list | Comma-separated `host:port`, whitespace/empty items removed. Entries still require dynamic registration. |
| `FLEXPD_DECODE_INSTANCES` | D Gateway `--decode`; initial TPOT target default | Empty list | Same parsing. Does not populate the P Gateway's Decode-Gateway list. |
| `FLEXPD_INSTANCE_HOST` | Both Instance roles `--host` | Empty string | Required; also used to reach the local vLLM host and construct `report_label`. |
| `FLEXPD_GATEWAY_ADDR` | Instance `--gateway` | Role-specific derived Gateway address, otherwise empty | An explicitly set empty value overrides that fallback and fails the required-option check. |
| `FLEXPD_GPU_INDICES` | Instance `--gpu-indices` | `0` | Comma-separated unique non-negative integers. Invalid/empty defaults can fail even with an explicit CLI override. |

P/D Instance `--port` and `--vllm_port` have **no configured default** (`None`);
pass both explicitly. In normal deployment use reachable concrete addresses:
Gateway host is advertised to Controller, and Instance host is used for both
advertising and local vLLM access. `0.0.0.0` is not an advertised remote target.

`FLEXPD_METADATA_HOST` / `FLEXPD_METADATA_PORT` (empty / `8080`) and
`FLEXPD_REDIS_HOST` / `FLEXPD_REDIS_PORT` (empty / `6378`) remain declarations in
ADDRESS_CONFIG, but no current runtime consumer uses the resulting constants.
They do not start a metadata/Redis service or rewrite LMCache YAML. Their port
values are still parsed when ADDRESS_CONFIG is imported.

### Management and liveness

These values are read when a service starts. The matching CLI option overrides
the environment default; changing either after startup does not reconfigure the
process.

| Name | Owner / default | Resolution / behavior |
| --- | --- | --- |
| `FLEXPD_GATEWAY_HEARTBEAT_INTERVAL_SEC` | Both Gateways: `10` seconds | Normal Pool load and P network report cadence. Actual request timing includes work and HTTP duration. |
| `FLEXPD_INSTANCE_METRIC_INTERVAL_SEC` | P/D Instance: `0.02` seconds | Sleep after each metrics collection and report. This is not a guaranteed 20 ms arrival period. |
| `FLEXPD_INSTANCE_HEARTBEAT_TTL_SEC` | Both Gateways: `30` seconds | Monotonic age limit; age at or above the TTL removes an Instance from local scheduling. |
| `FLEXPD_INSTANCE_EXPIRY_SCAN_INTERVAL_SEC` | Both Gateways: `1` second | Background state-transition scan; selection and dispatch also check freshness directly. |
| `FLEXPD_MANAGEMENT_CONNECT_TIMEOUT_SEC` | Gateway and Instance admin requests: `2` seconds | Requests uses a separate connect timeout. |
| `FLEXPD_MANAGEMENT_READ_TIMEOUT_SEC` | Gateway and Instance admin requests: `3` seconds | Read timeout is separate; the two values do not promise a strict five-second total. |
| `FLEXPD_REGISTRATION_RETRY_MAX_INTERVAL_SEC` | Gateway and Instance registration recovery: `10` seconds | Retries back off from 1, 2, 4, 8 seconds and then use this ceiling. |

### Profiles, capacity metadata, forwarding, and logs

| Name / setting | Owner and default | Effect / invalid or fallback behavior | Source |
| --- | --- | --- | --- |
| `PDSCHEDULE_PROFILE_DIR` | Profile root: source checkout `profile/`, installed application working-directory `profile/` | Non-empty trimmed path is expanded/resolved; a relative override is relative to process CWD. Models are not supplied. Set before startup; Gateway explicit model-file flags select individual files. | [paths.py](../../pdschedule/paths.py), [profile guide](../../profile/README.md) |
| `PDSCHEDULE_MODEL_PATH` | Controller default remains `/workspace/llm-stack/models/deepseek-ai/DeepSeek-R1-Distill-Qwen-1.5B` | Optional local path used for startup tokenizer warmup. It does not point to or download model weights. | [Controller config](../../config/controller.py) |
| `PDSCHEDULE_MODEL_NAME` | `DeepSeek-R1-Distill-Qwen-1.5B` | Optional key used with `PDSCHEDULE_TOKENIZER_PATH`. | Same source |
| `PDSCHEDULE_TOKENIZER_PATH` | Unset | Optional local tokenizer directory mapped to `PDSCHEDULE_MODEL_NAME`. Loading remains offline. | Same source, [tokenizer](../../utils/tokenizer_registry.py) |
| `PDSCHEDULE_TOKENIZER_MAP` | Existing `llama3-70b` Linux mapping | Optional JSON object merged with the default tokenizer map; use explicit local directory paths. | Same sources |
| `PDSCHEDULE_CONTROLLER_LOG_DIR` | Existing source/runtime-root `global_schedule.log` paths | Optional absolute directory for both Controller SLO and demand-aware schedule logs. | [Controller proxy](../../core/handlers/controller/proxy.py), [demand-aware](../../scheduler/controller/strategies/demand_aware.py) |
| `PDSCHEDULE_CLIENT_HOST` / `PDSCHEDULE_CLIENT_PORT` | `127.0.0.1` / `5050` | Browser Client bind settings; CLI `--host` and `--port` take precedence. | [Client](../../examples/clientUI/client.py) |
| `HF_HUB_OFFLINE` / `TRANSFORMERS_OFFLINE` | Controller assigns `1` / `1` | These assignments affect that process, not other terminals. Prepare model/tokenizer files locally. | [Controller config](../../config/controller.py) |
| `MY_GPU_TYPE` | Gateway nominal capacity: `NVIDIA RTX5090` | String label, not GPU autodetection. | [Instance state](../../store/gateway/instance.py), [Gateway store](../../store/gateway/manager.py) |
| `MY_FLOPS` | Gateway nominal P capacity: `17.9` | Parsed float; invalid text raises. Relative capacity input, not measured inference throughput. | Same sources |
| `MY_MEM_BANDWIDTH` | Gateway nominal D capacity: `800.0` | Parsed float; invalid text raises. Not an inter-Gateway network bandwidth measurement. | Same sources |
| `MY_MAX_PROMPT_THROUGHPUT` | Gateway P reference throughput: `37829.0` tokens/s | Parsed float; used to normalize reported prompt throughput. No automatic profiling or Pool-size aggregation. | Same sources, [Pool updates](../../core/handlers/controller/update.py) |
| `MY_MAX_DECODE_REQ_NUM` | Gateway D request capacity: `128` | Parsed integer; invalid text raises. Pool reporting uses at least 1. | [Gateway store](../../store/gateway/manager.py) |
| `OPENAI_API_KEY` | Forwarding helper: unset | Default downstream `Authorization: Bearer <value>`; unset currently produces `Bearer None`. Explicit extra headers override it where the caller supplies them. This is not application ingress authentication and does not guarantee client-header propagation through every hop. | [forward.py](../../proxy/forward.py) |
| `FLEXPD_GATEWAY_LOG_DIR` | Source root `gateway_logs/` | Custom relative path resolves against the module's project root, not CWD. Absolute per-Gateway paths are recommended. Directory/write failures log warnings. | [observability](../../utils/gateway_observability.py) |
| `FLEXPD_RESOURCE_LOG_INTERVAL_SEC` | Resource log interval: `1` second | Parsed float, clamped to ≥0; malformed text raises. Controls log emission, not heartbeat sampling. | Same source |
| `FLEXPD_RESOURCE_ZERO_PAUSE_SEC` | Idle resource-log pause: `30` seconds | Parsed float, clamped to ≥0; malformed text raises. Not a liveness timeout. | Same source |
| `FLEXPD_INSTANCE_NETWORK_SAMPLE_SEC` | Instance cached network sample: `1` second | Parsed float, clamped to ≥0.1; malformed text is caught by the enclosing metric-update error handler. Not the whole Instance reporting interval. | [Instance reporting](../../coord/instance/up_store.py) |

Gateway capacity defaults are read before actual Instance resource reports.
Later heartbeats change load, not the registered hardware-capacity fields. Use
deployment-specific values before startup and re-register after changing them;
do not interpret nominal defaults as measured heterogeneous-Pool rankings.

Demand-aware global logs use `runtime_root()/global_schedule.log`. Controller SLO
traces independently use a path relative to `core/handlers/controller/proxy.py`;
they coincide in a source checkout but need not coincide after wheel installation.
Gateway's default log root is also module-relative after installation.
`PDSCHEDULE_PROFILE_DIR` changes neither log root. See
[installation path notes](../installation.md#namespaces-and-resources).

### Network-estimate inputs

These affect [PCIe metric collection](../../utils/pcie_metric.py), not NIXL's actual
transport configuration. Invalid integer/float text falls back to the listed
value; sample count is clamped to ≥1 and sample interval to ≥0.

| Name | Default | Meaning |
| --- | --- | --- |
| `MY_PCIE_GPU_INDEX` | `0` | NVML physical device used for the Gateway's link estimate. |
| `MY_PCIE_RTT` | `0.1` ms | Configured RTT estimate, not measured TCP RTT. |
| `MY_PCIE_SAMPLE_COUNT` | `1` | Samples per collection. |
| `MY_PCIE_SAMPLE_INTERVAL` | `0.0` seconds | Delay between those samples. |
| `MY_PCIE_BANDWIDTH_FLOOR` | `0.0` Mbps | Used to raise fallback capacity when NVML is unavailable; not a floor applied to every successful NVML sample. |

NVML/default estimates use Mbps. Same-IP P/D Pools are treated as same-machine
links. Link records are cost estimates, not proof of measured per-pair NIXL TCP
bandwidth. See [multi-Pool resource semantics](../multi_pool.md#5-inspect-resources-and-switch-the-global-policy).

## 3. Service CLI

The [installed console commands](../installation.md#installed-service-commands)
accept the same arguments as the source files linked below. `--help` shows the
actual parser. No command here creates models, Redis, or vLLM processes for you.

| Entry | Option | Default / constraint |
| --- | --- | --- |
| [Controller](../../entrypoints/controller.py) | `--host`, `--port` | Shared address defaults above. Host required; port default 5000. Interactive CLI required; no `--no-cli`, model-path, or scheduler-policy startup flag. |
| [P Gateway](../../entrypoints/gatewayP.py) | `--host`, `--port`, `--controller` | P defaults above; host and Controller address required. Controller value is `host:port`, not an HTTP URL. |
| [D Gateway](../../entrypoints/gatewayD.py) | `--host`, `--port`, `--controller` | D defaults above; same requirement. |
| P Gateway | `--prefill` / `-p` | One or more local P Proxy addresses when supplied; defaults to the environment member list. |
| P Gateway | `--decode` / `-d` | One or more D **Gateway** addresses when supplied; default `[]`, not `FLEXPD_DECODE_INSTANCES`. Normal Controller PD forwarding uses the selected Task's D Gateway; the diagnostic analysis handler uses the first configured Decode target. |
| D Gateway | `--decode` / `-d` | One or more local D **Proxy** addresses when supplied; defaults to its environment member list. |
| P Gateway | `--prefill-scheduler-strategy` | `slo_aware_prefill`; choices `slo_aware_prefill`, `jsq`, `round_robin`. |
| D Gateway | `--decode-scheduler-strategy` | `slo_aware_decode`; choices `round_robin`, `lowest_generation_throughput`, `slo_aware_decode`. |
| Both Gateways | `--no-cli` | False; supplied flag disables their interactive CLI. No remote HTTP policy-switch endpoint. |
| Both Gateways | `--gateway-heartbeat-interval-sec`, `--instance-heartbeat-ttl-sec`, `--instance-expiry-scan-interval-sec` | Defaults `10`, `30`, `1` seconds; all values must be finite and positive. |
| Both Gateways | `--management-connect-timeout-sec`, `--management-read-timeout-sec`, `--registration-retry-max-interval-sec` | Defaults `2`, `3`, `10` seconds; applies to management requests and registration recovery. |
| [P Instance](../../entrypoints/instanceP.py), [D Instance](../../entrypoints/instanceD.py) | `--host`, `--port`, `--vllm_port`, `--gateway` | Host/parent defaults above; ports default `None`. Main rejects missing/empty/zero required options. The vLLM flag uses an **underscore**. |
| Both Instances | `--metric-interval-sec` | `0.02` seconds default sleep after sampling and reporting. |
| Both Instances | `--management-connect-timeout-sec`, `--management-read-timeout-sec`, `--registration-retry-max-interval-sec` | Defaults `2`, `3`, `10` seconds; all values must be finite and positive. |
| Both Instances | `--gpu-indices` | Parsed environment default `[0]`; comma-separated unique non-negative NVML indices. Does not allocate the engine's GPUs. |
| Both Gateways and Instances | `--connector-name` | `nixl`; choices `nixl`, `lmcache`. |
| Both Gateways and Instances | `--connector-version` | String `1`; string equality is required for compatibility. This is descriptor compatibility metadata, not the installed vLLM/NIXL/LMCache package version. |
| Both Gateways and Instances | `--connector-metadata` | JSON object expected; parser default `{}`. JSON syntax is parsed by argparse; Adapter validation determines required fields. |

### Predictor loading and warmup

Sources: [P config](../../config/gatewayP.py), [D config](../../config/gatewayD.py),
[P lifecycle](../../core/lifespans/gatewayP.py), [D lifecycle](../../core/lifespans/gatewayD.py).
Detailed commands and failure handling belong to [SLO operation](../slo.md).

| P option / D option | Default and behavior |
| --- | --- |
| `--ttft-warmup` / `--tpot-warmup` | Enabled by default; load the local model first, otherwise attempt real background warmup. |
| `--no-ttft-warmup` / `--no-tpot-warmup` | Mutually exclusive with the corresponding enable flag; disables both loading and fitting. |
| `--ttft-warmup-repeats` / `--tpot-warmup-repeats` | `3` / `2`; integer parsing, no positive-range check in the service parser. Use positive values. |
| `--ttft-target` / `--tpot-target` | First role-specific environment member if present, otherwise first CLI member when main resolves a missing target. Explicitly supply the real vLLM API `host:port`, not the Proxy, for live calibration. An environment-derived non-empty target is not replaced merely by supplying a different member list. |
| `--ttft-model-id` / `--tpot-model-id` | `/workspace/llm-stack/models/deepseek-ai/DeepSeek-R1-Distill-Qwen-7B`. |
| `--ttft-tokenizer-path` / `--tpot-tokenizer-path` | Same 7B local path; must match the served model. |
| `--ttft-model-file` | Profile root `TTFT_predictor/artifacts/prefill_ttft_regressor.pkl`. |
| `--tpot-model-file` | Profile root `TPOT_predictor/artifacts/decode_tpot_regressor.pkl`. |

With warmup enabled, startup requires a target or configured member even when a
persisted file is expected. Readiness is established by predictor logs, not by
`/connectors`. There is no model hot-reload or predictor-ready HTTP endpoint.

### Inspection and policy commands

Controller: `show pool_id`, `show scheduler`, `switch scheduler -m <name>`, `quit`.
Global choices are `demand_aware` (default), `least_load`, and `round_robin`.
Gateways: `show scheduler`, `switch scheduler -m <role-policy>`, `quit` when CLI
is enabled. See [Controller CLI](../../core/consoles/controller.py) and
[Gateway CLI](../../core/consoles/gateway.py). Changes apply to that running process,
not future launches. Policy switching does not clear calibrated-profile caches.

Additional source-only diagnostic commands are
[network_status.py](../../entrypoints/network_status.py) (`--ip` or `--interface`,
`--interval` default 1 second, `--top-n` 5, `--count` 0 for continuous display) and
[network_transfer_analyzer.py](../../entrypoints/network_transfer_analyzer.py).
The latter has a machine-specific default Gateway: pass `--prefill-gateway`
explicitly, and observe the analysis-interface limitation below. Neither is an
application readiness check or an additional installed service entrypoint.

## 4. HTTP interfaces

The next tables list application routes from the current router modules. Keep
registration/session mutations under the application's control; normal clients
submit inference requests to Controller, not management payloads to Gateways.
FastAPI also provides its default `/docs`, `/redoc`, and `/openapi.json` routes;
they describe routes but do not validate registration or transfer readiness.
Many handlers manually parse a `Request`, so OpenAPI does not fully describe
their inner JSON fields or errors.

### Controller

Source: [router](../../core/routers/controller.py), [inference](../../core/handlers/controller/proxy.py),
[registration and network handlers](../../core/handlers/controller/update.py).

| Method and path | Input | Result / important boundary |
| --- | --- | --- |
| `POST /v1/chat/completions`, `POST /v1/completions` | Client fields in Section 5 | Builds internal scheduling context and forwards inference. Not a transparent implementation of every OpenAI/vLLM option. |
| `GET /`, `GET /requests` | None | HTML request-history page; not JSON health/status. |
| `POST /prefill_pool/register` | `proxy_ip_address`, `proxy_port`, `flops`, `connector`; optional `GPU_type`, `max_prompt_throughput` (0) | Returns `status`, assigned `pool_id`, `message`. Missing IP can be derived from TCP source. |
| `POST /decode_pool/register` | `proxy_ip_address`, `proxy_port`, `mem_bandwidth`, `connector`; optional `GPU_type`, `max_decode_req_num` (128, clamped to ≥1) | Same registration response. Connector descriptor validation occurs before registration. |
| `POST /prefill_pool/update` | `pool_id`, `proxy_ip_address`, `proxy_port`, `current_load` | Address and ID must match the same registered Pool under one lock. `current_load` is prompt throughput divided by registered `max_prompt_throughput`; 404 means unknown ID, 409 means address binding conflict. |
| `POST /decode_pool/update` | `pool_id`, `proxy_ip_address`, `proxy_port`, and `running_reqs` or `current_load`; optional `max_decode_req_num` | Same atomic address binding. `running_reqs` takes precedence and is normalized by capacity; it includes active Instances' running **and waiting** requests. 404/409 signal that the Gateway must re-register. |
| `POST /prefill_pool/unregister`, `POST /decode_pool/unregister` | `pool_id`, `proxy_ip_address`, `proxy_port` | Removes only the matching Pool and its links; 404 means unknown ID and 409 means another address owns that ID. ID-only management requests are rejected. |
| `POST /network/register` | `gateway_ip`, `gateway_port`, `bandwidth` | Currently verifies the Gateway exists and acknowledges; this handler does not itself create/refresh link records. Pool registration creates links; network updates refresh them. |
| `POST /network/update` | `gateway_ip`, `gateway_port`, `bandwidth`; optional `rtt` (10), `pcie_bandwidth` (bandwidth), `pcie_rtt` (0.1) | Updates this Gateway's links. Bandwidth is Mbps, RTT ms; same-IP links use PCIe fields. Returns `status`, `updated_gateway`, `bandwidth`, `pcie_bandwidth`. |

Malformed JSON is 400, missing or invalid fields/descriptors are 422, unknown
Pools are 404, and a Pool ID bound to a different Gateway address is 409.
Registration refreshes the full capacity, GPU type, and Connector descriptor
for a repeated role/address/port tuple. A successful registration response must
contain a positive integer `pool_id`; Gateway code rejects a 200 response without
one. Registration restores Pool compute state, while the P Gateway separately
reports its network state. `/network/register` acknowledgement alone still does
not prove that a P/D link is usable.

### Prefill and Decode Gateways

Sources: [P router](../../core/routers/gatewayP.py), [D router](../../core/routers/gatewayD.py),
[P Instance handlers](../../core/handlers/gateway/prefill/update.py),
[D Instance handlers](../../core/handlers/gateway/decode/update.py),
[Connector handlers](../../core/handlers/gateway/connector.py).

| Method and path | Input | Result / important boundary |
| --- | --- | --- |
| `POST /v1/chat/completions`, `POST /v1/completions` | Internal `BaseRequest` envelope with `Request_ID`, `Request_type`, `Prompt`, `Service`, `Task` | Local scheduling and forwarding. A normal client should not build this internal context itself. |
| `POST /instance/register` | `report_label` (`host:port`), matching `role`, `connector`; optional `vllm_port`, `gpu_indices` | Returns `report_label`, `role`; wrong role/invalid Connector rejected with 400. A newly created Instance is offline until a successful metric update; re-registration preserves existing state. |
| `POST /instance/update` | Registered `report_label`, non-negative integer `running_reqs` and `waiting_reqs`; optional numeric resource fields, `gpu_devices`, `network_metric` | The route role must match the registered Instance. Only a valid update activates or refreshes it. Empty 200 on success, 404 for unknown label, 409 for role mismatch, 400 for invalid metrics. |
| `POST /instance/unregister` | `report_label` | Marks Instance offline and clears live load; empty 200, unknown label 404, missing label 400. Stored identity is retained. |
| `GET /connectors` | None | `connector`, `mode`, `supported`; configuration only, not engine/registration/cache validation. |
| `POST /pd/select_instance` | Internal `BaseRequest` envelope | Advisory `instance`, empty `kv_addr`, `connector` (role/descriptor); no active Instance or inconsistent metadata gives 503. It is not a lease or KV-transfer handshake. |
| `POST /connector/sessions` | `connector`; optional `prefill_endpoint`, `decode_endpoint`, `session_id` | Returns process-local session state; invalid descriptor/compatibility gives 422. Endpoints may carry a `connector` descriptor; same active ID reuses the session. |
| `GET /connector/sessions/{session_id}` | Session ID | Session object, or 404 in this process. Controller-local sessions are not automatically mirrored to Gateways. |
| `DELETE /connector/sessions/{session_id}` | Session ID | Marks session closed, returning `session_id`, `state`; unknown ID 404. Does not delete engine KV or close a global distributed lease. |
| `POST /analysis/network-transfer` (**P only**) | Non-empty `prompt`; optional `model`, `request_id` | Diagnostic Prefill/Decode timing. Uses the first configured Decode target and does not construct the normal Controller Connector session/context. Not a replacement for NIXL end-to-end verification; `observed_transfer_time_ms` includes scheduling/forwarding effects. |

Session objects contain `session_id`, `connector`, `prefill_endpoint`,
`decode_endpoint`, `context`, `state`, `created_at`, `updated_at`. They live in
memory and are lost on process restart; closing retains a closed record.
See [session models](../../connectors/models.py) and [manager](../../connectors/manager.py).

### Instance Proxies and optional Client UI

Sources: [Instance router](../../core/routers/instance.py), [forwarding](../../core/handlers/instance/proxy.py),
[metadata](../../core/handlers/instance/metadata.py), [identity](../../core/handlers/instance/connector.py).

| Method and path | Input | Result / important boundary |
| --- | --- | --- |
| `POST /v1/chat/completions`, `POST /v1/completions` | vLLM/OpenAI-style payload prepared by Gateway | Forwards to `InstanceStore.host:vllm_port`; normal deployment does not schedule Pools here. |
| `GET /connectors` | None | `connector`, `mode`; unlike Gateway response, no `supported` list. |
| `PUT /metadata?key=...` | UTF-8-decodable raw body; no JSON schema | Volatile process-local byte storage; text acknowledgement. Current logging decodes the body as UTF-8. Does not register an Instance or establish NIXL pairing. |
| `GET /metadata?key=...`, `DELETE /metadata?key=...` | Key query; omitted key becomes empty string | Stored bytes / text acknowledgement, or 404. Content type is declared JSON but acknowledgements and missing-key bodies are plain text; do not blindly JSON-decode them. |

[Client UI](../../examples/clientUI/client.py) listens on `127.0.0.1:5050` and has
`GET /`, `POST /api/send_command`, `GET /api/get_history`, and
`POST /api/clear_history`. These are browser-helper interfaces, not Controller
routes; history is in memory. Its command-mode field allowlist is narrower than
Controller's request parser, so use the Python request examples for affinity or
SLO observation workloads.

There is no general `/healthz`, `/debug/status`, application `/metrics`, old
`/pd/pair`, or HTTP scheduler-switch route in these core routers. Query **vLLM's**
`/v1/models` and `/metrics` on the engine port, not on the Proxy/Gateway port.

## 5. Client request fields and forwarding limits

Source: [request construction and serialization](../../core/schemas/request.py).
The internal envelope is not the public client body.

| Field | Current Controller interpretation |
| --- | --- |
| `model` | Required non-empty model identifier. Use the same reachable local model/tokenizer path as the deployment example. |
| `messages` | Chat input must be a list. Selects the **last user message**, or concatenates role/content text if there is no user message; forwarding rebuilds one user message, not the complete original conversation. |
| `prompt` | Completion input: string, or first item of a non-empty list. Not multi-prompt batch forwarding. |
| `max_tokens` | Default 1000; integer conversion; 0/empty-like values fall back to 1000. Not a full positivity/context validation layer. |
| `stream` | Default false; parsed by [boolean helper](../../utils/parse_str_flag.py). Prefer JSON booleans. |
| `temperature` | Default 1.0; parse failures or values ≤0 become 1.0. Passing 0 does not request greedy decoding through this parser. |
| `top_p` | Default 1.0; invalid values/outside `(0,1]` become 1.0. |
| `Enable_Affinity`, `user_id`, `session_id` | Affinity defaults off; requires enabled flag and both non-empty identities. Single-P/D routing takes precedence; cached routes are checked for current Connector compatibility. |
| Client source IP | Constructs PD enablement, TTFT/TPOT/E2E budgets, and internal context. See the exact [SLO mapping](../slo.md#slo-source-and-units). Extra client SLO/PD fields are not overrides. |

Fields such as tools, multimodal content, penalties, or stream options are not a
general pass-through contract. Prompt token counting can fall back to character
count if the matching tokenizer cannot load. Invalid client fields do not all
receive a consistent 400/422 envelope: request construction can raise before the
Controller's PD-preparation error handler. That handler specifically returns
503 `pd_instance_selection_failed` on failures inside `_prepare_pd_request()`.

Forwarders can return a `StreamingResponse` before a downstream error occurs.
Some helpers forward a 4xx body as bytes instead of propagating its HTTP status;
an upstream client can see HTTP 200 with an error body or an interrupted stream.
Inspect `error` / `choices`, SSE termination, and service logs rather than status
alone. See [forwarding implementation](../../proxy/forward.py) and the
[deployment troubleshooting](../quickstart_v1.md#13-troubleshooting).

## 6. Connector descriptor boundary

| Adapter | Gateway metadata | Instance metadata | Compatibility |
| --- | --- | --- | --- |
| [NIXL](../../connectors/adapters/nixl.py) | `{}` allowed. Non-empty metadata must include all three Instance fields. | `side_channel_host` (non-empty), `side_channel_port` (positive integer), `tp_size` (positive integer). | Name/version must match; TP equality is not required by this Adapter. Actual model/layout/parallel compatibility still depends on the engines. |
| [LMCache](../../connectors/adapters/lmcache.py) | Non-empty `backend`, `namespace`, `config_fingerprint`. | Same three non-empty identity fields. | Name/version and all three fields must match within the Pool and between paired endpoints. Metadata labels are operator declarations, not a live backend-content probe. |

All Instances within a Pool use its Connector family/version. Cross-Pool
heterogeneity is allowed, but a PD request cannot bridge NIXL to LMCache. Adapter
names are not vLLM class names: `--connector-name nixl` does not start/configure
`NixlConnector` in a serving engine. Version `1` is not a NIXL software version.

Root [lmcache.yaml](../../lmcache.yaml) is deployment-specific: it contains a fixed
Redis endpoint, `cachegen`, chunk size 32, and disabled local CPU / Decode-cache
saving. Declaring compatible metadata neither applies that YAML to vLLM nor
proves cache reuse/Redis writes. NIXL deployment does not need Redis or LMCache.

## 7. Refresh, liveness, and where to troubleshoot

Sources: [Pool store](../../store/controller/pool_manager.py), [link store](../../store/controller/network.py),
[resource snapshots](../../scheduler/controller/resource_state.py),
[Controller lifecycle](../../core/lifespans/controller.py),
[Instance lifecycle](../../core/lifespans/instance.py), and the Gateway/Instance reporting modules linked above.

Successful Pool registration/update/unregister and network updates refresh
Controller snapshots; the existing 1-second reconciliation still filters Pools
after 3000 seconds and links after 60 seconds. These Controller timeouts are
unchanged.

Gateway lifespans read the configured 10-second reporting interval at startup.
If a Pool update returns 404 or 409, the Gateway re-registers and immediately
sends its current compute load; the P Gateway also reports its current network
metrics. Connection, timeout, and 5xx failures use bounded retry delays. Instance
metrics sleep for 20 ms by default after each sample/report, but actual arrival
interval also includes collection and HTTP time.

Gateway Instance eligibility requires registration, online state, and a valid
heartbeat younger than the 30-second monotonic TTL. A one-second scan marks stale
Instances offline and clears live load; selector, reservation, preselection, and
dispatch paths check freshness too. A later valid heartbeat restores the same
registered Instance. This measures the Proxy reporting path, not the health of
the vLLM engine; a failed metrics sample can still produce zero-valued fields.
Expiry does not replay or migrate requests already sent to an Instance. The
Controller still does not require a Pool to contain an online Instance, so its
Pool view can temporarily lag local Gateway readiness.

| Topic | Maintained operational reference | Relevant checks |
| --- | --- | --- |
| Python/serving environment, imports, models, ports | [Environment](../../env/README.md#6-environment-troubleshooting), [installation](../installation.md#installation-troubleshooting) | [package tests](../../test/test_package_installation.py) |
| Single-Pool registration, forwarding, NIXL errors | [Deployment](../quickstart_v1.md#13-troubleshooting) | [runtime registration tests](../../test/test_runtime_instance_registration.py), [Connector tests](../../test/test_connector_protocol.py) |
| Missing/stale Pools/Instances, policy and link observations | [Multi-Pool](../multi_pool.md#8-troubleshooting-multi-pool-operation) | [Controller component tests](../../test/test_controller_scheduler_components.py), [single-Gateway tests](../../test/test_single_gateway_routing.py) |
| Predictor load, shared profile keys, SLO fallback/measurement | [SLO operation](../slo.md#9-troubleshooting-slo-operation), [Controller profiles](../../profile/controller/README.md) | [Gateway strategy tests](../../test/test_gateway_scheduler_strategy.py), [observability tests](../../test/test_gateway_observability.py) |

The [public CPU suite](../../test/README.md) checks application contracts, not live
vLLM, backend cache hits, GPU performance, or actual KV movement. Those require
the real request and engine-metric checks in the deployment guides.
