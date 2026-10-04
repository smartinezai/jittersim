"""Bounded filesystem handoff from the dashboard to signed root staging.

The runtime lock is never removed. A ready record reserves admission briefly.
Physical lock ownership protects inputs for the entire operation, including
after a caller times out. Everything in the dashboard spool is untrusted.
"""

import fcntl
import hashlib
import json
import math
import os
import re
import stat
import subprocess
import sys
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from jittersim import updates
from jittersim.updates import (
    MAX_ARCHIVE, MAX_SIGNATURE, UPLOAD_ARCHIVE, UPLOAD_SIGNATURE, UpdateError, copy_bounded,
)

LOCK_PATH = Path("/run/jittersim/release-upload.lock")
RECORD = ".upload.json"
MAX_RECORD_BYTES = 1024
MAX_READY_SECONDS = 120
MAX_RECEIVE_SECONDS = 300
MAX_FOLDERS = 8
MAX_ENTRIES = 32
IDENTIFIER = re.compile(r"[0-9a-f]{32}")
LEAVES = (UPLOAD_ARCHIVE, UPLOAD_SIGNATURE)
FLAGS = os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0)
DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0)


class UploadBusy(UpdateError):
    def __init__(self):
        super().__init__("another release is being received or checked. Try again shortly")


def _now():
    return time.clock_gettime(time.CLOCK_MONOTONIC)


def _boot_id():
    try:
        with open("/proc/sys/kernel/random/boot_id", "rb") as handle:
            raw = handle.read(128)
    except FileNotFoundError:
        if sys.platform != "darwin":
            raise UpdateError("the machine boot identity is unavailable") from None
        raw = subprocess.run(["/usr/sbin/sysctl", "-n", "kern.boottime"],
                             check=True, capture_output=True, timeout=2).stdout
    if not raw or len(raw) > 128:
        raise UpdateError("the machine boot identity is unavailable")
    return hashlib.sha256(raw.strip()).hexdigest()


def _open_directory(path):
    """Anchor every component without following a dashboard-controlled link."""
    path = Path(path).absolute()
    descriptor = os.open(path.anchor, DIRECTORY_FLAGS)
    try:
        for part in path.parts[1:]:
            child = _open_at(part, DIRECTORY_FLAGS, descriptor, "the upload directory is not safe to open")
            os.close(descriptor)
            descriptor = child
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _open_at(name, flags, parent, message):
    try:
        return os.open(name, flags, dir_fd=parent)
    except OSError:
        raise UpdateError(message) from None


def _check_lock(descriptor):
    info = os.fstat(descriptor)
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != updates.TRUSTED_OWNER_UID
            or info.st_mode & 0o027):
        raise UpdateError("the release upload lock must be a protected administrator-owned file")


def prepare_lock(path=LOCK_PATH):
    """Root creates one stable inode. Restart must reuse it rather than replace it."""
    path = Path(path)
    parent = _open_directory(path.parent)
    try:
        info = os.fstat(parent)
        if info.st_uid != updates.TRUSTED_OWNER_UID or info.st_mode & 0o022:
            raise UpdateError("the release upload lock directory is not protected")
        created = False
        try:
            descriptor = os.open(path.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL
                                 | getattr(os, "O_NOFOLLOW", 0), 0o640, dir_fd=parent)
        except FileExistsError:
            descriptor = _open_at(path.name, FLAGS, parent, "the release upload lock is not safe to open")
        else:
            created = True
        try:
            if created:
                os.fchown(descriptor, updates.TRUSTED_OWNER_UID, info.st_gid)
                os.fchmod(descriptor, 0o640)
            _check_lock(descriptor)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        os.fsync(parent)
    finally:
        os.close(parent)


@contextmanager
def _lock(path):
    parent = _open_directory(Path(path).parent)
    try:
        descriptor = _open_at(Path(path).name, FLAGS, parent,
                              "the release upload lock is unavailable or is not safe to open")
    finally:
        os.close(parent)
    try:
        _check_lock(descriptor)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise UploadBusy() from None
        yield
    finally:
        os.close(descriptor)


def _entries(descriptor):
    names = []
    with os.scandir(descriptor) as entries:
        for entry in entries:
            names.append(entry.name)
            if len(names) > MAX_ENTRIES:
                raise UpdateError("too many retained upload entries. Administrator cleanup is needed")
    return names


