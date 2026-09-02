from apps.system_tools.storage_encryption.luks import (
    find_command,
    privileged_mount_command,
    suggested_mapping_name,
    validate_mount_request,
)


def _all_commands(_name: str) -> str:
    return "/usr/bin/available"


def test_luks_command_search_includes_standard_sbin_locations_when_gui_path_is_minimal():
    assert find_command("cryptsetup", which=lambda _name: None) in {
        "/usr/sbin/cryptsetup",
        "/sbin/cryptsetup",
    }


def test_luks_mount_request_requires_existing_container_and_empty_mount_point(tmp_path):
    container = tmp_path / "vault.img"
    container.write_bytes(b"not checked until the privileged LUKS check")
    mount_point = tmp_path / "mount"
    mount_point.mkdir()

    validation = validate_mount_request(
        str(container), str(mount_point), which=_all_commands
    )

    assert validation.is_valid
    assert validation.request is not None
    assert validation.request.container_path == container
    assert validation.request.mount_point == mount_point
    assert validation.request.mapping_name == suggested_mapping_name(container)
    assert any("実行時に管理者権限" in message for message in validation.messages)


def test_luks_mount_request_refuses_nonempty_or_relative_mount_targets(tmp_path):
    container = tmp_path / "vault.img"
    container.write_bytes(b"container")
    nonempty = tmp_path / "nonempty"
    nonempty.mkdir()
    (nonempty / "do-not-hide").write_text("x", encoding="utf-8")

    nonempty_validation = validate_mount_request(str(container), str(nonempty), which=_all_commands)
    relative_validation = validate_mount_request(str(container), "relative/mount", which=_all_commands)

    assert not nonempty_validation.is_valid
    assert any("空にしてください" in message for message in nonempty_validation.messages)
    assert not relative_validation.is_valid
    assert any("絶対パス" in message for message in relative_validation.messages)


def test_privileged_luks_mount_command_uses_fixed_script_and_positional_paths(tmp_path):
    container = tmp_path / "strange $(not executed).img"
    container.write_bytes(b"container")
    mount_point = tmp_path / "mount"
    mount_point.mkdir()
    request = validate_mount_request(str(container), str(mount_point), which=_all_commands).request
    assert request is not None

    program, arguments = privileged_mount_command(request, passphrase_byte_count=12)

    assert program == "pkexec"
    assert arguments[:3] == ("/bin/sh", "-c", arguments[2])
    assert arguments[-4] == str(container)
    assert "source_path=$1" in arguments[2]
    assert str(container) not in arguments[2]
    assert "cryptsetup isLuks \"$source_path\"" in arguments[2]
    assert "--key-file - --keyfile-size \"$passphrase_byte_count\"" in arguments[2]
    assert arguments[-1] == "12"
    assert "cryptsetup close \"$mapping_name\"" in arguments[2]
