"""Inject one named failure into a durable state write on a disposable fixture.

The stand-in replaces the `os` name inside one production module only. It
delegates everything else to the real module, so a test can fail exactly one
step of one writer without touching other code in the process. It fails the
operations of temporary state files and the directory syncs next to them, never
a path outside the fixture.
"""

import errno
import os
import stat
import threading

GUARD_STAGES = ("write", "flush", "file-sync", "replace", "dir-sync", "ancestor-sync")
GATEWAY_STAGES = ("write", "flush", "file-sync", "replace", "dir-sync")
# Writes that fail before the atomic rename leave the old committed file.
# Writes that fail at or after a directory sync already replaced it.
BEFORE_RENAME = ("write", "flush", "file-sync", "replace")


class FaultPlan:
    """Which step fails and whether the failure is switched on right now.

    With `stalled` set, the step waits for the test to release it and then
    succeeds. That models a storage stall instead of a storage error.
    """

    STALL_LIMIT_SECONDS = 20

    def __init__(self, stage, *, stalled=False):
        self.stage = stage
        self.armed = False
        self.hits = 0
        self.directory_syncs = 0
        self.entered = threading.Event()
        self.release = threading.Event()
        self.stalled = stalled

    def fires(self, stage):
        """Return True when this step must raise. A stalled step waits and returns False."""

        if not (self.armed and stage == self.stage):
            return False
        self.hits += 1
        if not self.stalled:
            return True
        self.entered.set()
        # A bounded wait keeps a broken test from holding a thread forever.
        self.release.wait(self.STALL_LIMIT_SECONDS)
        return False


class _FaultyStream:
    def __init__(self, stream, plan, owner):
        self._stream = stream
        self._plan = plan
        self._owner = owner
        self._descriptor = stream.fileno()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
        return False

    def write(self, data):
        if self._plan.fires("write"):
            raise OSError(errno.ENOSPC, "injected write failure")
        return self._stream.write(data)

    def flush(self):
        if self._plan.fires("flush"):
            raise OSError(errno.EIO, "injected flush failure")
        return self._stream.flush()

    def fileno(self):
        return self._descriptor

    def close(self):
        self._owner.temporary.discard(self._descriptor)
        self._stream.close()


class FaultyOs:
    """Behaves like `os` except that one step of a temporary-file write fails."""

    def __init__(self, plan, temporary_prefix):
        self.plan = plan
        self.prefix = temporary_prefix
        self.temporary = set()

    def __getattr__(self, name):
        return getattr(os, name)

    def open(self, path, flags, *args, **kwargs):
        descriptor = os.open(path, flags, *args, **kwargs)
        if os.path.basename(os.fspath(path)).startswith(self.prefix):
            self.temporary.add(descriptor)
            self.plan.directory_syncs = 0
        return descriptor

    def fdopen(self, descriptor, *args, **kwargs):
        stream = os.fdopen(descriptor, *args, **kwargs)
        if descriptor in self.temporary:
            return _FaultyStream(stream, self.plan, self)
        return stream

    def fsync(self, descriptor):
        if stat.S_ISDIR(os.fstat(descriptor).st_mode):
            self.plan.directory_syncs += 1
            stage = "dir-sync" if self.plan.directory_syncs == 1 else "ancestor-sync"
        elif descriptor in self.temporary:
            stage = "file-sync"
        else:
            stage = None
        if stage is not None and self.plan.fires(stage):
            raise OSError(errno.EIO, "injected " + stage + " failure")
        return os.fsync(descriptor)

    def replace(self, *args, **kwargs):
        if self.plan.fires("replace"):
            raise OSError(errno.EIO, "injected rename failure")
        return os.replace(*args, **kwargs)
