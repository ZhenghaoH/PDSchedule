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
