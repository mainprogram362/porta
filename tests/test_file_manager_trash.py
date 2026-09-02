from subprocess import CompletedProcess

from apps.file_tools.file_manager import trash_workflow


def test_trash_preview_requires_gio_and_existing_sources(tmp_path, monkeypatch):
    source = tmp_path / "item.txt"
    source.write_text("content", encoding="utf-8")
    monkeypatch.setattr(trash_workflow.shutil, "which", lambda _name: None)

    preview = trash_workflow.build_trash_preview(str(source))

    assert not preview.is_ready
    assert "gio" in preview.text


def test_execute_trash_plan_uses_gio_without_permanent_delete(tmp_path, monkeypatch):
    source = tmp_path / "item.txt"
    source.write_text("content", encoding="utf-8")
    calls = []

    def fake_run(command, **_kwargs):
        calls.append(command)
        return CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(trash_workflow.subprocess, "run", fake_run)
    result = trash_workflow.execute_trash_plan(trash_workflow.TrashPlan((source,)))

    assert result == [source]
    assert calls == [["gio", "trash", str(source)]]
    assert source.exists()


def test_trash_uses_the_symbolic_link_path_not_its_target(tmp_path, monkeypatch):
    target = tmp_path / "target.txt"
    target.write_text("content", encoding="utf-8")
    link = tmp_path / "visible-link.txt"
    link.symlink_to(target)
    monkeypatch.setattr(trash_workflow.shutil, "which", lambda _name: "/usr/bin/gio")
    calls = []

    def fake_run(command, **_kwargs):
        calls.append(command)
        return CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(trash_workflow.subprocess, "run", fake_run)
    preview = trash_workflow.build_trash_preview(str(link))
    assert preview.plan is not None
    trash_workflow.execute_trash_plan(preview.plan)

    assert calls == [["gio", "trash", str(link)]]
    assert target.read_text(encoding="utf-8") == "content"
