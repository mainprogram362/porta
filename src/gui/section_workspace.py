"""One application with retained, lazily created section screens."""
from PySide6.QtWidgets import QWidget, QPushButton
from gui import AppPageLayout, AppHeader
from gui.current_page_stack import CurrentPageStack
from gui.work_state import describe_work


class SectionWorkspace(QWidget):
    def __init__(self, back, title, sections):
        super().__init__()
        self.pages = {}
        self.section_title = title
        self.stack = CurrentPageStack()
        layout = AppPageLayout(self)
        layout.addWidget(self.stack)
        home = QWidget()
        home_layout = AppPageLayout(home)
        home_layout.addWidget(AppHeader(back, title=title))
        self.stack.addWidget(home)
        for key, label, factory in sections:
            button = QPushButton(label)
            button.clicked.connect(lambda checked=False, k=key, f=factory: self.open_section(k, f))
            home_layout.addWidget(button)
        home_layout.addStretch(1)

    def open_section(self, key, factory):
        if key not in self.pages:
            page = factory(lambda: self.stack.setCurrentIndex(0))
            for header in page.findChildren(AppHeader):
                header.back_button.setText(f'← {self.section_title}')
                header.back_button.setToolTip(f'{self.section_title}の機能選択へ戻ります。入力は保持します。')
            self.pages[key] = page
            self.stack.addWidget(page)
        self.stack.setCurrentWidget(self.pages[key])

    def describe_work_state(self):
        states = [describe_work(page, include_process_activities=False) for page in self.pages.values()]
        return max(states, key=lambda s: s['level'] if s['level'] is not None else 3) if states else {'level': 1, 'reason': '機能を選択中です。'}

    def shutdown(self):
        for page in self.pages.values():
            shutdown = getattr(page, 'shutdown', None)
            if callable(shutdown):
                shutdown()
