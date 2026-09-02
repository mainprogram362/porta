import csv
import json
from pathlib import Path

from apps.media_tools.youtube_downloader import settings
from media import downloader
from media.downloader import VideoRecord, _creator_tab_urls, export_creator_videos, split_urls
from media.video_catalog import (
    CATALOG_SCHEMA_VERSION,
    CatalogVideo,
    DEFAULT_CATALOG_FILENAME,
    resolve_catalog_path,
    write_video_catalog,
)


def test_wpc_browser_detection_and_temporary_profile_cleanup(monkeypatch, tmp_path):
    monkeypatch.setattr(downloader, "_find_wpc_browser_path", lambda: tmp_path / "chromium")

    with downloader._EphemeralWpcProfile() as profile:
        assert profile.exists()
        assert profile.name.startswith("porta-wpc-")
        assert str(profile).startswith("/dev/shm/")

    assert not profile.exists()

    monkeypatch.setattr(downloader, "_find_wpc_browser_path", lambda: None)
    ready, message = downloader.wpc_provider_status()
    assert ready is False
    assert "Chromium/Chrome" in message


def test_po_token_status_treats_a_missing_optional_plugin_as_not_installed(
    monkeypatch, tmp_path
):
    provider_home = tmp_path / "provider"
    (provider_home / "src").mkdir(parents=True)
    (provider_home / "src" / "generate_once.ts").touch()
    (provider_home / "node_modules").mkdir()
    monkeypatch.setattr(downloader, "_POT_PROVIDER_SERVER_HOME", provider_home)

    def missing_plugin(_name):
        raise ModuleNotFoundError("yt_dlp_plugins")

    monkeypatch.setattr(downloader.importlib.util, "find_spec", missing_plugin)

    ready, message = downloader._po_token_provider_status()

    assert ready is False
    assert "未導入" in message


