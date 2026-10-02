"""Install administrator-approved releases with a local recovery copy.

Only the root-only staging command accepts an archive or a path. Dashboard
requests can name only the single release already approved on this Pi.
"""

import argparse
import fcntl
import gzip
import hashlib
import http.client
import json
import os
import re
import shutil
import socket
import ssl
import subprocess
import tarfile
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path, PurePosixPath

UPDATE_ROOT = Path("/var/lib/jittersim-updates")
CODE_ROOT = Path("/opt/jittersim")
CONFIG_ROOT = Path("/etc/jittersim")
DATA_ROOT = Path("/var/lib/jittersim")
ACCESS_ROOT = Path("/var/lib/jittersim-access")
UNITS_ROOT = Path("/etc/systemd/system")
MANAGER_PATH = Path("/usr/local/lib/jittersim-update.py")
MAX_ARCHIVE = 25 * 1024 * 1024
MAX_EXPANDED = 100 * 1024 * 1024
SERVICES = (
    "jittersim-helper.service",
    "jittersim-dashboard.service",
    "jittersim-browser-proxy.service",
)
UNITS = SERVICES + (
    "jittersim-control-firewall.service", "jittersim-update.service",
    "jittersim-dns.service", "jittersim-access-boot.service", "jittersim-ap.service",
    "jittersim-service-watch.service",
)
VERSION = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")


class UpdateError(RuntimeError):
    """The release cannot be installed or recovered safely."""


def atomic_json(path, value):
    temporary = path.with_suffix(".tmp")
    with temporary.open("w") as handle:
        temporary.chmod(0o600)
        handle.write(json.dumps(value))
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)
    descriptor = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def read_json(path):
    if not path.exists():
        return {}
    if path.stat().st_size > 65536:
        raise UpdateError("update record is too large")
    return json.loads(path.read_text())


