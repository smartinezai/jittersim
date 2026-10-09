"""Root-only stop intent kept outside runtime files and release backups."""

import argparse
import fcntl
import hashlib
import json
import os
import secrets
import stat
import sys
import time
from pathlib import Path

PANIC_GUARD_FORMAT = 2
PANIC_GUARD_PATH = Path("/var/lib/jittersim-control/panic-state.json")
PANIC_LEGACY_PATH = Path("/run/jittersim/gateway-state.json")
PANIC_FRESH_UPDATE_ROOT = Path("/var/lib/jittersim-updates")
PANIC_INSTALL_LOCK_FD = 9
PANIC_FRESH_WITNESSES = tuple(
    Path(name)
    for name in (
        "/opt/jittersim",
        "/var/lib/jittersim",
        "/var/lib/jittersim-access",
        "/etc/jittersim",
        "/run/jittersim",
        "/usr/local/bin/jittersim",
        "/usr/local/lib/jittersim-update.py",
        "/usr/local/lib/jittersim-panic-guard.py",
        "/etc/sudoers.d/jittersim-update",
        "/etc/NetworkManager/dispatcher.d/80-jittersim-mdns",
    )
) + tuple(
    Path("/etc/systemd/system") / name
    for name in (
        "jittersim-helper.service",
        "jittersim-access-boot.service",
        "jittersim-dashboard.service",
        "jittersim-ap.service",
        "jittersim-dns.service",
        "jittersim-service-watch.service",
        "jittersim-update.service",
        "jittersim-mdns.service",
        "jittersim-browser-proxy.service",
        "jittersim-control-firewall.service",
    )
)
PANIC_GUARD_MAX_BYTES = 4096
PANIC_GUARD_FIELDS = (
    "panic_stop_latched",
    "household_stop_latched",
    "panic_recovery_failed",
    "control_revision_required",
)
PANIC_COMPATIBILITY_FILES = (
    "src/jittersim/panic_guard.py",
    "src/jittersim/networking/helper.py",
    "src/jittersim/networking/access.py",
)
PANIC_COMPATIBILITY_MAX_FILE_BYTES = 1024 * 1024


class PanicGuardError(OSError):
    """Stop intent cannot be safely read or durably saved."""


def validate_panic_guard(state):
    legacy_fields = tuple(
        field for field in PANIC_GUARD_FIELDS if field != "household_stop_latched"
    )
    if isinstance(state, dict) and type(state.get("version")) is int and state["version"] == 1:
        if set(state) != {"version", *legacy_fields} or any(
            type(state.get(field)) is not bool for field in legacy_fields
        ):
            raise PanicGuardError("Panic stop state is invalid")
        state = {
            **state,
            "version": PANIC_GUARD_FORMAT,
            "household_stop_latched": state["panic_stop_latched"],
        }
    if (
        not isinstance(state, dict)
        or set(state) != {"version", *PANIC_GUARD_FIELDS}
        or type(state.get("version")) is not int
        or state["version"] != PANIC_GUARD_FORMAT
        or any(type(state.get(field)) is not bool for field in PANIC_GUARD_FIELDS)
    ):
        raise PanicGuardError("Panic stop state is invalid")
    if (
        state["panic_recovery_failed"]
        and not (state["panic_stop_latched"] and state["household_stop_latched"])
    ) or (
        (state["panic_stop_latched"] or state["household_stop_latched"])
        and not state["control_revision_required"]
    ):
        raise PanicGuardError("Panic stop state is inconsistent")
    return dict(state)


