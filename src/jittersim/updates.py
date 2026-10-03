"""Install signed releases with a local recovery copy.

Only the root-only staging command accepts an archive or a path. It stages an
archive only when a key in /etc/jittersim/release-signers signed exactly its
bytes and its release is newer than every release installed before. Dashboard
requests can name only the single release already staged on this Pi.
"""

import argparse
import base64
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
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
from collections import namedtuple
from contextlib import contextmanager
from pathlib import Path, PurePosixPath

UPDATE_ROOT = Path("/var/lib/jittersim-updates")
CODE_ROOT = Path("/opt/jittersim")
CONFIG_ROOT = Path("/etc/jittersim")
# deploy-from-mac.sh looks for this line to learn that this manager installs only signed
# releases.
SIGNED_RELEASES = True
# The owner's trusted release signing keys in OpenSSH allowed signers format. Only
# trust-key and untrust-key write it.
SIGNERS_NAME = "release-signers"
# The read-only GitHub token of the online release check.
TOKEN_NAME = "update-token"
# Trust settings that a rollback keeps as they are now: restoring an older copy of
# /etc/jittersim must never bring back a key or a token the owner removed.
PRESERVED_CONFIG = (SIGNERS_NAME, TOKEN_NAME)
# The highest release sequence this Pi ever installed. A rollback keeps it.
FLOOR_NAME = "release-floor.json"
SUDOERS_PATH = Path("/etc/sudoers.d/jittersim-update")
SSH_KEYGEN = "/usr/bin/ssh-keygen"
VISUDO = "/usr/sbin/visudo"
# Signatures for anything else (git commits, files) never approve a release.
NAMESPACE = "jittersim-release"
PRINCIPAL = "jittersim-release"
# The one archive path the passwordless rule lets the jittersim login stage.
INCOMING_ARCHIVE = "/home/jittersim/incoming/jittersim-release.tar.gz"
# The signed release manifest build-release.sh writes: commit, version label and sequence.
# It sits in deploy/ because managers from before signed releases accept no other new
# top-level name so they can still install the first signed release.
MANIFEST = "deploy/RELEASE"
MAX_SIGNATURE = 16 * 1024
# A release uploaded over the dashboard's HTTPS API waits here. The dashboard process
# (user jittersim) streams the two files into this folder and nothing else. The root helper
# then copies them into the root-only INCOMING_FOLDER of the update root before it checks them.
UPLOAD_SPOOL = Path("/var/lib/jittersim/incoming-release")
UPLOAD_ARCHIVE = "jittersim-release.tar.gz"
UPLOAD_SIGNATURE = UPLOAD_ARCHIVE + ".sig"
INCOMING_FOLDER = "incoming"
MAX_KEY_FILE = 16 * 1024
MAX_MANIFEST = 4096
MAX_KEYS = 16
MAX_SEQUENCE = 2 ** 53
LABEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9._@-]{0,63}\Z")
# An ed25519 public key: the type name and 32 bytes of key in 68 base64 characters.
KEY_TEXT = re.compile(r"[A-Za-z0-9+/]{68}\Z")
ED25519_PREFIX = b"\x00\x00\x00\x0bssh-ed25519\x00\x00\x00\x20"
SIGNER_LINE = re.compile(
    r'jittersim-release namespaces="jittersim-release" ssh-ed25519 '
    r"(?P<key>[A-Za-z0-9+/]{68}) (?P<label>[A-Za-z0-9][A-Za-z0-9._@-]{0,63})\Z"
)
COMMIT = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")
SIGNERS_HEADER = (
    "# Release signing keys this Pi trusts. Change the list only with\n"
    "# 'sudo jittersim update trust-key' and 'sudo jittersim update untrust-key'.\n"
)
SUDOERS_TEXT = (
    "# Written by 'jittersim update enable-passwordless-staging'. Remove it with\n"
    "# 'sudo jittersim update disable-passwordless-staging'.\n"
    "# The jittersim login may stage and install a release without a password. Staging\n"
    "# accepts only an archive signed by a key in /etc/jittersim/release-signers so this\n"
    "# rule alone cannot install other code.\n"
    "jittersim ALL=(root) NOPASSWD: "
    "/usr/local/bin/jittersim update stage " + INCOMING_ARCHIVE + ", "
    "/usr/local/bin/jittersim update install --yes --wait\n"
)
NO_KEY = (
    "no release signing key is trusted on this Pi yet. "
    "Run 'sudo jittersim update trust-key PUBLIC_KEY_FILE' once"
)
BAD_SIGNATURE = (
    "the release signature is not valid: the archive or its signature changed "
    "or a key this Pi does not trust signed it"
)
Signer = namedtuple("Signer", "label key fingerprint")
DATA_ROOT = Path("/var/lib/jittersim")
ACCESS_ROOT = Path("/var/lib/jittersim-access")
UNITS_ROOT = Path("/etc/systemd/system")
MANAGER_PATH = Path("/usr/local/lib/jittersim-update.py")
DISPATCHER_PATH = Path("/etc/NetworkManager/dispatcher.d/80-jittersim-mdns")
COMMAND_PATH = Path("/usr/local/bin/jittersim")
MAX_ARCHIVE = 25 * 1024 * 1024
MAX_EXPANDED = 100 * 1024 * 1024
PYTHON = sys.executable or "python3"
SERVICES = (
    "jittersim-helper.service",
    "jittersim-dashboard.service",
    "jittersim-browser-proxy.service",
)
WATCHER = "jittersim-service-watch.service"
WIFI_UNIT = "jittersim-ap.service"
NAME_UNIT = "jittersim-mdns.service"
# Every unit file install.sh copies into /etc/systemd/system.
UNITS = SERVICES + (
    "jittersim-control-firewall.service", "jittersim-update.service",
    "jittersim-dns.service", "jittersim-access-boot.service", WIFI_UNIT,
    WATCHER, NAME_UNIT,
)
# Recovery copies made before they listed their files covered these units.
LEGACY_UNITS = tuple(unit for unit in UNITS if unit != NAME_UNIT)
UNIT_NAME = re.compile(r"jittersim-[a-z0-9-]{1,48}\.service\Z")
# The systemctl calls of install.sh in its order. The test Wi-Fi unit only
# starts when its connection is down. Nothing restarts or stops it so an update
# never drops the running Wi-Fi.
SERVICE_STEPS = (
    ("enable", "jittersim-update.service"),
    ("daemon-reload",),
    ("enable", "jittersim-access-boot.service", WIFI_UNIT),
    ("restart", "jittersim-access-boot.service"),
    ("enable", "jittersim-control-firewall.service"),
    ("restart", "jittersim-control-firewall.service"),
    ("enable", *SERVICES, WATCHER),
    ("restart", "jittersim-helper.service"),
    ("start", WIFI_UNIT),
    ("restart", "jittersim-browser-proxy.service"),
    ("restart", WATCHER),
    ("restart", "jittersim-dashboard.service"),
)
# install.sh publishes the dashboard name as a best effort. A failure only
# means the name is not announced.
NAME_STEPS = (
    ("daemon-reload",),
    ("enable", NAME_UNIT),
    ("restart", NAME_UNIT),
)
PI = "deploy/raspberry-pi/"
PREFLIGHT = PI + "preflight.py"
EGRESS_CHECK = PI + "check-egress-hook.py"
BOOTSTRAP = PI + "bootstrap-dashboard-access.py"
FIREWALL_TEMPLATE = PI + "jittersim-control-firewall.nft"
FIREWALL_RULES = "jittersim-control-firewall.nft"
# install.sh imports these as the service user after the package install.
IMPORT_CHECK = (
    "import jittersim.api.server, jittersim.networking.helper, "
    "jittersim.networking.browser_proxy, jittersim.networking.dns_watch, "
    "jittersim.networking.access, jittersim.dashboard_access_cli"
)
INTERFACE = re.compile(r"[A-Za-z0-9_.:-]{1,15}\Z")
VERSION = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")


