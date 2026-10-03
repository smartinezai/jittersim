"""Root-only stop intent kept outside runtime files and release backups."""

import fcntl
import json
import os
import secrets
import stat
from pathlib import Path

PANIC_GUARD_FORMAT = 1
PANIC_GUARD_PATH = Path("/var/lib/jittersim-control/panic-state.json")
PANIC_GUARD_MAX_BYTES = 4096
PANIC_GUARD_FIELDS = (
    "panic_stop_latched",
    "panic_recovery_failed",
    "control_revision_required",
)


class PanicGuardError(OSError):
    """Stop intent cannot be safely read or durably saved."""


def validate_panic_guard(state):
    if (
        not isinstance(state, dict)
        or set(state) != {"version", *PANIC_GUARD_FIELDS}
        or type(state.get("version")) is not int
        or state["version"] != PANIC_GUARD_FORMAT
        or any(type(state.get(field)) is not bool for field in PANIC_GUARD_FIELDS)
    ):
        raise PanicGuardError("Panic stop state is invalid")
    if (state["panic_recovery_failed"] and not state["panic_stop_latched"]) or (
        state["panic_stop_latched"] and not state["control_revision_required"]
    ):
        raise PanicGuardError("Panic stop state is inconsistent")
    return dict(state)


class PanicGuard:
    def __init__(self, path=PANIC_GUARD_PATH, *, owner_uid=0):
        self.path = Path(path)
        self.owner_uid = owner_uid

    def _directory(self, create=False):
        if create:
            try:
                self.path.parent.mkdir(mode=0o700)
            except FileExistsError:
                pass
        descriptor = os.open(self.path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            metadata = os.fstat(descriptor)
            if metadata.st_uid != self.owner_uid or stat.S_IMODE(metadata.st_mode) != 0o700:
                raise PanicGuardError("Panic stop directory is not root-private")
        except BaseException:
            os.close(descriptor)
            raise
        return descriptor

    def read(self):
        """None means a first unprovisioned namespace, never a missing record."""

        try:
            parent = self._directory()
        except FileNotFoundError:
            # A dangling symlink is not an unprovisioned namespace.
            if self.path.parent.is_symlink():
                raise PanicGuardError("Panic stop directory is invalid")
            return None
        try:
            descriptor = os.open(
                self.path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent
            )
            with os.fdopen(descriptor, "rb") as source:
                metadata = os.fstat(source.fileno())
                if (
                    not stat.S_ISREG(metadata.st_mode)
                    or metadata.st_nlink != 1
                    or metadata.st_uid != self.owner_uid
                    or stat.S_IMODE(metadata.st_mode) != 0o600
                ):
                    raise PanicGuardError("Panic stop record is not root-private")
                contents = source.read(PANIC_GUARD_MAX_BYTES + 1)
            if len(contents) > PANIC_GUARD_MAX_BYTES:
                raise PanicGuardError("Panic stop record is too large")
            try:
                return validate_panic_guard(json.loads(contents.decode("utf-8")))
            except (UnicodeDecodeError, ValueError) as error:
                raise PanicGuardError("Panic stop record is invalid") from error
        finally:
            os.close(parent)

    def write(self, state, *, initialize_only=False):
        state = validate_panic_guard(state)
        parent = self._directory(create=True)
        temporary = ".panic-" + secrets.token_bytes(16).hex()
        lock = None
        try:
            lock = os.open(
                ".guard.lock",
                os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK,
                0o600,
                dir_fd=parent,
            )
            metadata = os.fstat(lock)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_nlink != 1
                or metadata.st_uid != self.owner_uid
                or stat.S_IMODE(metadata.st_mode) != 0o600
            ):
                raise PanicGuardError("Panic stop lock is not root-private")
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            if initialize_only:
                try:
                    existing = self.read()
                except FileNotFoundError:
                    existing = None
                if existing is not None:
                    return existing
            descriptor = os.open(
                temporary,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
                dir_fd=parent,
            )
            with os.fdopen(descriptor, "w", encoding="utf-8") as output:
                json.dump(state, output, sort_keys=True)
                output.write("\n")
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, self.path.name, src_dir_fd=parent, dst_dir_fd=parent)
            os.fsync(parent)
            # Persist first namespace creation as well as replacement within it.
            ancestor = os.open(
                self.path.parent.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
            )
            try:
                os.fsync(ancestor)
            finally:
                os.close(ancestor)
            return state
        finally:
            try:
                os.unlink(temporary, dir_fd=parent)
            except FileNotFoundError:
                pass
            if lock is not None:
                os.close(lock)
            os.close(parent)

    def provision(self, legacy_path=None):
        state = self.read()
        if state is not None:
            return state
        state = {"version": PANIC_GUARD_FORMAT, **dict.fromkeys(PANIC_GUARD_FIELDS, False)}
        if legacy_path is not None:
            try:
                descriptor = os.open(legacy_path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            except FileNotFoundError:
                descriptor = None
            if descriptor is not None:
                with os.fdopen(descriptor, "rb") as source:
                    metadata = os.fstat(source.fileno())
                    if (
                        not stat.S_ISREG(metadata.st_mode)
                        or metadata.st_uid != self.owner_uid
                        or metadata.st_nlink != 1
                        or metadata.st_mode & 0o022
                    ):
                        raise PanicGuardError("Existing Panic stop record is invalid")
                    contents = source.read(PANIC_GUARD_MAX_BYTES + 1)
                if len(contents) > PANIC_GUARD_MAX_BYTES:
                    raise PanicGuardError("Existing Panic stop record is too large")
                try:
                    old = json.loads(contents.decode("utf-8"))
                    if not isinstance(old, dict) or old.get("version") != 1:
                        raise ValueError("invalid state")
                    for field in PANIC_GUARD_FIELDS:
                        if field in old:
                            if type(old[field]) is not bool:
                                raise ValueError("invalid flag")
                            state[field] = old[field]
                    if state["panic_stop_latched"]:
                        state["control_revision_required"] = True
                except (UnicodeDecodeError, ValueError) as error:
                    raise PanicGuardError("Existing Panic stop record is invalid") from error
        return self.write(state, initialize_only=True)
