#!/usr/bin/env python3
"""Remove only unsupported homepage settings before a target dashboard starts."""

import json
import os
import sqlite3
import sys
import tempfile
import time
from contextlib import closing
from dataclasses import fields
from pathlib import Path


def convert(database, profile_type, reader):
    database = Path(database)
    if not database.exists():
        return False
    if "homepage_bounce_percent" in {field.name for field in fields(profile_type)}:
        return False
    uri = database.resolve().as_uri() + "?mode=rw"
    with closing(sqlite3.connect(uri, uri=True, timeout=0.1)) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA synchronous=FULL")
        connection.execute("BEGIN IMMEDIATE")
        if connection.execute("SELECT count(*) FROM profiles").fetchone()[0] > 10000:
            raise ValueError("too many profiles for bounded compatibility conversion")
        # Validate the entire target-readable configuration before saving any change.
        changed = False
        for row in connection.execute("SELECT * FROM profiles"):
            if len(row["settings_json"]) > 65536:
                raise ValueError("profile settings are too large")
            settings = json.loads(row["settings_json"] or "{}")
            if not isinstance(settings, dict):
                raise ValueError("profile settings are malformed")
            value = settings.pop("homepage_bounce_percent", None)
            if value is not None:
                if type(value) is not int or not 0 <= value <= 100:
                    raise ValueError("homepage chance is malformed")
                changed = True
            candidate = dict(row)
            candidate["settings_json"] = json.dumps(settings, sort_keys=True)
            reader(candidate)
        if not changed:
            return False
        backup = database.with_name("homepage-profile-compat.sqlite")
        deadline = time.monotonic() + 15
        descriptor, temporary = tempfile.mkstemp(
            prefix=".homepage-compat-", dir=str(database.parent)
        )
        os.close(descriptor)
        try:

            def check(*_):
                if time.monotonic() >= deadline:
                    raise TimeoutError("compatibility backup timed out")

            with closing(
                sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)
            ) as source:
                with closing(sqlite3.connect(temporary)) as target:
                    source.backup(target, pages=128, progress=check)
            os.chmod(temporary, 0o600)
            with open(temporary, "rb") as stream:
                os.fsync(stream.fileno())
            os.replace(temporary, backup)
            descriptor = os.open(str(database.parent), os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        finally:
            Path(temporary).unlink(missing_ok=True)
        for row in connection.execute("SELECT id, settings_json FROM profiles"):
            settings = json.loads(row["settings_json"] or "{}")
            if "homepage_bounce_percent" in settings:
                del settings["homepage_bounce_percent"]
                connection.execute(
                    "UPDATE profiles SET settings_json=? WHERE id=?",
                    (json.dumps(settings, sort_keys=True), row["id"]),
                )
        # Supported current settings and security tables stay intact.
        connection.execute(
            "UPDATE runtime_state SET configuration_version=configuration_version+1 WHERE id=1"
        )
        connection.commit()
        return True


def main():
    from jittersim.config.models import Profile
    from jittersim.config.storage import ConfigRepository

    convert(Path(sys.argv[1]), Profile, ConfigRepository._profile_from_row)


if __name__ == "__main__":
    main()
