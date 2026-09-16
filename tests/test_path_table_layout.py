"""Rendered path-table geometry at both compact and expanded window sizes."""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QDialog, QVBoxLayout

from apps.media_tools.media_information.window import MediaInformationScreen
from gui.composites import PathListInput


def _visible_column_width(widget: PathListInput) -> int:
    tree = widget._tree
    return sum(
        tree.columnWidth(column)
        for column in range(tree.columnCount())
        if not tree.isColumnHidden(column)
    )


def _show_in_dialog(widget: PathListInput, *, width: int) -> QDialog:
    dialog = QDialog()
    layout = QVBoxLayout(dialog)
    layout.addWidget(widget)
    dialog.resize(width, 360)
    dialog.show()
    QApplication.instance().processEvents()
    return dialog


def test_full_path_table_fills_empty_space_and_keeps_fixed_right_controls():
    QApplication.instance() or QApplication([])
    paths = PathListInput(supplemental_column_label="紐づけ", show_column_headers=True)
    paths.set_supplemental_column_fixed("紐づけ", 235)
    paths.show_full_paths()
    dialog = _show_in_dialog(paths, width=900)
    tree = paths._tree
    header = tree.header()
    controls = (tree.columnWidth(0), tree.columnWidth(paths._state_column), tree.columnWidth(paths._remove_column))
    try:
        assert _visible_column_width(paths) == tree.viewport().width()
        assert tree.horizontalScrollBar().maximum() == 0

        # Resizing the actual window keeps the table flush on both sides.
        dialog.resize(540, 360)
        QApplication.instance().processEvents()
        assert _visible_column_width(paths) == tree.viewport().width()
        assert (tree.columnWidth(0), tree.columnWidth(paths._state_column), tree.columnWidth(paths._remove_column)) == controls

        # A header-edge drag cannot make a gap. Enlarging the same edge keeps
        # the fixed action columns and exposes the extra width by scrolling.
        minimum = paths._full_path_viewport_width()
        header.resizeSection(1, max(1, minimum - 80))
        QApplication.instance().processEvents()
        assert tree.columnWidth(1) >= paths._full_path_viewport_width()
        header.resizeSection(1, paths._full_path_viewport_width() + 180)
        QApplication.instance().processEvents()
        assert tree.horizontalScrollBar().maximum() > 0
        assert (tree.columnWidth(0), tree.columnWidth(paths._state_column), tree.columnWidth(paths._remove_column)) == controls
    finally:
        dialog.close()


def test_hiding_a_fixed_supplemental_column_does_not_leave_a_ghost_gap():
    QApplication.instance() or QApplication([])
    paths = PathListInput(supplemental_column_label="紐づけ", show_column_headers=True)
    paths.set_supplemental_column_fixed("紐づけ", 235)
    paths.set_supplemental_column_visible("紐づけ", False)
    paths.show_full_paths()
    dialog = _show_in_dialog(paths, width=900)
    tree = paths._tree
    header = tree.header()
    try:
        assert header.sectionPosition(paths._state_column) == tree.columnWidth(0) + tree.columnWidth(1)
        assert header.sectionPosition(paths._remove_column) == header.sectionPosition(paths._state_column) + tree.columnWidth(paths._state_column)
        assert _visible_column_width(paths) == tree.viewport().width()
        assert tree.horizontalScrollBar().maximum() == 0
    finally:
        dialog.close()


def test_full_path_table_grows_for_a_long_value_then_uses_horizontal_scroll():
    QApplication.instance() or QApplication([])
    paths = PathListInput(show_column_headers=True)
    paths.show_full_paths()
    dialog = _show_in_dialog(paths, width=760)
    tree = paths._tree
    try:
        paths.setPlainText("/tmp/" + "very-long-directory-name/" * 100 + "movie.mkv")
        QApplication.instance().processEvents()
        assert tree.columnWidth(1) > paths._full_path_viewport_width()
        assert tree.horizontalScrollBar().maximum() > 0
        assert tree.textElideMode().name == "ElideNone"
    finally:
        dialog.close()


def test_media_information_stretches_the_last_visible_fact_or_the_path():
    QApplication.instance() or QApplication([])
    screen = MediaInformationScreen(lambda: None)
    screen.resize(1200, 800)
    screen.show()
    QApplication.instance().processEvents()
    paths = screen.path_input
    tree = paths._tree
    try:
        assert _visible_column_width(paths) == tree.viewport().width()
        assert tree.header().sectionResizeMode(paths.supplemental_column_index("見どころ")).name == "Stretch"

        # Hiding the final visible fact hands spare width to the next one.
        screen._set_path_table_column_visible("見どころ", False)
        QApplication.instance().processEvents()
        assert _visible_column_width(paths) == tree.viewport().width()
        assert tree.header().sectionResizeMode(paths.supplemental_column_index("評価")).name == "Stretch"

        # With no optional facts, the full path is the only sensible stretch.
        screen._set_path_table_column_visible("評価", False)
        screen._set_path_table_column_visible("サイズ", False)
        screen.resize(700, 800)
        QApplication.instance().processEvents()
        assert _visible_column_width(paths) == tree.viewport().width()
        assert tree.header().sectionResizeMode(1).name == "Stretch"
    finally:
        screen.close()
