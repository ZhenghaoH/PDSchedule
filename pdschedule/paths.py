"""Separate read-only package resources from deployment-generated state."""

import os
from pathlib import Path


def source_root() -> Path | None:
    candidate = Path(__file__).resolve().parents[1]
    if (candidate / "pyproject.toml").is_file() and (
        candidate / "profile" / "TTFT_predictor"
    ).is_dir():
        return candidate
    return None


def runtime_root() -> Path:
    """Keep source defaults; installed services write under their working directory."""
    return source_root() or Path.cwd()


def profile_root() -> Path:
    override = os.getenv("PDSCHEDULE_PROFILE_DIR", "").strip()
    return Path(override).expanduser().resolve() if override else runtime_root() / "profile"


def controller_log_file(default_path: Path) -> Path:
    """Resolve the Controller schedule log, honoring an absolute local override."""
    override = os.getenv("PDSCHEDULE_CONTROLLER_LOG_DIR", "").strip()
    if not override:
        return Path(default_path)

    directory = Path(override).expanduser()
    if not directory.is_absolute():
        raise ValueError("PDSCHEDULE_CONTROLLER_LOG_DIR must be an absolute path")
    directory.mkdir(parents=True, exist_ok=True)
    return directory.resolve() / "global_schedule.log"
