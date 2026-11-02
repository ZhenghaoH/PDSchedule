# Development and validation

## Expected environments

| Environment | Expectation | Individually auditable command |
| --- | --- | --- |
| CPU development | Python 3.12, dependencies installed in an isolated environment | `python -m pytest -q` |
| Package validation | Build frontend/backend installed; no GPU or package index required for the checks | `python -m pytest -q -m packaging` |
| Deployment | Compatible NVIDIA GPU, serving stack, local model and real NIXL or LMCache backend | Follow [environment](../../env/README.md) and [quick start](../quickstart_v1.md) |
| Documentation | Complete checkout with local links and source references | `git diff --check`; inspect links and anchors |

## Required final-head checklist

For changes, check affected commands, interfaces, defaults, links and tests against the current source. Run the relevant CPU tests and package tests when packaging changes. Verify the wheel and sdist contents where manifest or package resources change. A real serving validation is separate and should only be run in a prepared environment; do not replace it with mocked tests. Detailed test commands are in [test README](../../test/README.md).

## Result reporting vocabulary

**Passed** means the named command ran successfully for this revision. **Failed** includes its observed failure. **Blocked/not run** includes the missing prerequisite or reason. State deselections, environment and hardware when material. Historical logs are not a result for the current change.

## Required PR evidence

When a change is reviewed, report affected surfaces, exact tests and their outcomes, remaining limitations and any actual real-serving evidence. No extra approval process or CI outcome is implied by this section.
