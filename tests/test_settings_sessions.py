from runtime.settings_sessions import SettingsSession


def test_newer_settings_screen_blocks_older_until_it_closes(tmp_path):
    directory = tmp_path / "sessions"
    root = tmp_path / "porta"
    older = SettingsSession(directory=directory, root=root)
    newer = SettingsSession(directory=directory, root=root)
    try:
        assert len(older.others()) == 1
        assert not older.is_newest()
        assert newer.is_newest()
        newer.close()
        assert older.is_newest()
    finally:
        older.close()
        newer.close()


def test_settings_screens_from_another_porta_are_not_mixed(tmp_path):
    directory = tmp_path / "sessions"
    first = SettingsSession(directory=directory, root=tmp_path / "first")
    second = SettingsSession(directory=directory, root=tmp_path / "second")
    try:
        assert first.others() == ()
        assert second.others() == ()
    finally:
        first.close()
        second.close()
