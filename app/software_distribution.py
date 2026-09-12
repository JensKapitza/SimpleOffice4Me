"""Compatibility facade for Git-independent software distribution."""
from .software_distribution_core import (
    SoftwareDistributionStore,
    apply_release_archive,
    application_root,
    build_release_archive,
    clone_release_archive,
    inspect_release_archive,
    is_newer_release,
    local_release_info,
)

__all__ = [
    "SoftwareDistributionStore",
    "apply_release_archive",
    "application_root",
    "build_release_archive",
    "clone_release_archive",
    "inspect_release_archive",
    "is_newer_release",
    "local_release_info",
]
