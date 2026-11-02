# Compatibility and migrations

This table describes the current version's supported installation surfaces, not a claim that a package migration has been implemented.

| Old path | Canonical path | Shim type | Object identity | Wire compatibility | Focused tests | Source checkout | Editable install | Wheel install | Dynamic/module-string references | Service entrypoints | Removal condition | Status |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Source entrypoint files | Installed console commands | Entry-point mapping | Same `main` function | CLI options unchanged | [packaging](../../test/test_package_installation.py) | Run `entrypoints/*.py` | Both forms | Console scripts | Keep role module names valid | Six commands in [installation](../installation.md) | No removal planned | Current |
| `profile/TTFT_predictor`, `profile/TPOT_predictor` | `pdschedule.profiling.*` | Package-dir mapping / source bridge | Verify imports against current source | Not a network wire contract | [packaging](../../test/test_package_installation.py) | Original files retained | Canonical import | Canonical import | Preserve exact names used by loaders | Gateway predictor flags | No removal planned | Current |
| Source `templates/requests.html` | `pdschedule.resources.templates` | Package data mapping | Resource contents | HTML page, not API versioning | [packaging](../../test/test_package_installation.py) | Source template | Installed resource | Installed resource | Resolve through resource helper | Controller | No removal planned | Current |
| Source profile directory | `PDSCHEDULE_PROFILE_DIR` or runtime default | Path selection | Deployment files, not package resources | Model format depends on fitted artifact | [packaging](../../test/test_package_installation.py) | Defaults to source `profile/` | See source detection | Defaults to CWD `profile/` | Set override before service starts | Gateway predictor flags | Not applicable | Current |
| Root modules (`core`, `config`, etc.) | Explicit package list | None | Existing modules remain root-level | Do not infer HTTP compatibility from imports | [module map](package-and-module-map.md) | Present | Installed | Installed | Avoid conflicting top-level package names | See [installation](../installation.md) | No full move approved | Current |

## Change checklist

Check explicit package mappings, all dynamic imports and CLI module strings; compare HTTP fields and Connector identity; build wheel and sdist; check resource and profile locations from outside the checkout; update links and deployment examples. The repository does not promise redirects for changed documentation URLs.
