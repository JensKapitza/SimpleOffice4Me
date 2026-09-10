"""Writable, versioned WebDAV endpoint for LibreOffice remote editing.

Implementation is split into ordered modules while this module remains the public
compatibility and Flask blueprint surface.
"""
from __future__ import annotations

import sys
import types

from . import webdav_part_1 as _part_1
from . import webdav_part_2 as _part_2
from . import webdav_part_3 as _part_3
from . import webdav_part_4 as _part_4
from . import webdav_part_5 as _part_5
from . import webdav_part_6 as _part_6
from .webdav_part_6 import *

_webdav_parts = (_part_1, _part_2, _part_3, _part_4, _part_5, _part_6,)

# Functions retain the globals of their defining module. Mirror all WebDAV
# symbols after every part is imported so forward helper calls behave exactly
# like the former single-module implementation.
for _target in _webdav_parts:
    for _source in _webdav_parts:
        _target.__dict__.update({
            _name: _value for _name, _value in vars(_source).items()
            if not _name.startswith("__")
        })


class _MirroredWebDavModule(types.ModuleType):
    """Propagate runtime patches on app.webdav to defining part modules."""

    def __setattr__(self, name, value):
        super().__setattr__(name, value)
        if name == "_webdav_parts":
            return
        for part in self.__dict__.get("_webdav_parts", ()):
            if name in part.__dict__:
                part.__dict__[name] = value


sys.modules[__name__].__class__ = _MirroredWebDavModule
