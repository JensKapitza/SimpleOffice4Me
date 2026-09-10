"""Compatibility shim for older pip/setuptools editable-install paths.

Project metadata remains authoritative in pyproject.toml.  This file exists so
older distro-provided pip versions (notably Ubuntu 22.04) can fall back to the
legacy editable-install path instead of aborting when PEP 660 build_editable
is unavailable in their effective build backend.
"""

from setuptools import setup


setup()
