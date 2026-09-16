"""Compatibility import; implementation lives in runtime.operation_progress."""
from importlib import import_module as _import_module
import sys as _sys

_sys.modules[__name__] = _import_module("runtime.operation_progress")