def release_files(*, code, units, dispatcher):
    """Files install.sh copies from a release as (source, destination, mode)."""
    files = [(PI + unit, units / unit, 0o644) for unit in UNITS]
    files.append(
        (PI + "publish-dashboard-name.sh", code / PI / "publish-dashboard-name.sh", 0o755)
    )
    files.append((PI + "jittersim-mdns-dispatcher", dispatcher, 0o755))
    return files


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
        top = path.parts[0] if path.parts else None
        if (
            not path.parts
            or path.is_absolute()
            or ".." in path.parts
            or member.issym()
            or member.islnk()
            or not (member.isdir() or member.isfile())
            # A top-level file name is never a folder.
            or not (
                top in ("src", "deploy")
                or top in ("README.md", "pyproject.toml")
                and len(path.parts) == 1
                and member.isfile()
            )
            or str(path) in names
            or len(member.name) > 240
        ):
            raise UpdateError("release archive contains an unsafe or unexpected entry")
        names.add(str(path))
        total += member.size
        if total > MAX_EXPANDED or len(members) >= 5000:
            raise UpdateError("release archive exceeds its size or file limit")
        members.append(member)
    # A file never has entries below it.
    files = {str(PurePosixPath(member.name)) for member in members if member.isfile()}
    if any(
        str(parent) in files for member in members for parent in PurePosixPath(member.name).parents
    ):
        raise UpdateError("release archive contains an unsafe or unexpected entry")
    required = {
        "pyproject.toml",
        "README.md",
        MANIFEST,
        "src/jittersim/api/server.py",
        "src/jittersim/updates.py",
        PREFLIGHT,
        EGRESS_CHECK,
        BOOTSTRAP,
        FIREWALL_TEMPLATE,
    }
    required.update(
        source
        for source, _, _ in release_files(
            code=CODE_ROOT, units=UNITS_ROOT, dispatcher=DISPATCHER_PATH
        )
    )
    if not required.issubset(
        {str(PurePosixPath(member.name)) for member in members if member.isfile()}
    ):
        raise UpdateError("release archive is missing required files")
    return members


def key_fingerprint(key):
    """The SHA256 fingerprint ssh-keygen shows for a base64 public key."""
    digest = hashlib.sha256(base64.b64decode(key)).digest()
    return "SHA256:" + base64.b64encode(digest).decode().rstrip("=")


def _check_key_text(key):
    if not KEY_TEXT.fullmatch(key):
        return False
    blob = base64.b64decode(key, validate=True)
    return len(blob) == 51 and blob.startswith(ED25519_PREFIX)


def parse_public_key(text, label=None):
    """Read exactly one ed25519 public key line. Options, other key types and
    certificates are refused."""
    if len(text) > MAX_KEY_FILE:
        raise UpdateError("the key file is too large to be one public key")
    if "PRIVATE KEY" in text:
        raise UpdateError(
            "this is a private key. Give the public key (the .pub file) and keep the "
            "private key on your Mac"
        )
    text = text.replace("\r\n", "\n")
    if any(not (32 <= ord(character) <= 126 or character in "\n\t") for character in text):
        raise UpdateError("the key file holds characters that a public key never has")
    lines = [line.strip() for line in text.split("\n")]
    keys = [line for line in lines if line and not line.startswith("#")]
    if len(keys) != 1:
        raise UpdateError("the key file must hold exactly one public key")
    fields = keys[0].split()
    if fields[0] != "ssh-ed25519" or len(fields) < 2:
        raise UpdateError(
            "only a plain ssh-ed25519 public key can sign releases: no options, "
            "certificates or other key types"
        )
    if not _check_key_text(fields[1]):
        raise UpdateError("the ssh-ed25519 key is damaged")
    name = label if label is not None else " ".join(fields[2:])
    if not LABEL.fullmatch(name):
        if label is None:
            raise UpdateError(
                "the key's comment cannot be used as its label. Give one with --label NAME"
            )
        raise UpdateError(
            "the label must be 1 to 64 letters, digits, dots, dashes, underscores or @ "
            "and start with a letter or digit"
        )
    return Signer(name, fields[1], key_fingerprint(fields[1]))


# The trusted key list and its folder must belong to root whoever reads them, so that
# the jittersim login can run list-keys and a file the caller owns is never trusted.
TRUSTED_OWNER_UID = 0


