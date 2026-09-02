"""File-manager-specific additions to a reusable path-list context menu."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from PySide6.QtWidgets import QMenu, QTreeWidgetItem


def add_registered_paths_menu(
    menu: QMenu,
    paths: list[str],
    *,
    choose_path: Callable[[str], None],
    title: str = "登録パスを追加",
) -> None:
    """Append an explicit local-path chooser only when usable entries exist."""
    if not paths:
        return
    if menu.actions():
        menu.addSeparator()
    # Keep an explicit Qt parent.  Context menus are built on demand and a
    # submenu created only from a Python return value can otherwise vanish
    # before its action is triggered.
    registered_menu = QMenu(title, menu)
    menu.addMenu(registered_menu)
    registered_menu.setToolTipsVisible(True)
    for path_text in paths:
        action = registered_menu.addAction(path_text)
        action.setToolTip(path_text)
        action.triggered.connect(
            lambda _checked=False, value=path_text: choose_path(value)
        )


def add_file_manager_path_actions(
    menu: QMenu,
    item: QTreeWidgetItem | None,
    *,
    request_expand: Callable[[str, bool], None],
    open_one_directory: Callable[[], None] | None = None,
    choose_one_directory: Callable[[], None] | None = None,
    send_to_media_information: Callable[[], None] | None = None,
    send_to_video_encoder: Callable[[], None] | None = None,
    copy_one_path: Callable[[Path], None] | None = None,
    copy_checked_paths: Callable[[], None] | None = None,
    copy_selected_paths: Callable[[], None] | None = None,
    copy_all_paths: Callable[[], None] | None = None,
    copy_one_file_name: Callable[[Path], None] | None = None,
    copy_checked_file_names: Callable[[], None] | None = None,
    copy_selected_file_names: Callable[[], None] | None = None,
    copy_all_file_names: Callable[[], None] | None = None,
    copy_one_real_item: Callable[[Path], None] | None = None,
    copy_checked_real_items: Callable[[], None] | None = None,
    copy_selected_real_items: Callable[[], None] | None = None,
    checked_real_item_count: int = 0,
    selected_real_item_count: int = 0,
    compress_one_real_item: Callable[[Path], None] | None = None,
    compress_checked_real_items: Callable[[], None] | None = None,
    compress_selected_real_items: Callable[[], None] | None = None,
    checked_compression_item_count: int = 0,
    selected_compression_item_count: int = 0,
    extract_one_archive: Callable[[Path], None] | None = None,
    extract_checked_archives: Callable[[], None] | None = None,
    extract_selected_archives: Callable[[], None] | None = None,
    checked_archive_count: int = 0,
    selected_archive_count: int = 0,
) -> None:
    """Append local file-manager actions while leaving generic list actions untouched."""
    menu.addSeparator()
    if any(
        callback is not None
        for callback in (
            copy_one_real_item,
            copy_checked_real_items,
            copy_selected_real_items,
            compress_one_real_item,
            compress_checked_real_items,
            compress_selected_real_items,
            extract_one_archive,
            extract_checked_archives,
            extract_selected_archives,
        )
    ):
        real_actions = QMenu("実体操作", menu)
        menu.addMenu(real_actions)
        if copy_one_real_item is not None and item is not None and item.text(1).strip():
            selected_path = Path(item.text(1).strip())
            display_name = selected_path.name or str(selected_path)
            one_action = real_actions.addAction(f"この1件のみをコピー（{display_name}）…")
            one_action.setToolTip(f"この項目の実体だけをコピーします: {selected_path}")
            one_action.triggered.connect(
                lambda _checked=False, path=selected_path: copy_one_real_item(path)
            )
        if copy_checked_real_items is not None:
            checked_action = real_actions.addAction(
                f"チェック済み{checked_real_item_count}件すべてをコピー…"
            )
            checked_action.setEnabled(checked_real_item_count > 0)
            checked_action.setToolTip("チェック欄が有効な項目の実体を、独立したコピー画面で処理します。")
            checked_action.triggered.connect(copy_checked_real_items)
        if copy_selected_real_items is not None:
            selected_action = real_actions.addAction(
                f"選択中{selected_real_item_count}件をコピー…"
            )
            selected_action.setEnabled(selected_real_item_count > 0)
            selected_action.setToolTip("青く選択中の項目だけを、独立したコピー画面で処理します。")
            selected_action.triggered.connect(copy_selected_real_items)
        if compress_one_real_item is not None or compress_checked_real_items is not None:
            real_actions.addSeparator()
        if compress_one_real_item is not None and item is not None and item.text(1).strip():
            compression_path = Path(item.text(1).strip())
            compress_action = real_actions.addAction(
                f"この1件を圧縮（{compression_path.name}）…"
            )
            compress_action.setToolTip(
                f"この項目を7zまたはZIPとして圧縮します: {compression_path}"
            )
            compress_action.triggered.connect(
                lambda _checked=False, path=compression_path: compress_one_real_item(path)
            )
        if compress_checked_real_items is not None:
            checked_compress_action = real_actions.addAction(
                f"チェック済み{checked_compression_item_count}件を圧縮…"
            )
            checked_compress_action.setEnabled(checked_compression_item_count > 0)
            checked_compress_action.setToolTip(
                "チェック済みの実体を、独立した圧縮画面で7zまたはZIPにします。"
            )
            checked_compress_action.triggered.connect(compress_checked_real_items)
        if compress_selected_real_items is not None:
            selected_compress_action = real_actions.addAction(
                f"選択中{selected_compression_item_count}件を圧縮…"
            )
            selected_compress_action.setEnabled(selected_compression_item_count > 0)
            selected_compress_action.setToolTip("青く選択中の実体だけを圧縮します。")
            selected_compress_action.triggered.connect(compress_selected_real_items)
        if extract_one_archive is not None or extract_checked_archives is not None:
            real_actions.addSeparator()
        if extract_one_archive is not None and item is not None and item.text(1).strip():
            archive_path = Path(item.text(1).strip())
            extract_action = real_actions.addAction(
                f"この1件を解凍（{archive_path.name}）…"
            )
            extract_action.setToolTip(
                f"同梱された解凍エンジンで、この圧縮ファイルを解凍します: {archive_path}"
            )
            extract_action.triggered.connect(
                lambda _checked=False, path=archive_path: extract_one_archive(path)
            )
        if extract_checked_archives is not None:
            checked_extract_action = real_actions.addAction(
                f"チェック済み{checked_archive_count}件の圧縮ファイルを解凍…"
            )
            checked_extract_action.setEnabled(checked_archive_count > 0)
            checked_extract_action.setToolTip(
                "チェック済みのZIP・7z・RARを、独立した解凍画面で処理します。"
            )
            checked_extract_action.triggered.connect(extract_checked_archives)
        if extract_selected_archives is not None:
            selected_extract_action = real_actions.addAction(
                f"選択中{selected_archive_count}件の圧縮ファイルを解凍…"
            )
            selected_extract_action.setEnabled(selected_archive_count > 0)
            selected_extract_action.setToolTip("青く選択中のZIP・7z・RARだけを解凍します。")
            selected_extract_action.triggered.connect(extract_selected_archives)
    if any(
        callback is not None
        for callback in (
            copy_one_path,
            copy_checked_paths,
            copy_selected_paths,
            copy_all_paths,
            copy_one_file_name,
            copy_checked_file_names,
            copy_selected_file_names,
            copy_all_file_names,
        )
    ):
        copy_menu = QMenu("パス・ファイル名コピー", menu)
        menu.addMenu(copy_menu)
        for prefix, one, checked, selected, all_items in (
            ("パス", copy_one_path, copy_checked_paths, copy_selected_paths, copy_all_paths),
            ("ファイル名", copy_one_file_name, copy_checked_file_names, copy_selected_file_names, copy_all_file_names),
        ):
            if one is not None and item is not None and item.text(1).strip():
                path = Path(item.text(1).strip())
                one_action = copy_menu.addAction(f"{prefix}：この1件をコピー")
                one_action.triggered.connect(lambda _checked=False, value=path, callback=one: callback(value))
            if checked is not None:
                checked_action = copy_menu.addAction(f"{prefix}：チェック済みをコピー")
                checked_action.triggered.connect(checked)
            if selected is not None:
                selected_action = copy_menu.addAction(f"{prefix}：選択中をコピー")
                selected_action.triggered.connect(selected)
            if all_items is not None:
                all_action = copy_menu.addAction(f"{prefix}：全件をコピー")
                all_action.triggered.connect(all_items)
    expand_menu = QMenu("展開", menu)
    menu.addMenu(expand_menu)
    if open_one_directory is not None:
        open_action = expand_menu.addAction("この1件を開く（一覧全体を置換）")
        open_action.triggered.connect(open_one_directory)
    if choose_one_directory is not None:
        choose_action = expand_menu.addAction("この1件の直下を選んで展開…")
        choose_action.triggered.connect(choose_one_directory)
    if open_one_directory is not None or choose_one_directory is not None:
        expand_menu.addSeparator()
    checked_expand = expand_menu.addAction("チェック済みフォルダを展開")
    checked_expand.triggered.connect(lambda: request_expand("checked", False))
    checked_query = expand_menu.addAction("チェック済みに条件を指定して展開…")
    checked_query.triggered.connect(lambda: request_expand("checked", True))
    selected_expand = expand_menu.addAction("選択中フォルダを展開")
    selected_expand.triggered.connect(lambda: request_expand("selected", False))
    selected_query = expand_menu.addAction("選択中に条件を指定して展開…")
    selected_query.triggered.connect(lambda: request_expand("selected", True))
    if send_to_media_information is not None or send_to_video_encoder is not None:
        # Give the submenu an explicit Qt parent.  The context menu is built
        # on demand, so parent ownership is needed after this helper returns.
        handoff_menu = QMenu("他のツールへ送る", menu)
        menu.addMenu(handoff_menu)
        if send_to_media_information is not None:
            media_action = handoff_menu.addAction("メディア情報整理へ送る")
            media_action.setToolTip("チェック済みのパスを一時的に渡して、メディア情報整理を直接開きます。")
            media_action.triggered.connect(send_to_media_information)
        if send_to_video_encoder is not None:
            encoder_action = handoff_menu.addAction("動画変換へ送る")
            encoder_action.setToolTip("チェック済みのパスを一時的に渡して、動画変換を直接開きます。")
            encoder_action.triggered.connect(send_to_video_encoder)
