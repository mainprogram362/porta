from pathlib import Path

from apps.system_tools.configuration.templates import (
    all_configuration_templates,
    config_reset_templates,
    create_single_configuration_template,
    create_user_space_template,
)
from foundation.user_space import paths_for_root


def test_user_space_template_creates_only_the_standard_structure_and_config(tmp_path: Path) -> None:
    root = tmp_path / "porta_user[new]"

    result = create_user_space_template(root)

    assert result.root == root
    assert root.is_dir()
    assert all(path.is_dir() for path in paths_for_root(root).directories())
    expected = {root / "config" / Path(relative) for relative in config_reset_templates()}
    assert set(result.config_files) == expected
    assert all(path.is_file() for path in expected)
    assert not any(path.is_file() for path in (root / "local_data").rglob("*"))


def test_user_space_template_never_replaces_an_existing_target(tmp_path: Path) -> None:
    root = tmp_path / "porta_user[new]"
    root.mkdir()
    marker = root / "keep.txt"
    marker.write_text("keep", encoding="utf-8")

    try:
        create_user_space_template(root)
    except FileExistsError:
        pass
    else:
        raise AssertionError("existing user-space target must not be replaced")

    assert marker.read_text(encoding="utf-8") == "keep"


def test_single_configuration_template_uses_selected_template_without_overwrite(tmp_path: Path) -> None:
    template = next(
        value for value in all_configuration_templates() if value.key == "media_information"
    )
    destination = tmp_path / "config" / "media_information[new].json"

    created = create_single_configuration_template(template, destination)

    assert created == destination
    assert destination.read_text(encoding="utf-8") == template.template_text()
    try:
        create_single_configuration_template(template, destination)
    except FileExistsError:
        pass
    else:
        raise AssertionError("existing template file must not be replaced")
