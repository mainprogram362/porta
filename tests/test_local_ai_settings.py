from apps.system_tools.local_ai import settings


def test_local_ai_settings_are_optional_and_shared(tmp_path, monkeypatch):
    path = tmp_path / "local_ai.json"
    monkeypatch.setattr(settings, "SETTINGS_PATH", path)

    assert settings.load_settings() == {"runner_path": "", "model_path": ""}

    saved = settings.save_text(
        '{"runner_path": "/tools/llama-server", "model_path": "/models/qwen.gguf"}'
    )

    assert saved == {"runner_path": "/tools/llama-server", "model_path": "/models/qwen.gguf"}
    assert settings.load_settings() == saved


def test_local_ai_settings_reject_unknown_and_relative_paths():
    try:
        settings.validate_text('{"runner_path": "bin/llama-server"}')
    except ValueError as exc:
        assert "runner_path" in str(exc)
    else:
        raise AssertionError("相対パスを受け入れてはいけません")

    try:
        settings.validate_text('{"other": "value"}')
    except ValueError as exc:
        assert "runner_path" in str(exc)
    else:
        raise AssertionError("未対応項目を受け入れてはいけません")
