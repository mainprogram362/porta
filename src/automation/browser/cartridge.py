"""Bounded declarative cartridges; loading never executes their contents."""
from __future__ import annotations

import json
from urllib.parse import urlsplit

MAX_BYTES = 256 * 1024
ACTIONS = {"check", "extract", "fill", "click", "wait_page"}


def web_origin(url):
    parsed = urlsplit(url)
    if parsed.scheme not in {"https", "http"} or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("URLは認証情報を含まないhttp/httpsの完全URLにしてください。")
    return f"{parsed.scheme}://{parsed.netloc}"


def parse_cartridge(text):
    if len(text.encode("utf-8")) > MAX_BYTES:
        raise ValueError("カートリッジは256KiB以内にしてください。")
    try:
        data = json.loads(text)
    except RecursionError as error:
        raise ValueError("JSONの入れ子が深すぎます。") from error
    if not isinstance(data, dict) or set(data) != {"version", "name", "origins", "steps"}:
        raise ValueError("version / name / origins / steps の4項目を指定してください。")
    if type(data["version"]) is not int or data["version"] != 1:
        raise ValueError("対応形式はversion 1です。")
    if not isinstance(data["name"], str) or not 1 <= len(data["name"].strip()) <= 100:
        raise ValueError("名前は1～100文字です。")
    origins = data["origins"]
    if not isinstance(origins, list) or not 1 <= len(origins) <= 20:
        raise ValueError("originsは1～20件です。")
    for origin in origins:
        if not isinstance(origin, str) or web_origin(origin) != origin or urlsplit(origin).path:
            raise ValueError("originsには https://example.com の形式で指定してください。")
    steps = data["steps"]
    if not isinstance(steps, list) or not 1 <= len(steps) <= 100:
        raise ValueError("手順は1～100件です。繰り返しは初期版では対応しません。")
    allowed = {"action", "url", "selector", "text", "value", "attribute", "limit", "timeout", "many"}
    for index, step in enumerate(steps, 1):
        if not isinstance(step, dict) or set(step) - allowed:
            raise ValueError(f"手順{index}: 不明な項目があります。")
        action = step.get("action")
        if not isinstance(action, str) or action not in ACTIONS:
            raise ValueError(f"手順{index}: 未対応の操作です。")
        url = step.get("url")
        if not isinstance(url, str) or len(url) > 8192 or web_origin(url) not in origins:
            raise ValueError(f"手順{index}: 許可したorigin内の完全URLが必要です。")
        if action != "wait_page" and (not isinstance(step.get("selector"), str) or not 1 <= len(step["selector"]) <= 2000):
            raise ValueError(f"手順{index}: CSS selectorが必要です。")
        for key in ("text", "value", "attribute"):
            if key in step and (not isinstance(step[key], str) or len(step[key]) > 16000):
                raise ValueError(f"手順{index}: {key}は16000文字以内の文字列です。")
        if action == "fill" and "value" not in step:
            raise ValueError(f"手順{index}: 入力するvalueが必要です。")
        if action == "wait_page" and (index == 1 or steps[index - 2]["action"] != "click"):
            raise ValueError("wait_pageはclickの直後にだけ指定できます。")
        if "many" in step and type(step["many"]) is not bool:
            raise ValueError("manyはtrue/falseです。")
        for key, default, ceiling in (("limit", 100, 500), ("timeout", 10, 30)):
            value = step.get(key, default)
            if type(value) is not int or not 1 <= value <= ceiling:
                raise ValueError(f"{key}は1～{ceiling}の整数です。")
        if step.get("attribute", "text") not in {"text", "href", "src", "value"}:
            raise ValueError("抽出属性はtext / href / src / valueです。")
    return data


def template():
    return {
        "version": 1, "name": "ページのリンクを取得",
        "origins": ["https://example.com"],
        "steps": [{"action": "extract", "url": "https://example.com/",
                   "selector": "a[href]", "attribute": "href", "many": True, "limit": 100}],
    }
