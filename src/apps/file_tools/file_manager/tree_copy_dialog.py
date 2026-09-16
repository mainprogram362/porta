"""Read-only filesystem tree snapshots shown in an editable text window."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSpinBox,
    QTextEdit,
    QVBoxLayout,
)
from gui.layout_policy import preferred_window_size


MAX_TREE_DEPTH = 4
MAX_TREE_ENTRIES = 1_000
DEFAULT_TREE_DEPTH = 3


@dataclass(frozen=True)
class TreeNode:
    """One entry captured from a directory without following links."""

    path: Path
    depth: int
    is_directory: bool
    is_symlink: bool
    children: tuple["TreeNode", ...] = ()
    notice: str | None = None


@dataclass(frozen=True)
class TreeSnapshot:
    """A bounded in-memory scan which can be rendered at depths one to four."""

    roots: tuple[TreeNode, ...]
    entry_count: int
    limit_reached: bool


@dataclass
class _ScanState:
    entry_count: int = 0
    limit_reached: bool = False


def build_tree_snapshot(
    roots: tuple[Path, ...],
    *,
    maximum_depth: int = MAX_TREE_DEPTH,
    maximum_entries: int = MAX_TREE_ENTRIES,
) -> TreeSnapshot:
    """Capture at most four levels and 1,000 child entries for later display.

    The root itself is not counted.  Directory symlinks are shown but never
    followed, so a link cycle cannot make the scan recurse indefinitely.
    """
    if not 1 <= maximum_depth <= MAX_TREE_DEPTH:
        raise ValueError(f"取得できる深さは1〜{MAX_TREE_DEPTH}層です。")
    if maximum_entries < 1:
        raise ValueError("最大件数は1件以上にしてください。")

    invalid_roots = [path for path in roots if not path.is_dir() or path.is_symlink()]
    if invalid_roots:
        raise ValueError("ツリー表示は通常のフォルダだけに使えます。")

    state = _ScanState()

    def scan_directory(path: Path, depth: int) -> tuple[tuple[TreeNode, ...], str | None]:
        if depth >= maximum_depth:
            return (), None
        try:
            entries = sorted(path.iterdir(), key=lambda entry: entry.name.casefold())
        except PermissionError:
            return (), "… 読み取り権限がないため、このフォルダの中身は取得できません。"
        except OSError as exc:
            return (), f"… 中身を取得できません: {type(exc).__name__}"

        children: list[TreeNode] = []
        for entry in entries:
            if state.entry_count >= maximum_entries:
                state.limit_reached = True
                return tuple(children), "… 表示上限1000件に達したため、以降は取得していません。"
            state.entry_count += 1
            try:
                is_symlink = entry.is_symlink()
                is_directory = entry.is_dir() and not is_symlink
            except OSError:
                is_symlink = False
                is_directory = False

            grandchildren: tuple[TreeNode, ...] = ()
            notice: str | None = None
            if is_directory:
                grandchildren, notice = scan_directory(entry, depth + 1)
            children.append(
                TreeNode(
                    path=entry,
                    depth=depth + 1,
                    is_directory=is_directory,
                    is_symlink=is_symlink,
                    children=grandchildren,
                    notice=notice,
                )
            )
            if state.limit_reached:
                return tuple(children), "… 表示上限1000件に達したため、以降は取得していません。"
        return tuple(children), None

    snapshots: list[TreeNode] = []
    for root in roots:
        if state.limit_reached:
            snapshots.append(
                TreeNode(
                    path=root,
                    depth=0,
                    is_directory=True,
                    is_symlink=False,
                    notice="… 表示上限1000件に達したため、このフォルダは取得していません。",
                )
            )
            continue
        children, notice = scan_directory(root, 0)
        snapshots.append(
            TreeNode(
                path=root,
                depth=0,
                is_directory=True,
                is_symlink=False,
                children=children,
                notice=notice,
            )
        )
    return TreeSnapshot(tuple(snapshots), state.entry_count, state.limit_reached)


def render_tree_snapshot(snapshot: TreeSnapshot, *, display_depth: int) -> str:
    """Render a frozen snapshot as ordinary editable plain text."""
    if not 1 <= display_depth <= MAX_TREE_DEPTH:
        raise ValueError(f"表示できる深さは1〜{MAX_TREE_DEPTH}層です。")

    lines: list[str] = []

    def append_notice(notice: str, prefix: str, is_last: bool) -> None:
        connector = "└── " if is_last else "├── "
        lines.append(f"{prefix}{connector}{notice}")

    def append_children(node: TreeNode, prefix: str) -> None:
        if node.notice is not None:
            append_notice(node.notice, prefix, not node.children)
        if node.depth >= display_depth:
            return
        visible = node.children
        for index, child in enumerate(visible):
            is_last = index == len(visible) - 1 and child.notice is None
            connector = "└── " if is_last else "├── "
            name = child.path.name
            if child.is_symlink:
                name += " [シンボリックリンク]"
            elif child.is_directory:
                name += "/"
            lines.append(f"{prefix}{connector}{name}")
            child_prefix = prefix + ("    " if is_last else "│   ")
            append_children(child, child_prefix)

    for index, root in enumerate(snapshot.roots, start=1):
        if lines:
            lines.append("")
        lines.append(f"[{index}] {root.path}")
        append_children(root, "")
    if snapshot.limit_reached:
        lines.extend(("", "※ 合計1000件まで取得しています。表示が途中で途切れている箇所があります。"))
    return "\n".join(lines)


class TreeCopyDialog(QDialog):
    """Independent window for browsing and editing a captured folder tree."""

    def __init__(self, roots: tuple[Path, ...]) -> None:
        super().__init__(None)
        self._snapshot = build_tree_snapshot(roots)
        self.setWindowTitle("ツリーコピー")
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setModal(False)
        self.resize(preferred_window_size(self))

        layout = QVBoxLayout(self)
        layout.addWidget(
            QLabel(
                f"対象フォルダ {len(roots)}件｜取得済み {self._snapshot.entry_count}件"
                f"｜最大{MAX_TREE_DEPTH}層・{MAX_TREE_ENTRIES}件"
            )
        )
        layout.addWidget(QLabel("頂点はフルパスです。表示する深さを変更すると、下の編集内容は取得済みのツリーで置き換わります。"))

        controls = QHBoxLayout()
        controls.addWidget(QLabel("表示する深さ"))
        self.depth_spin = QSpinBox()
        self.depth_spin.setRange(1, MAX_TREE_DEPTH)
        self.depth_spin.setValue(DEFAULT_TREE_DEPTH)
        self.depth_spin.setSuffix(" 層")
        self.depth_spin.valueChanged.connect(self._refresh_text)
        controls.addWidget(self.depth_spin)
        controls.addStretch(1)
        copy_button = QPushButton("表示内容をコピー")
        copy_button.clicked.connect(self._copy_text)
        controls.addWidget(copy_button)
        close_button = QPushButton("閉じる")
        close_button.clicked.connect(self.close)
        controls.addWidget(close_button)
        layout.addLayout(controls)

        self.text_edit = QTextEdit()
        self.text_edit.setAcceptRichText(False)
        self.text_edit.setPlainText(render_tree_snapshot(self._snapshot, display_depth=DEFAULT_TREE_DEPTH))
        layout.addWidget(self.text_edit, 1)

    def _refresh_text(self) -> None:
        self.text_edit.setPlainText(
            render_tree_snapshot(self._snapshot, display_depth=self.depth_spin.value())
        )

    def _copy_text(self) -> None:
        QApplication.clipboard().setText(self.text_edit.toPlainText())
