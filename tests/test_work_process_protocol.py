"""Protocol-only cases prepared for a separate environment; not executed here."""
import pytest

from foundation.work_process import validate_command


@pytest.mark.parametrize("op", ["status", "focus", "close"])
def test_supported_commands_allow_extension_fields(op):
    message = {"version": 1, "op": op, "future_field": "ignored"}
    assert validate_command(message) == message


@pytest.mark.parametrize("message", [None, [], {}, {"version": True, "op": "close"},
                                     {"version": 2, "op": "close"}, {"version": 1, "op": "kill"},
                                     {"version": 1, "op": "execute"}])
def test_unknown_versions_and_destructive_commands_are_rejected(message):
    with pytest.raises(ValueError):
        validate_command(message)


@pytest.mark.parametrize("deadline", [0, -1, float("nan"), float("inf"), True, "later"])
def test_expired_or_invalid_commands_do_not_get_delivered(deadline):
    with pytest.raises(ValueError):
        validate_command({"version": 1, "op": "close", "deadline": deadline})
