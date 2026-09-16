"""A finite operation whose signals are delivered back to the GUI thread."""
from PySide6.QtCore import QThread, Signal
from runtime.operation_progress import OperationCancelled, OperationFailure, OperationRecord, observe_operation
from runtime.runtime_activity import runtime_activity


class OperationWorker(QThread):
    succeeded = Signal(object)
    failed = Signal(object)
    progress = Signal(str)

    def __init__(self, operation, parent=None):
        super().__init__(parent)
        self.operation = operation
        self.records = []

    def _observe(self, message):
        if isinstance(message, OperationRecord):
            self.records.append(message)
            self.progress.emit(f"{message.state}: {message.source}")
            return
        if self.isInterruptionRequested():
            raise OperationCancelled("取消要求を受け付けました。完了済みの出力は保持します。")
        if message:
            self.progress.emit(message)

    def run(self):
        try:
            with runtime_activity("ファイル操作中"), observe_operation(self._observe):
                self._observe("")
                result = self.operation()
            self.succeeded.emit(result)
        except Exception as exc:
            self.failed.emit(OperationFailure(str(exc) or type(exc).__name__, self.records,
                                             cancelled=self.isInterruptionRequested()))
