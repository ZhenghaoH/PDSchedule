# Public tests

The public regression suite checks scheduling, Connector compatibility, registration,
forwarding errors, metrics, and profiling logic without GPUs or running inference services.
It does not establish real KV transfer, cache reuse, performance gains, or GPU compatibility.

## Browser entrypoints and manual services

| Component | Default example address | Purpose |
| --- | --- | --- |
| Controller request page | `http://127.0.0.1:5000/requests` | Request observation after Controller starts. |
| Optional browser client | `http://127.0.0.1:5050/` | Manually send requests to a ready Controller; see [client](../examples/clientUI/README.md). |

These are manually started services, not automated tests. The [full NIXL path](../doc/quickstart_v1.md) includes service startup, a real request, transfer checks and shutdown. Stop only services created for your own run and clean up only its dedicated temporary artifacts.

## 1. Prepare a CPU test environment

Use **Python 3.12**, matching the documented application baseline. The NumPy 1.26.4
constraint is not a Python 3.13 installation target. Run commands from the repository root.

Linux/macOS, with Python 3.12 selected:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m pip check
python -m pytest -q
```

Windows PowerShell, with `python` pointing to Python 3.12:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe -m pytest -q
```

`requirements.txt` includes application requirements, pytest/httpx, the
build/setuptools/wheel tools used by the offline package checks, and research tools.
The suite uses real FastAPI and PyYAML imports; it does not replace those packages
globally with incomplete stubs. Request, scheduler, and NVML fakes remain local to
the tests that need them.

Installing these requirements does not install Torch, vLLM, NIXL, LMCache, or Redis.
It also does not download model weights or tokenizers. The research tools included
in the dependency file are not required for this suite. For a GPU serving
environment, follow [the environment guide](../env/README.md) instead of
replacing an existing CUDA-compatible package stack.

## 2. Default entrypoint and scope

```bash
python -m pytest --collect-only -q
python -m pytest -q
```

`pytest.ini` limits default collection to `test/test_*.py` and adds the repository root
to the import path. `test/conftest.py` excludes `test/test_sync_script.py` independently
of the invocation directory. That ignored local-only
file depends on the unpublished `sync.sh` and must not determine public test results.
Neither the ignored root simulator nor `plan/` audit copies are default test inputs.

| Module | Checks |
|---|---|
| `test_affinity_scheduler.py` | Affinity enabled/disabled and incomplete session identity; pytest runs only its offline functional checks. |
| `test_connector_protocol.py` | Registry/CLI scope, NIXL asymmetric TP, shared LMCache identity, compatible candidate filtering, and absent legacy routes. |
| `test_controller_scheduler_components.py` | Global strategy contracts, consistent resource snapshots, and successful-update notification. |
| `test_decode_pool_load.py` | Online Decode running/waiting load and non-negative totals. |
| `test_distserve_lm_profiler.py` | Profiler arguments, saturation logic, output files, fake NVML sampling, and local HTTP SSE framing. |
| `test_gateway_observability.py` | Separate logs, timing semantics, unavailable measurements, and per-GPU metrics. |
| `test_gateway_scheduler_strategy.py` | Prefill/Decode policies, request batching, CLI behavior, Connector validation, and HTTP status preservation. |
| `test_package_installation.py` | Metadata, writable paths, wheel/sdist contents, and dependency-light installation outside the source checkout. |
| `test_runtime_instance_registration.py` | Dynamic registration, Connector identity, reservations, heartbeat fields, and address configuration. |
| `test_single_gateway_routing.py` | Single-P/D fast path, transitions to multi-Pool selection, metadata, and local profile fallback. |

The current suite collects **117 tests** across these ten modules. A complete run
should finish with `117 passed`; a skipped or deselected subset is not the same
result. Test counts can change when coverage changes.

## 3. Focused checks

```bash
python -m pytest -q test/test_connector_protocol.py
python -m pytest -q test/test_gateway_scheduler_strategy.py
python -m pytest -q test/test_runtime_instance_registration.py test/test_single_gateway_routing.py
python -m pytest -q test/test_affinity_scheduler.py
```

Use file paths rather than `python -m unittest test.test_...`: `test` can resolve to
Python's standard-library package. Existing unittest classes can also run with
discovery, but pytest is the complete entrypoint: the Connector/profiler functions
and parametrized cases are not collected by unittest discovery.

The affinity script has a separate manual mode. To run only its functional checks:

```bash
python test/test_affinity_scheduler.py --skip-benchmark
```

Running that script **without** `--skip-benchmark` requests a real Controller and
model. Supply an explicit `--url` and `--model`; this benchmark is never invoked
by the default pytest entrypoint.

## 4. Local networking and diagnostics

One test, `test_measure_request_handles_real_sse_framing`, is marked `loopback`.
It creates an ephemeral HTTP server on `127.0.0.1` and closes it afterward; no
external vLLM service is needed. Run it separately or omit it explicitly:

```bash
python -m pytest -q -m loopback
python -m pytest -q -m "not loopback"
```

The subset without this test has 116 cases; report the deselection rather than
claiming full-suite coverage. Windows asyncio also uses internal loopback sockets
when constructing an event loop. A sandbox that blocks these sockets can stall
async tests even before an HTTP request is sent. Use an environment that permits
local loopback; do not turn a blocked network check into a passing test.

The configured `faulthandler_timeout=30` prints thread stacks after 30 seconds.
It is diagnostic output, **not** a timeout that terminates a test. For more detail:

```bash
python -m pytest -vv -s
```

If the platform's pytest temporary directory is not writable, choose a **new,
dedicated** directory with `--basetemp`. Pytest may remove that directory; never
point it at the repository, a home directory, or existing user data.

## 5. Installation checks

```bash
python -m pytest -q -m packaging
```

The four package checks run by default as part of the full suite. They build the
wheel from the source distribution with the installed backend tools, inspect
archive metadata/resources, and install the wheel without dependencies into a
fresh temporary environment. This path uses no package index. The lean install
checks identity, template access, writable profile defaults, and the standard
library's profile/cProfile modules, not full runtime or GPU behavior.

Build tools must be installed before running these checks. Use a short temporary
path on Windows if the checkout and installed package paths approach the legacy
path-length limit. See [the installation guide](../doc/installation.md) for a
complete dependency installation and the six service commands.

## 6. What requires a separate serving environment

- Actual NIXL KV transfer and engine/side-channel matching.
- LMCache store/load behavior and external cache reuse.
- GPU/NVML hardware measurements, TP topology, and profiling against vLLM.
- Streaming/non-streaming requests through a complete deployed Controller/Gateway/Instance chain.
- The manual affinity TTFT benchmark or paper workload measurements.

Use [the real NIXL deployment guide](../doc/quickstart_v1.md) for service startup,
registration, requests, transfer metrics, and shutdown. Connector validation logs
prove configuration compatibility, not physical KV delivery. A CPU test pass must
not be described as a successful GPU deployment.