def _read_record(spool):
    try:
        descriptor = os.open(RECORD, FLAGS, dir_fd=spool)
    except FileNotFoundError:
        return None
    except OSError:
        raise UpdateError("the upload record is not safe to open") from None
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise UpdateError("the upload record must be a regular file")
        raw = os.read(descriptor, MAX_RECORD_BYTES + 1)
    finally:
        os.close(descriptor)
    if len(raw) > MAX_RECORD_BYTES:
        raise UpdateError("the upload record is too large")
    try:
        record = json.loads(raw)
    except (ValueError, UnicodeError):
        raise UpdateError("the upload record is not understood") from None
    if (not isinstance(record, dict) or set(record) != {"id", "phase", "boot", "started", "expires"}
            or not isinstance(record["id"], str) or not IDENTIFIER.fullmatch(record["id"])
            or record["phase"] not in ("receiving", "ready")
            or not isinstance(record["boot"], str) or not re.fullmatch(r"[0-9a-f]{32,64}", record["boot"])):
        raise UpdateError("the upload record is not understood")
    for field in ("started", "expires"):
        try:
            finite = type(record[field]) in (int, float) and math.isfinite(record[field])
        except OverflowError:
            finite = False
        if not finite:
            raise UpdateError("the upload record has invalid times")
    maximum = MAX_READY_SECONDS if record["phase"] == "ready" else MAX_RECEIVE_SECONDS
    if not record["started"] < record["expires"] <= record["started"] + maximum:
        raise UpdateError("the upload record exceeds its time limit")
    return record


def _write_record(spool, record):
    temporary = ".meta-" + uuid.uuid4().hex
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL
                         | getattr(os, "O_NOFOLLOW", 0), 0o600, dir_fd=spool)
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(json.dumps(record).encode("ascii"))
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, RECORD, src_dir_fd=spool, dst_dir_fd=spool)
        os.fsync(spool)
    finally:
        try:
            os.unlink(temporary, dir_fd=spool)
        except FileNotFoundError:
            pass


def _live(record):
    now = _now()
    return (record["boot"] == _boot_id() and record["started"] <= now < record["expires"]
            and record["expires"] <= now + MAX_READY_SECONDS)


def _remove_folder(parent, name, opened=None):
    descriptor = opened
    if descriptor is None:
        descriptor = _open_at(name, DIRECTORY_FLAGS, parent,
                              "the retained upload folder is not safe to open")
    try:
        names = _entries(descriptor)
        if any(name not in LEAVES for name in names):
            raise UpdateError("the retained upload folder has unknown files")
        for leaf in names:
            os.unlink(leaf, dir_fd=descriptor)
        os.fsync(descriptor)
        try:
            current = os.stat(name, dir_fd=parent, follow_symlinks=False)
        except FileNotFoundError:
            return
        if stat.S_ISLNK(current.st_mode):
            os.unlink(name, dir_fd=parent)
        elif (current.st_dev, current.st_ino) == (
                os.fstat(descriptor).st_dev, os.fstat(descriptor).st_ino):
            os.rmdir(name, dir_fd=parent)
        os.fsync(parent)
    finally:
        if opened is None:
            os.close(descriptor)


def _clean(spool, *, reserved=None, incoming=False):
    names = _entries(spool)
    folders = [name for name in names if IDENTIFIER.fullmatch(name)]
    if len(folders) > MAX_FOLDERS:
        raise UpdateError("too many retained upload folders. Administrator cleanup is needed")
    allowed = {RECORD, *LEAVES} if not incoming else {".copy.lock"}
    for name in names:
        if name in folders:
            if name != reserved:
                _remove_folder(spool, name)
        elif name.startswith(".meta-") and IDENTIFIER.fullmatch(name[6:]):
            os.unlink(name, dir_fd=spool)
        elif not incoming and name in LEAVES:
            os.unlink(name, dir_fd=spool)
        elif name not in allowed:
            raise UpdateError("the upload folder has unknown entries")


def _open_spool(path, *, create=False):
    path = Path(path)
    if create:
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor = _open_directory(path)
    info = os.fstat(descriptor)
    if info.st_mode & 0o022:
        os.close(descriptor)
        raise UpdateError("the upload directory must not be writable by others")
    return descriptor


@dataclass
class Receipt:
    identifier: str
    folder: Path
    spool: int
    published: bool = False

    def ready(self):
        directory = os.open(self.identifier, DIRECTORY_FLAGS, dir_fd=self.spool)
        try:
            for leaf, maximum in ((UPLOAD_ARCHIVE, MAX_ARCHIVE), (UPLOAD_SIGNATURE, MAX_SIGNATURE)):
                source = os.open(leaf, FLAGS, dir_fd=directory)
                try:
                    info = os.fstat(source)
                    if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= maximum:
                        raise UpdateError("the upload files are incomplete or too large")
                    os.fsync(source)
                finally:
                    os.close(source)
            os.fsync(directory)
        finally:
            os.close(directory)
        now = _now()
        _write_record(self.spool, {"id": self.identifier, "phase": "ready", "boot": _boot_id(),
                                   "started": now, "expires": now + MAX_READY_SECONDS})
        self.published = True


