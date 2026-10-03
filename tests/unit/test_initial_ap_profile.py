"""Exercise absence, ownership and interruption decisions independently."""

import os
import runpy
import time
from pathlib import Path
from unittest.mock import patch

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "deploy/raspberry-pi/create-ap-profile.py"
UUID = "b8073ffb-a013-4853-9295-d84be8a343aa"


def test_invalid_secret_cannot_create_or_probe_a_profile():
    module = runpy.run_path(str(SCRIPT))
    create = module["create_profile"]
    for value in (b"short", b"password\nquit", b"x" * 64, b"password\x00", b"password\x80"):
        with patch.dict(create.__globals__, {"command": lambda *_a, **_k: pytest.fail("command ran")}):
            with pytest.raises(module["ProfileError"]):
                create("wlan0", "TestNet", value)


def test_existence_failure_and_duplicate_names_are_not_absence():
    module = runpy.run_path(str(SCRIPT))
    state = module["profile_state"]
    with patch.dict(state.__globals__, {"command": lambda *_a, **_k: (_ for _ in ()).throw(
            module["ProfileError"]("query failed"))}):
        with pytest.raises(module["ProfileError"]):
            state("wlan0")
    duplicate = f"jittersim-ap:{UUID}\njittersim-ap:ba415ba6-0c15-4995-acb2-1f4139bb6272\n".encode()
    with patch.dict(state.__globals__, {"command": lambda *_a, **_k: duplicate}):
        with pytest.raises(module["ProfileError"]):
            state("wlan0")


def test_interrupted_profile_is_refused_without_any_mutation_or_retry():
    module = runpy.run_path(str(SCRIPT))
    create = module["create_profile"]
    calls = []

    def command(arguments, **_options):
        calls.append(arguments)
        if "--fields" in arguments:
            return f"jittersim-ap:{UUID}\n".encode()
        return f"802-11-wireless\nwlan0\nno\nap\nsae\n{module['PENDING_PREFIX']}{UUID}\n".encode()

    with patch.dict(create.__globals__, {"command": command}):
        with pytest.raises(module["ProfileError"], match="incomplete"):
            create("wlan0", "TestNet", b"fixture-password")
    assert len(calls) == 2
    assert all("show" in arguments for arguments in calls)


def test_failed_save_or_marker_clear_deletes_only_the_assigned_fresh_uuid():
    module = runpy.run_path(str(SCRIPT))
    create = module["create_profile"]
    for failing_operation in ("edit", "modify"):
        calls = []

        def command(arguments, **_options):
            calls.append(arguments)
            if failing_operation in arguments:
                raise module["ProfileError"]("private failure")
            return b""

        with patch.dict(create.__globals__, {"command": command}):
            with pytest.raises(module["ProfileError"], match="removed"):
                create("wlan0", "TestNet", b"fixture-password")
        addition = next(arguments for arguments in calls if "add" in arguments)
        identifier = addition[addition.index("connection.uuid") + 1]
        assert calls[-1] == ("--wait", "3", "connection", "delete", "uuid", identifier)
        assert addition[addition.index("connection.autoconnect") + 1] == "no"
        assert addition[addition.index("802-11-wireless-security.key-mgmt") + 1] == "sae"
        assert addition[addition.index("connection.stable-id") + 1].endswith(identifier)
        assert all("up" not in arguments for arguments in calls)


def test_success_clears_receipt_only_after_exact_private_secret_check():
    module = runpy.run_path(str(SCRIPT))
    create = module["create_profile"]
    calls = []

    def command(arguments, **options):
        calls.append((arguments, options))
        return b""

    with patch.dict(create.__globals__, {"command": command}):
        create("wlan0", "TestNet", b" leading and trailing ")
    secret_check = next(index for index, (args, _) in enumerate(calls) if "--show-secrets" in args)
    clear = next(index for index, (args, _) in enumerate(calls) if "modify" in args)
    assert secret_check < clear
    assert calls[secret_check][1]["expected"] == b" leading and trailing \n"
    assert calls[-1][1]["expected"].endswith(b"\n\n")


def test_stalled_and_oversize_input_are_bounded():
    module = runpy.run_path(str(SCRIPT))
    read, write = os.pipe()
    try:
        with os.fdopen(read, "rb", closefd=False) as stream:
            started = time.monotonic()
            with pytest.raises(module["ProfileError"], match="timed out"):
                module["read_bounded"](stream, 64, time.monotonic() + 0.1)
            assert time.monotonic() - started < 1
            os.write(write, b"x" * 65)
            with pytest.raises(module["ProfileError"], match="limit"):
                module["read_bounded"](stream, 64, time.monotonic() + 0.1)
    finally:
        os.close(read)
        os.close(write)


def test_existing_profile_on_another_interface_is_refused_without_changes():
    module = runpy.run_path(str(SCRIPT))
    state = module["profile_state"]
    calls = []

    def command(arguments, **_options):
        calls.append(arguments)
        if "--fields" in arguments:
            return f"jittersim-ap:{UUID}\n".encode()
        return b"802-11-wireless\nwlan-management\nno\nap\nsae\n\n"

    with patch.dict(state.__globals__, {"command": command}):
        with pytest.raises(module["ProfileError"], match="ownership"):
            state("wlan0")
    assert len(calls) == 2
    assert all("show" in arguments for arguments in calls)
