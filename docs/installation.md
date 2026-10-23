# Python installation and distribution

PDSchedule 0.1.0 supports a Python **3.12** application installation. This package
does not install Torch, vLLM, NIXL, LMCache, model weights, or fitted predictors.
Prepare a compatible GPU runtime using [the environment guide](environment.md)
before running real inference services. A package version is not a GitHub Release.

## Source checkout and editable installation

The existing Python startup commands continue to work from the repository root.
For development, create a Python 3.12 virtual environment and run:

```bash
python -m pip install -r requirements-dev.txt
python -m pip install -e . --no-deps --no-build-isolation
python -m pip check
python -m pytest -q
```

Application dependency constraints have one source: `requirements.txt`, which is
also read into the package metadata at build time. `requirements-dev.txt` adds
tests and build tools; `requirements-research.txt` remains optional. There are
no separately maintained copies of the application dependency list in pyproject.

In an existing GPU environment, first install application dependencies with the
runtime constraints from the environment guide. Then use `pip install -e .
--no-deps` to avoid resolving a different serving stack. Do not replace a working
CUDA environment with the CPU test environment.

## Build wheel and source distribution

```bash
python -m pip install -r requirements-dev.txt
python -m build --no-isolation
```

The default build produces:

```text
dist/pdschedule-0.1.0.tar.gz
dist/pdschedule-0.1.0-py3-none-any.whl
```

The wheel is built from the source distribution by the default build command.
`--no-isolation` uses the build tools already installed by requirements-dev;
without that option the build frontend prepares its own backend environment.
`py3-none-any` describes the application archive, not GPU-runtime portability.

In a second Python 3.12 environment, install the wheel with its declared
application dependencies:

```bash
python -m pip install /absolute/path/to/pdschedule-0.1.0-py3-none-any.whl
python -m pip check
```

For an already prepared serving environment, use `--no-deps` only after checking
that all application requirements are satisfied. The source distribution can
also be passed to `pip install`; it needs the declared build backend tools.

## Installed service commands

These commands work outside the source checkout and accept the same arguments as
their corresponding Python files:

| Command | Existing source entrypoint |
|---|---|
| `pdschedule-controller` | `entrypoints/controller.py` |
| `pdschedule-gateway-p` | `entrypoints/gatewayP.py` |
| `pdschedule-gateway-d` | `entrypoints/gatewayD.py` |
| `pdschedule-instance-p` | `entrypoints/instanceP.py` |
| `pdschedule-instance-d` | `entrypoints/instanceD.py` |
| `pdschedule-client-ui` | `examples/clientUI/client.py` |

For example:

```bash
pdschedule-controller --help
pdschedule-gateway-p --help
python -m entrypoints.instanceD --help
```

Use the addresses, registration metadata, GPU assignments, and readiness checks
from [the real NIXL guide](quickstart.md). Installation does not remove the
Controller's interactive CLI requirement or change its source-address PD/SLO mapping.
The optional browser Client retains its current loopback address and port 5050.

## Namespaces and resources

`pdschedule.__version__` is the version source. Existing runtime packages such as
`core`, `config`, `connectors`, and `scheduler` retain their current import names;
this is a minimal transitional installation, not a full namespace migration.
Avoid co-installing unrelated projects that provide those same generic names.

The two predictor packages are installed as:

```python
from pdschedule.profiling.TTFT_predictor import prefill_predictor
from pdschedule.profiling.TPOT_predictor import TPOT_predictor
```

No top-level `profile` package is installed, so Python's `profile` and `cProfile`
remain available. Source checkouts keep their existing `profile/` directories;
the canonical namespace bridges to them without copying predictor implementations.
Root offline profiling scripts remain repository tools and are not wheel entrypoints.

The wheel includes the Controller request template, browser Client template, and
predictor code/README files. Package resources are read-only. Fitted models and
logs are deployment state, not files to write into site-packages.

| State | Source checkout default | Installed application default |
|---|---|---|
| Profile root | Repository `profile/` | Working directory `profile/` |
| Controller profiles | Profile root `controller/` | Profile root `controller/` |
| Gateway models | Profile root `TTFT_predictor/artifacts/` or `TPOT_predictor/artifacts/` | Same subdirectories under the writable profile root |
| Global scheduler log | Repository `global_schedule.log` | Working directory `global_schedule.log` |

Set `PDSCHEDULE_PROFILE_DIR` to a deployment-owned profile root **before starting
services** to override the default. Gateway `--ttft-model-file` / `--tpot-model-file`
can still select explicit files. Missing Controller models retain the existing
fallback; installing a wheel does not supply calibration data.

`plan/`, root `simulator/`, local launch/publish scripts, tests, audit files,
logs, and fitted models are excluded from the runtime wheel. `instance/simulator`
is runtime code and remains included. The source distribution contains the
inputs needed to rebuild the wheel, not a copy of the developer's workspace.

## Package checks

```bash
python -m pytest -q -m packaging
```

Package checks build without network isolation downloads, inspect wheel/sdist
contents, and install the wheel without dependencies into a fresh environment
to check lightweight imports, resources, and the standard-library profile module.
They do not substitute for a full dependency installation or GPU deployment.
The full CPU suite includes these checks; see [test/README.md](../test/README.md).
