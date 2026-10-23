"""Canonical predictor namespace, without installing a top-level profile package."""

from pdschedule.paths import source_root

# Source checkouts retain the existing profile/ layout. Wheels put the two
# predictor packages directly below this namespace via explicit package-dir maps.
_source = source_root()
if _source is not None:
    __path__.append(str(_source / "profile"))
