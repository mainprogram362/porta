"""Finite Qt worker; all UI changes are delivered through signals."""
from PySide6.QtCore import QThread, Signal

from runtime.runtime_activity import runtime_activity


class BrowserWorker(QThread):
    succeeded = Signal(object)
    failed = Signal(str)

    def __init__(self, operation, parent):
        super().__init__(parent)
        self.operation = operation

    def run(self):
        try:
            with runtime_activity("Firefoxとの連携"):
                self.succeeded.emit(self.operation())
        except Exception as error:
            self.failed.emit(str(error) or type(error).__name__)
