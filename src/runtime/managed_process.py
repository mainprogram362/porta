"""Observe launches without changing command arguments or cancellation policy."""
from __future__ import annotations

import subprocess

from runtime.process_registry import get_registry
from runtime.runtime_activity import runtime_activity


def popen(*args, label: str, role: str = 'worker', ownership: str = 'owned', **kwargs):
    process = subprocess.Popen(*args, **kwargs)
    get_registry().track(getattr(process, 'pid', None), label, role=role, ownership=ownership)
    return process


def run(*args, label: str, **kwargs):
    # The observer discovers descendants of third-party/synchronous commands;
    # short-lived probes leave no completed-process history.
    with runtime_activity(label):
        return subprocess.run(*args, **kwargs)
