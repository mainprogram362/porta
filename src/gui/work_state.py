"""Common display contract; absence of a provider never means disposable."""
from .work_lifecycle import running_work, dependent_windows

STATE_NAMES = {1: "初期状態", 2: "設定・入力あり", 3: "保持する作業あり", 4: "処理実行中"}


def describe_work(screen, *, include_process_activities=True):
    """Describe one screen without letting another local tab change its state."""
    if include_process_activities:
        from runtime.process_registry import get_registry
        try:
            activities = get_registry().activity_labels()
        except Exception:
            return {"level": None, "reason": "処理状態を取得できません。終了前に作業を確認してください。"}
        if activities:
            return {"level": 4, "reason": "実行中: " + " / ".join(activities)[:1000]}
    if running_work(screen):
        return {"level": 4, "reason": "処理実行中。終了前に作業側で停止・完了を確認してください。"}
    if dependent_windows(screen):
        return {"level": 3, "reason": "補助画面があります。入力・途中結果を確認してください。"}
    provider = getattr(screen, "describe_work_state", None)
    if not callable(provider):
        return {"level": None, "reason": "この画面は状態報告に未対応です。閉じる前に内容を確認してください。"}
    try:
        value = provider()
        if not isinstance(value, dict) or type(value.get("level")) is not int or value["level"] not in (1, 2, 3, 4):
            raise ValueError("invalid state")
        return {"level": value["level"], "reason": str(value.get("reason", ""))[:1000]}
    except Exception:
        return {"level": None, "reason": "状態の取得に失敗しました。内容は保持しています。"}
