import sys
from importlib import import_module
sys.modules[__name__] = import_module("apps.porta_control.configuration.window")
