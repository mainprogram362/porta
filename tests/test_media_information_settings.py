from apps.media_tools.media_information import settings


def test_media_information_settings_are_optional_and_minimal(tmp_path, monkeypatch):
    path = tmp_path / "media_information.json"
    monkeypatch.setattr(settings, "SETTINGS_PATH", path)

    loaded = settings.load_settings()
    assert loaded["auto_fill_single_json_path"] is True
    assert loaded["registered_paths"] == []
    assert loaded["mpv_path"] == "/usr/bin/mpv"
    assert loaded["default_visible_columns"] == ["サイズ", "評価", "見どころ"]
    assert loaded["mpv_shortcuts"]["アプリ連携キー"]["見どころ地点を記録"] == {
        "enabled": True,
        "key": "m",
    }
    assert loaded["mpv_tag_choices"] == []

    saved = settings.save_text('{"auto_fill_single_json_path": false}')

    assert saved["auto_fill_single_json_path"] is False
    assert saved["mpv_path"] == "/usr/bin/mpv"
    assert "mpv_shortcuts" in saved
    assert saved["registered_paths"] == []
    assert saved["default_visible_columns"] == ["サイズ", "評価", "見どころ"]
    assert settings.load_settings() == saved


def test_media_information_settings_normalize_mpv_tag_choices():
    saved = settings.validate_text(
        '{"mpv_tag_choices": [" お気に入り ", "コント", "お気に入り"]}'
    )

    assert saved["mpv_tag_choices"] == ["お気に入り", "コント"]
    try:
        settings.validate_text('{"mpv_tag_choices": ["ok", ""]}')
    except ValueError as exc:
        assert "mpv_tag_choices" in str(exc)
    else:
        raise AssertionError("空欄のタグ候補を受け入れてはいけません")


def test_media_information_settings_validate_explicit_default_columns():
    saved = settings.validate_text(
        '{"auto_fill_single_json_path": true, "default_visible_columns": ["評価", "解像度"]}'
    )

    assert saved["default_visible_columns"] == ["解像度", "評価"]
    try:
        settings.validate_text('{"default_visible_columns": ["存在しない列"]}')
    except ValueError as exc:
        assert "表示できない" in str(exc)
    else:
        raise AssertionError("未対応の表示列を受け入れてはいけません")


def test_media_information_settings_accept_registered_absolute_paths(tmp_path):
    saved = settings.validate_text(
        '{"registered_paths": ["' + str(tmp_path) + '", ""]}'
    )
    assert saved["registered_paths"] == [str(tmp_path)]

    try:
        settings.validate_text('{"registered_paths": ["relative/path"]}')
    except ValueError as exc:
        assert "registered_paths" in str(exc)
    else:
        raise AssertionError("相対パスを登録パスとして受け入れてはいけません")


def test_media_information_settings_reject_unrelated_persistent_data():
    try:
        settings.validate_text('{"history": ["/secret"]}')
    except ValueError as exc:
        assert "未対応" in str(exc)
    else:
        raise AssertionError("未対応の永続データを受け入れてはいけません")


def test_media_information_settings_drop_the_legacy_saved_output_path(tmp_path, monkeypatch):
    path = tmp_path / "media_information.json"
    path.write_text('{"catalog_json_path": "/old/private/output.json"}', encoding="utf-8")
    monkeypatch.setattr(settings, "SETTINGS_PATH", path)

    editable = settings.editable_text()
    loaded = settings.load_settings()

    assert "catalog_json_path" not in editable
    assert "/old/private/output.json" not in editable
    assert loaded["auto_fill_single_json_path"] is True
