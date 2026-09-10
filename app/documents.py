"""Authenticated document pages assembled from focused route modules."""
from __future__ import annotations

from .documents_core import *  # noqa: F401,F403
from . import documents_routes_content as _routes_1
from . import documents_routes_workflows as _routes_2
from . import documents_routes_admin as _routes_3

_route_modules = (_routes_1, _routes_2, _routes_3,)
# Functions keep the globals of their defining route module. Mirror route names
# between those modules so the rare direct cross-route call remains compatible.
for _target in _route_modules:
    for _source in _route_modules:
        _target.__dict__.update({
            _name: _value for _name, _value in vars(_source).items()
            if not _name.startswith("__")
        })
for _source in _route_modules:
    globals().update({
        _name: _value for _name, _value in vars(_source).items()
        if not _name.startswith("__")
    })
