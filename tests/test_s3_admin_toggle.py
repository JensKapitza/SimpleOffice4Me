"""Regression tests for persistent S3 admin switch."""

from app.settings_store import SettingsStore


def test_s3_disabled_by_default(tmp_path):
    store = SettingsStore(tmp_path)
    assert store.settings()["s3"]["enabled"] is False


def test_s3_toggle_persists_across_instances(tmp_path):
    store = SettingsStore(tmp_path)
    settings = store.settings()
    settings["s3"]["enabled"] = True
    store.save(settings, "test-admin")
    assert SettingsStore(tmp_path).settings()["s3"]["enabled"] is True
    settings = store.settings()
    settings["s3"]["enabled"] = False
    store.save(settings, "test-admin")
    assert SettingsStore(tmp_path).settings()["s3"]["enabled"] is False


def test_s3_setting_rejects_non_boolean(tmp_path):
    import pytest
    store = SettingsStore(tmp_path)
    settings = store.settings()
    settings["s3"]["enabled"] = "false"
    with pytest.raises(ValueError, match="S3 enabled"):
        store.save(settings, "test-admin")