def install_manager(source, destination):
    """Replace recovery code atomically so interruption keeps a runnable copy."""
    temporary = destination.with_suffix(".next")
    shutil.copyfile(source, temporary)
    temporary.chmod(0o644)
    with temporary.open("rb") as handle:
        os.fsync(handle.fileno())
    temporary.replace(destination)
    descriptor = os.open(destination.parent, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


@contextmanager
def update_lock(root=UPDATE_ROOT):
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    if root.is_symlink() or root.stat().st_uid != os.geteuid() or root.stat().st_mode & 0o022:
        raise UpdateError("update directory must be owned by the administrator")
    with (root / "lock").open("a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise UpdateError("an update operation is already running") from error
        yield


def validate_archive(archive):
    """Inspect all members before writing a single extracted file."""
    members = []
    total = 0
    names = set()
    for member in archive:
        path = PurePosixPath(member.name)
        if (
            not path.parts
            or path.is_absolute()
            or ".." in path.parts
            or member.issym()
            or member.islnk()
            or not (member.isdir() or member.isfile())
            or path.parts[0] not in {"src", "deploy", "README.md", "pyproject.toml"}
            or str(path) in names
            or len(member.name) > 240
        ):
            raise UpdateError("release archive contains an unsafe or unexpected entry")
        names.add(str(path))
        total += member.size
        if total > MAX_EXPANDED or len(members) >= 5000:
            raise UpdateError("release archive exceeds its size or file limit")
        members.append(member)
    required = {
        "pyproject.toml",
        "README.md",
        "src/jittersim/api/server.py",
        "src/jittersim/updates.py",
    }
    required.update("deploy/raspberry-pi/" + unit for unit in UNITS)
    if not required.issubset(
        {str(PurePosixPath(member.name)) for member in members if member.isfile()}
    ):
        raise UpdateError("release archive is missing required files")
    return members


def stage_release(path, digest, release_version, *, root=UPDATE_ROOT):
    if not VERSION.fullmatch(release_version) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise UpdateError("version or SHA-256 is invalid")
    if not path.is_file() or path.stat().st_size > MAX_ARCHIVE:
        raise UpdateError("release archive is too large")
    with update_lock(root):
        if (root / "request.json").exists():
            raise UpdateError("a queued update must finish before staging another release")
        with tempfile.TemporaryDirectory(dir=root) as work:
            work = Path(work)
            copied = work / "release.tar"
            # Hash and extract the same private copy, even if the input changes.
            with path.open("rb") as source, copied.open("wb") as target:
                remaining = MAX_ARCHIVE + 1
                while remaining:
                    block = source.read(min(65536, remaining))
                    if not block:
                        break
                    target.write(block)
                    remaining -= len(block)
                if remaining == 0:
                    raise UpdateError("release archive is too large")
            if hashlib.sha256(copied.read_bytes()).hexdigest() != digest:
                raise UpdateError("release SHA-256 does not match the approved checksum")
            extracted = work / "source"
            extracted.mkdir()
            # Expand once under a strict limit before the tar parser reads metadata.
            plain = work / "plain.tar"
            with copied.open("rb") as source:
                magic = source.read(2)
                source.seek(0)
                stream = gzip.GzipFile(fileobj=source) if magic == b"\x1f\x8b" else source
                with plain.open("wb") as target:
                    remaining = MAX_EXPANDED + 16 * 1024 * 1024
                    while True:
                        block = stream.read(min(65536, remaining + 1))
                        if not block:
                            break
                        remaining -= len(block)
                        if remaining < 0:
                            raise UpdateError("expanded release archive is too large")
                        target.write(block)
            with tarfile.open(plain, mode="r:") as archive:
                members = validate_archive(archive)
                for member in members:
                    destination = extracted / member.name
                    if member.isdir():
                        destination.mkdir(parents=True, exist_ok=True)
                    else:
                        destination.parent.mkdir(parents=True, exist_ok=True)
                        with (
                            archive.extractfile(member) as source,
                            destination.open("wb") as target,
                        ):
                            shutil.copyfileobj(source, target)
                        destination.chmod(0o755 if member.mode & 0o111 else 0o644)
            atomic_json(
                extracted / ".jittersim-release.json",
                {"version": release_version, "sha256": digest},
            )
            staged = root / "staged"
            if staged.exists():
                shutil.rmtree(staged)
            extracted.rename(staged)
        atomic_json(
            root / "status.json",
            {"phase": "ready", "message": "Release verified and ready to install."},
        )


def update_status(*, root=UPDATE_ROOT, code=CODE_ROOT):
    installed = read_json(code / ".jittersim-release.json")
    staged = read_json(root / "staged" / ".jittersim-release.json")
    status = read_json(root / "status.json")
    return {
        "installed": installed.get("version", "Unversioned installation"),
        "available": staged.get("version"),
        "phase": status.get("phase", "idle"),
        "message": status.get("message", "No approved release has been staged on this Pi."),
        "rollback_available": (root / "backup" / "complete.json").exists(),
    }


def run_command(command, timeout=300):
    try:
        subprocess.run(
            command,
            check=True,
            timeout=timeout,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except (subprocess.SubprocessError, OSError) as error:
        raise UpdateError("release command failed: " + Path(command[0]).name) from error


def request_update(
    release_version, *, rollback=False, root=UPDATE_ROOT, code=CODE_ROOT, run=run_command
):
    with update_lock(root):
        status = update_status(root=root, code=code)
        if (root / "request.json").exists():
            raise UpdateError("an update is already queued")
        if rollback:
            if not status["rollback_available"]:
                raise UpdateError("no recovery copy is available")
        elif release_version != status["available"] or not isinstance(release_version, str):
            raise UpdateError("check the staged release again before installing")
        atomic_json(root / "request.json", {"version": release_version, "rollback": rollback})
        atomic_json(
            root / "status.json",
            {"phase": "queued", "message": "Update queued. The dashboard will reconnect."},
        )
        try:
            run(["systemctl", "start", "--no-block", "jittersim-update.service"], 10)
        except Exception:
            (root / "request.json").unlink()
            atomic_json(
                root / "status.json",
                {"phase": "failed", "message": "The update service could not start."},
            )
            raise


def healthy(*, config=CONFIG_ROOT, run=run_command, port=8080):
    try:
        for service in SERVICES:
            run(["systemctl", "is-active", "--quiet", service], 5)
        token = next(
            line.split("=", 1)[1].strip()
            for line in (config / "control.env").read_text().splitlines()
            if line.startswith("JITTERSIM_ADMIN_TOKEN=")
        )
        hostname = socket.gethostname().split(".")[0] + ".local"
        context = ssl.create_default_context(cafile=str(config / "dashboard.crt"))
        connection = http.client.HTTPSConnection(hostname, port, timeout=3, context=context)
        try:
            connection.sock = context.wrap_socket(
                socket.create_connection(("127.0.0.1", port), timeout=3), server_hostname=hostname
            )
            connection.request("GET", "/api/health", headers={"Authorization": "Bearer " + token})
            result = connection.getresponse()
            body = result.read(65537)
            if len(body) > 65536:
                return False
            data = json.loads(body)
            return (
                result.status == 200
                and data.get("status") == "ok"
                and data.get("helper_reachable") is True
            )
        finally:
            connection.close()
    except (OSError, ValueError, StopIteration, UpdateError, http.client.HTTPException):
        return False


def apply_update(
    *,
    root=UPDATE_ROOT,
    code=CODE_ROOT,
    config=CONFIG_ROOT,
    data=DATA_ROOT,
    access=ACCESS_ROOT,
    units=UNITS_ROOT,
    manager=MANAGER_PATH,
    run=run_command,
    health=None,
):
    health = health or (lambda: healthy(config=config, run=run))
    with update_lock(root):
        request = read_json(root / "request.json")
        previous_phase = read_json(root / "status.json").get("phase")
        if not request:
            return
        backup = root / "backup"
        if previous_phase in ("installing", "checking", "rolling-back"):
            completed = read_json(backup / "complete.json")
            if completed.get("target_version") == request.get("version"):
                request["rollback"] = True

        if request.get("rollback") and (access / "policy.json").exists():
            try:
                with (access / "policy.json").open('rb') as stream:
                    raw_policy = stream.read(256 * 1024 + 1)
                if len(raw_policy) > 256 * 1024:
                    raise ValueError('policy is too large')
                policy = json.loads(raw_policy)
                if (policy.get("options", {}).get("enabled", True)
                        and not (backup / "code/src/jittersim/networking/access.py").exists()):
                    raise UpdateError("disable household restrictions before restoring a release without access controls")
            except (ValueError, OSError, AttributeError, UpdateError) as error:
                message = str(error) if isinstance(error, UpdateError) else "Saved access rules could not be checked before rollback."
                atomic_json(root / 'status.json', {'phase': 'failed', 'message': message})
                (root / 'request.json').unlink(missing_ok=True)
                raise UpdateError(message) from error

        def progress(phase, message):
            atomic_json(root / "status.json", {"phase": phase, "message": message})

        def restore():
            if not (backup / "complete.json").exists():
                raise UpdateError("the recovery copy is incomplete")
            ownership = read_json(backup / "complete.json").get("ownership", {})
            for name, destination in (("code", code), ("config", config), ("data", data)):
                if destination.exists():
                    shutil.rmtree(destination)
                shutil.copytree(backup / name, destination, symlinks=True)
                if name in ownership:
                    uid, gid = ownership[name]
                    for entry in [destination, *destination.rglob("*")]:
                        os.chown(entry, uid, gid, follow_symlinks=False)
            for unit in UNITS:
                source = backup / "units" / unit
                if source.exists():
                    shutil.copy2(source, units / unit)
                elif (units / unit).exists():
                    (units / unit).unlink()
            if (backup / "access").exists():
                if access.exists():
                    shutil.rmtree(access)
                shutil.copytree(backup / "access", access, symlinks=True)
                if "access" in ownership:
                    uid, gid = ownership["access"]
                    for entry in [access, *access.rglob("*")]:
                        os.chown(entry, uid, gid, follow_symlinks=False)
            if (backup / "manager.py").exists():
                install_manager(backup / "manager.py", manager)
            run(["systemctl", "daemon-reload"], 30)
            run(["systemctl", "start", *SERVICES], 60)

        def wait_healthy():
            for _ in range(30):
                if health():
                    return True
                time.sleep(1)
            return False

        recovered = False
        keep_request = True
        backup_taken = False
        try:
            progress("stopping", "Stopping services before the settings backup.")
            run(["systemctl", "stop", *reversed(SERVICES)], 60)
            if request.get("rollback"):
                progress("rolling-back", "Restoring the previous installation and settings.")
                restore()
                if not wait_healthy():
                    raise UpdateError("recovery health check failed")
                recovered = True
                progress("rolled-back", "Previous installation restored and health check passed.")
                keep_request = False
                return
            staged = root / "staged"
            if read_json(staged / ".jittersim-release.json").get("version") != request.get(
                "version"
            ):
                raise UpdateError("staged release changed")
            progress("backing-up", "Backing up the installation and settings.")
            candidate = root / "backup-next"
            if candidate.exists():
                shutil.rmtree(candidate)
            candidate.mkdir()
            required = sum(
                item.stat().st_size
                for directory in (code, config, data)
                for item in directory.rglob("*")
                if item.is_file() and not item.is_symlink()
            )
            if shutil.disk_usage(root).free < required * 2 + MAX_EXPANDED:
                raise UpdateError("not enough free space for update and recovery")
            for name, source in (("code", code), ("config", config), ("data", data)):
                shutil.copytree(source, candidate / name, symlinks=True)
            if access.exists():
                shutil.copytree(access, candidate / "access", symlinks=True)
            (candidate / "units").mkdir()
            for unit in UNITS:
                if (units / unit).exists():
                    shutil.copy2(units / unit, candidate / "units" / unit)
            if manager.exists():
                shutil.copy2(manager, candidate / "manager.py")
            atomic_json(
                candidate / "complete.json",
                {
                    "created_at": int(time.time()),
                    "target_version": request.get("version"),
                    "ownership": {
                        name: [source.stat().st_uid, source.stat().st_gid]
                        for name, source in (("code", code), ("config", config), ("data", data), ("access", access))
                        if source.exists()
                    },
                },
            )
            if backup.exists():
                shutil.rmtree(backup)
            candidate.rename(backup)
            backup_taken = True
            progress("installing", "Installing the approved release.")
            for name in ("src", "deploy"):
                if (code / name).exists():
                    shutil.rmtree(code / name)
                shutil.copytree(staged / name, code / name)
            for name in ("pyproject.toml", "README.md", ".jittersim-release.json"):
                shutil.copy2(staged / name, code / name)
            run(
                [
                    str(code / ".venv/bin/python"),
                    "-m",
                    "pip",
                    "install",
                    "--no-input",
                    "--disable-pip-version-check",
                    str(code),
                ],
                300,
            )
            for unit in UNITS:
                source = code / "deploy/raspberry-pi" / unit
                if not source.is_file():
                    raise UpdateError("release is missing a service definition")
                shutil.copy2(source, units / unit)
            run(["systemctl", "daemon-reload"], 30)
            progress("checking", "Restarting services and checking the dashboard and helper.")
            run(["systemctl", "start", *SERVICES], 60)
            if not wait_healthy():
                raise UpdateError("updated services failed their health check")
            install_manager(code / "src/jittersim/updates.py", manager)
            progress("complete", "Release installed. Dashboard and helper health checks passed.")
            keep_request = False
        except Exception:
            needs_recovery = request.get("rollback") or backup_taken
            try:
                if needs_recovery:
                    # Persist recovery intent before touching the installation.
                    request["rollback"] = True
                    atomic_json(root / "request.json", request)
                    progress("rolling-back", "Update failed. Restoring the recovery copy.")
                    run(["systemctl", "stop", *reversed(SERVICES)], 60)
                    restore()
                    recovered = wait_healthy()
                else:
                    run(["systemctl", "start", *SERVICES], 60)
            except Exception:
                recovered = False
            keep_request = bool(needs_recovery and not recovered)
            progress(
                "rolled-back" if recovered else "failed",
                "Update failed. Previous installation restored and checked."
                if recovered
                else "Update or recovery failed. Use Ethernet or SSH to inspect the update service log.",
            )
            raise
        finally:
            if not keep_request:
                (root / "request.json").unlink(missing_ok=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Approve or install a local JitterSim release")
    commands = parser.add_subparsers(dest="command", required=True)
    stage = commands.add_parser("stage")
    stage.add_argument("archive", type=Path)
    stage.add_argument("--sha256", required=True)
    stage.add_argument("--version", required=True)
    commands.add_parser("apply")
    args = parser.parse_args(argv)
    if os.geteuid() != 0:
        parser.error("release staging and installation require root")
    if args.command == "stage":
        stage_release(args.archive, args.sha256, args.version)
    else:
        apply_update()


if __name__ == "__main__":
    main()
