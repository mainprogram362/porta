from types import SimpleNamespace

from foundation.linux_system import action_availability, system_status_lines


def test_lock_uses_the_current_session_without_guessing():
    availability = action_availability(
        "lock",
        environment={"XDG_SESSION_ID": "42"},
        which=lambda command: "/bin/" + command if command == "loginctl" else None,
    )

    assert availability.available
    assert availability.action is not None
    assert availability.action.program == "loginctl"
    assert availability.action.arguments == ("lock-session", "42")


def test_logout_without_a_known_session_is_disabled():
    availability = action_availability("logout", environment={}, which=lambda _command: "/bin/loginctl")

    assert not availability.available
    assert "特定" in availability.reason


def test_hibernate_only_appears_when_systemctl_reports_support():
    def successful_run(*_args, **_kwargs):
        return SimpleNamespace(returncode=0, stdout="yes\n")

    availability = action_availability(
        "hibernate",
        which=lambda command: "/bin/" + command if command == "systemctl" else None,
        run=successful_run,
    )

    assert availability.available
    assert availability.action is not None
    assert availability.action.arguments == ("hibernate",)


def test_status_is_read_only_and_tolerates_missing_systemctl():
    assert system_status_lines(which=lambda _command: None)[-1].startswith("systemctl:")