def trusted_keys(config=CONFIG_ROOT):
    """The trusted keys. A list that root does not own, that others may change or that
    holds a line trust-key did not write is refused as a whole."""
    path = config / SIGNERS_NAME
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return []
    if not stat.S_ISREG(info.st_mode):
        raise UpdateError(f"the trusted key list {path} must be a regular file, not a link")
    for item, name in ((info, f"the trusted key list {path}"), (os.stat(config), str(config))):
        if item.st_uid != TRUSTED_OWNER_UID or item.st_mode & 0o022:
            raise UpdateError(f"{name} must belong to root and must not be writable by others")
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(descriptor, "rb") as handle:
        raw = handle.read(65537)
    damaged = UpdateError(
        f"{path} has a line JitterSim did not write. Remove that line or the file and "
        "trust the keys again with trust-key"
    )
    if len(raw) > 65536:
        raise damaged
    try:
        text = raw.decode("ascii")
    except UnicodeDecodeError:
        raise damaged from None
    signers = []
    for line in text.split("\n"):
        if not line or line.startswith("#"):
            continue
        match = SIGNER_LINE.fullmatch(line)
        if match is None or not _check_key_text(match["key"]):
            raise damaged
        signers.append(Signer(match["label"], match["key"], key_fingerprint(match["key"])))
    return signers


def _signer_lines(signers):
    return "".join(
        f'{PRINCIPAL} namespaces="{NAMESPACE}" ssh-ed25519 {signer.key} {signer.label}\n'
        for signer in signers
    )


