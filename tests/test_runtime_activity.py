import os

from foundation.runtime_activity import RuntimeActivityInfo, active_runtime_activities, begin_runtime_activity


def test_runtime_activity_is_visible_only_while_the_handle_is_open():
    activity = begin_runtime_activity("動画変換中")
    try:
        assert active_runtime_activities({os.getpid()}) == (
            RuntimeActivityInfo(os.getpid(), "動画変換中"),
        )
    finally:
        activity.close()

    assert active_runtime_activities({os.getpid()}) == ()
