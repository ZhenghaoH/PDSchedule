# Windows local runtime: Conda Python 3.13

This runbook operates the PDSchedule Controller, Prefill/Decode Gateways, browser
pages, registration APIs, Connector metadata APIs and input-driven offline tools on
Windows. It uses the existing Conda `pdschedule` environment at Python 3.13.13 and
keeps all configuration, logs, process state, model profiles, tokenizers and
generated output under `D:\代码\Environments\PDScheduleLocal`.

This is a CPU control-plane setup. It does not install or start Torch, vLLM, NIXL,
LMCache, Redis or a model engine. A page loading, a Gateway registration or an
application Connector session does not establish that inference or KV transfer
works. The real GPU serving guide remains on Python 3.12.

## Install into the existing environment

The local configuration is in `D:\代码\Environments\PDScheduleLocal\config\local.json`.
It points at the existing checkout and `D:\Annaconda3\envs\pdschedule\python.exe`.
The setup script verifies Python 3.13.13 and never creates, deletes or replaces a
Conda environment.

From PowerShell:

```powershell
powershell -ExecutionPolicy Bypass -File D:\代码\Environments\PDScheduleLocal\tools\Setup.ps1 -Install
```

`-Install` installs `requirements.txt` and the checkout in editable mode into the
configured `pdschedule` environment, then runs `pip check`. The Python 3.13 markers
select NumPy 2 and compatible current scientific packages; Python 3.12 keeps the
existing NumPy 1.26, SciPy 1.16, scikit-learn 1.7 and Matplotlib 3.10 serving/test
baseline. The setup script does not touch Conda base.

Before installing in an existing environment, this runtime retains both an exact
Conda clone and before/after package manifests under `PDScheduleLocal\backup`.
The clone is named `pdschedule-backup-20261103-a246422`. To make a separate recovery
environment from it, run:

```powershell
D:\Annaconda3\Scripts\conda.exe create --name pdschedule-recovered --clone pdschedule-backup-20261103-a246422 --yes
```

This leaves the current `pdschedule` environment and the backup available for
inspection.

## Start, inspect and stop services

Default service addresses bind to `127.0.0.1`:

| Service | Port | Startup policy |
|---|---:|---|
| Controller and `/requests` page | 5000 | Demand-aware Controller; warmup is offline. |
| Prefill Gateway | 8081 | `jsq`; predictor warmup disabled. |
| Decode Gateway | 8082 | `round_robin`; predictor warmup disabled. |
| Browser Client UI | 5050 | Page and request-builder UI; no request is sent at startup. |
| Additional Prefill/Decode Gateways | 8083 / 8084 | Started by the multi-pool command; no Instances are added. |
| Optional Instance Proxy APIs | 7001–7004 | Start only with `-AllowNoEngine`; no inference engine is assumed. |

Run the base control plane, exercise its local pages and application APIs, observe
it for five minutes, and stop the processes:

```powershell
powershell -ExecutionPolicy Bypass -File D:\代码\Environments\PDScheduleLocal\tools\Start-ControlPlane.ps1
powershell -ExecutionPolicy Bypass -File D:\代码\Environments\PDScheduleLocal\tools\Invoke-LocalAcceptance.ps1
powershell -ExecutionPolicy Bypass -File D:\代码\Environments\PDScheduleLocal\tools\Observe-LocalRuntime.ps1 -DurationMinutes 5
powershell -ExecutionPolicy Bypass -File D:\代码\Environments\PDScheduleLocal\tools\Get-Status.ps1
powershell -ExecutionPolicy Bypass -File D:\代码\Environments\PDScheduleLocal\tools\Stop-LocalRuntime.ps1
```

By default, service windows are hidden and stdout/stderr are recorded in `logs`.
Add `-Interactive` to `Start-ControlPlane.ps1` or `Start-MultiPool.ps1` to open
visible terminals with the Controller/Gateway CLIs. The Controller accepts
`show scheduler`, `switch scheduler -m <mode>`, `show pool_id` and `quit`; each
Gateway accepts `show scheduler`, `switch scheduler -m <mode>` and `quit`.

The launchers check every requested port before starting a service. They do not
stop a port occupant unless its PID, process start time, Python executable and
entrypoint match a record created by this runtime. `Get-Status.ps1` reports
unmanaged port listeners and leaves them alone. `Stop-LocalRuntime.ps1` only stops
verified managed PIDs. If optional proxies were started, it waits through the
Gateway instance-heartbeat TTL before stopping Gateways so expiry can be observed.

Start four Gateways without Instances using:

```powershell
powershell -ExecutionPolicy Bypass -File D:\代码\Environments\PDScheduleLocal\tools\Start-MultiPool.ps1
```

The additional Gateways register as empty Pools. Controller link records created
from Pool registration use the application's configured/default values; they are
not WAN measurements. No request can be inferred ready from this topology. An
Instance Proxy can only register with actual Connector metadata; the Gateway
rejects empty NIXL metadata, so the launcher requires real side-channel fields
from a serving configuration instead of inventing them. With no such configuration,
leave Instance Proxies stopped. If actual metadata is available, a control-only
proxy can be inspected explicitly with:

```powershell
powershell -ExecutionPolicy Bypass -File D:\代码\Environments\PDScheduleLocal\tools\Start-InstanceProxy.ps1 -Role prefill -Index 1 -ConnectorName nixl -ConnectorMetadataJson '<actual JSON object with side_channel_host, side_channel_port and tp_size>' -AllowNoEngine
```

It probes the configured unused placeholder backend port. An unavailable or zero
metric is not engine health. `USE_MOCK` stays false. Stop it with
`Stop-LocalRuntime.ps1`; the script waits for heartbeat expiry.

## Offline workbench

`tools\offline_workbench.py` runs existing project code against explicit local
inputs. It never sends an inference request. Its `strategies` command evaluates
all three Controller policies and all six Gateway policies using a JSON snapshot.
The snapshot must contain a non-empty `source`, a `data_kind` of `measured`,
`historical` or `operator_declared`, actual input rows, and a request's explicit
token counts/SLOs. Global policy evaluation also needs every compatible P/D link;
pool-local evaluation needs both P and D Instance rows. Results repeat the
supplied source and label declared values as non-live inputs.

The JSON sections are:

| Section | Required fields |
|---|---|
| Root | `source` names the file/record/source of these values; `data_kind` is `measured`, `historical` or `operator_declared`. Optional `connector` supplies the default descriptor. |
| `prefill_pools` | `pool_id`, `host`, `port`; optional load/capacity values and Connector descriptor. |
| `decode_pools` | `pool_id`, `host`, `port`; optional load, capacity, request-count and Connector values. |
| `networks` | `prefill_pool_id`, `decode_pool_id`, `bandwidth_mbps`, `rtt_ms`; provide a record for each compatible P/D pair. |
| `instances.prefill` | `address`; optionally supplied prompt throughput and running/waiting counts. |
| `instances.decode` | `address`; optionally supplied generation throughput, KV use and running/waiting counts. |
| `request` | `request_id`, `prompt_tokens`, `max_tokens`, `slo_ttft_ms`, `slo_tpot_ms`; optional model label, E2E SLO and compression factor. |

Save the source-backed snapshot under `inputs`, then run:

```powershell
D:\Annaconda3\envs\pdschedule\python.exe D:\代码\Environments\PDScheduleLocal\tools\offline_workbench.py strategies --input D:\代码\Environments\PDScheduleLocal\inputs\your-snapshot.json
```

`slo_aware_prefill` uses an explicitly supplied trusted TTFT model when passed;
without one, this implementation's fallback estimate is labelled uncalibrated and
the workbench disables its optional HTTP prediction call. `slo_aware_decode` reports
predictor unavailability and uses its existing code fallback. To load a fitted
pickle, specify `--ttft-model` and/or `--tpot-model` together with `--trust-pickle`.
Only load files whose origin you trust.

Other commands use only local files or process state:

```powershell
# Exact tokenization; fails if the local tokenizer files are missing.
D:\Annaconda3\envs\pdschedule\python.exe D:\代码\Environments\PDScheduleLocal\tools\offline_workbench.py tokenize --tokenizer D:\代码\Environments\PDScheduleLocal\tokenizers\your-tokenizer --text "your input"

# Fit from real TTFT CSV rows: batch_size,prompt_length,ttft_seconds.
D:\Annaconda3\envs\pdschedule\python.exe D:\代码\Environments\PDScheduleLocal\tools\offline_workbench.py fit-ttft --input D:\代码\Environments\PDScheduleLocal\inputs\ttft.csv --output D:\代码\Environments\PDScheduleLocal\output\ttft.pkl

# Fit from real TPOT CSV rows: batch_size,prompt_length,generated_tokens,generation_seconds.
D:\Annaconda3\envs\pdschedule\python.exe D:\代码\Environments\PDScheduleLocal\tools\offline_workbench.py fit-tpot --input D:\代码\Environments\PDScheduleLocal\inputs\tpot.csv --output D:\代码\Environments\PDScheduleLocal\output\tpot.pkl

# Summarize actual fields in a selected log or CSV; no source logs are copied.
D:\Annaconda3\envs\pdschedule\python.exe D:\代码\Environments\PDScheduleLocal\tools\offline_workbench.py analyze --input D:\代码\PD分离推理调度代码\PDSchedule\PDSchedule\global_schedule.log

# Read local CPU, memory, disk, network interface counters and NVML if available.
D:\Annaconda3\envs\pdschedule\python.exe D:\代码\Environments\PDScheduleLocal\tools\offline_workbench.py system

# Validate descriptors and in-memory session create/query/close logic; no KV transfer.
D:\Annaconda3\envs\pdschedule\python.exe D:\代码\Environments\PDScheduleLocal\tools\offline_workbench.py connector --session-lifecycle
```

Training commands do not fabricate samples. A missing tokenizer, measurement CSV or
trusted fitted model yields an explicit unavailable/error result; it does not create
a calibrated artifact. `system` reports cumulative network counters, not bandwidth.
NVML errors remain unavailable instead of being converted to zero. `request-check`
parses and validates a Client command without sending it. Use it to inspect
request-construction errors without contacting a model endpoint.

The local runtime keeps generated content in `PDScheduleLocal\logs`, `profiles`,
`inputs`, `output` and `state`. The repository clone and its untracked files are
preserved. No commit or push is part of this runbook.