def _fsync_folder(folder):
    descriptor = os.open(folder, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _replace_file(folder, prefix, destination, text, mode, check=None):
    """Replace DESTINATION in one step with a root-owned file that CHECK accepted. An
    interruption keeps the old file and leaves no partial one."""
    descriptor, temporary = tempfile.mkstemp(prefix=prefix, dir=folder)
    try:
        with os.fdopen(descriptor, "w") as handle:
            os.fchmod(handle.fileno(), mode)
            if os.geteuid() == 0:
                os.fchown(handle.fileno(), 0, 0)
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        if check is not None:
            check(temporary)
        os.replace(temporary, destination)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
    _fsync_folder(folder)


def _write_signers(config, signers):
    if not config.is_dir():
        raise UpdateError(f"{config} does not exist. Install JitterSim first")
    _replace_file(config, ".release-signers.", config / SIGNERS_NAME,
                  SIGNERS_HEADER + _signer_lines(signers), 0o644)


def _forget_kept(root, name):
    """Drop the copy an interrupted restore kept so it can never come back later."""
    keep = _trust_keep_directory(root)
    try:
        (keep / name).unlink()
    except FileNotFoundError:
        return
    _fsync_folder(keep)


def _trust_keep_directory(root, *, create=False):
    keep = root / "keep"
    if create:
        keep.mkdir(mode=0o700, exist_ok=True)
    try:
        info = keep.lstat()
    except FileNotFoundError:
        return keep
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o022:
        raise UpdateError("the saved current trust directory must belong to the administrator "
                          "and must not be a link or writable by others")
    if create:
        _fsync_folder(root)
    record = keep / ".trust-format.json"
    if not record.exists() and not record.is_symlink():
        if any(keep.iterdir()):
            raise UpdateError("unfinished trust recovery was made by an older manager. "
                              "Use administrator access to inspect its saved current settings "
                              "before trying recovery again")
        if create:
            _replace_file(keep, ".format-", record, json.dumps({"version": 1}), 0o600)
    else:
        _check_trust_file(record)
        if read_json(record) != {"version": 1}:
            raise UpdateError("the saved current trust record is not understood")
    return keep


def _check_trust_file(path):
    try:
        info = path.lstat()
    except FileNotFoundError:
        return
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o022:
        raise UpdateError("saved current trust settings must be regular administrator-owned "
                          "files and must not be writable by others")
    if path.name == TOKEN_NAME and info.st_mode & 0o077:
        raise UpdateError("the saved current update token must be private to the administrator")


def _keys_for_change(config, root):
    """Recovery hides the live list until it finishes. Root may still change the
    saved current list so a revocation also applies to the next restore."""
    path = config / SIGNERS_NAME
    keep = _trust_keep_directory(root)
    if not path.exists() and not path.is_symlink() and read_json(root / "request.json").get("rollback"):
        return trusted_keys(keep)
    return trusted_keys(config)


def trust_key(text, *, label=None, config=CONFIG_ROOT, root=UPDATE_ROOT):
    """Add one public key to the trusted key list."""
    signer = parse_public_key(text, label)
    with update_lock(root):
        signers = _keys_for_change(config, root)
        for existing in signers:
            if existing.key == signer.key:
                raise UpdateError(f"this key is already trusted as {existing.label}")
            if existing.label == signer.label:
                raise UpdateError(
                    f"the label {signer.label} is already used by another key. "
                    "Choose another with --label"
                )
        if len(signers) >= MAX_KEYS:
            raise UpdateError(
                f"at most {MAX_KEYS} keys can be trusted. Remove one with untrust-key first"
            )
        _write_signers(config, signers + [signer])
        _forget_kept(root, SIGNERS_NAME)
    return signer


def untrust_key(name, *, config=CONFIG_ROOT, root=UPDATE_ROOT, sudoers=SUDOERS_PATH):
    """Remove a key by label or fingerprint. Installed releases stay installed. A staged
    release it signed is removed."""
    messages = []
    with update_lock(root):
        signers = _keys_for_change(config, root)
        removed = [signer for signer in signers if name in (signer.label, signer.fingerprint)]
        if not removed:
            raise UpdateError(
                "No trusted key has that label or fingerprint. "
                "See 'jittersim update list-keys'"
            )
        remaining = [signer for signer in signers if signer not in removed]
        _write_signers(config, remaining)
        _forget_kept(root, SIGNERS_NAME)
        for signer in removed:
            messages.append(
                f"Removed release signing key {signer.label} ({signer.fingerprint}). "
                "Releases already installed stay installed."
            )
        staged = root / "staged"
        record = _record(staged / ".jittersim-release.json")
        if record.get("signer") in {signer.fingerprint for signer in removed}:
            shutil.rmtree(staged)
            atomic_json(
                root / "status.json",
                {
                    "phase": "idle",
                    "message": "The staged release was removed because the key that "
                    "signed it is no longer trusted.",
                },
            )
            messages.append("The staged release it signed was removed.")
        if not remaining and disable_passwordless_staging(sudoers=sudoers):
            messages.append("Passwordless staging is off because no key is trusted now.")
    return removed, messages


def enable_passwordless_staging(*, config=CONFIG_ROOT, sudoers=SUDOERS_PATH, visudo=VISUDO):
    """Let the jittersim login run exactly the staging and install commands without a
    password. visudo checks the rule before it is moved into place."""
    if not trusted_keys(config):
        raise UpdateError(
            "trust a release signing key first with 'sudo jittersim update trust-key'. "
            "Without one passwordless staging could never install anything"
        )
    folder = sudoers.parent
    if not folder.is_dir():
        raise UpdateError(f"{folder} does not exist. Is sudo installed?")

    def check(candidate):
        if visudo is None:
            return
        try:
            result = subprocess.run(
                [visudo, "-cf", candidate], stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30,
            )
        except (OSError, subprocess.SubprocessError) as error:
            raise UpdateError("visudo could not check the passwordless staging rule") from error
        if result.returncode != 0:
            raise UpdateError("visudo rejected the passwordless staging rule")

    # sudo skips files whose name has a dot so a leftover candidate is never a rule.
    _replace_file(folder, ".jittersim-update.", sudoers, SUDOERS_TEXT, 0o440, check)


def disable_passwordless_staging(*, sudoers=SUDOERS_PATH):
    try:
        sudoers.unlink()
    except FileNotFoundError:
        return False
    return True


def copy_bounded(source, destination, limit, what):
    """Copy a regular file into a new private file. Links, folders, devices and pipes
    are refused without waiting on them."""
    flags = (os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
             | getattr(os, "O_CLOEXEC", 0))
    try:
        descriptor = os.open(source, flags)
    except OSError as error:
        raise UpdateError(
            f"the {what} could not be opened. Give a regular file that is not a link"
        ) from error
    info = os.fstat(descriptor)
    if not stat.S_ISREG(info.st_mode):
        os.close(descriptor)
        raise UpdateError(f"the {what} must be a regular file")
    with os.fdopen(descriptor, "rb") as handle:
        if info.st_size > limit:
            raise UpdateError(f"the {what} is too large")
        target = os.open(
            destination,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        with os.fdopen(target, "wb") as output:
            remaining = limit + 1
            while remaining:
                block = handle.read(min(65536, remaining))
                if not block:
                    break
                output.write(block)
                remaining -= len(block)
            if remaining == 0:
                raise UpdateError(f"the {what} is too large")
            output.flush()
            os.fsync(output.fileno())


def verify_signature(archive, signature, signers, work):
    """Check with ssh-keygen that a trusted key signed exactly the bytes of ARCHIVE for
    JitterSim releases. Return the key that signed it."""
    if not signers:
        raise UpdateError(NO_KEY)
    # ssh-keygen reads a private copy of the list that was just checked.
    allowed = work / "allowed-signers"
    allowed.write_text(_signer_lines(signers))
    allowed.chmod(0o600)
    command = [SSH_KEYGEN, "-Y", "verify", "-f", str(allowed), "-I", PRINCIPAL,
               "-n", NAMESPACE, "-s", str(signature)]
    try:
        with archive.open("rb") as message:
            result = subprocess.run(
                command, stdin=message, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                timeout=120, check=False, env={"PATH": "/usr/bin:/bin", "LC_ALL": "C"},
            )
    except FileNotFoundError as error:
        raise UpdateError(f"{SSH_KEYGEN} is missing. Install openssh-client") from error
    except (OSError, subprocess.SubprocessError) as error:
        raise UpdateError("the signature check could not run") from error
    if result.returncode != 0:
        raise UpdateError(BAD_SIGNATURE)
    key, namespace = _signature_fields(signature.read_bytes())
    signer = next((item for item in signers if item.key == key), None)
    if signer is None or namespace != NAMESPACE:
        raise UpdateError(BAD_SIGNATURE)
    return signer


def _signature_fields(raw):
    """The public key (base64) and the namespace written in an OpenSSH signature or Nones.
    ssh-keygen checked the signature. This names the trusted key that made it."""
    try:
        lines = raw.decode("ascii").strip().splitlines()
        if lines[0] != "-----BEGIN SSH SIGNATURE-----" or lines[-1] != "-----END SSH SIGNATURE-----":
            return None, None
        blob = base64.b64decode("".join(lines[1:-1]), validate=True)
        if blob[:6] != b"SSHSIG":
            return None, None
        fields = []
        offset = 10
        for _ in range(2):
            length = int.from_bytes(blob[offset:offset + 4], "big")
            fields.append(blob[offset + 4:offset + 4 + length])
            offset += 4 + length
        return base64.b64encode(fields[0]).decode(), fields[1].decode("ascii")
    except (UnicodeDecodeError, IndexError, ValueError):
        return None, None


def _is_sequence(value):
    return type(value) is int and 1 <= value < MAX_SEQUENCE


def read_manifest(path):
    """The signed RELEASE file: the commit, the version label and the release sequence."""
    try:
        raw = path.read_bytes()
    except OSError as error:
        raise UpdateError("the release manifest is missing") from error
    if len(raw) > MAX_MANIFEST:
        raise UpdateError("the release manifest is too large")
    try:
        data = json.loads(raw.decode("ascii"))
    except (UnicodeDecodeError, ValueError):
        raise UpdateError("the release manifest is not valid") from None
    if (
        not isinstance(data, dict)
        or set(data) != {"format", "commit", "version", "sequence"}
        or type(data["format"]) is not int
        or data["format"] != 1
        or not isinstance(data["commit"], str)
        or not COMMIT.fullmatch(data["commit"])
        or not isinstance(data["version"], str)
        or not VERSION.fullmatch(data["version"])
        or not _is_sequence(data["sequence"])
    ):
        raise UpdateError("the release manifest is not valid")
    return data


def _record(path):
    try:
        value = read_json(path)
    except (OSError, ValueError) as error:
        raise UpdateError(f"{path} cannot be read") from error
    return value if isinstance(value, dict) else {}


def release_floor(*, root=UPDATE_ROOT, code=CODE_ROOT):
    """The installed release sequence or the highest one installed before if higher."""
    values = [
        record.get("sequence")
        for record in (_record(code / ".jittersim-release.json"), _record(root / FLOOR_NAME))
    ]
    return max((value for value in values if _is_sequence(value)), default=0)


def _raise_floor(root, sequence):
    mark = _record(root / FLOOR_NAME).get("sequence")
    if not _is_sequence(mark) or sequence > mark:
        atomic_json(root / FLOOR_NAME, {"sequence": sequence})


def staged_problem(record, signers, *, root=UPDATE_ROOT, code=CODE_ROOT):
    """Why the staged release must not be installed now or None when it may be."""
    if not record.get("signer") or not _is_sequence(record.get("sequence")):
        return "This release was staged without a signature. Stage a signed release."
    if record["signer"] not in {signer.fingerprint for signer in signers}:
        return "The key that signed the staged release is no longer trusted."
    if record["sequence"] <= release_floor(root=root, code=code):
        return "The staged release is not newer than the installed release."
    return None


def stage_release(path, *, signature=None, root=UPDATE_ROOT, code=CODE_ROOT,
                  config=CONFIG_ROOT, sha256=None, version=None):
    """Stage a release that a trusted key signed. Nothing reads the archive's contents
    before its signature matched."""
    if sha256 is not None and not re.fullmatch(r"[0-9a-f]{64}", sha256):
        raise UpdateError("SHA-256 is invalid")
    if version is not None and not VERSION.fullmatch(version):
        raise UpdateError("version is invalid")
    path = Path(path)
    signature = Path(signature) if signature is not None else path.with_name(path.name + ".sig")
    with update_lock(root):
        if (root / "request.json").exists():
            raise UpdateError("a queued update must finish before staging another release")
        signers = trusted_keys(config)
        if not signers:
            raise UpdateError(NO_KEY)
        with tempfile.TemporaryDirectory(dir=root) as work:
            work = Path(work)
            copied = work / "release.tar"
            signed = work / "release.sig"
            # Verify, hash and extract the same private copy, even if the input changes.
            copy_bounded(path, copied, MAX_ARCHIVE, "release archive")
            copy_bounded(signature, signed, MAX_SIGNATURE, "release signature")
            signer = verify_signature(copied, signed, signers, work)
            digest = hashlib.sha256(copied.read_bytes()).hexdigest()
            if sha256 is not None and digest != sha256:
                raise UpdateError("release SHA-256 does not match the approved checksum")
            extracted = work / "source"
            extracted.mkdir()
            # Expand once under a strict limit before the tar parser reads metadata.
            plain = work / "plain.tar"
            try:
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
            except (OSError, EOFError) as error:
                raise UpdateError("the release archive could not be unpacked") from error
            try:
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
            except (OSError, tarfile.TarError, EOFError) as error:
                raise UpdateError("the release archive could not be unpacked") from error
            release = read_manifest(extracted / MANIFEST)
            if version is not None and release["version"] != version:
                raise UpdateError("the signed release manifest names another version")
            sequence = release["sequence"]
            floor = release_floor(root=root, code=code)
            if sequence <= floor:
                raise UpdateError(
                    f"release {release['version']} (sequence {sequence}) is not newer than "
                    f"the installed release (sequence {floor}). An older release comes back "
                    "only with 'sudo jittersim update rollback'"
                )
            record = {
                "version": release["version"],
                "sha256": digest,
                "commit": release["commit"],
                "sequence": sequence,
                "signer": signer.fingerprint,
                "signer_label": signer.label,
            }
            atomic_json(extracted / ".jittersim-release.json", record)
            staged = root / "staged"
            if staged.exists():
                shutil.rmtree(staged)
            extracted.rename(staged)
        atomic_json(
            root / "status.json",
            {
                "phase": "ready",
                "message": f"Release {record['version']} signed by {signer.label} "
                "verified and ready to install.",
            },
        )
    return record


def _remove_quietly(path):
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass


def stage_upload(*, spool=UPLOAD_SPOOL, root=UPDATE_ROOT, code=CODE_ROOT, config=CONFIG_ROOT):
    """Stage the release the dashboard received over HTTPS.

    The spool belongs to the dashboard's user so nothing in it is trusted. Both files are
    copied into a folder only root may enter and are removed from the spool at once. Then
    stage_release does the real work: a trusted key must have signed exactly these bytes
    and the release must be newer than every release installed before. So the admin token
    alone can stage only a release the owner signed.
    """

    spool = Path(spool)
    try:
        folder_info = spool.lstat()
    except OSError as error:
        raise UpdateError("no uploaded release is waiting") from error
    if not stat.S_ISDIR(folder_info.st_mode):
        raise UpdateError("the upload folder is not a plain folder")
    incoming = root / INCOMING_FOLDER
    try:
        incoming.mkdir(mode=0o700, parents=True, exist_ok=True)
        info = incoming.lstat()
        if not stat.S_ISDIR(info.st_mode) or (
            hasattr(os, "geteuid") and info.st_uid != os.geteuid()
        ):
            raise UpdateError("the incoming folder is not safe to use")
        incoming.chmod(0o700)
    except OSError as error:
        raise UpdateError("the incoming folder could not be prepared") from error
    archive = incoming / UPLOAD_ARCHIVE
    signature = incoming / UPLOAD_SIGNATURE
    for old in (archive, signature):
        _remove_quietly(old)
    try:
        try:
            copy_bounded(spool / UPLOAD_ARCHIVE, archive, MAX_ARCHIVE, "uploaded release archive")
            copy_bounded(spool / UPLOAD_SIGNATURE, signature, MAX_SIGNATURE,
                         "uploaded release signature")
        finally:
            for name in (UPLOAD_ARCHIVE, UPLOAD_SIGNATURE):
                _remove_quietly(spool / name)
        return stage_release(archive, signature=signature, root=root, code=code, config=config)
    finally:
        for leftover in (archive, signature):
            _remove_quietly(leftover)


def update_status(*, root=UPDATE_ROOT, code=CODE_ROOT, config=CONFIG_ROOT):
    installed = read_json(code / ".jittersim-release.json")
    staged = _record(root / "staged" / ".jittersim-release.json")
    status = read_json(root / "status.json")
    try:
        signers = trusted_keys(config)
        key_problem = None
    except (UpdateError, OSError) as error:
        signers = []
        key_problem = str(error)
    reason = None
    if staged:
        try:
            reason = key_problem or staged_problem(staged, signers, root=root, code=code)
        except UpdateError as error:
            reason = str(error)
    return {
        "installed": installed.get("version", "Unversioned installation"),
        "available": staged.get("version"),
        "phase": status.get("phase", "idle"),
        "message": status.get("message", "No approved release has been staged on this Pi."),
        "rollback_available": (root / "backup" / "complete.json").exists(),
        "installable": bool(staged) and reason is None,
        "signed_by": staged.get("signer_label") if staged.get("signer") else None,
        "reason": reason,
        "trusted_keys": len(signers),
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
        # Name the release script rather than the Python that ran it.
        name = next((part for part in command[1:2] if part.endswith(".py")), command[0])
        raise UpdateError("release command failed: " + Path(name).name) from error


def read_command(command, timeout=10):
    try:
        result = subprocess.run(
            command,
            check=True,
            timeout=timeout,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
    except (subprocess.SubprocessError, OSError) as error:
        raise UpdateError("release command failed: " + Path(command[0]).name) from error
    return result.stdout.strip()


def runtime_interfaces(config):
    """Read the interfaces like install.sh, with its defaults for an empty file."""
    path = config / "runtime.env"
    if not path.exists() or path.stat().st_size == 0:
        return "eth0", "wlan0"
    values = {}
    for line in path.read_text().splitlines():
        name, separator, value = line.strip().partition("=")
        if separator:
            values[name] = value.strip().strip("'\"")
    interfaces = values.get("JITTERSIM_UPLINK"), values.get("JITTERSIM_TEST_INTERFACE")
    if not all(name and INTERFACE.fullmatch(name) for name in interfaces):
        raise UpdateError("runtime.env must name a safe uplink and test interface")
    return interfaces


def install_file(source, destination, mode):
    shutil.copyfile(source, destination)
    destination.chmod(mode)


def render_firewall(template, destination, uplink, test_interface):
    """Fill in the interfaces like the sed call of install.sh."""
    rules = template.read_text()
    rules = rules.replace("@UPLINK_INTERFACE@", uplink).replace("@TEST_INTERFACE@", test_interface)
    temporary = destination.with_name(destination.name + ".next")
    temporary.write_text(rules)
    temporary.chmod(0o644)
    temporary.replace(destination)


def link_command(target, link):
    """Point the link at target like ln -sf, replacing it in one step."""
    temporary = link.with_name(link.name + ".next")
    if temporary.is_symlink() or temporary.exists():
        temporary.unlink()
    os.symlink(target, temporary)
    temporary.replace(link)


def request_update(
    release_version, *, rollback=False, root=UPDATE_ROOT, code=CODE_ROOT, config=CONFIG_ROOT,
    run=run_command,
):
    with update_lock(root):
        status = update_status(root=root, code=code, config=config)
        if (root / "request.json").exists():
            raise UpdateError("an update is already queued")
        if rollback:
            if not status["rollback_available"]:
                raise UpdateError("no recovery copy is available")
        elif release_version != status["available"] or not isinstance(release_version, str):
            raise UpdateError("check the staged release again before installing")
        elif not status["installable"]:
            raise UpdateError(status["reason"] or "the staged release cannot be installed")
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
    dispatcher=DISPATCHER_PATH,
    command=COMMAND_PATH,
    run=run_command,
    query=read_command,
    health=None,
):
    """Install the staged release with the release steps of install.sh.

    The steps that check the Pi run before anything stops. Every file the
    install replaces is in the recovery copy first.
    """
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

        def present(names):
            return [name for name in names if (units / name).exists()]

        def restart_services():
            """Run the systemctl steps of install.sh for the units installed now."""
            try:
                wifi = query(
                    ["nmcli", "-g", "GENERAL.CONNECTION", "device", "show",
                     runtime_interfaces(config)[1]],
                    10,
                )
            except (UpdateError, OSError):
                # An unknown state must never restart the running Wi-Fi.
                wifi = "jittersim-ap"
            for action, *names in SERVICE_STEPS:
                installed = present(names)
                if names and not installed:
                    continue
                if action == "start" and wifi == "jittersim-ap":
                    continue
                run(["systemctl", action, *installed], 120)
            if present([NAME_UNIT]):
                for action, *names in NAME_STEPS:
                    try:
                        run(["systemctl", action, *names], 120)
                    except UpdateError:
                        break

        def keep_trust_settings():
            """Copy the trust settings aside before the configuration is replaced. An
            interruption then leaves them out (nothing is trusted) and never brings back
            an older list."""
            keep = _trust_keep_directory(root, create=True)
            # Validate existing snapshots before changing any live folder.
            for name in PRESERVED_CONFIG:
                _check_trust_file(keep / name)
            trusted_keys(keep)
            for name in PRESERVED_CONFIG:
                current = config / name
                _check_trust_file(current)
                # The filtered restore never copies old trust into the live
                # configuration. A live file therefore includes any later root
                # change even if that change was interrupted before deleting keep.
                if current.is_file() and not current.is_symlink():
                    copy_trust_setting(current, keep / name)
            trusted_keys(keep)
            return keep

        def copy_trust_setting(source, destination):
            """A failed copy must not leave a partial token or key list for retry."""
            descriptor, temporary = tempfile.mkstemp(prefix=".trust-", dir=destination.parent)
            os.close(descriptor)
            try:
                shutil.copy2(source, temporary)
                Path(temporary).chmod(0o600 if destination.name == TOKEN_NAME else 0o644)
                with open(temporary, "rb") as handle:
                    os.fsync(handle.fileno())
                os.replace(temporary, destination)
                _fsync_folder(destination.parent)
            finally:
                Path(temporary).unlink(missing_ok=True)

        def put_back_trust_settings(keep):
            for name in PRESERVED_CONFIG:
                restored = config / name
                # A recovery copy from an older manager may hold one.
                if restored.is_symlink() or restored.exists():
                    restored.unlink()
                if (keep / name).is_file():
                    copy_trust_setting(keep / name, restored)
            shutil.rmtree(keep)
            _fsync_folder(root)

        def restore():
            if not (backup / "complete.json").exists():
                raise UpdateError("the recovery copy is incomplete")
            completed = read_json(backup / "complete.json")
            ownership = completed.get("ownership", {})
            targets = []
            for name in completed.get("managed", ["units/" + unit for unit in LEGACY_UNITS]):
                if name == "dispatcher":
                    targets.append((name, dispatcher))
                elif name == "command":
                    targets.append((name, command))
                elif name.startswith("units/") and UNIT_NAME.fullmatch(name[6:]):
                    targets.append((name, units / name[6:]))
                else:
                    raise UpdateError("the recovery copy names an unknown file")
            keep = keep_trust_settings()
            for name, destination in (("code", code), ("config", config), ("data", data)):
                if destination.exists():
                    shutil.rmtree(destination)
                # Older recovery copies contain the signer list and token. They
                # must never become live even if a later copy is interrupted.
                ignore = (
                    lambda folder, names: PRESERVED_CONFIG
                    if Path(folder) == backup / "config" else ()
                ) if name == "config" else None
                shutil.copytree(backup / name, destination, symlinks=True, ignore=ignore)
                if name in ownership:
                    uid, gid = ownership[name]
                    for entry in [destination, *destination.rglob("*")]:
                        os.chown(entry, uid, gid, follow_symlinks=False)
            put_back_trust_settings(keep)
            for name, destination in targets:
                saved = backup / name
                if saved.is_symlink():
                    link_command(os.readlink(saved), destination)
                elif saved.exists():
                    shutil.copy2(saved, destination)
                elif destination.is_symlink() or destination.exists():
                    unit = name[6:] if name.startswith("units/") else None
                    if unit:
                        # The update added this unit. The running Wi-Fi and the
                        # update itself keep running.
                        keep = unit in (WIFI_UNIT, "jittersim-update.service")
                        try:
                            run(["systemctl", "disable", *([] if keep else ["--now"]), unit], 60)
                        except UpdateError:
                            pass
                    destination.unlink()
            if (backup / "access").exists():
                if access.exists():
                    shutil.rmtree(access)
                shutil.copytree(backup / "access", access, symlinks=True)
                if "access" in ownership:
                    uid, gid = ownership["access"]
                    for entry in [access, *access.rglob("*")]:
                        os.chown(entry, uid, gid, follow_symlinks=False)
            # The recovery manager is separate from the application. Rolling it
            # back could undo trust protections needed by later recovery attempts.
            restart_services()

        def wait_healthy():
            for _ in range(30):
                if health():
                    return True
                time.sleep(1)
            return False

        recovered = False
        keep_request = True
        backup_taken = False
        stopped = False
        try:
            if not request.get("rollback"):
                staged = root / "staged"
                record = _record(staged / ".jittersim-release.json")
                if record.get("version") != request.get("version"):
                    raise UpdateError("staged release changed")
                # The key may have been removed or another release installed since staging.
                problem = staged_problem(record, trusted_keys(config), root=root, code=code)
                if problem:
                    raise UpdateError(problem)
                uplink, test_interface = runtime_interfaces(config)
                # Like install.sh, check the Pi with the release's own scripts
                # before anything changes.
                progress("stopping", "Checking the Pi network before the update.")
                run(
                    [PYTHON, str(staged / PREFLIGHT),
                     "--uplink", uplink, "--test-interface", test_interface],
                    60,
                )
                run([PYTHON, str(staged / EGRESS_CHECK), test_interface], 60)
            progress("stopping", "Stopping services before the settings backup.")
            stopped = True
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

            def trust_settings(folder, names):
                # A rollback keeps the trust settings as they are then.
                return [name for name in names if Path(folder) == config and name in PRESERVED_CONFIG]

            for name, source in (("code", code), ("config", config), ("data", data)):
                shutil.copytree(source, candidate / name, symlinks=True, ignore=trust_settings)
            if access.exists():
                shutil.copytree(access, candidate / "access", symlinks=True)
            (candidate / "units").mkdir()
            for unit in UNITS:
                if (units / unit).exists():
                    shutil.copy2(units / unit, candidate / "units" / unit)
            for name, path in (("dispatcher", dispatcher), ("command", command)):
                if path.is_symlink():
                    os.symlink(os.readlink(path), candidate / name)
                elif path.exists():
                    shutil.copy2(path, candidate / name)
            if manager.exists():
                shutil.copy2(manager, candidate / "manager.py")
            atomic_json(
                candidate / "complete.json",
                {
                    "created_at": int(time.time()),
                    "target_version": request.get("version"),
                    # A missing entry means the file did not exist before.
                    "managed": ["units/" + unit for unit in UNITS] + ["dispatcher", "command"],
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
            # Like replace-source.py: an old build folder could put removed
            # modules back into the package.
            if (code / "build").exists():
                shutil.rmtree(code / "build")
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
            # The root helper runs this code so only root may change it.
            for entry in [code, *code.rglob("*")]:
                if not entry.is_symlink():
                    entry.chmod(entry.stat().st_mode & ~0o022)
            python = str(code / ".venv/bin/python")
            run(["runuser", "-u", "jittersim", "--", python, "-I", "-c", IMPORT_CHECK], 120)
            run([python, str(code / BOOTSTRAP)], 60)
            link_command(str(code / ".venv/bin/jittersim"), command)
            render_firewall(
                staged / FIREWALL_TEMPLATE, config / FIREWALL_RULES, uplink, test_interface
            )
            dispatcher.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
            for source, destination, mode in release_files(
                code=code, units=units, dispatcher=dispatcher
            ):
                install_file(staged / source, destination, mode)
            progress("checking", "Restarting services and checking the dashboard and helper.")
            restart_services()
            if not wait_healthy():
                raise UpdateError("updated services failed their health check")
            # From now on only a newer release can be staged, also after a rollback.
            _raise_floor(root, record["sequence"])
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
                elif stopped:
                    # Stopping the helper also stopped the watcher that needs it.
                    run(["systemctl", "start", *present(SERVICES + (WATCHER,))], 60)
            except Exception:
                recovered = False
            keep_request = bool(needs_recovery and not recovered)
            if recovered:
                message = "Update failed. Previous installation restored and checked."
            elif not stopped:
                message = (
                    "Update stopped before it changed anything. "
                    "Use Ethernet or SSH to inspect the update service log."
                )
            else:
                message = (
                    "Update or recovery failed. "
                    "Use Ethernet or SSH to inspect the update service log."
                )
            progress("rolled-back" if recovered else "failed", message)
            raise
        finally:
            if not keep_request:
                (root / "request.json").unlink(missing_ok=True)


def _read_key_file(source):
    if source == "-":
        stream = getattr(sys.stdin, "buffer", None)
        raw = stream.read(MAX_KEY_FILE + 1) if stream else sys.stdin.read(MAX_KEY_FILE + 1).encode()
    else:
        try:
            with open(source, "rb") as handle:
                if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
                    raise UpdateError("the key file must be a regular file")
                raw = handle.read(MAX_KEY_FILE + 1)
        except OSError as error:
            raise UpdateError("the key file could not be read") from error
    if len(raw) > MAX_KEY_FILE:
        raise UpdateError("the key file is too large to be one public key")
    try:
        return raw.decode("ascii")
    except UnicodeDecodeError:
        raise UpdateError("the key file holds characters that a public key never has") from None


def running_as_root():
    return os.geteuid() == 0


PASSWORDLESS_ON = (
    "Passwordless staging is on: the jittersim login may run 'sudo jittersim update stage "
    + INCOMING_ARCHIVE + "' and 'sudo jittersim update install --yes --wait' without a "
    "password. Only archives signed by a trusted key are staged."
)


def main(argv=None, prog=None):
    parser = argparse.ArgumentParser(
        prog=prog, description="Stage, install and trust signed JitterSim releases"
    )
    commands = parser.add_subparsers(dest="command", required=True)
    stage = commands.add_parser(
        "stage", help="verify a signed release archive and stage it (root)",
        description="Stage a release archive signed by a trusted key. Its signature is "
        "ARCHIVE.sig unless --signature names it.",
    )
    stage.add_argument("archive", type=Path)
    stage.add_argument("--signature", type=Path, help="the signature (default: ARCHIVE.sig)")
    stage.add_argument("--sha256", help="also require this SHA-256 checksum")
    stage.add_argument("--version", help="also require this release label")
    commands.add_parser("apply", help="run a queued install (the update service runs it)")
    trust = commands.add_parser(
        "trust-key", help="trust one ed25519 release signing key (root)",
        description="Add one ssh-ed25519 public key to /etc/jittersim/release-signers.",
    )
    trust.add_argument("key_file", metavar="PUBLIC_KEY_FILE",
                       help="the .pub file of the key or - to read it from standard input")
    trust.add_argument("--label", help="a short name for the key (default: its comment)")
    trust.add_argument("--enable-passwordless-staging", action="store_true",
                       help="also let the jittersim login stage and install without a password")
    untrust = commands.add_parser(
        "untrust-key", help="stop trusting a release signing key (root)",
        description="Remove a key. Installed releases stay installed. A staged release it "
        "signed is removed.",
    )
    untrust.add_argument("name", metavar="LABEL_OR_FINGERPRINT")
    commands.add_parser("list-keys", help="show the trusted release signing keys")
    commands.add_parser(
        "enable-passwordless-staging",
        help="let the jittersim login stage and install signed releases without a password",
    )
    commands.add_parser(
        "disable-passwordless-staging", help="ask for the sudo password again (root)"
    )
    args = parser.parse_args(argv)
    name = parser.prog
    if args.command != "list-keys" and not running_as_root():
        print(f"{name}: this command needs root. Run it with sudo.", file=sys.stderr)
        return 2
    try:
        if args.command == "stage":
            record = stage_release(
                args.archive, signature=args.signature, root=UPDATE_ROOT, code=CODE_ROOT,
                config=CONFIG_ROOT, sha256=args.sha256, version=args.version,
            )
            print(f"Staged release {record['version']} signed by {record['signer_label']}. "
                  "Install it with 'sudo jittersim update install' or in the dashboard.")
        elif args.command == "apply":
            apply_update(root=UPDATE_ROOT, code=CODE_ROOT, config=CONFIG_ROOT)
        elif args.command == "trust-key":
            signer = trust_key(_read_key_file(args.key_file), label=args.label,
                               config=CONFIG_ROOT, root=UPDATE_ROOT)
            print(f"Trusted release signing key {signer.label} ({signer.fingerprint}).")
            if args.enable_passwordless_staging:
                try:
                    enable_passwordless_staging(config=CONFIG_ROOT, sudoers=SUDOERS_PATH,
                                                visudo=VISUDO)
                except UpdateError as error:
                    print(f"{name}: the key is trusted but passwordless staging stays off: "
                          f"{error}", file=sys.stderr)
                    return 1
                print(PASSWORDLESS_ON)
        elif args.command == "untrust-key":
            for message in untrust_key(args.name, config=CONFIG_ROOT, root=UPDATE_ROOT,
                                       sudoers=SUDOERS_PATH)[1]:
                print(message)
        elif args.command == "list-keys":
            keys = trusted_keys(CONFIG_ROOT)
            if not keys:
                print("No release signing key is trusted on this Pi.")
            for key in keys:
                print(f"{key.label}  {key.fingerprint}")
            if running_as_root():
                state = "on" if SUDOERS_PATH.exists() else "off"
                print(f"Passwordless staging: {state}")
        elif args.command == "enable-passwordless-staging":
            enable_passwordless_staging(config=CONFIG_ROOT, sudoers=SUDOERS_PATH, visudo=VISUDO)
            print(PASSWORDLESS_ON)
        else:
            removed = disable_passwordless_staging(sudoers=SUDOERS_PATH)
            print("Passwordless staging is off." if removed else
                  "Passwordless staging was already off.")
    except UpdateError as error:
        print(f"{name}: {error}", file=sys.stderr)
        if args.command != "apply":
            print("Nothing was changed.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
