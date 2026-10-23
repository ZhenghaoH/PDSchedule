"""Locations of templates shipped with the distribution."""

from pathlib import Path

from pdschedule.paths import source_root


def controller_template_dir() -> Path:
    source = source_root()
    return source / "templates" if source is not None else Path(__file__).parent / "templates"