def check_panic_candidate(state, candidate_code, approved_sources, *, owner_uid=0):
    """Require reviewed consumers for every established persistent stop record."""

    state = validate_panic_guard(state)
    refused = "The selected package cannot preserve Panic protection. Keep the current package."
    if (
        not isinstance(approved_sources, (list, tuple))
        or not 1 <= len(approved_sources) <= 32
        or any(
            not isinstance(bundle, dict)
            or set(bundle) != set(PANIC_COMPATIBILITY_FILES)
            or any(
                not isinstance(value, str)
                or len(value) != 64
                or any(character not in "0123456789abcdef" for character in value)
                for value in bundle.values()
            )
            for bundle in approved_sources
        )
    ):
        raise PanicGuardError(refused)

    observed = {}
    for name in PANIC_COMPATIBILITY_FILES:
        descriptors = []
        try:
            current = os.open(candidate_code, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            descriptors.append(current)
            for component in Path(name).parts[:-1]:
                current = os.open(
                    component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=current
                )
                descriptors.append(current)
            for directory in descriptors:
                metadata = os.fstat(directory)
                if metadata.st_uid != owner_uid or metadata.st_mode & 0o022:
                    raise PanicGuardError(refused)
            descriptor = os.open(
                Path(name).name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=current
            )
            with os.fdopen(descriptor, "rb") as source:
                metadata = os.fstat(source.fileno())
                if (
                    not stat.S_ISREG(metadata.st_mode)
                    or metadata.st_nlink != 1
                    or metadata.st_uid != owner_uid
                    or metadata.st_mode & 0o022
                ):
                    raise PanicGuardError(refused)
                contents = source.read(PANIC_COMPATIBILITY_MAX_FILE_BYTES + 1)
            if len(contents) > PANIC_COMPATIBILITY_MAX_FILE_BYTES:
                raise PanicGuardError(refused)
            observed[name] = hashlib.sha256(contents).hexdigest()
        except OSError as error:
            raise PanicGuardError(refused) from error
        finally:
            for directory in reversed(descriptors):
                os.close(directory)
    # The standalone update manager owns the reviewed allowlist. Never learn
    # compatibility from candidate labels or imports into a replaced environment.
    if not any(bundle == observed for bundle in approved_sources):
        raise PanicGuardError(refused)


def _fresh_update_directory(update_root, owner_uid):
    descriptor = os.open(update_root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        metadata = os.fstat(descriptor)
        if metadata.st_uid != owner_uid or stat.S_IMODE(metadata.st_mode) != 0o700:
            raise PanicGuardError("Prior update state cannot be classified as fresh")
    except BaseException:
        os.close(descriptor)
        raise
    return descriptor


def _fresh_lock_metadata(metadata, owner_uid):
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != owner_uid
        or metadata.st_nlink != 1
        or metadata.st_size != 0
        or metadata.st_mode & 0o022
    ):
        raise PanicGuardError("Prior update state cannot be classified as fresh")


def verify_fresh_installer_lock(update_root, descriptor, *, owner_uid=0):
    parent = _fresh_update_directory(update_root, owner_uid)
    try:
        opened = os.fstat(descriptor)
        named = os.stat("lock", dir_fd=parent, follow_symlinks=False)
        _fresh_lock_metadata(opened, owner_uid)
        _fresh_lock_metadata(named, owner_uid)
        if (opened.st_dev, opened.st_ino) != (named.st_dev, named.st_ino):
            raise PanicGuardError("The fresh installer lock could not be confirmed")
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    finally:
        os.close(parent)


# A concurrent writer (access-boot provisioning or the installer) holds the guard
# lock only for one fsynced save. Panic's save waits this long for it instead of
# failing at once (gap audit 2026-10-09, finding 8).
GUARD_LOCK_WAIT_SECONDS = 2.0


def _bounded_exclusive_lock(descriptor):
    deadline = time.monotonic() + GUARD_LOCK_WAIT_SECONDS
    while True:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return
        except BlockingIOError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.02)


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
            _bounded_exclusive_lock(lock)
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
        return self.write(self.inspect_upgrade(legacy_path), initialize_only=True)

    def inspect_upgrade(self, legacy_path):
        """Read proven current intent without creating or changing guard state.

        A caller must stop writers then provision again. This result is a preview
        and must never replace the producer's fresh read at that boundary.
        """

        state = self.read()
        if state is not None:
            return state
        if legacy_path is None:
            raise PanicGuardError(
                "The previous installation cannot prove Panic state for this upgrade"
            )
        state = {"version": PANIC_GUARD_FORMAT, **dict.fromkeys(PANIC_GUARD_FIELDS, False)}
        if legacy_path is not None:
            try:
                descriptor = os.open(legacy_path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            except FileNotFoundError:
                raise PanicGuardError(
                    "The previous installation cannot prove Panic state for this upgrade"
                ) from None
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
                    if (
                        not isinstance(old, dict)
                        or type(old.get("version")) is not int
                        or old["version"] != 1
                        or old.get("gateway_state") not in ("unknown", "available", "unavailable")
                        or any(
                            type(old.get(field)) is not bool
                            for field in (
                                "persistence_enabled",
                                "outage_observed",
                                "outage_stop_latched",
                                "reset_pending",
                                "recovery_pending",
                            )
                        )
                        or type(old.get("consecutive_failures")) is not int
                        or not 0 <= old["consecutive_failures"] <= 3
                        or type(old.get("consecutive_successes")) is not int
                        or not 0 <= old["consecutive_successes"] <= 2
                    ):
                        raise ValueError("invalid state")
                    if any(
                        field not in old
                        for field in PANIC_GUARD_FIELDS
                        if field != "household_stop_latched"
                    ):
                        raise ValueError("Panic state is incomplete")
                    for field in PANIC_GUARD_FIELDS:
                        if field in old:
                            if type(old[field]) is not bool:
                                raise ValueError("invalid flag")
                            state[field] = old[field]
                    state["household_stop_latched"] = old.get(
                        "household_stop_latched", old["panic_stop_latched"]
                    )
                    validate_panic_guard(state)
                except (UnicodeDecodeError, ValueError) as error:
                    raise PanicGuardError("Existing Panic stop record is invalid") from error
        return state

    def provision_upgrade(self, legacy_path):
        """Migrate complete proven old intent without repairing or inventing it."""

        return self.provision(legacy_path)

    def provision_fresh(
        self, *, witnesses=PANIC_FRESH_WITNESSES, update_root=PANIC_FRESH_UPDATE_ROOT
    ):
        state = self.read()
        if state is not None:
            return state
        if not isinstance(witnesses, (tuple, list)) or not 1 <= len(witnesses) <= 32:
            raise PanicGuardError("The installation could not be classified as fresh")
        for witness in witnesses:
            try:
                os.lstat(witness)
            except FileNotFoundError:
                continue
            raise PanicGuardError("Existing installation state must not be classified as fresh")
        try:
            parent = _fresh_update_directory(update_root, self.owner_uid)
        except FileNotFoundError:
            parent = None
        if parent is not None:
            try:
                with os.scandir(parent) as entries:
                    for count, entry in enumerate(entries):
                        if count >= 1 or entry.name != "lock":
                            raise PanicGuardError(
                                "Prior update state must not be classified as fresh"
                            )
                        _fresh_lock_metadata(entry.stat(follow_symlinks=False), self.owner_uid)
            finally:
                os.close(parent)
        return self.write(
            {"version": PANIC_GUARD_FORMAT, **dict.fromkeys(PANIC_GUARD_FIELDS, False)},
            initialize_only=True,
        )


def main(argv=None):
    parser = argparse.ArgumentParser(description="Preserve proven Panic state before an upgrade.")
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--provision-upgrade", action="store_true")
    modes.add_argument("--provision-fresh", action="store_true")
    arguments = parser.parse_args(argv)
    try:
        guard = PanicGuard()
        if arguments.provision_fresh:
            verify_fresh_installer_lock(
                PANIC_FRESH_UPDATE_ROOT, PANIC_INSTALL_LOCK_FD, owner_uid=guard.owner_uid
            )
            state = guard.provision_fresh(
                witnesses=PANIC_FRESH_WITNESSES, update_root=PANIC_FRESH_UPDATE_ROOT
            )
        else:
            state = guard.provision_upgrade(PANIC_LEGACY_PATH)
    except OSError:
        print(
            "Panic state is not proven for this upgrade. Keep the current package.", file=sys.stderr
        )
        return 1
    print(json.dumps(state, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
