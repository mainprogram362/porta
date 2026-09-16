"""Compatibility import; implementation lives in settings.user_space."""
from importlib import import_module as _import_module
import sys as _sys

_sys.modules[__name__] = _import_module("settings.user_space")
