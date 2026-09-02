"""Read-only, best-effort Linux battery and external-power information.

The Linux power-supply class exposes these files when the platform driver can
report them.  This module deliberately never changes a power profile, starts
charging, suspends the machine, or writes any state.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


POWER_SUPPLY_ROOT = Path("/sys/class/power_supply")


@dataclass(frozen=True)
class PowerStatus:
    """A conservative snapshot of battery state for a local UI."""

    battery_percent: int | None
    battery_status: str | None
    external_power: bool | None
    battery_count: int = 0

    @property
    def battery_available(self) -> bool:
        return self.battery_percent is not None

    @property
    def receiving_external_power(self) -> bool:
        """Treat a positive AC reading or a charging/full battery as evidence."""
        return self.external_power is True or self.battery_status in {"Charging", "Full"}

    def summary(self) -> str:
        if self.battery_percent is None:
            return "充電情報を取得できません"
        status = self.battery_status or "状態不明"
        power = (
            "外部給電あり"
            if self.external_power is True
            else "外部給電なし"
            if self.external_power is False
            else "外部給電は確認不可"
        )
        return f"充電 {self.battery_percent}%（{status} / {power}）"


def read_power_status(root: Path = POWER_SUPPLY_ROOT) -> PowerStatus:
    """Return available Linux battery data without assuming a ``BAT0`` name.

    Multiple batteries are weighted by reported energy/charge where possible;
    otherwise their percentage readings are averaged.  Missing optional sysfs
    attributes simply yield an unknown field instead of an exception.
    """
    try:
        entries = tuple(path for path in root.iterdir() if path.is_dir())
    except OSError:
        return PowerStatus(None, None, None)

    batteries = [entry for entry in entries if _read_text(entry / "type") == "Battery"]
    percentages: list[int] = []
    weighted_values: list[tuple[int, int]] = []
    battery_states: list[str] = []
    for battery in batteries:
        percentage = _read_int(battery / "capacity")
        if percentage is not None and 0 <= percentage <= 100:
            percentages.append(percentage)
        now, full = _battery_capacity_pair(battery)
        if now is not None and full is not None and full > 0:
            weighted_values.append((now, full))
        state = _read_text(battery / "status")
        if state:
            battery_states.append(state)

    if weighted_values:
        percent = round(sum(now for now, _full in weighted_values) * 100 / sum(full for _now, full in weighted_values))
    elif percentages:
        percent = round(sum(percentages) / len(percentages))
    else:
        percent = None

    online_values = [
        _read_int(entry / "online")
        for entry in entries
        if _read_text(entry / "type") != "Battery" and (entry / "online").is_file()
    ]
    known_online = [value for value in online_values if value in {0, 1}]
    external_power = any(known_online) if known_online else None
    return PowerStatus(percent, _combined_battery_state(battery_states), external_power, len(batteries))


def _battery_capacity_pair(battery: Path) -> tuple[int | None, int | None]:
    for now_name, full_name in (("energy_now", "energy_full"), ("charge_now", "charge_full")):
        now = _read_int(battery / now_name)
        full = _read_int(battery / full_name)
        if now is not None and full is not None:
            return now, full
    return None, None


def _combined_battery_state(states: list[str]) -> str | None:
    if "Charging" in states:
        return "Charging"
    if states and all(state == "Full" for state in states):
        return "Full"
    if "Discharging" in states:
        return "Discharging"
    return states[0] if states else None


def _read_text(path: Path) -> str | None:
    try:
        value = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return value or None


def _read_int(path: Path) -> int | None:
    value = _read_text(path)
    try:
        return int(value) if value is not None else None
    except ValueError:
        return None
