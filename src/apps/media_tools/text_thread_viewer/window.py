"""Read-only multi-thread text viewer with reply and cross-thread navigation."""

from __future__ import annotations

from collections.abc import Callable
from html import escape
import re

from PySide6.QtCore import QTimer, Qt, QUrl
from PySide6.QtGui import QCursor, QPalette
from PySide6.QtWidgets import (
    QDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTextBrowser,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

from gui import AppHeader, AppPageLayout, NoWheelComboBox, add_path_list_input
from media import (
    TextSearchHit,
    TextThreadDocument,
    TextThreadLoadResult,
    TextThreadReply,
    TextThreadReplyTree,
    find_text_hits,
    load_shitaraba_saved_texts,
    reply_descendant_tree,
)


_REFERENCE = re.compile(r">>\s*(?P<number>\d+)(?:\s*-\s*\d+)?")


def _html_colors(widget: QWidget) -> dict[str, str]:
    """Translate the active Qt palette into safe QTextBrowser HTML colors."""
    palette = widget.palette()
    role = QPalette.ColorRole
    return {
        "base": palette.color(role.Base).name(),
        "alternate": palette.color(role.AlternateBase).name(),
        "text": palette.color(role.Text).name(),
        "border": palette.color(role.Mid).name(),
        "accent": palette.color(role.Highlight).name(),
        "link": palette.color(role.Link).name(),
        "muted": palette.color(role.PlaceholderText).name(),
    }


def _html_document(widget: QWidget, blocks: str) -> str:
    colors = _html_colors(widget)
    return (
        "<html><head><style>"
        f"body {{ background:{colors['base']}; color:{colors['text']}; }}"
        f"a {{ color:{colors['link']}; }}"
        "</style></head><body>"
        f"{blocks}</body></html>"
    )


def _reference_preview(document: TextThreadDocument, link: str | QUrl) -> str:
    """Return a compact referenced-response preview for a ``>>number`` link."""
    url = link if isinstance(link, QUrl) else QUrl(link)
    number_text = url.path().lstrip("/") or url.host()
    if url.scheme() != "reply" or not number_text.isdigit():
        return ""
    number = int(number_text)
    reply = next((candidate for candidate in document.replies if candidate.number == number), None)
    if reply is None:
        return ""
    metadata = " / ".join(
        value for value in (reply.name, reply.posted_at, reply.poster_id) if value
    )
    body = reply.body.strip()
    if len(body) > 700:
        body = body[:700].rstrip() + "…"
    return f"[{reply.number}] {metadata}\n{body}"


class TextThreadViewerScreen(QWidget):
    """View only explicitly loaded response text; never edit or persist it."""

    def __init__(self, return_to_main: Callable[[], None]) -> None:
        super().__init__()
        self._return_to_main = return_to_main
        self._documents: tuple[TextThreadDocument, ...] = ()
        self._current_document_index = 0
        self._current_reply_number: int | None = None
        self._expanded_replies: set[tuple[int, int]] = set()
        self._search_hits: tuple[TextSearchHit, ...] = ()
        self._search_hit_index = -1
        self._search_result_windows: list[QDialog] = []
        self._viewer_is_expanded = False
        self._build_ui()

    def _build_ui(self) -> None:
        layout = AppPageLayout(self)

        self.setup_widget = AppHeader(self._return_to_main, title="テキストスレッド")
        setup_row = self.setup_widget.content_layout
        setup_row.addStretch(1)
        setup_row.addWidget(QLabel("読み取りモード"))
        self.mode_combo = NoWheelComboBox()
        self.mode_combo.addItem("したらば掲示板 (.txt)", "shitaraba_text")
        self.mode_combo.setToolTip("現在は、このアプリで保存したしたらばレス用テキストだけを読み取れます。")
        self.mode_combo.currentIndexChanged.connect(self._mode_changed)
        setup_row.addWidget(self.mode_combo)
        layout.addWidget(self.setup_widget)
        self.status_label = QLabel("テキストを追加して「読み込む」を押してください。読み込み内容は今回だけ保持します。")
        self.status_label.setWordWrap(True)
        self.status_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.status_label)

        workspace = QHBoxLayout()
        source_box = QGroupBox("読み込むテキスト")
        self.source_box = source_box
        source_layout = QVBoxLayout(source_box)
        self.source_input = add_path_list_input(
            source_layout,
            rows=12,
            placeholder="保存した .txt を追加またはドロップします。複数追加できます。",
            accepted_path_kind="all",
            show_controls=False,
            path_column_label="テキストパス",
        )
        self.source_input.textChanged.connect(self._source_paths_changed)
        source_actions = QHBoxLayout()
        clear_sources = QPushButton("一覧を空にする")
        clear_sources.clicked.connect(self.source_input.clear_items)
        source_actions.addWidget(clear_sources)
        self.load_button = QPushButton("読み込む")
        self.load_button.setToolTip("一覧の .txt を今回だけ読み込みます。ファイルは変更しません。")
        self.load_button.clicked.connect(self.load_texts)
        source_actions.addWidget(self.load_button)
        self.reload_button = QPushButton("再読み込み")
        self.reload_button.setToolTip("同じパスから現在の内容を読み直します。")
        self.reload_button.clicked.connect(self.load_texts)
        source_actions.addWidget(self.reload_button)
        source_layout.addLayout(source_actions)
        workspace.addWidget(source_box, 1)

        viewer_box = QGroupBox("レスビューア")
        self.viewer_box = viewer_box
        viewer_layout = QVBoxLayout(viewer_box)
        thread_row = QHBoxLayout()
        previous_thread = QPushButton("前スレッド")
        previous_thread.clicked.connect(lambda: self._move_document(-1))
        thread_row.addWidget(previous_thread)
        self.thread_combo = NoWheelComboBox()
        self.thread_combo.setEnabled(False)
        self.thread_combo.currentIndexChanged.connect(self._thread_changed)
        thread_row.addWidget(self.thread_combo, 1)
        next_thread = QPushButton("次スレッド")
        next_thread.clicked.connect(lambda: self._move_document(1))
        thread_row.addWidget(next_thread)
        self.thread_info_label = QLabel("未読込")
        thread_row.addWidget(self.thread_info_label)
        self.expand_viewer_button = QPushButton("閲覧を広げる")
        self.expand_viewer_button.setToolTip("入力一覧などを隠し、閲覧と検索だけを広く表示します。")
        self.expand_viewer_button.clicked.connect(self._toggle_expanded_viewer)
        thread_row.addWidget(self.expand_viewer_button)
        viewer_layout.addLayout(thread_row)

        search_row = QHBoxLayout()
        search_row.addWidget(QLabel("検索"))
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("読み込み済みの全スレッドを検索")
        self.search_input.textChanged.connect(self._search_changed)
        search_row.addWidget(self.search_input, 1)
        previous_hit = QPushButton("前のヒット")
        previous_hit.clicked.connect(lambda: self._move_search_hit(-1))
        search_row.addWidget(previous_hit)
        next_hit = QPushButton("次のヒット")
        next_hit.clicked.connect(lambda: self._move_search_hit(1))
        search_row.addWidget(next_hit)
        self.open_search_results_button = QPushButton("ヒット一覧を別ウィンドウ")
        self.open_search_results_button.setToolTip("現在の検索語で、各スレッドのヒットしたレスだけを別ウィンドウに表示します。")
        self.open_search_results_button.clicked.connect(self._open_search_results_window)
        search_row.addWidget(self.open_search_results_button)
        self.search_info_label = QLabel("検索語を入力")
        search_row.addWidget(self.search_info_label)
        viewer_layout.addLayout(search_row)

        self.reply_browser = QTextBrowser()
        self.reply_browser.setOpenLinks(False)
        self.reply_browser.setOpenExternalLinks(False)
        self.reply_browser.setPlaceholderText("読み込み後、レス番号・返信・検索結果をここで確認できます。")
        self.reply_browser.anchorClicked.connect(self._browser_link_clicked)
        self.reply_browser.highlighted.connect(self._reference_hovered)
        viewer_layout.addWidget(self.reply_browser, 1)
        workspace.addWidget(viewer_box, 2)
        layout.addLayout(workspace, 1)

    def _mode_changed(self) -> None:
        self._clear_loaded_documents("読み取りモードを変更しました。テキストを読み込み直してください。")

    def _source_paths_changed(self) -> None:
        if self._documents:
            self._clear_loaded_documents("一覧が変わりました。「読み込む」で現在のファイルを反映します。")

    def _clear_loaded_documents(self, message: str) -> None:
        self._documents = ()
        self._current_document_index = 0
        self._current_reply_number = None
        self._expanded_replies.clear()
        self._search_hits = ()
        self._search_hit_index = -1
        self.thread_combo.blockSignals(True)
        self.thread_combo.clear()
        self.thread_combo.blockSignals(False)
        self.thread_combo.setEnabled(False)
        self.thread_info_label.setText("未読込")
        self.search_info_label.setText("検索語を入力")
        self.reply_browser.clear()
        self.status_label.setText(message)

    def load_texts(self) -> None:
        if self.mode_combo.currentData() != "shitaraba_text":
            self._clear_loaded_documents("この読み取りモードはまだ実装されていません。")
            return
        paths = self.source_input.paths(deduplicate=True)
        if not paths:
            self._clear_loaded_documents(".txt ファイルを1件以上追加してください。")
            return
        result = load_shitaraba_saved_texts(paths)
        self._apply_load_result(result)

    def _apply_load_result(self, result: TextThreadLoadResult) -> None:
        self._documents = result.documents
        self._current_document_index = 0
        self._current_reply_number = None
        self._expanded_replies.clear()
        self._search_hit_index = -1
        self._search_changed(self.search_input.text())
        if self._documents:
            self._render_current_document()
        else:
            self.reply_browser.clear()
        details = [f"{len(self._documents)} スレッドを読み込みました。"]
        if result.errors:
            details.extend(result.errors)
        self.status_label.setText("\n".join(details))

    def _thread_changed(self, index: int) -> None:
        if index < 0 or index >= len(self._documents):
            return
        self._current_document_index = index
        self._current_reply_number = None
        self._search_hit_index = -1
        self._update_search_info()
        self._render_current_document()

    def _move_document(self, amount: int) -> None:
        if not self._documents:
            self.status_label.setText("先にテキストを読み込んでください。")
            return
        index = (self._current_document_index + amount) % len(self._documents)
        self._search_hit_index = -1
        self._show_reply(index, None)
        self._update_search_info()

    def _toggle_expanded_viewer(self) -> None:
        """Temporarily devote the whole screen to the read-only viewer."""
        self._viewer_is_expanded = not self._viewer_is_expanded
        self.setup_widget.setVisible(not self._viewer_is_expanded)
        self.status_label.setVisible(not self._viewer_is_expanded)
        self.source_box.setVisible(not self._viewer_is_expanded)
        self.expand_viewer_button.setText(
            "通常表示に戻す" if self._viewer_is_expanded else "閲覧を広げる"
        )

    def _open_search_results_window(self) -> None:
        query = self.search_input.text().strip()
        if not query:
            self.status_label.setText("検索語を入力してから、ヒット一覧を開いてください。")
            return
        if not self._documents:
            self.status_label.setText("先にテキストを読み込んでください。")
            return
        window = TextThreadSearchResultsWindow(self._documents, query, parent=self)
        window.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        window.destroyed.connect(
            lambda *_ignored, target=window: self._search_result_windows.remove(target)
            if target in self._search_result_windows
            else None
        )
        self._search_result_windows.append(window)
        window.show()
        window.raise_()
        window.activateWindow()

    def _search_changed(self, text: str) -> None:
        self._search_hits = find_text_hits(self._documents, text)
        self._search_hit_index = -1
        self._refresh_thread_combo()
        self._update_search_info()

    def _refresh_thread_combo(self) -> None:
        """Keep the selector informative without treating hit counts as titles."""
        selected_index = self._current_document_index
        hit_counts = [0] * len(self._documents)
        for hit in self._search_hits:
            hit_counts[hit.document_index] += 1
        has_query = bool(self.search_input.text().strip())
        self.thread_combo.blockSignals(True)
        self.thread_combo.clear()
        for index, document in enumerate(self._documents):
            suffix = (
                f"ヒット {hit_counts[index]}件 / レス {len(document.replies)}件"
                if has_query
                else f"レス {len(document.replies)}件"
            )
            self.thread_combo.addItem(f"{document.title}（{suffix}）", index)
        self.thread_combo.setCurrentIndex(selected_index if self._documents else -1)
        self.thread_combo.blockSignals(False)
        self.thread_combo.setEnabled(bool(self._documents))

    def _update_search_info(self) -> None:
        """Show current-thread and all-loaded hit counts without choosing a hit."""
        if not self.search_input.text().strip():
            self.search_info_label.setText("検索語を入力")
            return
        current_count = sum(
            hit.document_index == self._current_document_index for hit in self._search_hits
        )
        self.search_info_label.setText(
            f"このスレッド {current_count}件 / 全{len(self._search_hits)}件"
        )

    def _move_search_hit(self, amount: int) -> None:
        if not self._search_hits:
            self.status_label.setText("検索ヒットがありません。")
            return
        if self._search_hit_index < 0:
            self._search_hit_index = self._first_hit_in_current_document(amount)
        else:
            self._search_hit_index = (self._search_hit_index + amount) % len(self._search_hits)
        hit = self._search_hits[self._search_hit_index]
        self._show_reply(hit.document_index, hit.reply_number)
        current_count = sum(
            candidate.document_index == hit.document_index for candidate in self._search_hits
        )
        current_position = sum(
            candidate.document_index == hit.document_index
            for candidate in self._search_hits[: self._search_hit_index + 1]
        )
        self.search_info_label.setText(
            f"このスレッド {current_position}/{current_count}件 / 全{self._search_hit_index + 1}/{len(self._search_hits)}件"
        )

    def _first_hit_in_current_document(self, amount: int) -> int:
        """Start a fresh search in the displayed thread before crossing to another."""
        current_hits = [
            index
            for index, hit in enumerate(self._search_hits)
            if hit.document_index == self._current_document_index
        ]
        if current_hits:
            return current_hits[0] if amount > 0 else current_hits[-1]

        # A thread with no hit still participates in seamless traversal: begin
        # from the closest following/preceding loaded thread rather than always
        # jumping back to the first loaded document.
        document_count = len(self._documents)
        for offset in range(1, document_count + 1):
            document_index = (self._current_document_index + amount * offset) % document_count
            candidates = [
                index
                for index, hit in enumerate(self._search_hits)
                if hit.document_index == document_index
            ]
            if candidates:
                return candidates[0] if amount > 0 else candidates[-1]
        return 0

    def _show_reply(self, document_index: int, reply_number: int | None) -> None:
        if document_index < 0 or document_index >= len(self._documents):
            return
        self._current_document_index = document_index
        self._current_reply_number = reply_number
        self.thread_combo.blockSignals(True)
        self.thread_combo.setCurrentIndex(document_index)
        self.thread_combo.blockSignals(False)
        self._render_current_document()
        if reply_number is not None:
            QTimer.singleShot(0, lambda: self.reply_browser.scrollToAnchor(f"reply-{reply_number}"))

    def _browser_link_clicked(self, url: QUrl) -> None:
        number_text = url.path().lstrip("/") or url.host()
        if not number_text.isdigit() or not self._documents:
            return
        number = int(number_text)
        if url.scheme() == "reply":
            if number in self._documents[self._current_document_index].reply_numbers:
                self._show_reply(self._current_document_index, number)
            return
        if url.scheme() == "children":
            scroll_position = self.reply_browser.verticalScrollBar().value()
            key = (self._current_document_index, number)
            if key in self._expanded_replies:
                self._expanded_replies.remove(key)
            else:
                self._expanded_replies.add(key)
            self._render_current_document()
            # 開閉では親レスへスクロールし直さない。表示中の位置のまま、
            # その直下だけを広げるための復元である。
            QTimer.singleShot(
                0,
                lambda: self.reply_browser.verticalScrollBar().setValue(scroll_position),
            )

    def _reference_hovered(self, link: str | QUrl) -> None:
        if not self._documents:
            QToolTip.hideText()
            return
        preview = _reference_preview(self._documents[self._current_document_index], link)
        if preview:
            QToolTip.showText(QCursor.pos(), preview, self.reply_browser)
        else:
            QToolTip.hideText()

    def _render_current_document(self) -> None:
        if not self._documents:
            return
        document = self._documents[self._current_document_index]
        self.thread_info_label.setText(f"{len(document.replies)} レス")
        reply_numbers = document.reply_numbers
        blocks = [self._reply_html(document, reply, reply_numbers) for reply in document.replies]
        self.reply_browser.setHtml(_html_document(self.reply_browser, "".join(blocks)))

    def _reply_html(
        self,
        document: TextThreadDocument,
        reply: TextThreadReply,
        reply_numbers: frozenset[int],
    ) -> str:
        current = (
            self._current_document_index,
            reply.number,
        ) == (self._current_document_index, self._current_reply_number)
        metadata = " / ".join(
            value for value in (reply.name, reply.posted_at, reply.poster_id) if value
        )
        colors = _html_colors(self.reply_browser)
        background = colors["alternate"] if current else colors["base"]
        border = colors["accent"] if current else colors["border"]
        children = reply_descendant_tree(document, reply.number)
        children_html = ""
        if children:
            expanded = (self._current_document_index, reply.number) in self._expanded_replies
            marker = "▲ 閉じる" if expanded else "▼ 開く"
            children_html = (
                f'<p style="margin:6px 0 0"><a href="children:///{reply.number}">'
                f"返信ツリー {self._tree_reply_count(children)}件 {marker}</a></p>"
            )
            if expanded:
                expanded_blocks = "".join(
                    self._expanded_reply_html(child, reply_numbers) for child in children
                )
                children_html += (
                    f'<div style="margin:6px 0 0 18px; border-left:3px solid {colors["accent"]}; '
                    'padding-left:7px">'
                    f"{expanded_blocks}</div>"
                )
        return (
            f'<a name="reply-{reply.number}"></a>'
            f'<div style="background:{background}; border:1px solid {border}; '
            'border-radius:4px; margin:5px 2px; padding:7px">'
            f"<b>[{reply.number}]</b> {escape(metadata)}<br>"
            f'<div style="margin-top:5px; white-space:pre-wrap">'
            f"{self._body_html(reply.body, reply_numbers)}</div>{children_html}</div>"
        )

    @staticmethod
    def _tree_reply_count(nodes: tuple[TextThreadReplyTree, ...]) -> int:
        return sum(1 + TextThreadViewerScreen._tree_reply_count(node.children) for node in nodes)

    def _expanded_reply_html(
        self,
        node: TextThreadReplyTree,
        reply_numbers: frozenset[int],
    ) -> str:
        """Render a bounded reply tree below its parent without navigating away."""
        reply = node.reply
        colors = _html_colors(self.reply_browser)
        metadata = " / ".join(
            value for value in (reply.name, reply.posted_at, reply.poster_id) if value
        )
        nested = ""
        if node.children:
            nested = (
                f'<div style="margin:5px 0 0 13px; border-left:2px solid {colors["accent"]}; '
                'padding-left:6px">'
                + "".join(self._expanded_reply_html(child, reply_numbers) for child in node.children)
                + "</div>"
            )
        if node.depth_limit_reached:
            nested += f'<p style="margin:5px 0; color:{colors["muted"]}">返信は10階層までを表示しています。</p>'
        return (
            f'<div style="background:{colors["alternate"]}; border:1px solid {colors["border"]}; '
            'border-radius:3px; margin:5px 0; padding:5px">'
            f'<a href="reply:///{reply.number}"><b>→ [{reply.number}]</b></a> '
            f"{escape(metadata)}<br>"
            f'<div style="margin-top:4px; white-space:pre-wrap">'
            f"{self._body_html(reply.body, reply_numbers)}</div>{nested}</div>"
        )

    @staticmethod
    def _body_html(body: str, reply_numbers: frozenset[int]) -> str:
        parts: list[str] = []
        cursor = 0
        for match in _REFERENCE.finditer(body):
            parts.append(escape(body[cursor : match.start()]).replace("\n", "<br>"))
            number = int(match.group("number"))
            label = escape(match.group(0))
            parts.append(
                f'<a href="reply:///{number}">{label}</a>'
                if number in reply_numbers
                else label
            )
            cursor = match.end()
        parts.append(escape(body[cursor:]).replace("\n", "<br>"))
        return "".join(parts)


