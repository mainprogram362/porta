"""Deliver startup and forwarded requests without replacing existing work."""
from __future__ import annotations

from collections import deque

from PySide6.QtCore import QObject, QTimer, Qt
from PySide6.QtWidgets import QApplication, QMessageBox

from runtime.single_instance import validate_request


def activate_window(window) -> None:
    # Preserve maximization/fullscreen while removing only minimization.
    window.setWindowState(window.windowState() & ~Qt.WindowState.WindowMinimized)
    window.show()
    target = QApplication.activeModalWidget() or window
    target.raise_()
    target.activateWindow()


class LaunchRequests(QObject):
    def __init__(self, window) -> None:
        super().__init__(window)
        self.window = window
        self._pending: deque[dict] = deque()
        self._dispatching = False
        self._menu_pending = False
        self._timer = QTimer(self)
        self._timer.setInterval(100)
        self._timer.timeout.connect(self._drain)

    def submit(self, request: dict) -> None:
        validate_request(request)
        has_work = any(request[field] is not None for field in ("target", "record_id", "record_output"))
        if has_work and len(self._pending) >= 32:
            raise ValueError("起動要求が混み合っています。")
        if not has_work:
            if QApplication.activeModalWidget() is not None:
                self._menu_pending = True
                self._timer.start()
            else:
                self.window.return_to_menu_if_navigation()
        activate_window(self.window)
        if has_work:
            self._pending.append(request)
            self._timer.start()

    def _drain(self) -> None:
        # Dialog exec() runs a nested event loop. Keep subsequent requests in
        # order until both the active operation and its modal dialog finish.
        if self._dispatching or QApplication.activeModalWidget() is not None:
            return
        if self._menu_pending:
            self._menu_pending = False
            self.window.return_to_menu_if_navigation()
        if not self._pending:
            self._timer.stop()
            return
        self._dispatching = True
        try:
            self._open(self._pending.popleft())
        except Exception:
            QMessageBox.warning(self.window, "起動要求を開けません", "受け取った画面を開けませんでした。既存の作業はそのまま残っています。")
        finally:
            self._dispatching = False

    def _open(self, request: dict) -> None:
        from foundation.external_open import build_external_open_request, ExternalOpenValidationError

        # Recheck paths at use time: files may disappear while a dialog is open.
        external = None
        if request["target"] is not None:
            try:
                external = build_external_open_request(request["target"], request["paths"])
            except ExternalOpenValidationError as exc:
                QMessageBox.warning(self.window, "外部パスを開けません", exc.summary())
                return
        if request["record_output"]:
            from records import record_service
            response = record_service.request({"action": "output", "id": request["record_output"]})
            if response.get("ok") and isinstance(response.get("text"), str):
                self.window.receive_record_bundle_output(response["text"])
            else:
                QMessageBox.warning(self.window, "出力を取得できません", "元の対応表に接続できないか、出力を作成できませんでした。")
        if request["record_id"]:
            from apps.launcher.catalog import app_for_key
            self.window._open_local_work(app_for_key("file_manager"), record_id=request["record_id"])
        if external is not None:
            self.window.open_external_paths(external.target, external.paths)
