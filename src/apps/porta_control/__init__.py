def create_screen(back):
    from gui.section_workspace import SectionWorkspace
    from .configuration import create_screen as settings
    from .diagnostics.environment_check import create_screen as environment
    return SectionWorkspace(back, 'PORTA管理', (
        ('settings', '設定', settings),
        ('environment', '環境診断', environment),
    ))