class TextThreadSearchResultsWindow(QDialog):
    """Separate read-only window showing only matching replies per thread."""

    def __init__(
        self,
        documents: tuple[TextThreadDocument, ...],
        query: str,
        *,
        parent: QWidget,
    ) -> None:
        super().__init__(parent)
        self._documents = documents
        self._query = query
        self._hits = find_text_hits(documents, query)
        self._current_document_index = 0
        self._expanded_replies: set[tuple[int, int]] = set()
        self._hit_numbers_by_document = tuple(
            frozenset(
                hit.reply_number for hit in self._hits if hit.document_index == document_index
            )
            for document_index in range(len(documents))
        )
        self.setWindowTitle("検索ヒット一覧")
        self.resize(860, 620)
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        summary = QLabel(
            f"検索語: {self._query}　/　全{len(self._hits)}件。"
            "選択したスレッドのヒットしたレスだけを表示します。"
        )
        summary.setWordWrap(True)
        layout.addWidget(summary)

        selector = QHBoxLayout()
        selector.addWidget(QLabel("スレッド"))
        self.thread_combo = NoWheelComboBox()
        for index, document in enumerate(self._documents):
            count = len(self._hit_numbers_by_document[index])
            self.thread_combo.addItem(
                f"{document.title}（ヒット {count}件 / レス {len(document.replies)}件）",
                index,
            )
        self.thread_combo.currentIndexChanged.connect(self._render_selected_document)
        selector.addWidget(self.thread_combo, 1)
        layout.addLayout(selector)

        self.browser = QTextBrowser()
        self.browser.setOpenLinks(False)
        self.browser.setOpenExternalLinks(False)
        self.browser.anchorClicked.connect(self._browser_link_clicked)
        self.browser.highlighted.connect(self._reference_hovered)
        layout.addWidget(self.browser, 1)

        close_button = QPushButton("閉じる")
        close_button.clicked.connect(self.close)
        layout.addWidget(close_button, alignment=Qt.AlignmentFlag.AlignRight)
        self._render_selected_document(0)

    def _render_selected_document(self, index: int) -> None:
        if index < 0 or index >= len(self._documents):
            self.browser.clear()
            return
        self._current_document_index = index
        document = self._documents[index]
        hit_numbers = self._hit_numbers_by_document[index]
        replies = [reply for reply in document.replies if reply.number in hit_numbers]
        if not replies:
            self.browser.setPlainText(
                f"「{self._query}」に一致するレスは、このスレッドにはありません（0件）。"
            )
            return
        blocks = [self._reply_html(document, reply) for reply in replies]
        self.browser.setHtml(_html_document(self.browser, "".join(blocks)))

    def _browser_link_clicked(self, url: QUrl) -> None:
        number_text = url.path().lstrip("/") or url.host()
        if not number_text.isdigit():
            return
        number = int(number_text)
        if url.scheme() == "reply":
            self.browser.scrollToAnchor(f"reply-{number}")
            return
        if url.scheme() != "children":
            return
        scroll_position = self.browser.verticalScrollBar().value()
        key = (self._current_document_index, number)
        if key in self._expanded_replies:
            self._expanded_replies.remove(key)
        else:
            self._expanded_replies.add(key)
        self._render_selected_document(self._current_document_index)
        QTimer.singleShot(0, lambda: self.browser.verticalScrollBar().setValue(scroll_position))

    def _reference_hovered(self, link: str | QUrl) -> None:
        preview = _reference_preview(self._documents[self._current_document_index], link)
        if preview:
            QToolTip.showText(QCursor.pos(), preview, self.browser)
        else:
            QToolTip.hideText()

    def _reply_html(self, document: TextThreadDocument, reply: TextThreadReply) -> str:
        colors = _html_colors(self.browser)
        metadata = " / ".join(
            value for value in (reply.name, reply.posted_at, reply.poster_id) if value
        )
        children = reply_descendant_tree(document, reply.number)
        children_html = ""
        if children:
            key = (self._current_document_index, reply.number)
            expanded = key in self._expanded_replies
            marker = "▲ 閉じる" if expanded else "▼ 開く"
            children_html = (
                f'<p style="margin:6px 0 0"><a href="children:///{reply.number}">'
                f"返信ツリー {TextThreadViewerScreen._tree_reply_count(children)}件 {marker}</a></p>"
            )
            if expanded:
                children_html += (
                    f'<div style="margin:6px 0 0 18px; border-left:3px solid {colors["accent"]}; '
                    'padding-left:7px">'
                    + "".join(self._tree_html(node, document.reply_numbers) for node in children)
                    + "</div>"
                )
        return (
            f'<a name="reply-{reply.number}"></a>'
            f'<div style="background:{colors["base"]}; border:1px solid {colors["border"]}; '
            'border-radius:4px; margin:5px 2px; padding:7px">'
            f"<b>[{reply.number}]</b> {escape(metadata)}<br>"
            f'<div style="margin-top:5px; white-space:pre-wrap">'
            f"{TextThreadViewerScreen._body_html(reply.body, document.reply_numbers)}</div>"
            f"{children_html}</div>"
        )

    def _tree_html(
        self,
        node: TextThreadReplyTree,
        reply_numbers: frozenset[int],
    ) -> str:
        reply = node.reply
        colors = _html_colors(self.browser)
        metadata = " / ".join(
            value for value in (reply.name, reply.posted_at, reply.poster_id) if value
        )
        nested = ""
        if node.children:
            nested = (
                f'<div style="margin:5px 0 0 13px; border-left:2px solid {colors["accent"]}; '
                'padding-left:6px">'
                + "".join(self._tree_html(child, reply_numbers) for child in node.children)
                + "</div>"
            )
        if node.depth_limit_reached:
            nested += f'<p style="margin:5px 0; color:{colors["muted"]}">返信は10階層までを表示しています。</p>'
        return (
            f'<a name="reply-{reply.number}"></a>'
            f'<div style="background:{colors["alternate"]}; border:1px solid {colors["border"]}; '
            'border-radius:3px; margin:5px 0; padding:5px">'
            f'<a href="reply:///{reply.number}"><b>→ [{reply.number}]</b></a> '
            f"{escape(metadata)}<br>"
            f'<div style="margin-top:4px; white-space:pre-wrap">'
            f"{TextThreadViewerScreen._body_html(reply.body, reply_numbers)}</div>{nested}</div>"
        )


def create_screen(return_to_main: Callable[[], None]) -> TextThreadViewerScreen:
    """Factory used by the central launcher catalog."""
    return TextThreadViewerScreen(return_to_main)
