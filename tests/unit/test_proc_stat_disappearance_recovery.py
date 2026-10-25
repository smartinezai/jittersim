"""Disappearing procfs entries must not hide remaining command-group members."""
import io
import time
from types import SimpleNamespace

import pytest

from jittersim import updates


def proc_entries(monkeypatch, entries):
    class StatFile(io.StringIO):
        def __init__(self, value):
            self.value = value
            super().__init__(value if isinstance(value, str) else "")

        def read(self, size=-1):
            if isinstance(self.value, BaseException):
                raise self.value
            return super().read(size)

    class ProcPath:
        def __init__(self, path):
            self.path = str(path)

        def is_dir(self):
            assert self.path == "/proc"
            return True

        def __truediv__(self, name):
            assert name == "stat"
            return ProcPath(self.path + "/stat")

        def open(self):
            value = entries[self.path.split("/")[2]]
            if isinstance(value, tuple):
                stage, value = value
                if stage == "open":
                    raise value
            return StatFile(value)

    class Scan:
        def __enter__(self):
            return iter(SimpleNamespace(name=pid, path="/proc/" + pid) for pid in entries)

        def __exit__(self, *args):
            return False

    monkeypatch.setattr(updates, "Path", ProcPath)
    monkeypatch.setattr(updates.os, "scandir", lambda path: Scan())


@pytest.mark.parametrize("stage", ["open", "read"])
def test_disappearing_entry_does_not_hide_a_later_live_group_member(monkeypatch, stage):
    proc_entries(monkeypatch, {
        "123": (stage, ProcessLookupError(3, "process disappeared")),
        "124": "124 (fixture process) S 1 77 0 0",
    })
    assert updates.command_group_running(77, time.monotonic() + 2) is True


@pytest.mark.parametrize("stage", ["open", "read"])
def test_all_disappeared_entries_mean_no_remaining_group_member(monkeypatch, stage):
    proc_entries(monkeypatch, {"123": (stage, ProcessLookupError(3, "process disappeared"))})
    assert updates.command_group_running(77, time.monotonic() + 2) is False


@pytest.mark.parametrize("error", [PermissionError(13, "not readable"), OSError(5, "I/O failure")])
def test_other_os_failures_still_fail_closed(monkeypatch, error):
    proc_entries(monkeypatch, {"123": error})
    with pytest.raises(type(error)):
        updates.command_group_running(77, time.monotonic() + 2)


def test_malformed_stat_still_fails_closed(monkeypatch):
    proc_entries(monkeypatch, {"123": "not a proc stat record"})
    with pytest.raises(updates.UpdateError, match="could not be confirmed"):
        updates.command_group_running(77, time.monotonic() + 2)
