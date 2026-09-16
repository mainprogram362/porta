"""Offline validation cases. Added without execution on the user's computer."""
import json

import pytest

from apps.automation_tools.browser.cartridge import parse_cartridge, template


def parse(data):
    return parse_cartridge(json.dumps(data))


def test_template_is_a_bounded_read_only_cartridge():
    data = parse(template())
    assert data["steps"][0]["action"] == "extract"
    assert data["steps"][0]["limit"] == 100


@pytest.mark.parametrize("action", [[], {}, None, "eval", "loop"])
def test_unsupported_actions_rejected_as_validation_errors(action):
    data = template()
    data["steps"][0]["action"] = action
    with pytest.raises(ValueError):
        parse(data)


@pytest.mark.parametrize("url", ["file:///tmp/a", "https://other.example/",
                                 "https://name:secret@example.com/", "about:config"])
def test_other_sites_and_non_web_targets_are_rejected(url):
    data = template()
    data["steps"][0]["url"] = url
    with pytest.raises(ValueError):
        parse(data)


@pytest.mark.parametrize("key,value", [("limit", 501), ("limit", True), ("timeout", 31), ("many", "true")])
def test_invalid_execution_limits_rejected(key, value):
    data = template()
    data["steps"][0][key] = value
    with pytest.raises(ValueError):
        parse(data)


def test_navigation_wait_requires_an_immediately_preceding_click():
    data = template()
    wait = {"action": "wait_page", "url": "https://example.com/next", "timeout": 10}
    data["steps"].append(wait)
    with pytest.raises(ValueError):
        parse(data)
    data["steps"][0] = {"action": "click", "url": "https://example.com/", "selector": "a.next"}
    assert parse(data)["steps"][1] == wait


def test_cartridge_cannot_supply_arbitrary_script():
    data = template()
    data["steps"][0]["script"] = "alert(1)"
    with pytest.raises(ValueError):
        parse(data)


def test_step_count_is_bounded():
    data = template()
    data["steps"] *= 101
    with pytest.raises(ValueError):
        parse(data)
