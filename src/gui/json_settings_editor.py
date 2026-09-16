"""Two-mode JSON settings editor: friendly form and exact source."""

from __future__ import annotations

from gui.current_page_stack import CurrentPageStack

from collections.abc import Callable, Mapping
from copy import deepcopy
from dataclasses import dataclass
import json
from pathlib import Path
import unicodedata
from typing import Any

from settings.persistent_settings import resolve_config_path
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)


_READ_ONLY_SOURCE_STATES = frozenset(
    {"missing_bootstrap", "invalid_bootstrap", "missing_directory", "not_directory"}
)


@dataclass(frozen=True)
class JsonFieldSpec:
    """Presentation metadata for one JSON key or an exact dotted path."""

    label: str
    description: str = ""
    choices: tuple[str, ...] = ()
    read_only: bool = False
    hidden: bool = False
    item_template: object | None = None


def user_settings_source_unavailable(state: str) -> bool:
    return state in _READ_ONLY_SOURCE_STATES


class JsonSettingsEditor(QWidget):
    """Edit the same JSON document through a form or exact source text."""

    def __init__(
        self,
        *,
        validate: Callable[[str], object] | None = None,
        path_keys: set[str] | frozenset[str] = frozenset(),
        fields: Mapping[str, JsonFieldSpec] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._validate = validate
        self._path_keys = frozenset(path_keys)
        self._fields = dict(fields or {})
        self._value: Any = {}
        self._read_only = False
        self._source_state = ""
        self._source_detail = ""
        self._mode_error = ""
        self._preferred_mode = "normal"
        self._widgets: list[QWidget] = []
        self._field_editors: dict[str, QWidget] = {}
        self._input_errors: dict[str, str] = {}
        self._save_buttons: list[QPushButton] = []
        self._edit_buttons: list[QPushButton] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        modes = QHBoxLayout()
        self.normal_button = QPushButton("通常表示")
        self.raw_button = QPushButton("元データ直接編集")
        group = QButtonGroup(self)
        group.setExclusive(True)
        for button in (self.normal_button, self.raw_button):
            button.setCheckable(True)
            group.addButton(button)
            modes.addWidget(button)
        modes.addStretch(1)
        layout.addLayout(modes)

        self.notice = QLabel()
        self.notice.setWordWrap(True)
        layout.addWidget(self.notice)
        self.stack = CurrentPageStack()
        layout.addWidget(self.stack, 1)

        normal_page = QWidget()
        normal_layout = QVBoxLayout(normal_page)
        normal_layout.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.form_content = QWidget()
        self.form_layout = QVBoxLayout(self.form_content)
        self.form_layout.setContentsMargins(2, 2, 8, 2)
        scroll.setWidget(self.form_content)
        normal_layout.addWidget(scroll, 1)
        self.strict_preview = QLabel(
            "各入力欄の下に、JSONへ保存される正確な値と不可視文字を表示します。"
        )
        self.strict_preview.setWordWrap(True)
        self.strict_preview.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        normal_layout.addWidget(self.strict_preview)
        self.stack.addWidget(normal_page)

        self.raw_editor = QTextEdit()
        self.raw_editor.setAcceptRichText(False)
        self.raw_editor.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
        self.raw_editor.setToolTip("保存対象のJSON元データです。省略や置換はありません。")
        self.stack.addWidget(self.raw_editor)

        self.normal_button.clicked.connect(self._show_normal)
        self.raw_button.clicked.connect(self._show_raw)
        self.normal_button.setChecked(True)
        self._refresh_notice()

    def set_source_state(self, state: str, detail: str, *, lock_unavailable: bool = True) -> None:
        self._source_state = state
        self._source_detail = detail
        self._read_only = lock_unavailable and user_settings_source_unavailable(state)
        self._apply_read_only()
        self._refresh_notice()

    def isReadOnly(self) -> bool:  # noqa: N802
        return self._read_only

    def setReadOnly(self, value: bool) -> None:  # noqa: N802
        self._read_only = value
        self._apply_read_only()
        self._refresh_notice()

    def bind_save_button(self, button: QPushButton) -> None:
        if button not in self._save_buttons:
            self._save_buttons.append(button)
        button.setEnabled(not self._read_only)

    def bind_edit_button(self, button: QPushButton) -> None:
        if button not in self._edit_buttons:
            self._edit_buttons.append(button)
        button.setEnabled(not self._read_only)

    def setPlainText(self, text: str) -> None:  # noqa: N802
        self.raw_editor.setPlainText(text)
        self._mode_error = ""
        if self._load_form(text):
            index = 0 if self._preferred_mode == "normal" else 1
            self.stack.setCurrentIndex(index)
            (self.normal_button if index == 0 else self.raw_button).setChecked(True)
        else:
            self.stack.setCurrentIndex(1)
            self.raw_button.setChecked(True)
        self._refresh_notice()

    def toPlainText(self) -> str:  # noqa: N802
        if self._read_only:
            raise ValueError(
                "ユーザー領域を読み込めなかったため、表示中の内蔵雛形は保存できません。"
                "メインメニューの「設定」で保存先を確認・作成してください。"
            )
        return self.raw_editor.toPlainText() if self.stack.currentIndex() == 1 else self._form_text()

    def has_unsaved_changes(self, saved_text: str) -> bool:
        try:
            return json.loads(self.toPlainText()) != json.loads(saved_text)
        except (TypeError, ValueError, json.JSONDecodeError):
            return self.raw_editor.toPlainText() != saved_text

    def field_editor(self, path: str) -> QWidget | None:
        """Expose a form control by JSON path for UI tests."""
        return self._field_editors.get(path)

    def _show_raw(self) -> None:
        self._preferred_mode = "raw"
        if self.stack.currentIndex() == 0:
            try:
                text = self._form_text()
            except Exception as exc:
                self._mode_error = f"通常表示の変更を反映できませんでした: {exc}"
            else:
                self.raw_editor.setPlainText(text)
                self._mode_error = ""
        self.stack.setCurrentIndex(1)
        self.raw_button.setChecked(True)
        self._refresh_notice()

    def _show_normal(self) -> None:
        self._preferred_mode = "normal"
        if not self._load_form(self.raw_editor.toPlainText()):
            self.stack.setCurrentIndex(1)
            self.raw_button.setChecked(True)
            self._refresh_notice()
            return
        self.stack.setCurrentIndex(0)
        self.normal_button.setChecked(True)
        self._mode_error = ""
        self._refresh_notice()

    def _load_form(self, text: str) -> bool:
        try:
            value = json.loads(text)
            if self._validate is not None:
                self._validate(text)
        except Exception as exc:
            self._mode_error = "通常表示を構築できないため、元データ直接編集を表示しています: " + str(exc)
            return False
        self._value = deepcopy(value)
        self._rebuild()
        return True

    def _rebuild(self) -> None:
        while self.form_layout.count():
            item = self.form_layout.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        self._widgets.clear()
        self._field_editors.clear()
        self._input_errors.clear()
        if isinstance(self._value, dict):
            for key in self._value:
                self._render(self._value, key, (str(key),), self.form_layout)
        elif isinstance(self._value, list):
            holder = {"$": self._value}
            self._render(holder, "$", ("$",), self.form_layout)
            self._value = holder["$"]
        else:
            holder = {"$": self._value}
            self._render(holder, "$", ("$",), self.form_layout)
            self._value = holder["$"]
        self.form_layout.addStretch(1)
        self._apply_read_only()

    def _render(
        self,
        container: dict[str, Any] | list[Any],
        key: str | int,
        path: tuple[str, ...],
        parent: QVBoxLayout,
    ) -> None:
        value = container[key]
        spec = self._spec(path)
        if spec.hidden:
            return
        if isinstance(value, dict):
            box = QGroupBox(f"{spec.label}（{len(value)}項目）")
            layout = QVBoxLayout(box)
            self._heading(layout, path, spec.description)
            for child in value:
                if isinstance(value[child], bool):
                    continue
                self._render(value, child, path + (str(child),), layout)
            self._render_boolean_row(value, path, layout)
            parent.addWidget(box)
        elif isinstance(value, list):
            self._render_list(container, key, path, parent, spec)
        else:
            self._render_scalar(container, key, path, parent, spec)

    def _render_scalar(
        self,
        container: dict[str, Any] | list[Any],
        key: str | int,
        path: tuple[str, ...],
        parent: QVBoxLayout,
        spec: JsonFieldSpec,
    ) -> None:
        value = container[key]
        frame = QFrame()
        frame.setFrameShape(QFrame.Shape.StyledPanel)
        layout = QVBoxLayout(frame)
        title = QLabel(spec.label)
        title.setStyleSheet("font-weight: 600;")
        layout.addWidget(title)
        self._heading(layout, path, spec.description)

        exact = QLabel()
        exact.setWordWrap(True)
        exact.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        exact.setStyleSheet("color: palette(text); font-size: 11px;")
        path_text = self._path_text(path)

        if isinstance(value, bool):
            editor: QWidget = QCheckBox("使用する")
            editor.setChecked(value)
            editor.toggled.connect(lambda checked, c=container, k=key: c.__setitem__(k, checked))
            editor.toggled.connect(
                lambda _checked, e=exact, c=container, k=key, p=path: self._exact(e, c[k], p)
            )
            layout.addWidget(editor)
        elif isinstance(value, str) and spec.choices:
            combo = QComboBox()
            combo.addItems(spec.choices)
            if value not in spec.choices:
                combo.addItem(value)
            combo.setCurrentText(value)
            combo.currentTextChanged.connect(lambda text, c=container, k=key: c.__setitem__(k, text))
            combo.currentTextChanged.connect(
                lambda _text, e=exact, c=container, k=key, p=path: self._exact(e, c[k], p)
            )
            editor = combo
            layout.addWidget(editor)
        else:
            line = QLineEdit(value if isinstance(value, str) else json.dumps(value, ensure_ascii=False))
            line.setClearButtonEnabled(isinstance(value, str))
            if isinstance(value, str):
                line.textChanged.connect(lambda text, c=container, k=key: c.__setitem__(k, text))
            else:
                line.textChanged.connect(
                    lambda text, c=container, k=key, original=value, p=path, w=line: self._set_typed(
                        c, k, text, original, p, w
                    )
                )
            line.textChanged.connect(
                lambda _text, e=exact, c=container, k=key, p=path: self._exact(e, c[k], p)
            )
            editor = line
            if self._is_path(path):
                row = QHBoxLayout()
                row.addWidget(line, 1)
                choose_file = QPushButton("ファイル…")
                choose_dir = QPushButton("フォルダ…")
                choose_file.clicked.connect(lambda _=False, target=line: self._choose_file(target))
                choose_dir.clicked.connect(lambda _=False, target=line: self._choose_directory(target))
                row.addWidget(choose_file)
                row.addWidget(choose_dir)
                layout.addLayout(row)
                self._widgets.extend((choose_file, choose_dir))
            else:
                layout.addWidget(line)

        editor.setProperty("spec_read_only", spec.read_only)
        editor.setEnabled(not spec.read_only)
        editor.setToolTip("JSONキー: " + path_text)
        layout.addWidget(exact)
        self._exact(exact, value, path)
        self._widgets.append(editor)
        self._field_editors[path_text] = editor
        parent.addWidget(frame)

    def _render_list(
        self,
        container: dict[str, Any] | list[Any],
        key: str | int,
        path: tuple[str, ...],
        parent: QVBoxLayout,
        spec: JsonFieldSpec,
    ) -> None:
        values: list[Any] = container[key]
        box = QGroupBox(f"{spec.label}（{len(values)}件）")
        layout = QVBoxLayout(box)
        self._heading(layout, path, spec.description)

        if spec.choices and all(isinstance(value, str) for value in values):
            checks: list[QCheckBox] = []

            def update_choices() -> None:
                container[key] = [choice for choice, check in zip(spec.choices, checks) if check.isChecked()]

            for choice in spec.choices:
                check = QCheckBox(choice)
                check.setChecked(choice in values)
                check.toggled.connect(update_choices)
                checks.append(check)
                self._widgets.append(check)
                layout.addWidget(check)
            parent.addWidget(box)
            return

        repeated_path = path[:-1] + (path[-1] + "[]",)
        for index, value in enumerate(values):
            item = QFrame()
            item.setFrameShape(QFrame.Shape.StyledPanel)
            item_layout = QVBoxLayout(item)
            controls = QHBoxLayout()
            controls.addWidget(QLabel(f"{index + 1}件目"))
            controls.addStretch(1)
            up, down, remove = QPushButton("上へ"), QPushButton("下へ"), QPushButton("削除")
            up.setEnabled(index > 0)
            down.setEnabled(index + 1 < len(values))
            up.clicked.connect(lambda _=False, c=container, k=key, i=index: self._move(c, k, i, -1))
            down.clicked.connect(lambda _=False, c=container, k=key, i=index: self._move(c, k, i, 1))
            remove.clicked.connect(lambda _=False, c=container, k=key, i=index: self._remove(c, k, i))
            for button in (up, down, remove):
                controls.addWidget(button)
                self._widgets.append(button)
            item_layout.addLayout(controls)
            if isinstance(value, dict):
                for child in value:
                    if isinstance(value[child], bool):
                        continue
                    self._render(value, child, repeated_path + (str(child),), item_layout)
                self._render_boolean_row(value, repeated_path, item_layout)
            else:
                self._render_scalar(values, index, repeated_path, item_layout, self._spec(repeated_path))
            layout.addWidget(item)
        add = QPushButton("項目を追加")
        add.clicked.connect(lambda _=False, c=container, k=key, s=spec: self._add(c, k, s))
        self._widgets.append(add)
        layout.addWidget(add)
        parent.addWidget(box)

    def _render_boolean_row(
        self,
        values: dict[str, Any],
        parent_path: tuple[str, ...],
        parent: QVBoxLayout,
    ) -> None:
        boolean_keys = [key for key, value in values.items() if isinstance(value, bool)]
        if not boolean_keys:
            return
        row = QHBoxLayout()
        for key in boolean_keys:
            path = parent_path + (key,)
            spec = self._spec(path)
            if spec.hidden:
                continue
            box = QGroupBox(spec.label)
            layout = QVBoxLayout(box)
            exact_key = QLabel("JSONキー: " + self._path_text(path))
            exact_key.setStyleSheet("font-size: 11px;")
            exact_key.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            layout.addWidget(exact_key)
            if spec.description:
                description = QLabel(spec.description)
                description.setWordWrap(True)
                layout.addWidget(description)
            check = QCheckBox("使用する")
            check.setChecked(values[key])
            saved = QLabel("保存値: " + json.dumps(values[key]))
            saved.setStyleSheet("font-size: 11px;")
            check.toggled.connect(lambda checked, c=values, k=key: c.__setitem__(k, checked))
            check.toggled.connect(
                lambda _checked, label=saved, c=values, k=key: label.setText(
                    "保存値: " + json.dumps(c[k])
                )
            )
            check.setProperty("spec_read_only", spec.read_only)
            layout.addWidget(check)
            layout.addWidget(saved)
            row.addWidget(box, 1)
            self._widgets.append(check)
            self._field_editors[self._path_text(path)] = check
        parent.addLayout(row)

    def _set_typed(
        self,
        container: dict[str, Any] | list[Any],
        key: str | int,
        text: str,
        original: Any,
        path: tuple[str, ...],
        editor: QLineEdit,
    ) -> None:
        name = self._path_text(path)
        try:
            parsed = json.loads(text)
            if isinstance(original, (int, float)) and not isinstance(original, bool):
                if not isinstance(parsed, (int, float)) or isinstance(parsed, bool):
                    raise ValueError
            elif original is None and parsed is not None:
                raise ValueError
        except (json.JSONDecodeError, ValueError):
            self._input_errors[name] = f"{name} の型が正しくありません。"
            editor.setStyleSheet("border: 1px solid #b00020;")
        else:
            container[key] = parsed
            self._input_errors.pop(name, None)
            editor.setStyleSheet("")

    def _add(
        self,
        container: dict[str, Any] | list[Any],
        key: str | int,
        spec: JsonFieldSpec,
    ) -> None:
        values: list[Any] = container[key]
        if spec.item_template is not None:
            prototype = deepcopy(spec.item_template)
        elif values and all(isinstance(value, dict) for value in values):
            prototype = {}
            for value in values:
                for child_key, child in value.items():
                    prototype.setdefault(child_key, self._blank_like(child))
        else:
            prototype = self._blank_like(values[0]) if values else ""
        values.append(prototype)
        self._rebuild()

    def _remove(self, container: dict[str, Any] | list[Any], key: str | int, index: int) -> None:
        del container[key][index]
        self._rebuild()

    def _move(self, container: dict[str, Any] | list[Any], key: str | int, index: int, offset: int) -> None:
        values: list[Any] = container[key]
        target = index + offset
        if 0 <= target < len(values):
            values[index], values[target] = values[target], values[index]
            self._rebuild()

    @classmethod
    def _blank_like(cls, value: Any) -> Any:
        if isinstance(value, dict):
            return {key: cls._blank_like(child) for key, child in value.items()}
        if isinstance(value, list):
            return []
        if isinstance(value, str):
            return ""
        if isinstance(value, bool):
            return False
        if isinstance(value, (int, float)):
            return 0
        return None

    def _form_text(self) -> str:
        if self._input_errors:
            raise ValueError(next(iter(self._input_errors.values())))
        text = json.dumps(self._value, ensure_ascii=False, indent=2) + "\n"
        if self._validate is not None:
            self._validate(text)
        return text

    def _spec(self, path: tuple[str, ...]) -> JsonFieldSpec:
        exact = self._path_text(path)
        key = path[-1].removesuffix("[]")
        return self._fields.get(exact, self._fields.get(key, JsonFieldSpec(self._default_label(key))))

    @staticmethod
    def _default_label(key: str) -> str:
        return "設定全体" if key == "$" else key.replace("_", " ")

    def _is_path(self, path: tuple[str, ...]) -> bool:
        exact = self._path_text(path)
        key = path[-1].removesuffix("[]")
        return exact in self._path_keys or key in self._path_keys

    @staticmethod
    def _path_text(path: tuple[str, ...]) -> str:
        return ".".join(path)

    @staticmethod
    def _heading(layout: QVBoxLayout, path: tuple[str, ...], description: str) -> None:
        key = QLabel("JSONキー: " + ".".join(path))
        key.setStyleSheet("color: palette(text); font-size: 11px;")
        key.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(key)
        if description:
            help_label = QLabel(description)
            help_label.setWordWrap(True)
            layout.addWidget(help_label)

    def _exact(self, label: QLabel, value: Any, path: tuple[str, ...] | None = None) -> None:
        text = json.dumps(value, ensure_ascii=False)
        if isinstance(value, str):
            text += "  ／ 可視化: " + self._visible_text(value)
            if path is not None and value and self._is_path(path):
                try:
                    resolved = Path(resolve_config_path(value))
                    state = "存在" if resolved.exists() else "未確認"
                    text += f"\n解決結果: {resolved}（{state}）"
                except ValueError as exc:
                    text += f"\n解決結果: 使用不可 — {exc}"
        label.setText("保存値: " + text)

    def _choose_file(self, editor: QLineEdit) -> None:
        selected, _ = QFileDialog.getOpenFileName(self, "ファイルを選択", editor.text())
        if selected:
            editor.setText(selected)

    def _choose_directory(self, editor: QLineEdit) -> None:
        selected = QFileDialog.getExistingDirectory(self, "フォルダを選択", editor.text())
        if selected:
            editor.setText(selected)

    def _apply_read_only(self) -> None:
        self.raw_editor.setReadOnly(self._read_only)
        for widget in self._widgets:
            if widget.property("form_enabled") is None:
                widget.setProperty("form_enabled", widget.isEnabled())
            widget.setEnabled(
                not self._read_only
                and bool(widget.property("form_enabled"))
                and not bool(widget.property("spec_read_only"))
            )
        for button in self._save_buttons:
            button.setEnabled(not self._read_only)
        for button in self._edit_buttons:
            button.setEnabled(not self._read_only)

    @staticmethod
    def _visible_text(value: str) -> str:
        result: list[str] = []
        for character in value:
            if character == " ":
                result.append("␠")
            elif character == "\n":
                result.append(r"\n")
            elif character == "\r":
                result.append(r"\r")
            elif character == "\t":
                result.append(r"\t")
            elif character.isspace() or unicodedata.category(character).startswith("C"):
                result.append(f"\\u{ord(character):04x}")
            else:
                result.append(character)
        return "".join(result) or "（空文字列）"

    def _refresh_notice(self) -> None:
        lines: list[str] = []
        if self._read_only:
            lines.append(
                "ユーザー領域を読み込めなかったため、この内蔵雛形を一時利用します。"
                "メインメニューの「設定」で保存先を確認・作成してください。雛形は編集・保存できません。"
            )
        if self._source_state or self._source_detail:
            lines.append(f"設定状態: {self._source_state} — {self._source_detail}")
        if self._mode_error:
            lines.append(self._mode_error)
        self.notice.setText("\n".join(lines))
        self.notice.setStyleSheet(
            "color: #b00020; font-weight: 600;" if self._read_only or self._mode_error else ""
        )
