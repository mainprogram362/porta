"""Qt lifecycle adapters for the Qt-independent process inventory."""
from PySide6.QtCore import QObject, QTimer

from runtime.instance_presence import InstancePresence
from runtime.process_registry import get_registry


def track_qprocess(process, label: str) -> None:
    """Connect before start, including synchronous-start and failed-start cases."""
    registry = get_registry()
    process.started.connect(lambda: registry.track(int(process.processId()), label))
    # The observer confirms exit from /proc, also if the QObject is destroyed.
    # No registration is made for FailedToStart (PID 0).


class PresenceHeartbeat(QObject):
    def __init__(self, presence: InstancePresence, parent: QObject) -> None:
        super().__init__(parent)
        self.presence = presence
        self.timer = QTimer(self)
        self.timer.setInterval(2000)
        self.timer.timeout.connect(presence.heartbeat)
        self.destroyed.connect(lambda: presence.close())
        presence.heartbeat()
        self.timer.start()

    def close(self) -> None:
        self.timer.stop()
        self.presence.close()
