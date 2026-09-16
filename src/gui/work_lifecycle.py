"""Recognize owned Qt work before disposing its UI owner."""
from PySide6.QtCore import QThread, QProcess
from PySide6.QtWidgets import QWidget


def running_work(owner):
    jobs = []
    for container in [owner, *_retained_windows(owner)]:
        jobs.extend(thread for thread in container.findChildren(QThread) if thread.isRunning())
        jobs.extend(process for process in container.findChildren(QProcess)
                    if process.state() != QProcess.ProcessState.NotRunning)
    return list(dict.fromkeys(jobs))


def dependent_windows(owner):
    """Include explicitly retained modeless windows which have no Qt parent."""
    return [window for window in _retained_windows(owner) if window.isVisible()]


def _retained_windows(owner):
    windows = [widget for widget in owner.findChildren(QWidget) if widget.isWindow()]
    for value in vars(owner).values():
        items = value.values() if isinstance(value, dict) else value if isinstance(value, (set, list, tuple)) else (value,)
        for item in items:
            if isinstance(item, QWidget):
                try:
                    if item is not owner and item.isWindow():
                        windows.append(item)
                except RuntimeError:
                    pass  # An already-deleted WA_DeleteOnClose window.
    return list(dict.fromkeys(windows))