def test_wpc_status_treats_a_missing_optional_plugin_as_not_installed(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(downloader, "_find_wpc_browser_path", lambda: tmp_path / "chromium")

    def missing_plugin(_name):
        raise ModuleNotFoundError("yt_dlp_plugins")

    monkeypatch.setattr(downloader.importlib.util, "find_spec", missing_plugin)

    ready, message = downloader.wpc_provider_status()

    assert ready is False
    assert "未導入" in message


def test_error_text_keeps_empty_timeout_errors_readable():
    assert downloader._error_text(TimeoutError()) == "TimeoutError"


def test_youtube_settings_fall_back_without_a_local_file(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "SETTINGS_PATH", tmp_path / "youtube_downloader.json")

    loaded = settings.load_settings()

    assert set(loaded) == {
        "download_output_directory",
        "metadata_export_directory",
        "catalog_json_path",
    }
    assert loaded["download_output_directory"]
    assert loaded["metadata_export_directory"]
    assert loaded["catalog_json_path"] == ""


def test_youtube_settings_are_explicit_and_validate_the_schema(tmp_path, monkeypatch):
    path = tmp_path / "youtube_downloader.json"
    monkeypatch.setattr(settings, "SETTINGS_PATH", path)

    saved = settings.save_text(
        json.dumps(
            {
                "download_output_directory": "/tmp/video-output",
                "metadata_export_directory": "/tmp/inventory-output",
                "catalog_json_path": "/tmp/catalog.json",
            }
        )
    )

    assert saved["download_output_directory"] == "/tmp/video-output"
    assert json.loads(path.read_text(encoding="utf-8"))["metadata_export_directory"] == "/tmp/inventory-output"
    try:
        settings.validate_text('{"history": []}')
    except ValueError as exc:
        assert "未対応" in str(exc)
    else:
        raise AssertionError("history must never become persistent settings")


def test_catalog_path_accepts_directories_or_json_files_only(tmp_path: Path):
    output = tmp_path / "videos"
    output.mkdir()

    assert resolve_catalog_path("", output_directory=output) == output / DEFAULT_CATALOG_FILENAME
    assert resolve_catalog_path(tmp_path, output_directory=output) == tmp_path / DEFAULT_CATALOG_FILENAME
    assert resolve_catalog_path("~/catalog.json", output_directory=output).name == "catalog.json"
    assert (
        resolve_catalog_path(str(tmp_path / "new-directory"), output_directory=output)
        == tmp_path / "new-directory" / DEFAULT_CATALOG_FILENAME
    )
    try:
        resolve_catalog_path(tmp_path / "catalog.txt", output_directory=output)
    except ValueError as exc:
        assert ".json" in str(exc)
    else:
        raise AssertionError("non-JSON catalog file must be rejected")


def test_video_catalog_appends_successes_and_updates_duplicates_without_losing_notes(tmp_path: Path):
    path = tmp_path / "library" / "video_catalog.json"
    first = CatalogVideo(
        title="First title",
        source_url="https://www.youtube.com/watch?v=example",
        source_video_id="example",
        upload_date="2026-08-20",
        channel_name="Example channel",
        source_kind="video",
        downloaded_at="2026-08-20T10:00:00+09:00",
        output_path=tmp_path / "video.mp4",
        size_bytes=123,
        format_selector="399+251",
    )

    report = write_video_catalog(path, [first])

    assert report.added == 1
    document = json.loads(path.read_text(encoding="utf-8"))
    assert document["schema_version"] == CATALOG_SCHEMA_VERSION
    assert document["kind"] == "media_catalog"
    assert document["media"][0]["record_id"] == "00001"
    assert all(attribute["key"] != "file.path" for attribute in document["media"][0]["attributes"])
    assert {
        attribute["value"]
        for attribute in document["media"][0]["attributes"]
        if attribute["key"] == "file.name.observed"
    } == {"video.mp4"}
    document["media"][0]["attributes"].append(
        {"key": "classification.tag", "value": "お気に入り", "value_type": "text", "source": "user"}
    )
    path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")

    updated = CatalogVideo(
        **{**first.__dict__, "title": "Updated title", "downloaded_at": "2026-08-21T10:00:00+09:00"}
    )
    report = write_video_catalog(path, [updated])
    document = json.loads(path.read_text(encoding="utf-8"))

    assert report.added == 0
    assert report.updated == 1
    assert len(document["media"]) == 1
    assert document["media"][0]["record_id"] == "00001"
    assert {
        attribute["value"]
        for attribute in document["media"][0]["attributes"]
        if attribute["key"] == "title.observed"
    } == {"First title", "Updated title"}
    assert any(
        attribute["key"] == "classification.tag" and attribute["value"] == "お気に入り"
        for attribute in document["media"][0]["attributes"]
    )


def test_legacy_download_catalog_is_migrated_without_retaining_a_live_path(tmp_path: Path):
    path = tmp_path / "legacy.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "media": [
                    {
                        "record_id": "00001",
                        "title": "Legacy title",
                        "source": {"service": "YouTube", "video_id": "legacy", "url": "https://example"},
                        "file": {"path": "/moved/somewhere/legacy.mp4", "size_bytes": 5},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    write_video_catalog(path, [])
    # An empty update intentionally does not rewrite. Supply the same source
    # to request the safe migration and normal attribute merge.
    write_video_catalog(
        path,
        [
            CatalogVideo(
                title="Legacy title",
                source_url="https://example",
                source_video_id="legacy",
                upload_date="",
                channel_name="",
                source_kind="video",
                downloaded_at="",
                output_path=None,
                size_bytes=None,
                format_selector="",
            )
        ],
    )
    document = json.loads(path.read_text(encoding="utf-8"))

    assert document["schema_version"] == CATALOG_SCHEMA_VERSION
    attributes = document["media"][0]["attributes"]
    assert not any(attribute["key"] == "file.path" for attribute in attributes)
    assert any(
        attribute["key"] == "file.name.observed" and attribute["value"] == "legacy.mp4"
        for attribute in attributes
    )


def test_creator_tabs_include_normal_videos_and_shorts():
    assert _creator_tab_urls("https://www.youtube.com/@example") == (
        ("video", "https://www.youtube.com/@example/videos"),
        ("short", "https://www.youtube.com/@example/shorts"),
    )
    assert _creator_tab_urls("https://www.youtube.com/channel/example/shorts")[0][1].endswith(
        "/channel/example/videos"
    )


def test_export_creator_videos_writes_reusable_url_list_and_small_metadata(tmp_path: Path):
    records = [
        VideoRecord(
            title="Sample title",
            url="https://www.youtube.com/watch?v=abc",
            video_id="abc",
            upload_date="2026-08-20",
            channel_name="Example",
        ),
        VideoRecord(
            title="Short title",
            url="https://www.youtube.com/watch?v=def",
            video_id="def",
            source_kind="short",
        ),
    ]

    report = export_creator_videos(records, tmp_path, creator_label="Example Channel")

    assert report.count == 2
    assert report.urls_path.read_text(encoding="utf-8").splitlines() == [
        "https://www.youtube.com/watch?v=abc",
        "https://www.youtube.com/watch?v=def",
    ]
    with report.details_path.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert rows[0]["title"] == "Sample title"
    assert rows[1]["kind"] == "short"


def test_url_input_is_line_oriented_and_de_duplicates():
    assert split_urls("\n https://youtu.be/a \nhttps://youtu.be/a\nhttps://youtu.be/b\n") == [
        "https://youtu.be/a",
        "https://youtu.be/b",
    ]


def test_creator_collection_reports_overview_then_streams_records_and_stops_safely(monkeypatch):
    class FakeYoutubeDL:
        def __init__(self, options):
            self.options = options

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def extract_info(self, url, download=False):
            if url.endswith("/videos"):
                return {"entries": [{"id": "new", "title": "Newest"}, {"id": "old", "title": "Old"}]}
            if url.endswith("/shorts"):
                return {"entries": [{"id": "short", "title": "Short"}]}
            video_id = url.rsplit("=", maxsplit=1)[-1]
            return {
                "id": video_id,
                "title": f"Detailed {video_id}",
                "webpage_url": url,
                "upload_date": "20260820",
            }

    monkeypatch.setattr(downloader, "YoutubeDL", FakeYoutubeDL)
    overviews = []
    progress = []
    result = downloader.collect_creator_videos(
        "https://www.youtube.com/@example",
        on_overview=overviews.append,
        on_progress=progress.append,
        should_cancel=lambda: len(progress) >= 1,
    )

    assert overviews[0].video_total == 2
    assert overviews[0].short_total == 1
    assert result.cancelled is True
    assert [record.video_id for record in result.records] == ["new"]
    assert progress[0].completed_total == 1


def test_quality_fallback_requires_the_exact_same_video_and_audio_formats():
    info = {
        "formats": [
            {"format_id": "137", "vcodec": "avc1", "acodec": "none", "height": 1080, "ext": "mp4", "url": "https://example/137"},
            {"format_id": "399", "vcodec": "av01", "acodec": "none", "height": 2160, "ext": "mp4", "url": "https://example/399"},
            {"format_id": "140", "vcodec": "none", "acodec": "mp4a", "abr": 128, "ext": "m4a", "url": "https://example/140"},
            {"format_id": "251", "vcodec": "none", "acodec": "opus", "abr": 160, "ext": "webm", "url": "https://example/251"},
        ]
    }

    selection = downloader._select_high_quality_format(info)

    assert selection is not None
    assert selection.selector == "399+251"
    assert downloader._offers_exact_format(info, selection) is True
    assert downloader._offers_exact_format(
        {"formats": [item for item in info["formats"] if item["format_id"] != "399"]}, selection
    ) is False


def test_403_fallback_retries_only_with_the_same_quality_format(tmp_path: Path, monkeypatch):
    calls = []

    class FakeYoutubeDL:
        def __init__(self, options):
            self.options = options

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def extract_info(self, _url, download=False):
            return {
                "formats": [
                    {"format_id": "399", "vcodec": "av01", "acodec": "none", "height": 2160, "ext": "mp4", "url": "https://example/399"},
                    {"format_id": "251", "vcodec": "none", "acodec": "opus", "abr": 160, "ext": "webm", "url": "https://example/251"},
                ]
            }

        def download(self, _urls):
            client = self.options.get("extractor_args", {}).get("youtube", {}).get("player_client", [None])[0]
            calls.append((client, self.options["format"], self.options["continuedl"]))
            if client is None:
                raise RuntimeError("HTTP Error 403: Forbidden")

    monkeypatch.setattr(downloader, "YoutubeDL", FakeYoutubeDL)
    report = downloader.download_videos(
        [VideoRecord("Example", "https://www.youtube.com/watch?v=example", "example")], tmp_path
    )

    assert report.succeeded == 1
    assert report.failed == 0
    assert calls == [(None, "399+251", True), ("web_embedded", "399+251", False)]
    assert len(report.catalog_videos) == 1
    assert report.catalog_videos[0].source_video_id == "example"


def test_po_token_retry_is_explicit_and_never_changes_the_selected_quality(tmp_path: Path, monkeypatch):
    full_info = {
        "formats": [
            {"format_id": "399", "vcodec": "av01", "acodec": "none", "height": 2160, "ext": "mp4", "url": "https://example/399"},
            {"format_id": "251", "vcodec": "none", "acodec": "opus", "abr": 160, "ext": "webm", "url": "https://example/251"},
        ]
    }
    lower_info = {"formats": [{"format_id": "137"}, {"format_id": "140"}]}
    download_calls = []

    def fake_fetch(_url, *, client=None, use_po_token_provider=False):
        return full_info if client in {None, "mweb"} else lower_info

    def fake_download(_url, _output, **kwargs):
        download_calls.append(kwargs)
        if kwargs["client"] is None:
            raise RuntimeError("HTTP Error 403: Forbidden")

    monkeypatch.setattr(downloader, "_fetch_video_info", fake_fetch)
    monkeypatch.setattr(downloader, "_download_one", fake_download)
    monkeypatch.setattr(downloader, "_po_token_provider_status", lambda: (True, "ready"))

    report = downloader.download_videos(
        [VideoRecord("Example", "https://www.youtube.com/watch?v=example", "example")],
        tmp_path,
        allow_po_token_provider=True,
    )

    assert report.succeeded == 1
    assert report.failed == 0
    assert download_calls[-1] == {
        "selector": "399+251",
        "client": "mweb",
        "restart": True,
        "use_po_token_provider": True,
    }


def test_wpc_retry_runs_only_after_other_same_quality_routes_fail(tmp_path: Path, monkeypatch):
    full_info = {
        "formats": [
            {"format_id": "399", "vcodec": "av01", "acodec": "none", "width": 1920, "height": 1080, "fps": 24, "tbr": 800, "dynamic_range": "SDR", "ext": "mp4", "url": "https://example/399"},
            {"format_id": "251", "vcodec": "none", "acodec": "opus", "abr": 128, "tbr": 128, "ext": "webm", "url": "https://example/251"},
        ]
    }
    lower_info = {"formats": [{"format_id": "137"}, {"format_id": "140"}]}
    calls = []

    def fake_fetch(_url, *, client=None, use_po_token_provider=False, use_wpc_provider=False):
        if client is None:
            return full_info
        return lower_info

    def fake_download(_url, _output, **kwargs):
        calls.append(kwargs)
        raise RuntimeError("HTTP Error 403: Forbidden")

    def fake_wpc_worker(url, output, reference_info, reference_selection):
        assert url.endswith("example")
        assert output == tmp_path
        assert reference_info == full_info
        assert reference_selection.selector == "399+251"
        return "399+251-12"

    monkeypatch.setattr(downloader, "_fetch_video_info", fake_fetch)
    monkeypatch.setattr(downloader, "_download_one", fake_download)
    monkeypatch.setattr(downloader, "_po_token_provider_status", lambda: (False, "not installed"))
    monkeypatch.setattr(downloader, "wpc_provider_status", lambda: (True, "ready"))
    monkeypatch.setattr(downloader, "_run_wpc_download_worker", fake_wpc_worker)

    report = downloader.download_videos(
        [VideoRecord("Example", "https://www.youtube.com/watch?v=example", "example")],
        tmp_path,
        allow_wpc_provider=True,
    )

    assert report.succeeded == 1
    assert calls == [{
        "selector": "399+251",
        "client": None,
        "restart": False,
    }]
    assert "一時ブラウザPO Token" in report.messages[-1]
