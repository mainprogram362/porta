"""Per-worker progress and cooperative cancellation; no Qt dependency."""
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass

_observer = ContextVar("porta_operation_observer", default=None)


class OperationCancelled(OSError):
    pass


@dataclass(frozen=True)
class OperationRecord:
    source: str
    output: str
    state: str


class OperationFailure(OSError):
    def __init__(self, message, records=(), *, cancelled=False):
        super().__init__(message)
        self.records = tuple(records)
        self.cancelled = cancelled


def completed(source, output, state):
    observer = _observer.get()
    if observer is not None:
        observer(OperationRecord(str(source), str(output), state))


def checkpoint(message: str = "") -> None:
    observer = _observer.get()
    if observer is not None:
        observer(message)


@contextmanager
def observe_operation(observer):
    token = _observer.set(observer)
    try:
        yield
    finally:
        _observer.reset(token)
