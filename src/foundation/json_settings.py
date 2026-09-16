"""Compatibility import; implementation lives in settings.json_settings."""
from importlib import import_module as _import_module
import sys as _sys

_sys.modules[__name__] = _import_module("settings.json_settings")
