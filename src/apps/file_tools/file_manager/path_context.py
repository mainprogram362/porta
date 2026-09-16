"""File-manager-specific additions to a reusable path-list context menu."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import QMenu, QTreeWidgetItem
from gui.path_list_context import add_favorite_paths_menu, add_path_expansion_menu


# Compatibility name retained for existing file-manager callers.
add_registered_paths_menu = add_favorite_paths_menu


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
    show_one_tree: Callable[[Path], None] | None = None,
    show_checked_trees: Callable[[], None] | None = None,
    show_selected_trees: Callable[[], None] | None = None,
    show_all_trees: Callable[[], None] | None = None,
    checked_tree_directory_count: int = 0,
    selected_tree_directory_count: int = 0,
    all_tree_directory_count: int = 0,
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
        if copy_one_real_item is not None and item is not None and item.text(1):
            selected_path = Path(item.text(1))
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
        if compress_one_real_item is not None and item is not None and item.text(1):
            compression_path = Path(item.text(1))
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
        if extract_one_archive is not None and item is not None and item.text(1):
            archive_path = Path(item.text(1))
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
            show_one_tree,
            show_checked_trees,
            show_selected_trees,
            show_all_trees,
        )
    ):
        copy_menu = QMenu("パス・ファイル名コピー", menu)
        menu.addMenu(copy_menu)
        for prefix, one, checked, selected, all_items in (
            ("パス", copy_one_path, copy_checked_paths, copy_selected_paths, copy_all_paths),
            ("ファイル名", copy_one_file_name, copy_checked_file_names, copy_selected_file_names, copy_all_file_names),
        ):
            if one is not None and item is not None and item.text(1):
                path = Path(item.text(1))
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
        if any(
            callback is not None
            for callback in (
                show_one_tree,
                show_checked_trees,
                show_selected_trees,
                show_all_trees,
            )
        ):
            copy_menu.addSeparator()
            clicked_is_directory = (
                item is not None
                and item.text(1)
                and Path(item.text(1)).is_dir()
                and not Path(item.text(1)).is_symlink()
            )
            if show_one_tree is not None and item is not None and item.text(1):
                tree_path = Path(item.text(1))
                one_tree = copy_menu.addAction("ツリー：この1件を表示…")
                one_tree.setEnabled(clicked_is_directory)
                one_tree.setToolTip("通常のフォルダだけを、最大4層・1000件までテキスト表示します。")
                one_tree.triggered.connect(
                    lambda _checked=False, path=tree_path: show_one_tree(path)
                )
            if show_checked_trees is not None:
                checked_tree = copy_menu.addAction(
                    f"ツリー：チェック済みフォルダを表示（{checked_tree_directory_count}件）…"
                )
                checked_tree.setEnabled(checked_tree_directory_count > 0)
                checked_tree.triggered.connect(show_checked_trees)
            if show_selected_trees is not None:
                selected_tree = copy_menu.addAction(
                    f"ツリー：選択中フォルダを表示（{selected_tree_directory_count}件）…"
                )
                selected_tree.setEnabled(selected_tree_directory_count > 0)
                selected_tree.triggered.connect(show_selected_trees)
            if show_all_trees is not None:
                all_tree = copy_menu.addAction(
                    f"ツリー：全件のフォルダを表示（{all_tree_directory_count}件）…"
                )
                all_tree.setEnabled(all_tree_directory_count > 0)
                all_tree.triggered.connect(show_all_trees)
    add_path_expansion_menu(
        menu,
        request_expand=request_expand,
        open_one_directory=open_one_directory,
        choose_one_directory=choose_one_directory,
    )
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
