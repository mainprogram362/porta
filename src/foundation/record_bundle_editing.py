"""Compatibility import; implementation lives in records.record_bundle_editing."""
from importlib import import_module as _import_module
import sys as _sys

_sys.modules[__name__] = _import_module("records.record_bundle_editing")
