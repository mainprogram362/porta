from pathlib import Path

from apps.system_tools.configuration.templates import (
    all_configuration_templates,
    config_reset_templates,
)
from apps.system_tools.environment_check.audit import EnvironmentReport
from foundation import persistent_settings


def test_every_configuration_template_is_valid_and_resettable() -> None:
    templates = all_configuration_templates()

    assert [template.key for template in templates] == [
        "bootstrap",
        "file_manager",
        "video_encoder",
        "youtube_downloader",
        "media_information",
        "text_thread_viewer",
        "storage_encryption",
        "local_ai",
        "external_program_locations",
    ]
    for template in templates:
        template.validate(template.template_text(), Path("/tmp/porta-config-test"))

    reset = config_reset_templates()
    assert len(reset) == len(templates) - 1
    assert "file_manager.json" in reset
    assert "shared/external_program_locations.json" in reset
    assert "persistent_settings.json" not in reset


def test_missing_files_return_the_same_in_memory_templates(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(persistent_settings, "BOOTSTRAP_PATH", tmp_path / "persistent_settings.json")

    for template in all_configuration_templates():
        assert template.editable_text() == template.template_text()


def test_environment_report_counts_levels() -> None:
    from apps.system_tools.environment_check.audit import AuditItem

    report = EnvironmentReport(
        (
            AuditItem("ok", "A", "one", "fine"),
            AuditItem("ok", "A", "two", "fine"),
            AuditItem("warning", "B", "three", "optional"),
        )
    )

    assert report.count("ok") == 2
    assert report.count("warning") == 1
    assert report.count("error") == 0
