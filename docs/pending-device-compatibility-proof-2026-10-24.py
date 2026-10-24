"""Offline probe. Pass an exported 3f5499a9 src directory as the sole argument.

Run with PYTHONPATH=src and the current repository Python dependencies.
The old subprocess imports the entire exported earlier source tree.
"""

import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from jittersim.config.pending_devices import PendingDeviceChoice
from jittersim.config.storage import ConfigRepository

OLD_PROBE = r"""
import json
import sqlite3
import sys
from dataclasses import replace
from pathlib import Path
from jittersim.config.models import Impairment, Profile, SavedSettings
from jittersim.config.storage import ConfigRepository, StorageError
import jittersim.config.storage as storage

path, mode, expected_source = sys.argv[1:]
assert Path(storage.__file__).resolve() == Path(expected_source).resolve()

def rows(repo):
    return {name: [tuple(row) for row in repo._connection.execute('SELECT * FROM "' + name + '"')]
            for (name,) in repo._connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")}

with ConfigRepository(path) as repo:
    assert repo._connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    if mode == 'initialize':
        repo.save_profile(Profile(id='prepared', name='Prepared', upload=Impairment(delay_ms=25)))
        assert repo._connection.execute(
            "SELECT name FROM sqlite_master WHERE name='pending_device_choices'").fetchone() is None
        print(json.dumps({'old_initialization': 'passed', 'foreign_keys': 1}))
    else:
        pending = rows(repo)['pending_device_choices']
        assert len(pending) == 1
        assert not repo.list_devices() and not repo.list_unaffected_devices()
        repo.save_profile(Profile(id='unrelated', name='Unrelated'))
        assert rows(repo)['pending_device_choices'] == pending
        before = rows(repo)
        try:
            repo.delete_profile('prepared')
        except StorageError as error:
            delete_error = str(error)
        else:
            raise AssertionError('old profile deletion unexpectedly succeeded')
        assert rows(repo) == before and not repo.in_transaction
        try:
            repo.replace_settings(SavedSettings(profiles=(repo.get_profile('unrelated'),)))
        except StorageError as error:
            import_error = str(error)
        else:
            raise AssertionError('old omitted-profile import unexpectedly succeeded')
        assert rows(repo) == before and not repo.in_transaction
        retained = replace(repo.get_profile('prepared'), name='Retained via old import')
        repo.replace_settings(SavedSettings(profiles=(retained,)))
        assert repo.get_profile('prepared') == retained
        assert rows(repo)['pending_device_choices'] == pending
        assert not repo.list_devices() and not repo.list_unaffected_devices()
        assert repo._connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert repo._connection.execute("PRAGMA foreign_key_check").fetchall() == []
        print(json.dumps({'old_open_read_unrelated_write': 'passed',
                          'old_profile_delete_atomic': 'passed', 'delete_error': delete_error,
                          'old_omitted_import_atomic': 'passed', 'import_error': import_error,
                          'old_retained_import': 'passed', 'foreign_keys': 1}))
"""


def main():
    old_src = Path(sys.argv[1]).resolve()
    old_storage = old_src / "jittersim/config/storage.py"
    digest = hashlib.sha256(old_storage.read_bytes()).hexdigest()
    assert digest == "d6490102d49a475cdb9e318428de2d76bf1b4441944079fb2d3f0af70605f43c"
    environment = dict(os.environ, PYTHONPATH=str(old_src))
    with tempfile.TemporaryDirectory() as directory:
        database = Path(directory) / "settings.db"
        for mode in ("initialize", "verify"):
            if mode == "verify":
                with ConfigRepository(database) as repo:
                    assert repo._connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
                    saved = repo.save_pending_device_choice(
                        PendingDeviceChoice(
                            mac_address="02:11:22:33:44:55", action="profile", profile_id="prepared"
                        )
                    )
            result = subprocess.run(
                [sys.executable, "-c", OLD_PROBE, str(database), mode, str(old_storage)],
                env=environment,
                text=True,
                capture_output=True,
                check=True,
            )
            print(result.stdout.strip())
        for _ in range(2):
            with ConfigRepository(database) as repo:
                assert repo.list_pending_device_choices() == (saved,)
                assert repo.get_profile("prepared").name == "Retained via old import"
                assert repo._connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
                backup_bytes = repo.backup_bytes()
        backup = Path(directory) / "backup.db"
        backup.write_bytes(backup_bytes)
        with ConfigRepository(backup) as repo:
            assert repo.list_pending_device_choices() == (saved,)
            assert repo._connection.execute("PRAGMA foreign_key_check").fetchall() == []
        print(
            json.dumps(
                {
                    "new_reopen_twice": "passed",
                    "sqlite_backup_restore": "passed",
                    "source_sha256": digest,
                }
            )
        )


if __name__ == "__main__":
    main()