@contextmanager
def receiving(spool, lock=LOCK_PATH, *, seconds=MAX_RECEIVE_SECONDS):
    if not 0 < seconds <= MAX_RECEIVE_SECONDS:
        raise UpdateError("the upload receipt time limit is invalid")
    with _lock(lock):
        descriptor = _open_spool(spool, create=True)
        receipt = None
        try:
            previous = _read_record(descriptor)
            if previous and previous["phase"] == "ready" and _live(previous):
                raise UploadBusy()
            _clean(descriptor)
            identifier = uuid.uuid4().hex
            now = _now()
            _write_record(descriptor, {"id": identifier, "phase": "receiving", "boot": _boot_id(),
                                      "started": now, "expires": now + seconds})
            os.mkdir(identifier, 0o700, dir_fd=descriptor)
            os.fsync(descriptor)
            receipt = Receipt(identifier, Path(spool) / identifier, descriptor)
            yield receipt
        finally:
            try:
                if receipt is not None and not receipt.published:
                    _remove_folder(descriptor, receipt.identifier)
                    os.unlink(RECORD, dir_fd=descriptor)
                    os.fsync(descriptor)
            finally:
                os.close(descriptor)


def discard(identifier, *, spool, lock=LOCK_PATH):
    try:
        with _lock(lock):
            descriptor = _open_spool(spool)
            try:
                record = _read_record(descriptor)
                if record and record["id"] == identifier:
                    _remove_folder(descriptor, identifier)
                    os.unlink(RECORD, dir_fd=descriptor)
                    os.fsync(descriptor)
                return True
            finally:
                os.close(descriptor)
    except UploadBusy:
        return False


@contextmanager
def root_inputs(identifier, *, spool, lock=LOCK_PATH, root=updates.UPDATE_ROOT):
    if identifier is not None and (not isinstance(identifier, str) or not IDENTIFIER.fullmatch(identifier)):
        raise UpdateError("the uploaded release identifier is invalid")
    with _lock(lock):
        spool_descriptor = _open_spool(spool)
        source_directory = None
        admitted = False
        try:
            record = _read_record(spool_descriptor)
            if identifier is None:
                if record:
                    raise UpdateError("an uploaded release identifier is required")
                source_directory = os.dup(spool_descriptor)
            else:
                if not record or record["id"] != identifier or record["phase"] != "ready":
                    raise UpdateError("the uploaded release waiting for checking changed")
                if not _live(record):
                    raise UpdateError("the uploaded release start allowance expired or its boot changed")
                source_directory = _open_at(identifier, DIRECTORY_FLAGS, spool_descriptor,
                                             "the uploaded release folder is not safe to open")
            admitted = True
            incoming = Path(root) / updates.INCOMING_FOLDER
            incoming.mkdir(mode=0o700, parents=True, exist_ok=True)
            incoming_descriptor = _open_directory(incoming)
            try:
                info = os.fstat(incoming_descriptor)
                if info.st_uid != updates.TRUSTED_OWNER_UID or info.st_mode & 0o077:
                    raise UpdateError("the private incoming release directory is not protected")
                copy_lock = incoming / ".copy.lock"
                # This root-only lock spans all custom spool calls too.
                prepare_lock(copy_lock)
                with _lock(copy_lock):
                    _clean(incoming_descriptor, incoming=True)
                    private_id = uuid.uuid4().hex
                    os.mkdir(private_id, 0o700, dir_fd=incoming_descriptor)
                    os.fsync(incoming_descriptor)
                    private = incoming / private_id
                    try:
                        archive, signature = [private / leaf for leaf in LEAVES]
                        for leaf, destination, maximum in (
                                (UPLOAD_ARCHIVE, archive, MAX_ARCHIVE),
                                (UPLOAD_SIGNATURE, signature, MAX_SIGNATURE)):
                            source = _open_at(leaf, FLAGS, source_directory,
                                              "uploaded release inputs are missing or are not safe to open")
                            try:
                                if not stat.S_ISREG(os.fstat(source).st_mode):
                                    raise UpdateError("uploaded release inputs must be regular files")
                                copy_bounded(source, destination, maximum,
                                             "uploaded release archive" if leaf == UPLOAD_ARCHIVE
                                             else "uploaded release signature")
                            finally:
                                os.close(source)
                        yield archive, signature
                    finally:
                        _remove_folder(incoming_descriptor, private_id)
            finally:
                os.close(incoming_descriptor)
        finally:
            try:
                if source_directory is not None:
                    try:
                        if admitted:
                            if identifier is None:
                                for leaf in LEAVES:
                                    try:
                                        os.unlink(leaf, dir_fd=source_directory)
                                    except FileNotFoundError:
                                        pass
                            else:
                                _remove_folder(spool_descriptor, identifier, source_directory)
                                current = _read_record(spool_descriptor)
                                if current and current["id"] == identifier:
                                    os.unlink(RECORD, dir_fd=spool_descriptor)
                                os.fsync(spool_descriptor)
                    finally:
                        os.close(source_directory)
            finally:
                os.close(spool_descriptor)
