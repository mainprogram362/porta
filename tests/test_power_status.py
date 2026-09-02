from pathlib import Path

from foundation.power_status import read_power_status


def _supply(root: Path, name: str, **values: str) -> Path:
    path = root / name
    path.mkdir()
    for key, value in values.items():
        (path / key).write_text(value, encoding="utf-8")
    return path


def test_read_power_status_uses_linux_supply_files_and_detects_external_power(tmp_path: Path):
    _supply(
        tmp_path,
        "BAT0",
        type="Battery",
        status="Charging",
        capacity="25",
        energy_now="1000",
        energy_full="4000",
    )
    _supply(tmp_path, "AC", type="Mains", online="1")

    status = read_power_status(tmp_path)

    assert status.battery_percent == 25
    assert status.battery_status == "Charging"
    assert status.external_power is True
    assert status.receiving_external_power
    assert "25%" in status.summary()


def test_read_power_status_is_safe_when_battery_information_is_unavailable(tmp_path: Path):
    _supply(tmp_path, "AC", type="Mains", online="0")

    status = read_power_status(tmp_path)

    assert status.battery_percent is None
    assert status.external_power is False
    assert not status.receiving_external_power
    assert status.summary() == "充電情報を取得できません"
