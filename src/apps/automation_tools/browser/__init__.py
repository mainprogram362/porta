"""Firefox cartridge authoring and execution screens (lazy GUI imports)."""


def create_screen(back):
    from gui.section_workspace import SectionWorkspace
    return SectionWorkspace(back, '自動操作', (
        ('use', 'カートリッジを使用', create_browser_screen),
        ('create', 'カートリッジを作成', create_editor),
    ))


def create_browser_screen(back):
    from .window import BrowserScreen
    return BrowserScreen(back)


def create_editor(back):
    from .editor import CartridgeEditor
    return CartridgeEditor(back)
