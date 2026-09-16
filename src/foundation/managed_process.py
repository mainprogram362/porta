"""Compatibility import; implementation lives in runtime.managed_process."""
from importlib import import_module as _import_module
import sys as _sys

_sys.modules[__name__] = _import_module("runtime.managed_process")
