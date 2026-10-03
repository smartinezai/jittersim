import importlib.util
import json
import sqlite3
from dataclasses import make_dataclass
from pathlib import Path

import pytest

from jittersim.config.models import Profile
from jittersim.config.storage import ConfigRepository

SCRIPT = Path(__file__).resolve().parents[2] / "deploy/raspberry-pi/homepage-profile-compat.py"
spec = importlib.util.spec_from_file_location("homepage_compat", SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
OldProfile = make_dataclass("OldProfile", [("id", str), ("name", str)])


def old_reader(row):
    values = json.loads(row["settings_json"])
    if "homepage_bounce_percent" in values:
        raise TypeError("older reader rejects homepage chance")
    return row


def test_conversion_is_private_idempotent_and_preserves_current_settings(tmp_path):
    database = tmp_path / "config.db"
    with ConfigRepository(database) as repository:
        repository.save_profile(
            Profile("p", "Current name", homepage_bounce_percent=70, outage_probability=25)
        )
    assert module.convert(database, OldProfile, old_reader)
    assert not module.convert(database, OldProfile, old_reader)
    with ConfigRepository(database) as repository:
        profile = repository.get_profile("p")
        assert profile.name == "Current name"
        assert profile.outage_probability == 25
        assert profile.homepage_bounce_percent == 0
    backup = tmp_path / "homepage-profile-compat.sqlite"
    assert backup.stat().st_mode & 0o777 == 0o600
    with sqlite3.connect(backup) as connection:
        assert (
            json.loads(connection.execute("SELECT settings_json FROM profiles").fetchone()[0])[
                "homepage_bounce_percent"
            ]
            == 70
        )


def test_failed_target_validation_leaves_original_untouched(tmp_path):
    database = tmp_path / "config.db"
    with ConfigRepository(database) as repository:
        repository.save_profile(Profile("p", "P", homepage_bounce_percent=70))

    def reject(_):
        raise ValueError("target reader refused")

    with pytest.raises(ValueError):
        module.convert(database, OldProfile, reject)
    with ConfigRepository(database) as repository:
        assert repository.get_profile("p").homepage_bounce_percent == 70
    assert not (tmp_path / "homepage-profile-compat.sqlite").exists()


def test_current_target_does_not_convert(tmp_path):
    database = tmp_path / "config.db"
    with ConfigRepository(database) as repository:
        repository.save_profile(Profile("p", "P", homepage_bounce_percent=70))
    assert not module.convert(database, Profile, old_reader)


@pytest.mark.parametrize("failed_sync", [1, 2])
def test_backup_sync_failure_prevents_conversion(tmp_path, monkeypatch, failed_sync):
    database = tmp_path / "config.db"
    with ConfigRepository(database) as repository:
        repository.save_profile(Profile("p", "Latest edit", homepage_bounce_percent=70))
        version = repository.get_runtime_state().configuration_version
    calls = 0
    real_fsync = module.os.fsync

    def fail_sync(descriptor):
        nonlocal calls
        calls += 1
        if calls == failed_sync:
            raise OSError("simulated durability failure")
        return real_fsync(descriptor)

    monkeypatch.setattr(module.os, "fsync", fail_sync)
    with pytest.raises(OSError, match="durability"):
        module.convert(database, OldProfile, old_reader)
    with ConfigRepository(database) as repository:
        assert repository.get_profile("p").homepage_bounce_percent == 70
        assert repository.get_profile("p").name == "Latest edit"
        assert repository.get_runtime_state().configuration_version == version
    assert not list(tmp_path.glob(".homepage-compat-*"))
    if failed_sync == 2:
        with sqlite3.connect(tmp_path / "homepage-profile-compat.sqlite") as connection:
            settings = json.loads(
                connection.execute("SELECT settings_json FROM profiles").fetchone()[0]
            )
            assert settings["homepage_bounce_percent"] == 70
