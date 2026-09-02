import json
from pathlib import Path

from apps.media_tools.video_encoder import settings as encoder_settings
from apps.media_tools.youtube_downloader import settings as youtube_settings
from foundation.path_tokens import HOME_TOKEN


def test_home_token_is_visible_in_templates_but_expanded_for_runtime():
    encoder_template = json.loads(encoder_settings.template_text())
    assert len(encoder_template["path_settings"]) == 5
    assert encoder_template["path_settings"][0] == {
        "path": "",
        "initial_source": True,
        "context_menu": True,
    }
    assert encoder_template["path_settings"][2] == {"path": ""}
    assert json.loads(youtube_settings.template_text())["download_output_directory"] == HOME_TOKEN

    loaded = youtube_settings.validate_text(
        '{"download_output_directory":"${HOME}/Videos","metadata_export_directory":"${HOME}","catalog_json_path":""}'
    )

    assert loaded["download_output_directory"] == str(Path.home() / "Videos")
    assert loaded["metadata_export_directory"] == str(Path.home())


def test_saving_keeps_the_explicit_home_token(tmp_path, monkeypatch):
    path = tmp_path / "youtube.json"
    monkeypatch.setattr(youtube_settings, "SETTINGS_PATH", path)

    youtube_settings.save_text(
        '{"download_output_directory":"${HOME}","metadata_export_directory":"${HOME}","catalog_json_path":""}'
    )

    assert json.loads(path.read_text(encoding="utf-8"))["download_output_directory"] == HOME_TOKEN
