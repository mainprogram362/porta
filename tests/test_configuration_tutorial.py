from apps.system_tools.configuration.window import nautilus_integration_tutorial_text


def test_external_integration_tutorial_does_not_install_host_files() -> None:
    guide = nautilus_integration_tutorial_text()

    assert "rm -f" not in guide
    assert "ln -s" not in guide
    assert "mkdir" not in guide
    assert "chmod" not in guide
    assert ".local/share" not in guide
    assert "自動作成しません" in guide
    assert "根幹機能は残しています" in guide
