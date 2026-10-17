"""Install signed releases with a local recovery copy.

Only the root-only staging command accepts an archive or a path. It stages an
archive only when a key in /etc/jittersim/release-signers signed exactly its
bytes and its release is newer than every release installed before. Dashboard
requests can name only the single release already staged on this Pi.
"""

import argparse
import ast
import base64
import codecs
import fcntl
import contextvars
import functools
import gzip
import grp
import hashlib
import http.client
import io
import ipaddress
import json
import logging
import math
import os
import re
import secrets
import selectors
import shutil
import signal
import socket
import ssl
import stat
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import types
from collections import namedtuple
from contextlib import contextmanager
from pathlib import Path, PurePosixPath
from urllib.parse import urljoin, urlsplit

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
    "jittersim-adblocking.service",
)
WATCHER = "jittersim-service-watch.service"
# The one-shot root unit that runs one online release check for the dashboard.
FETCH_UNIT = "jittersim-update-fetch.service"
WIFI_UNIT = "jittersim-ap.service"
NAME_UNIT = "jittersim-mdns.service"
# Every unit file install.sh copies into /etc/systemd/system.
UNITS = SERVICES + (
    "jittersim-control-firewall.service", "jittersim-update.service", FETCH_UNIT,
    "jittersim-dns.service", "jittersim-access-boot.service", WIFI_UNIT,
    WATCHER, NAME_UNIT,
)
# Recovery copies made before they listed their files covered these units.
LEGACY_UNITS = tuple(unit for unit in UNITS if unit not in (NAME_UNIT, "jittersim-adblocking.service"))
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
    ("restart", "jittersim-adblocking.service"),
    ("restart", "jittersim-dashboard.service"),
)
# install.sh publishes the dashboard name as a best effort. A failure only
# means the name is not announced. Avahi is limited first (see prepare_name_service
# in apply_update), because the control firewall lets mDNS in on the test Wi-Fi.
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
    "jittersim.networking.access, jittersim.dashboard_access_cli; "
    "import importlib.util; "
    "importlib.import_module('jittersim.networking.adblocking') "
    "if importlib.util.find_spec('jittersim.networking.adblocking') else None"
)
INTERFACE = re.compile(r"[A-Za-z0-9_.:-]{1,15}\Z")
# A release is MAJOR.MINOR.PATCH. A build that is not tagged is NUMBER.dev+COMMIT.
# Keep safe earlier labels readable during the first upgrade and recovery.
VERSION = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+\-]{0,63}\Z")
SEMANTIC_VERSION = re.compile(
    r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(\.dev\+[0-9a-f]{7,40})?\Z"
)


def version_key(version):
    """Order versions by number. A dev build comes before the release of its number."""

    if not isinstance(version, str) or len(version) > 64:
        return None
    match = SEMANTIC_VERSION.fullmatch(version)
    if match is None:
        return None
    major, minor, patch, dev = match.groups()
    return (int(major), int(minor), int(patch), 0 if dev else 1)


def release_files(*, code, units, dispatcher):
    """Files install.sh copies from a release as (source, destination, mode)."""
    files = [(PI + unit, units / unit, 0o644) for unit in UNITS
             if unit != "jittersim-adblocking.service"
             or (code / PI / unit).is_file()
             or (code / "src/jittersim/networking/adblocking.py").is_file()]
    files.append((PI + "jittersim-mdns-dispatcher", dispatcher, 0o755))
    return files


class UpdateError(RuntimeError):
    """The release cannot be installed or recovered safely."""


class NotNewerError(UpdateError):
    """The signed release is not newer than the installed one."""


CERTIFICATE_FILES = (
    "ca/ca.crt", "ca/ca.key", "dashboard.crt", "dashboard.key",
    "dashboard-wifi.crt", "trust.env",
)
CERTIFICATE_LIMIT = 64 * 1024
CERTIFICATE_SECONDS = 60.0
CERTIFICATE_FORMAT = 1
_CERTIFICATE_TIME = contextvars.ContextVar("certificate_recovery_time", default=None)
_CERTIFICATE_RUN = contextvars.ContextVar("certificate_recovery_runner", default=None)


class CertificateMaterial(bytes):
    """Keep diagnostic representations free of private certificate material."""

    def __repr__(self):
        return "<certificate material: %d bytes>" % len(self)


class CertificateFiles(dict):
    def __repr__(self):
        return "<certificate identity files: %s>" % ", ".join(sorted(self))


def _certificate_check():
    timing = _CERTIFICATE_TIME.get()
    if timing is not None:
        deadline, budget = timing
        if budget is not None:
            budget.check()
        if time.monotonic() >= deadline:
            raise UpdateError("certificate recovery exceeded its total time allowance")


def _certificate_operation(function):
    @functools.wraps(function)
    def checked(*args, **kwargs):
        if function.__name__ == "verify_certificate_identity" and args:
            args = (CertificateFiles({name: CertificateMaterial(content) for name, content in args[0].items()}),) + args[1:]
        parent = _CERTIFICATE_TIME.get()
        budget = kwargs.get("budget") or (parent[1] if parent else None)
        now = time.monotonic()
        deadline = kwargs.get("deadline")
        deadline = deadline if deadline is not None else now + CERTIFICATE_SECONDS
        if parent:
            deadline = min(deadline, parent[0])
        if budget is not None:
            budget.check()
            deadline = min(deadline, now + budget.limit(CERTIFICATE_SECONDS))
        kwargs["deadline"] = deadline
        kwargs["budget"] = budget
        token = _CERTIFICATE_TIME.set((deadline, budget))
        try:
            _certificate_check()
            result = function(*args, **kwargs)
            _certificate_check()
            return result
        finally:
            _CERTIFICATE_TIME.reset(token)
    return checked


def _certificate_directory(path, *, private=False, create=False):
    _certificate_check()
    if create:
        path.mkdir(mode=0o700, exist_ok=True)
    info = path.lstat()
    forbidden = 0o077 if private else 0o022
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or
            info.st_mode & forbidden):
        raise UpdateError("certificate recovery directories must belong to the administrator and not be links")
    return path


def _certificate_bytes(path, *, private=False):
    _certificate_check()
    parent = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) |
                     getattr(os, "O_NOFOLLOW", 0))
    try:
        info = os.fstat(parent)
        if info.st_uid != os.geteuid() or info.st_mode & 0o022:
            raise UpdateError("certificate files must be in an administrator-owned directory")
        descriptor = os.open(path.name, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) |
                             getattr(os, "O_NONBLOCK", 0), dir_fd=parent)
        try:
            info = os.fstat(descriptor)
            forbidden = 0o077 if private else 0o022
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or
                    info.st_mode & forbidden or info.st_size > CERTIFICATE_LIMIT):
                raise UpdateError("certificate recovery files must be bounded regular administrator-owned files")
            with os.fdopen(descriptor, "rb", closefd=False) as handle:
                content = CertificateMaterial(handle.read(CERTIFICATE_LIMIT + 1))
            if len(content) > CERTIFICATE_LIMIT:
                raise UpdateError("certificate recovery file is too large")
            _certificate_check()
            return content
        finally:
            os.close(descriptor)
    finally:
        os.close(parent)


def _certificate_command(arguments, deadline):
    _certificate_check()
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise UpdateError("certificate verification exceeded its total time allowance")
    try:
        run = _CERTIFICATE_RUN.get() or capture_command
        answer = run(["openssl", *arguments], remaining, limit=CERTIFICATE_LIMIT)
    except CommandNotStopped:
        raise
    except (OSError, subprocess.SubprocessError, UpdateError) as error:
        if isinstance(error, UpdateError) and type(error) is not UpdateError:
            raise
        _certificate_check()
        raise UpdateError("certificate identity or trust verification failed") from error
    _certificate_check()
    return answer


def _certificate_section(text, title):
    match = re.search(r"^[ \t]*X509v3 " + re.escape(title) + r"[^\n]*\n(.*?)(?=^[ \t]*X509v3 |^[ \t]*Signature Algorithm:|\Z)",
                      text, re.M | re.S)
    return match.group(0) if match else ""


def _certificate_names(text, *, allow_legacy_repeated_names=False):
    section = _certificate_section(text, "Subject Alternative Name")
    names = tuple(re.findall(r"DNS:([^,\s]+)", section))
    label = r"[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?"
    if (len(names) > 32 or (not allow_legacy_repeated_names and len(set(names)) != len(names)) or
            any(len(name) > 253 or not re.fullmatch(label + r"(?:\." + label + r")*", name)
                for name in names)):
        raise UpdateError("dashboard certificate names are invalid")
    addresses = re.findall(r"IP Address:([^,\s]+)", section)
    try:
        if len(addresses) > 8 or len(set(addresses)) != len(addresses):
            raise ValueError("too many or repeated addresses")
        for address in addresses:
            ipaddress.ip_address(address)
    except ValueError as error:
        raise UpdateError("dashboard certificate addresses are invalid") from error
    if not names and not addresses:
        raise UpdateError("the dashboard certificate has no usable names or addresses")
    return tuple(dict.fromkeys(names))


def _certificate_settings(content):
    values = {}
    try:
        lines = content.decode("utf-8").splitlines()
    except UnicodeError as error:
        raise UpdateError("the saved dashboard settings are unreadable") from error
    for line in lines:
        key, separator, value = line.partition("=")
        key = key.strip()
        if separator and key in ("JITTERSIM_DASHBOARD_NAME", "JITTERSIM_LOCAL_NAME"):
            if key in values:
                raise UpdateError("the saved dashboard name is repeated")
            value = value.strip().strip("'\"").lower().rstrip(".")
            suffix = r"\.local" if key == "JITTERSIM_DASHBOARD_NAME" else r"\.(?:local|home\.arpa)"
            if (not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?" + suffix, value) or
                    "jittersim" in value.replace("-", "")):
                raise UpdateError("the saved dashboard name is invalid")
            values[key] = value
    return values


@_certificate_operation
def verify_certificate_identity(files, *, authority_only=False, deadline=None, budget=None):
    """Verify only the supplied identity with no installed-package dependency."""
    deadline = deadline if deadline is not None else time.monotonic() + CERTIFICATE_SECONDS
    if set(files) - set(CERTIFICATE_FILES) or any(len(value) > CERTIFICATE_LIMIT for value in files.values()):
        raise UpdateError("certificate identity contains unsupported or oversized files")
    ca_present = "ca/ca.crt" in files or "ca/ca.key" in files
    if ca_present and not all(name in files for name in CERTIFICATE_FILES[:2]):
        raise UpdateError("the current certificate authority is incomplete")
    pair = ("dashboard.crt" in files, "dashboard.key" in files)
    if not authority_only and pair[0] != pair[1]:
        raise UpdateError("the current dashboard certificate and key are incomplete")
    if not authority_only and ca_present and not all(pair):
        raise UpdateError("the current authority has no complete dashboard identity")
    if not authority_only and ca_present and "dashboard-wifi.crt" not in files:
        raise UpdateError("the current authority has no complete Wi-Fi identity")
    if not authority_only and "dashboard-wifi.crt" in files and not all(pair):
        raise UpdateError("the Wi-Fi certificate has no matching dashboard key")
    settings = _certificate_settings(files.get("trust.env", b""))
    with tempfile.TemporaryDirectory(prefix="certificate-verify-") as temporary:
        root = Path(temporary)
        empty = root / "empty-trust"
        empty.mkdir(mode=0o700)
        paths = {}
        for name, content in files.items():
            path = root / name.replace("/", "-")
            path.write_bytes(content)
            path.chmod(0o600)
            paths[name] = str(path)
        version = _certificate_command(["version"], deadline)
        no_store = ["-no-CAstore"] if version.startswith((b"OpenSSL 3.", b"OpenSSL 4.")) else []

        def text(name):
            return _certificate_command(["x509", "-in", paths[name], "-noout", "-text"], deadline).decode("utf-8")

        def public(name, certificate):
            args = ["x509", "-in", paths[name], "-noout", "-pubkey"] if certificate else ["pkey", "-in", paths[name], "-pubout"]
            return _certificate_command(args, deadline)

        authority = paths.get("ca/ca.crt") if ca_present else paths.get("dashboard.crt")
        if authority:
            anchor = ["-CAfile", authority, "-CApath", str(empty), *no_store]
            identity_lines = _certificate_command(
                ["x509", "-in", authority, "-noout", "-issuer", "-subject", "-nameopt", "RFC2253"],
                deadline).decode("utf-8").splitlines()
            fields = dict(line.split("=", 1) for line in identity_lines if "=" in line)
            if not fields.get("issuer") or fields.get("issuer") != fields.get("subject"):
                raise UpdateError("the retained trust anchor is not self-signed")
        if ca_present:
            ca_text = text("ca/ca.crt")
            basic = _certificate_section(ca_text, "Basic Constraints")
            usage = _certificate_section(ca_text, "Key Usage")
            constraints = _certificate_section(ca_text, "Name Constraints")
            dns = set(re.findall(r"DNS:([^\s,]+)", constraints))
            networks = re.findall(r"IP:([^\s,]+)", constraints)
            if (not basic or not usage or "critical" not in basic.splitlines()[0] or "CA:TRUE, pathlen:0" not in basic or
                    "critical" not in usage.splitlines()[0] or "Certificate Sign" not in usage or
                    "CRL Sign" not in usage or not constraints or "critical" not in constraints.splitlines()[0] or
                    "Permitted:" not in constraints or "Excluded:" in constraints or
                    not dns or not dns <= {"local", "home.arpa"} or not 1 <= len(networks) <= 2 or
                    "prime256v1" not in ca_text):
                raise UpdateError("the retained authority constraints are not supported")
            try:
                ranges = tuple(ipaddress.IPv4Network(network) for network in networks)
            except ValueError as error:
                raise UpdateError("the retained authority address constraints are invalid") from error
            private = tuple(ipaddress.IPv4Network(network) for network in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"))
            if (ipaddress.IPv4Network("192.168.50.0/24") not in ranges or
                    any(not any(network.subnet_of(block) for block in private) and network.prefixlen < 24
                        for network in ranges)):
                raise UpdateError("the retained authority address ranges are too broad")
            if public("ca/ca.crt", True) != public("ca/ca.key", False):
                raise UpdateError("the retained authority key does not match its certificate")
            _certificate_command(["verify", *anchor, "-check_ss_sig", authority], deadline)
        if not authority_only and all(pair):
            key = public("dashboard.key", False)
            for name in ("dashboard.crt", "dashboard-wifi.crt"):
                if name not in files:
                    continue
                cert_text = text(name)
                names = _certificate_names(cert_text, allow_legacy_repeated_names=not ca_present)
                if public(name, True) != key:
                    raise UpdateError("the dashboard certificate does not match the retained key")
                _certificate_command(["verify", *anchor, "-purpose", "sslserver",
                                      *([] if ca_present else ["-check_ss_sig"]), paths[name]], deadline)
                if name == "dashboard-wifi.crt" and any("jittersim" in item.lower().replace("-", "") for item in names):
                    raise UpdateError("the Wi-Fi certificate contains a private dashboard name")
                required = settings.values() if ca_present else (
                    [settings["JITTERSIM_LOCAL_NAME"]] if "JITTERSIM_LOCAL_NAME" in settings else []
                )
                if any(value not in names for value in required):
                    raise UpdateError("the certificate does not cover the saved dashboard names")
            return "ca" if ca_present else "legacy"
    return "ca" if ca_present else "none"


def _certificate_write(path, content, *, mode=0o600):
    content = CertificateMaterial(content)
    _certificate_check()
    descriptor, temporary = tempfile.mkstemp(prefix=".certificate-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            os.fchmod(handle.fileno(), mode)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        _certificate_check()
        os.replace(temporary, path)
        _fsync_folder(path.parent)
        _certificate_check()
    finally:
        Path(temporary).unlink(missing_ok=True)


def _certificate_record(path):
    try:
        return json.loads(_certificate_bytes(path, private=True))
    except (ValueError, UnicodeError) as error:
        raise UpdateError("the certificate recovery record is damaged") from error


@contextmanager
def _certificate_lock(root, deadline):
    namespace = _certificate_directory(root / "certificate-recovery", private=True, create=True)
    descriptor = os.open(namespace / "lock", os.O_RDWR | os.O_CREAT |
                         getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0), 0o600)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
            raise UpdateError("the certificate recovery lock is not private")
        while True:
            _certificate_check()
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise UpdateError("certificate recovery lock exceeded its time allowance")
                time.sleep(min(0.02, remaining))
        _certificate_clean_temporary(namespace, {"a", "b", "lock", "current.json"})
        yield namespace
    finally:
        os.close(descriptor)


def _certificate_pointer(namespace):
    path = namespace / "current.json"
    if not os.path.lexists(path):
        return None
    value = _certificate_record(path)
    if (not isinstance(value, dict) or set(value) != {"format", "slot", "transaction", "active"} or
            type(value["format"]) is not int or value["format"] != CERTIFICATE_FORMAT or value["slot"] not in ("a", "b") or
            not isinstance(value["active"], bool) or
            not isinstance(value["transaction"], str) or
            not re.fullmatch(r"[0-9a-f]{64}", value["transaction"])):
        raise UpdateError("the certificate recovery pointer is not understood")
    return value


def _certificate_slot(namespace, slot):
    return _certificate_directory(namespace / slot, private=True)


def _certificate_slot_names(directory):
    allowed = {name.replace("/", "-") for name in CERTIFICATE_FILES} | {"complete.json"}
    return _certificate_clean_temporary(directory, allowed)


def _certificate_clean_temporary(directory, allowed):
    with os.scandir(directory) as entries:
        names = []
        temporary = []
        count = 0
        for entry in entries:
            _certificate_check()
            count += 1
            if count > len(allowed) + 2:
                raise UpdateError("certificate recovery generation contains too many files")
            if re.fullmatch(r"\.certificate-[a-zA-Z0-9_]{8}", entry.name):
                _certificate_bytes(directory / entry.name, private=True)
                temporary.append(entry.name)
            elif entry.name in allowed:
                names.append(entry.name)
            else:
                raise UpdateError("certificate recovery generation contains unknown files")
    for name in temporary:
        _certificate_check()
        (directory / name).unlink()
    if temporary:
        _fsync_folder(directory)
    return names


def _certificate_remove_slot(namespace, slot):
    path = namespace / slot
    if not os.path.lexists(path):
        return
    directory = _certificate_slot(namespace, slot)
    names = _certificate_slot_names(directory)
    for name in names:
        _certificate_check()
        _certificate_bytes(directory / name, private=True)
    for name in names:
        (directory / name).unlink()
    directory.rmdir()
    _fsync_folder(namespace)


def _certificate_load_slot(namespace, pointer, deadline):
    directory = _certificate_slot(namespace, pointer["slot"])
    names = _certificate_slot_names(directory)
    manifest = _certificate_record(directory / "complete.json")
    if (not isinstance(manifest, dict) or set(manifest) != {"format", "transaction", "identity", "files"} or
            type(manifest["format"]) is not int or manifest["format"] != CERTIFICATE_FORMAT or manifest["transaction"] != pointer["transaction"] or
            manifest["identity"] not in ("ca", "legacy", "none") or
            not isinstance(manifest["files"], dict) or set(manifest["files"]) - set(CERTIFICATE_FILES) or
            set(names) != {name.replace("/", "-") for name in manifest["files"]} | {"complete.json"}):
        raise UpdateError("the complete certificate recovery generation is not understood")
    files = CertificateFiles()
    for name, expected in manifest["files"].items():
        _certificate_check()
        content = _certificate_bytes(directory / name.replace("/", "-"), private=True)
        if not isinstance(expected, str) or hashlib.sha256(content).hexdigest() != expected:
            raise UpdateError("the certificate recovery generation has changed")
        files[name] = content
    if verify_certificate_identity(files, deadline=deadline) != manifest["identity"]:
        raise UpdateError("the retained certificate identity has changed")
    return files


def _certificate_live_files(config):
    _certificate_directory(config)
    if os.path.lexists(config / "ca"):
        _certificate_directory(config / "ca")
        if not all(os.path.lexists(config / name) for name in CERTIFICATE_FILES[:2]):
            raise UpdateError("the current certificate authority is incomplete")
    files = CertificateFiles()
    for name in CERTIFICATE_FILES:
        _certificate_check()
        path = config / name
        if os.path.lexists(path):
            try:
                files[name] = _certificate_bytes(path, private=name == "ca/ca.key")
            except OSError as error:
                raise UpdateError("the current certificate identity could not be read") from error
    return files


@_certificate_operation
def keep_certificate_identity(root, config, transaction, *, deadline=None, budget=None):
    """Durably retain the identity immediately before destructive restoration."""
    deadline = deadline if deadline is not None else time.monotonic() + CERTIFICATE_SECONDS
    if not re.fullmatch(r"[0-9a-f]{64}", transaction):
        raise UpdateError("certificate recovery needs a stable restore identifier")
    with _certificate_lock(root, deadline) as namespace:
        return _keep_certificate_locked(namespace, config, transaction, deadline)


def _keep_certificate_locked(namespace, config, transaction, deadline):
    previous = _certificate_pointer(namespace)
    if previous and previous["active"]:
        if previous["transaction"] != transaction:
            raise UpdateError("an earlier certificate recovery still needs to finish")
        _certificate_load_slot(namespace, previous, deadline)
        return previous
    files = _certificate_live_files(config)
    kind = verify_certificate_identity(files, deadline=deadline)
    slot = "b" if previous and previous["slot"] == "a" else "a"
    _certificate_remove_slot(namespace, slot)
    directory = namespace / slot
    directory.mkdir(mode=0o700)
    for name, content in files.items():
        _certificate_write(directory / name.replace("/", "-"), content)
    manifest = {"format": CERTIFICATE_FORMAT, "transaction": transaction,
                "identity": kind,
                "files": {name: hashlib.sha256(content).hexdigest() for name, content in files.items()}}
    _certificate_write(directory / "complete.json", json.dumps(manifest).encode("utf-8"))
    _fsync_folder(directory)
    _fsync_folder(namespace)
    pointer = {"format": CERTIFICATE_FORMAT, "slot": slot,
               "transaction": transaction, "active": True}
    _certificate_write(namespace / "current.json", json.dumps(pointer).encode("utf-8"))
    if previous:
        _certificate_remove_slot(namespace, previous["slot"])
    return pointer


@_certificate_operation
def prepare_certificate_restore(root, config, transaction, *, deadline=None, budget=None):
    """Recover an owned adoption before capturing a new destructive restore."""
    if not isinstance(transaction, str) or not re.fullmatch(r"[0-9a-f]{64}", transaction):
        raise UpdateError("certificate recovery needs a stable restore identifier")
    with _certificate_lock(root, deadline) as namespace:
        previous = _certificate_pointer(namespace)
        if not previous or not previous["active"]:
            recover_incomplete_certificate_adoption(config, deadline=deadline)
        return _keep_certificate_locked(namespace, config, transaction, deadline)


def _certificate_permissions(config, files):
    try:
        gid = grp.getgrnam("jittersim").gr_gid if os.geteuid() == 0 else os.getgid()
        owner = os.geteuid()
        modes = [(config, 0o750, gid)]
        if "ca/ca.crt" in files:
            modes.append((config / "ca", 0o750, gid))
        for name in files:
            mode = 0o600 if name == "ca/ca.key" else 0o640 if name in ("dashboard.key", "trust.env") else 0o644
            file_gid = gid if name in ("dashboard.key", "trust.env") else 0 if owner == 0 else os.getgid()
            modes.append((config / name, mode, file_gid))
        for path, mode, group in modes:
            _certificate_check()
            if path.is_symlink():
                raise UpdateError("certificate restoration cannot change permissions through a link")
            os.chown(path, owner, group, follow_symlinks=False)
            os.chmod(path, mode, follow_symlinks=False)
            info = path.lstat()
            if info.st_uid != owner or info.st_gid != group or stat.S_IMODE(info.st_mode) != mode:
                raise UpdateError("certificate ownership or permissions could not be restored")
    except (OSError, LookupError) as error:
        raise UpdateError("certificate ownership or permissions could not be restored") from error


@_certificate_operation
def restore_certificate_identity(root, config, retained, *, deadline=None, budget=None):
    """Restore verified current files before starting any application package."""
    deadline = deadline if deadline is not None else time.monotonic() + CERTIFICATE_SECONDS
    with _certificate_lock(root, deadline) as namespace:
        pointer = _certificate_pointer(namespace)
        if pointer != retained or not pointer["active"]:
            raise UpdateError("certificate recovery no longer belongs to this restore")
        files = _certificate_load_slot(namespace, pointer, deadline)
        _restore_certificate_files(config, files)


def _restore_certificate_files(config, files):
    _certificate_directory(config)
    if "ca/ca.crt" in files:
        (config / "ca").mkdir(mode=0o700, exist_ok=True)
        _certificate_directory(config / "ca")
    for name in CERTIFICATE_FILES:
        destination = config / name
        if name in files:
            _certificate_write(destination, files[name])
        elif os.path.lexists(destination):
            _certificate_bytes(destination)
            destination.unlink()
            _fsync_folder(destination.parent)
    if "ca/ca.crt" not in files and os.path.lexists(config / "ca"):
        directory = _certificate_directory(config / "ca")
        with os.scandir(directory) as entries:
            if next(entries, None) is not None:
                raise UpdateError("an unexpected authority remains after absent-identity restoration")
        directory.rmdir()
        _fsync_folder(config)
    _certificate_permissions(config, files)
    if _certificate_live_files(config) != files:
        raise UpdateError("the restored certificate identity does not match its retained generation")


def _adoption_files(directory, expected):
    _certificate_directory(directory, private=True)
    names = _certificate_clean_temporary(directory, {name.replace("/", "-") for name in CERTIFICATE_FILES})
    if set(names) != {name.replace("/", "-") for name in expected}:
        raise UpdateError("the adoption candidate generation is incomplete")
    files = CertificateFiles()
    for name, digest in expected.items():
        content = _certificate_bytes(directory / name.replace("/", "-"), private=True)
        if hashlib.sha256(content).hexdigest() != digest:
            raise UpdateError("the owned adoption candidate has changed")
        files[name] = content
    return files


def _adoption_proof(root, pointer, namespace, deadline):
    proof = _certificate_record(root / "adoption.json")
    if (not isinstance(proof, dict) or
            set(proof) != {"format", "transaction", "state", "original", "candidate"} or
            type(proof["format"]) is not int or proof["format"] != CERTIFICATE_FORMAT or
            proof["transaction"] != pointer["transaction"] or
            proof["state"] not in ("publishing", "restoring") or
            any(not isinstance(proof[key], dict) or set(proof[key]) - set(CERTIFICATE_FILES) or
                any(not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value)
                    for value in proof[key].values()) for key in ("original", "candidate"))):
        raise UpdateError("the active certificate adoption proof is not understood")
    original = _certificate_load_slot(namespace, pointer, deadline)
    if (verify_certificate_identity(original, deadline=deadline) != "legacy" or
            {name: hashlib.sha256(content).hexdigest() for name, content in original.items()} != proof["original"]):
        raise UpdateError("certificate adoption does not own a complete legacy identity")
    candidate = _adoption_files(root / "candidate", proof["candidate"])
    if verify_certificate_identity(candidate, deadline=deadline) != "ca":
        raise UpdateError("certificate adoption has no verified candidate authority")
    return proof, original, candidate


def _adoption_current(config, original, candidate):
    current = _certificate_live_files_for_adoption(config)
    for name, content in current.items():
        if content != original.get(name) and content != candidate.get(name):
            raise UpdateError("certificate adoption cannot replace an unrelated current identity")
    if set(original) - set(current):
        raise UpdateError("legacy identity files are missing outside an owned publication point")
    return current


def _certificate_live_files_for_adoption(config):
    # The only allowed incomplete directory belongs to the recorded new CA.
    _certificate_directory(config)
    if os.path.lexists(config / "ca"):
        directory = _certificate_directory(config / "ca")
        _certificate_clean_temporary(directory, {"ca.crt", "ca.key"})
    files = CertificateFiles()
    for name in CERTIFICATE_FILES:
        if os.path.lexists(config / name):
            files[name] = _certificate_bytes(config / name, private=name == "ca/ca.key")
    return files


@_certificate_operation
def publish_certificate_adoption(config, candidate=None, *, required_names=(), required_addresses=(),
                                 deadline=None, budget=None):
    """Publish only a verified candidate belonging to an explicit legacy transition."""
    root = _certificate_directory(config / ".certificate-adoption", private=True, create=True)
    with _certificate_lock(root, deadline) as namespace:
        pointer = _certificate_pointer(namespace)
        if pointer and pointer["active"]:
            proof, original, owned = _adoption_proof(root, pointer, namespace, deadline)
            if proof["state"] != "publishing":
                raise UpdateError("the earlier certificate adoption recovery must finish first")
        else:
            if candidate is None:
                return False
            original = _certificate_live_files(config)
            if verify_certificate_identity(original, deadline=deadline) != "legacy":
                raise UpdateError("certificate adoption requires a complete legacy identity")
            if verify_certificate_identity(candidate, deadline=deadline) != "ca":
                raise UpdateError("certificate adoption requires a complete candidate authority")
            directory = root / "candidate"
            if os.path.lexists(directory):
                _certificate_directory(directory, private=True)
                names = _certificate_clean_temporary(directory, {name.replace("/", "-") for name in CERTIFICATE_FILES})
                for name in names:
                    _certificate_bytes(directory / name, private=True)
                for name in names:
                    _certificate_check()
                    (directory / name).unlink()
                directory.rmdir()
            directory.mkdir(mode=0o700)
            owned = CertificateFiles({name: CertificateMaterial(content) for name, content in candidate.items()})
            for name, content in owned.items():
                _certificate_write(directory / name.replace("/", "-"), content)
            _fsync_folder(directory)
            proof = {"format": CERTIFICATE_FORMAT, "transaction": os.urandom(32).hex(),
                     "state": "publishing",
                     "original": {name: hashlib.sha256(content).hexdigest() for name, content in original.items()},
                     "candidate": {name: hashlib.sha256(content).hexdigest() for name, content in owned.items()}}
            _certificate_write(root / "adoption.json", json.dumps(proof).encode())
            pointer = _keep_certificate_locked(namespace, config, proof["transaction"], deadline)
            proof, original, owned = _adoption_proof(root, pointer, namespace, deadline)
        certificate_text = _certificate_command(
            ["x509", "-in", str(root / "candidate/dashboard.crt"), "-noout", "-text"], deadline).decode("utf-8")
        if (any(name not in _certificate_names(certificate_text) for name in required_names) or
                any("IP Address:" + address not in _certificate_section(certificate_text, "Subject Alternative Name")
                    for address in required_addresses)):
            raise UpdateError("the requested address changed during certificate adoption")
        _adoption_current(config, original, owned)
        _restore_certificate_files(config, owned)
        return True


@_certificate_operation
def recover_incomplete_certificate_adoption(config, *, deadline=None, budget=None):
    """Recover an interrupted owned transition without using historical snapshots."""
    root = config / ".certificate-adoption"
    if not os.path.lexists(root):
        return False
    _certificate_directory(root, private=True)
    with _certificate_lock(root, deadline) as namespace:
        pointer = _certificate_pointer(namespace)
        if not pointer or not pointer["active"]:
            return False
        proof, original, candidate = _adoption_proof(root, pointer, namespace, deadline)
        current = _adoption_current(config, original, candidate)
        try:
            kind = verify_certificate_identity(current, deadline=deadline)
        except UpdateError as error:
            if type(error) is not UpdateError:
                raise
            _certificate_check()
            kind = None
        if kind == "ca" and proof["state"] == "publishing":
            return False
        proof["state"] = "restoring"
        _certificate_write(root / "adoption.json", json.dumps(proof).encode())
        _restore_certificate_files(config, original)
        return True


@_certificate_operation
def finish_certificate_recovery(root, *, retained=None, deadline=None, budget=None):
    """Mark recovery complete only after the owning flow confirms healthy startup."""
    path = root / "certificate-recovery"
    if not os.path.lexists(path):
        return
    with _certificate_lock(root, deadline) as namespace:
        pointer = _certificate_pointer(namespace)
        if pointer is None or not pointer["active"]:
            return
        if retained is not None and pointer != retained:
            raise UpdateError("certificate completion belongs to a different transition")
        _certificate_load_slot(namespace, pointer, deadline)
        if root.name == ".certificate-adoption":
            proof, original, candidate = _adoption_proof(root, pointer, namespace, deadline)
            current = _adoption_current(root.parent, original, candidate)
            expected = candidate if proof["state"] == "publishing" else original
            if current != expected:
                raise UpdateError("the owned certificate adoption has not completed")
        pointer["active"] = False
        _certificate_write(namespace / "current.json", json.dumps(pointer).encode("utf-8"))


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


def _private_folder(root):
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    if root.is_symlink() or root.stat().st_uid != os.geteuid() or root.stat().st_mode & 0o022:
        raise UpdateError("update directory must be owned by the administrator")


def _lock_anchor(descriptor, directory):
    opened = os.fstat(descriptor)
    named = os.stat("lock", dir_fd=directory, follow_symlinks=False)
    if (not stat.S_ISREG(opened.st_mode) or not stat.S_ISREG(named.st_mode)
            or opened.st_uid != os.geteuid() or opened.st_mode & 0o022 or opened.st_nlink != 1
            or (opened.st_dev, opened.st_ino) != (named.st_dev, named.st_ino)):
        raise UpdateError("the operation has no safe update ownership")


OPERATION_RECORDS = ("operation-pending.json", "installer-pending.json")
PANIC_SOURCE_FILES = ("src/jittersim/panic_guard.py", "src/jittersim/networking/helper.py",
                      "src/jittersim/networking/access.py")
# The combined consumers were reviewed at 6fc3524. Labels cannot establish
# compatibility with persistent stop intent.
PANIC_APPROVED_SOURCES = ({
    "src/jittersim/panic_guard.py": "ba481860bd364e5f548aa14381abe3392afc75959bbf6b8086a17169a4c99b3d",
    "src/jittersim/networking/helper.py": "e9b0e6070c5bb27d2983711b808e33e541099c762fc562eaf5c73ede3d2045e4",
    "src/jittersim/networking/access.py": "1d4edebe57877e6db6bbbcd0d1adaecfd9fa48573c0f8830b10a2dcdaaf04ec6",
}, {
    # See docs/review-results-final-panic-consumers-2026-10-04.json.
    "src/jittersim/panic_guard.py": "ba481860bd364e5f548aa14381abe3392afc75959bbf6b8086a17169a4c99b3d",
    "src/jittersim/networking/helper.py": "84683fdeb036d0b4872ab32d8f1539bbeb32f8f8879af3caf507f23bc4205e7e",
    "src/jittersim/networking/access.py": "048c13f63e32c862a0d829715ee4259c47d2a2664be17a2ca65b78e21344ab77",
}, {
    # See docs/review-results-router-recovery-consumers-2026-10-05.json.
    "src/jittersim/panic_guard.py": "ba481860bd364e5f548aa14381abe3392afc75959bbf6b8086a17169a4c99b3d",
    "src/jittersim/networking/helper.py": "e940fc8f8da5149cf18fd25f230c1702110f1c3445362be9209a4d8f382e0499",
    "src/jittersim/networking/access.py": "048c13f63e32c862a0d829715ee4259c47d2a2664be17a2ca65b78e21344ab77",
}, {
    # See docs/review-results-router-recovery-optin-removal-2026-10-05.json.
    "src/jittersim/panic_guard.py": "ba481860bd364e5f548aa14381abe3392afc75959bbf6b8086a17169a4c99b3d",
    "src/jittersim/networking/helper.py": "1cbc5dd4eb2e93f6205035f38218fe4ca11b34281a23e2c998ccb6e8b76c4228",
    "src/jittersim/networking/access.py": "048c13f63e32c862a0d829715ee4259c47d2a2664be17a2ca65b78e21344ab77",
}, {
    # See docs/review-results-panic-consumers-outage-logging-2026-10-09.json. Persistent outage
    # logging and the bounded dashboard status wait. The diagnostic modules that the helper
    # dispatch calls are pinned by tests/unit/test_panic_dispatch_dependencies.py.
    "src/jittersim/panic_guard.py": "ba481860bd364e5f548aa14381abe3392afc75959bbf6b8086a17169a4c99b3d",
    "src/jittersim/networking/helper.py": "252ccaffac176fc0f97dad552d28edb9d8119f291958d8a45876c6b3ca1be166",
    "src/jittersim/networking/access.py": "ddc801a896afa2b977c12662bdb34388680502350b8b5b16099cbc3978725ddd",
}, {
    # See docs/review-results-gated-panic-batch-2026-10-10.json. The combined batch: restart latch,
    # Panic release of the household rules, protected-device emergency check, the Panic slot
    # reservation and the total Panic deadline. networking/dashboard_access.py and
    # networking/deadlines.py are pinned by tests/unit/test_dashboard_access_pin.py.
    "src/jittersim/panic_guard.py": "041cf171875d7b85d1c0664c2acff262542a3ac3c2b2e8fe51f80d2d59d6d639",
    "src/jittersim/networking/helper.py": "a765dc97cce4015e0d1189cf92373395e5ca88c9be19b2e25c000e0975c7314b",
    "src/jittersim/networking/access.py": "41a0f6f96cf2a20616d7ac613dd95acf2cd31085a13018ce14667dea2e11e1f3",
},)


class PanicProtectionError(UpdateError):
    """Keep the current installation and retained intent for management recovery."""


def owned_source_bytes(source, relative, *, maximum):
    """Read one fixed candidate module through owned non-following descriptors."""
    parts = PurePosixPath(relative).parts
    if not parts or relative.startswith("/") or ".." in parts or len(parts) > 6:
        raise UpdateError("the candidate module path is invalid")
    directories = []
    try:
        directory = os.open(source, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        directories.append(directory)
        for name in parts[:-1]:
            directory = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            directories.append(directory)
        for directory in directories:
            info = os.fstat(directory)
            if info.st_uid != os.geteuid() or info.st_mode & 0o022:
                raise UpdateError("the candidate module directory is not owned safely")
        descriptor = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directories[-1])
        with os.fdopen(descriptor, "rb") as handle:
            info = os.fstat(handle.fileno())
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                    or info.st_nlink != 1 or info.st_mode & 0o022 or info.st_size > maximum):
                raise UpdateError("the candidate module is not owned safely")
            raw = handle.read(maximum + 1)
            after = os.fstat(handle.fileno())
            if len(raw) > maximum or (info.st_size, info.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                raise UpdateError("the candidate module changed while reading it")
            return raw
    except OSError as error:
        raise UpdateError("the candidate module could not be verified") from error
    finally:
        for directory in reversed(directories):
            os.close(directory)


@contextmanager
def identity_modules(source):
    """Use fixed owned stdlib modules with this exact executing manager."""
    current = sys.modules.get(__name__)
    if current is None or current.__dict__ is not globals():
        raise UpdateError("the executing recovery manager is unavailable")
    names = ("localname", "localca", "mdnsname")
    contents = {name: owned_source_bytes(source, "src/jittersim/" + name + ".py",
                                         maximum=1024 * 1024) for name in names}
    prefix = "_jittersim_identity_" + secrets.token_hex(16)
    package = types.ModuleType(prefix)
    package.__path__ = []
    installed = {prefix: package, prefix + ".updates": current}
    result = types.SimpleNamespace()
    try:
        sys.modules.update(installed)
        for name in names:
            module = types.ModuleType(prefix + "." + name)
            module.__package__ = prefix
            module.__file__ = str(source / "src/jittersim" / (name + ".py"))
            installed[module.__name__] = module
            sys.modules[module.__name__] = module
            setattr(package, name, module)
            exec(compile(contents[name], module.__file__, "exec"), module.__dict__)
            setattr(result, name, module)
        yield result
    finally:
        for name, module in installed.items():
            if sys.modules.get(name) is module:
                del sys.modules[name]


def prepare_dashboard_identity(source, config, budget, *, group="jittersim"):
    """Keep the update owner and total allowance during certificate setup."""
    budget.check()
    with identity_modules(source) as modules:
        try:
            name, _ = modules.localname.install_config(config, group=group)
            modules.localca.prepare(config, host_name=socket.gethostname().split(".")[0] + ".local",
                                    neutral_name=name, ethernet_ip=modules.localca.detect_ethernet_ip(),
                                    budget=budget)
            modules.localca.set_permissions(config, group)
        except (CommandNotStopped, OperationOwnershipUnknown):
            raise
        except (OSError, ValueError, modules.localca.TrustError) as error:
            raise UpdateError("the dashboard identity could not be prepared") from error
    budget.check()


def configure_dashboard_name(source, config):
    with identity_modules(source) as modules:
        try:
            modules.mdnsname.configure_avahi(interfaces=runtime_interfaces(config))
        except (OSError, ValueError, modules.mdnsname.ConfigureError) as error:
            raise UpdateError("the dashboard name service could not be prepared") from error


def configure_outage_journal(code, config, manager, run):
    """Keep optional journal setup outside application and trust recovery."""
    source = code / "src/jittersim/journal_policy.py"
    utility = manager.with_name("jittersim-outage-journal.py")
    try:
        if source.is_file():
            raw = owned_source_bytes(code, "src/jittersim/journal_policy.py", maximum=64 * 1024)
            if utility.is_symlink():
                return False
            descriptor, temporary = tempfile.mkstemp(prefix=".outage-journal-", dir=utility.parent)
            try:
                with os.fdopen(descriptor, "wb") as stream:
                    stream.write(raw)
                    stream.flush()
                    os.fsync(stream.fileno())
                Path(temporary).chmod(0o644)
                os.replace(temporary, utility)
                _fsync_folder(utility.parent)
            finally:
                Path(temporary).unlink(missing_ok=True)
            action = "install"
        else:
            action = "remove"
        if not utility.exists() or utility.is_symlink():
            return False
        info = utility.stat()
        if info.st_uid != os.geteuid() or info.st_mode & 0o022 or not stat.S_ISREG(info.st_mode):
            return False
        run([PYTHON, "-I", str(utility), action, "--config-root",
             str(config.with_name(config.name + "-outage-journal"))], 10)
        return True
    except (OSError, ValueError):
        return False
    except UpdateError as error:
        # Unknown or running privileged commands retain the update's existing gate.
        if type(error) is not UpdateError:
            raise
        return False


def _panic_candidate(source):
    if not 1 <= len(PANIC_APPROVED_SOURCES) <= 32:
        raise UpdateError("the candidate needs reviewed Panic protection")
    contents = {name: owned_source_bytes(source, name, maximum=1024 * 1024)
                for name in PANIC_SOURCE_FILES}
    observed = {name: hashlib.sha256(raw).hexdigest() for name, raw in contents.items()}
    if not any(bundle == observed for bundle in PANIC_APPROVED_SOURCES):
        raise UpdateError("the candidate needs reviewed Panic protection")
    return _panic_module(contents[PANIC_SOURCE_FILES[0]], source / PANIC_SOURCE_FILES[0])


def _panic_module(raw, path):
    digest = hashlib.sha256(raw).hexdigest()
    if not any(bundle.get(PANIC_SOURCE_FILES[0]) == digest for bundle in PANIC_APPROVED_SOURCES):
        raise UpdateError("the retained producer needs reviewed Panic protection")
    module = types.ModuleType("_jittersim_installer_panic")
    module.__file__ = str(path)
    try:
        exec(compile(raw, module.__file__, "exec"), module.__dict__)
    except (SyntaxError, ValueError) as error:
        raise UpdateError("the candidate stop protection cannot run") from error
    module._verified_bytes = raw
    return module


class PanicProtection:
    """Inspect before stopping then establish latest intent under quiescence."""

    def __init__(self, source, *, retained=None, guard_path=None, legacy_path=None, owner_uid=None):
        try:
            if retained is not None and (retained.exists() or retained.is_symlink()):
                raw = owned_source_bytes(retained.parent, retained.name, maximum=1024 * 1024)
                module = _panic_module(raw, retained)
            else:
                module = _panic_candidate(source)
            self.module = module
            self.guard = module.PanicGuard(guard_path or module.PANIC_GUARD_PATH,
                                          owner_uid=0 if owner_uid is None else owner_uid)
            self.legacy = legacy_path or module.PANIC_LEGACY_PATH
        except (OSError, UpdateError) as error:
            raise PanicProtectionError("The selected package cannot preserve Panic protection.") from error

    def inspect(self, *sources):
        try:
            state = self.guard.inspect_upgrade(self.legacy)
            for source in sources:
                self.module.check_panic_candidate(state, source, PANIC_APPROVED_SOURCES,
                                                  owner_uid=self.guard.owner_uid)
            return state
        except OSError as error:
            raise PanicProtectionError("The selected package cannot preserve current Panic protection.") from error

    def provision(self, *sources):
        self.inspect(*sources)
        try:
            self.guard.provision_upgrade(self.legacy)
            # An independently established guard wins initialize-only publication.
            self.inspect(*sources)
        except OSError as error:
            raise PanicProtectionError("Current Panic protection could not be saved.") from error

    def retain(self, destination):
        descriptor, name = tempfile.mkstemp(prefix=".panic-manager-", dir=destination.parent)
        temporary = Path(name)
        try:
            with os.fdopen(descriptor, "wb") as output:
                output.write(self.module._verified_bytes)
                output.flush()
                os.fchmod(output.fileno(), 0o644)
                os.fsync(output.fileno())
            temporary.replace(destination)
            _fsync_folder(destination.parent)
        finally:
            temporary.unlink(missing_ok=True)


def prepare_manual_install(source, *, descriptor=9, root=UPDATE_ROOT):
    """Prove fresh state or inspect known legacy intent before the shell starts."""
    module = _panic_candidate(source)
    guard = module.PanicGuard()
    module.verify_fresh_installer_lock(root, descriptor, owner_uid=guard.owner_uid)
    if guard.read() is not None:
        return
    legacy = module.PANIC_LEGACY_PATH
    if legacy.exists() or legacy.is_symlink():
        guard.inspect_upgrade(legacy)
    else:
        guard.provision_fresh()


def _operation_record(root, name="operation-pending.json"):
    if name not in OPERATION_RECORDS:
        raise UpdateError("the command ownership record name is invalid")
    path = root / name
    if not path.exists() and not path.is_symlink():
        return None
    record = _recovery_record(path)
    if type(record.get("format")) is not int:
        raise UpdateError("the unfinished command ownership format is invalid")
    if record.get("format") == 1 and set(record) == {"format", "group", "boot"}:
        return record
    if (record.get("format") != OPERATION_FORMAT
            or set(record) != {"format", "state", "owner", "boot", "nonce", "group"}
            or record["state"] not in ("launching", "running")
            or type(record["owner"]) is not int or not 0 < record["owner"] < 2 ** 31
            or not isinstance(record["nonce"], str) or not ATTEMPT.fullmatch(record["nonce"])
            or (record["state"] == "launching" and record["group"] is not None)
            or (record["state"] == "running" and
                (type(record["group"]) is not int or not 0 < record["group"] < 2 ** 31))):
        raise UpdateError("the unfinished command ownership record is invalid")
    return record


def _operation_busy(root, budget, name=None):
    if name is None:
        return any(_operation_busy(root, budget, item) for item in OPERATION_RECORDS)
    record = _operation_record(root, name)
    if record is None:
        return False
    if record.get("state") != "launching":
        return command_still_running(record, budget)
    # Before GO the child can only wait on its private pipe. Parent death
    # closes that pipe and cannot authorize the requested executable.
    if not _same_boot(record):
        return False
    budget.check()
    try:
        os.kill(record["owner"], 0)
    except ProcessLookupError:
        return False
    return True


def _inherited_installer(record):
    identity = os.environ.get("JITTERSIM_INSTALLER_NONCE", "")
    return (record is not None and record.get("state") == "running" and _same_boot(record)
            and record["group"] == os.getpgrp()
            and ATTEMPT.fullmatch(identity) is not None
            and secrets.compare_digest(record["nonce"], identity))


def retain_command(root, error):
    """Keep physical ownership before an internal recovery handler hides it."""
    name = getattr(error, "lease_name", "operation-pending.json")
    existing = _operation_record(root, name)
    if existing is not None:
        if existing.get("group") != error.process.pid:
            raise OperationOwnershipUnknown("unfinished command ownership changed") from error
        return
    atomic_json(root / name, {
        "format": OPERATION_FORMAT, "state": "running", "owner": os.getpid(),
        "group": error.process.pid, "boot": _current_boot(), "nonce": secrets.token_hex(16),
    })


@contextmanager
def update_lock(root=UPDATE_ROOT, *, wait=0, descriptor=None):
    if not isinstance(wait, (int, float)) or not math.isfinite(wait) or not 0 <= wait <= 15:
        raise UpdateError("update lock wait is invalid")
    deadline = time.monotonic() + wait
    _private_folder(root)
    directory = None
    owned = descriptor is None
    previous = getattr(COMMAND_CONTEXT, "operation", None)
    previous_ready = getattr(COMMAND_CONTEXT, "operation_ready", False)
    previous_lease = getattr(COMMAND_CONTEXT, "lease_name", "operation-pending.json")
    entered = False
    try:
        directory = os.open(root, os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0))
        folder = os.fstat(directory)
        if folder.st_uid != os.geteuid() or folder.st_mode & 0o077:
            raise UpdateError("update directory must be private to the administrator")
        if owned:
            descriptor = os.open("lock", os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0),
                                 0o600, dir_fd=directory)
        _lock_anchor(descriptor, directory)
        while True:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError as error:
                if time.monotonic() >= deadline:
                    raise UpdateError("an update operation is already running") from error
                time.sleep(min(0.02, max(0, deadline - time.monotonic())))
        _lock_anchor(descriptor, directory)
        budget = UpdateBudget()
        budget.deadline = min(budget.deadline, time.monotonic() + 2)
        for name in OPERATION_RECORDS:
            pending = _operation_record(root, name)
            if pending is None:
                continue
            inherited = not owned and name == "installer-pending.json" and _inherited_installer(pending)
            if inherited:
                continue
            if _operation_busy(root, budget, name):
                raise UpdateError("an unfinished command still owns the operation")
            (root / name).unlink()
            _fsync_folder(root)
        COMMAND_CONTEXT.operation = (Path(root), descriptor)
        COMMAND_CONTEXT.operation_ready = False
        COMMAND_CONTEXT.lease_name = "operation-pending.json"
        entered = True
        try:
            yield descriptor
        except CommandNotStopped as error:
            retain_command(root, error)
            raise
    except OSError as error:
        if entered:
            raise
        raise UpdateError("the operation has no safe update ownership") from error
    finally:
        COMMAND_CONTEXT.operation = previous
        COMMAND_CONTEXT.operation_ready = previous_ready
        COMMAND_CONTEXT.lease_name = previous_lease
        if directory is not None:
            os.close(directory)
        # A borrowed open file description remains owned by the installer.
        # Closing our own descriptor also leaves inherited child ownership live.
        if owned and descriptor is not None:
            os.close(descriptor)


@contextmanager
def manual_identity_operation(root=UPDATE_ROOT, *, descriptor=None):
    if descriptor is None and os.environ.get("JITTERSIM_INSTALL_LOCK_FD") == "9":
        descriptor = 9
    with update_lock(root, descriptor=descriptor):
        request = root / "request.json"
        if request.exists() or request.is_symlink():
            raise UpdateError("a retained update must finish before changing dashboard access")
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
        descriptor = os.dup(source) if isinstance(source, int) else os.open(source, flags)
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
    staged_key = version_key(record.get("version"))
    installed_key = version_key(_record(code / ".jittersim-release.json").get("version"))
    if staged_key is not None and installed_key is not None and staged_key < installed_key:
        return (
            "The staged release is an older number than the installed one. "
            "Use Restore previous installation to go back."
        )
    return None


def stage_release(path, *, signature=None, root=UPDATE_ROOT, code=CODE_ROOT,
                  config=CONFIG_ROOT, sha256=None, version=None, origin=None, tag=None):
    """Stage a release that a trusted key signed. Nothing reads the archive's contents
    before its signature matched."""
    if sha256 is not None and not re.fullmatch(r"[0-9a-f]{64}", sha256):
        raise UpdateError("SHA-256 is invalid")
    if version is not None and not VERSION.fullmatch(version):
        raise UpdateError("version is invalid")
    if origin not in (None, "github") or (tag is not None and not LABEL.fullmatch(tag)):
        raise UpdateError("release source is invalid")
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
                raise NotNewerError(
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
            if origin is not None:
                record["source"] = origin
            if tag is not None:
                record["tag"] = tag
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


def stage_upload(upload_id=None, *, spool=UPLOAD_SPOOL, lock=None, root=UPDATE_ROOT,
                 code=CODE_ROOT, config=CONFIG_ROOT):
    """Stage the release the dashboard received over HTTPS.

    The spool belongs to the dashboard's user so nothing in it is trusted. Both files are
    copied into a folder only root may enter while ownership excludes replacement. Then
    stage_release does the real work: a trusted key must have signed exactly these bytes
    and the release must be newer than every release installed before. So the admin token
    alone can stage only a release the owner signed.
    """

    # The copied standalone manager never needs this dashboard-only module.
    from jittersim.release_uploads import LOCK_PATH, root_inputs

    with root_inputs(upload_id, spool=spool, lock=LOCK_PATH if lock is None else lock, root=root) as inputs:
        archive, signature = inputs
        return stage_release(archive, signature=signature, root=root, code=code, config=config)


MAX_NOTES = 16 * 1024


def staged_notes(root=UPDATE_ROOT):
    """The release notes inside the staged archive (deploy/RELEASE-NOTES.md), which the same
    signature as the code covers, as text cut at 16 KB. None when there are none. The file is
    opened without following links and must be a regular file."""

    path = root / "staged" / "deploy" / "RELEASE-NOTES.md"
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    except OSError:
        return None
    try:
        with os.fdopen(descriptor, "rb") as handle:
            if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
                return None
            raw = handle.read(MAX_NOTES)
    except OSError:
        return None
    # A character cut in two by the limit is dropped, not shown as a replacement mark.
    decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
    text = decoder.decode(raw, final=len(raw) < MAX_NOTES).strip()
    return text or None


def update_status(*, root=UPDATE_ROOT, code=CODE_ROOT, config=CONFIG_ROOT, query=None, retry_probe=True):
    installed = read_json(code / ".jittersim-release.json")
    staged_file = root / "staged" / ".jittersim-release.json"
    staged = _record(staged_file)
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
    try:
        rollback_available = (recovery_backup(root) / "complete.json").exists()
        recovery_problem = None
    except UpdateError as error:
        rollback_available = False
        recovery_problem = str(error)
    retry, retry_reason = _retry_info(root, status, query or read_command) if retry_probe else (None, None)
    orphaned = retry is not None and status.get("phase") != "failed"
    notes = None
    if staged and reason is None:
        notes = staged_notes(root)
        # A check can stage another release between the two reads above and the notes read.
        # Notes are shown only when the staged record is still the one that was judged.
        try:
            if notes is not None and _record(staged_file) != staged:
                notes = None
        except UpdateError:
            notes = None
    return {
        "installed": installed.get("version", "Unversioned installation"),
        "available": staged.get("version"),
        "phase": "failed" if orphaned else status.get("phase", "idle"),
        "message": "The update stopped before it finished. Retry when you are ready." if orphaned else
                   status.get("message", "No approved release has been staged on this Pi."),
        "rollback_available": rollback_available,
        "recovery_problem": recovery_problem,
        "installable": bool(staged) and reason is None,
        "signed_by": staged.get("signer_label") if staged.get("signer") else None,
        "reason": reason,
        "trusted_keys": len(signers),
        # Whether a token is saved. Its value never leaves root.
        "token_set": token_is_set(config),
        "source": staged.get("source"),
        "tag": staged.get("tag"),
        **_operation_fields(status),
        "retry": retry,
        "retry_unavailable_reason": retry_reason,
        # Only for a staged release a trusted key signed and that is newer than the installed
        # one (the same test as installable). The Panic source check still happens at install.
        "notes": notes,
    }


MAX_COMMAND_OUTPUT = 1024 * 1024
MAX_COMMAND_DIAGNOSTIC = 16 * 1024
MAX_COMMAND_ANSWER = 4096


class CommandNotStopped(UpdateError):
    """A command still owns its work after cancellation was requested."""

    def __init__(self, name, process):
        super().__init__("release command did not stop: " + name)
        self.process = process
        self.lease_name = getattr(COMMAND_CONTEXT, "lease_name", "operation-pending.json")


class OperationOwnershipUnknown(UpdateError):
    """Command admission did not establish a durable completion identity."""


# This child owns no application code before its parent publishes its group.
# EOF, a late GO or an invalid byte cannot execute the requested command.
COMMAND_BARRIER = (
    "import os,select,sys,time\n"
    "fd=int(sys.argv[1]); deadline=float(sys.argv[2])\n"
    "clock=lambda:time.clock_gettime(time.CLOCK_MONOTONIC)\n"
    "ready=select.select([fd],[],[],max(0,deadline-clock()))[0]\n"
    "go=os.read(fd,1) if ready else b''\n"
    "os.close(fd)\n"
    "if go!=b'G' or clock()>=deadline: sys.exit(124)\n"
    "os.execvpe(sys.argv[3],sys.argv[3:],os.environ)\n"
)


def _owned_command_start(command, deadline, **options):
    operation = getattr(COMMAND_CONTEXT, "operation", None)
    if operation is None:
        return subprocess.Popen(command, **options), None
    root, descriptor = operation
    lease_name = COMMAND_CONTEXT.lease_name
    if _operation_record(root, lease_name) is not None:
        raise OperationOwnershipUnknown("an unfinished command already owns the operation")
    if root == UPDATE_ROOT and not COMMAND_CONTEXT.operation_ready:
        # Older managers must never encounter an operation record they cannot
        # honor. Manual identity commands use this same fixed recovery reader.
        compatible_manager(Path(__file__))
        if Path(__file__).resolve() != MANAGER_PATH.resolve():
            install_manager(Path(__file__), MANAGER_PATH)
        COMMAND_CONTEXT.operation_ready = True
    lease = {"format": OPERATION_FORMAT, "state": "launching", "owner": os.getpid(),
             "boot": _current_boot(), "nonce": secrets.token_hex(16), "group": None}
    reader = writer = None
    process = None
    try:
        atomic_json(root / lease_name, lease)
        if time.monotonic() >= deadline:
            raise UpdateError("release command has no time remaining")
        reader, writer = os.pipe()
        child_deadline = time.clock_gettime(time.CLOCK_MONOTONIC) + max(0, deadline - time.monotonic())
        if lease_name == "installer-pending.json":
            options["env"] = {**options["env"], "JITTERSIM_INSTALLER_NONCE": lease["nonce"],
                              "JITTERSIM_INSTALL_LOCK_FD": str(descriptor)}
        process = subprocess.Popen(
            [sys.executable, "-I", "-S", "-c", COMMAND_BARRIER, str(reader), repr(child_deadline), *command],
            pass_fds=(reader, descriptor), **options,
        )
        os.close(reader)
        reader = None
        lease = {**lease, "state": "running", "group": process.pid}
        atomic_json(root / lease_name, lease)
        if time.monotonic() >= deadline:
            raise UpdateError("release command has no time remaining")
        os.write(writer, b"G")
        return process, (lease_name, lease["nonce"])
    except BaseException as error:
        # Close GO before waiting. The inert wrapper must see EOF if command
        # publication was interrupted. Never run recovery on ambiguous state.
        if writer is not None:
            os.close(writer)
            writer = None
        if process is not None:
            try:
                process.wait(timeout=max(0, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                # No GO was sent so this is exclusively our inert wrapper.
                process.kill()
                try:
                    process.wait(timeout=max(0, deadline - time.monotonic()))
                except subprocess.TimeoutExpired:
                    pass
            for stream in (process.stdin, process.stdout, process.stderr):
                if stream is not None:
                    stream.close()
        raise OperationOwnershipUnknown("release command ownership could not be confirmed") from error
    finally:
        for pipe in (reader, writer):
            if pipe is not None:
                os.close(pipe)


def _completed_command(nonce):
    if nonce is None:
        return
    root, _ = COMMAND_CONTEXT.operation
    name, identity = nonce
    current = _operation_record(root, name)
    if current is None or current.get("nonce") != identity:
        raise OperationOwnershipUnknown("completed command ownership changed")
    try:
        (root / name).unlink()
        _fsync_folder(root)
    except OSError as error:
        raise OperationOwnershipUnknown("completed command ownership could not be saved") from error


def _command_environment():
    environment = dict(os.environ)
    for name in tuple(environment):
        if name.startswith("PIP_") or "TOKEN" in name or "PASSWORD" in name:
            environment.pop(name)
    # This special pip setting disables system and user configuration too.
    environment["PIP_CONFIG_FILE"] = os.devnull
    environment.update(getattr(COMMAND_CONTEXT, "environment", None) or {})
    return environment


def command_group_running(group, deadline):
    """Confirm all group members have ended, including an unreaped descendant."""
    if not Path("/proc").is_dir():
        try:
            os.killpg(group, 0)
            return True
        except ProcessLookupError:
            return False
    count = 0
    with os.scandir("/proc") as entries:
        for entry in entries:
            if time.monotonic() >= deadline:
                raise UpdateError("command completion could not be confirmed in time")
            if not entry.name.isdigit():
                continue
            count += 1
            if count > 65536:
                raise UpdateError("too many processes to confirm command completion")
            try:
                with (Path(entry.path) / "stat").open() as handle:
                    fields = handle.read(4096).rsplit(")", 1)[1].split()
                if fields[0] != "Z" and int(fields[2]) == group:
                    return True
            except FileNotFoundError:
                continue
            except (ValueError, IndexError) as error:
                raise UpdateError("command completion could not be confirmed") from error
    return False


def _command_result(command, timeout, capture, *, input_bytes=b"", output_limit=MAX_COMMAND_OUTPUT,
                    answer_limit=MAX_COMMAND_ANSWER, binary=False):
    if not isinstance(input_bytes, bytes) or len(input_bytes) > 65536:
        raise UpdateError("release command input exceeds its allowance")
    if type(output_limit) is not int or not 0 < output_limit <= MAX_COMMAND_OUTPUT:
        raise UpdateError("release command output allowance is invalid")
    name = next((part for part in command[1:2] if part.endswith(".py")), command[0])
    name = Path(name).name
    if not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout <= 0:
        raise UpdateError("release command has no time remaining: " + name)
    deadline = time.monotonic() + timeout
    # Leave a short part of the caller's allowance to cancel and reap our process.
    work_deadline = deadline - min(0.1, timeout / 5)
    try:
        process, nonce = _owned_command_start(
            command, work_deadline, stdin=subprocess.PIPE if input_bytes else subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            start_new_session=True, env=_command_environment(), bufsize=0,
        )
    except OSError as error:
        raise UpdateError("release command could not start: " + name) from error
    answer = bytearray()
    diagnostic = bytearray()
    total = 0
    failure = None
    returncode = None

    def finished():
        nonlocal returncode
        if hasattr(os, "waitid"):
            # Linux keeps the group leader waitable until we have cancelled its
            # descendants. Its group number cannot refer to another process.
            outcome = os.waitid(os.P_PID, process.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
            if outcome is None:
                return False
            returncode = outcome.si_status if outcome.si_code == os.CLD_EXITED else -outcome.si_status
        else:
            returncode = process.poll()
        return returncode is not None

    try:
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ, "stdout")
            selector.register(process.stderr, selectors.EVENT_READ, "stderr")
            written = 0
            if input_bytes:
                os.set_blocking(process.stdin.fileno(), False)
                selector.register(process.stdin, selectors.EVENT_WRITE, "stdin")
            while selector.get_map() or not finished():
                observe = getattr(COMMAND_CONTEXT, "observe", None)
                if observe is not None:
                    observe()
                remaining = work_deadline - time.monotonic()
                if remaining <= 0:
                    raise UpdateError("release command timed out: " + name)
                for key, _ in selector.select(timeout=min(remaining, 0.1)):
                    if key.data == "stdin":
                        try:
                            written += os.write(key.fd, memoryview(input_bytes)[written:written + 65536])
                        except BlockingIOError:
                            continue
                        if written == len(input_bytes):
                            selector.unregister(key.fileobj)
                            process.stdin.close()
                        continue
                    block = os.read(key.fd, 65536)
                    if not block:
                        selector.unregister(key.fileobj)
                        continue
                    total += len(block)
                    if total > output_limit:
                        raise UpdateError("release command produced too much output: " + name)
                    if key.data == "stdout" and capture:
                        answer.extend(block)
                        if len(answer) > answer_limit:
                            raise UpdateError("release command answer has too much output: " + name)
                    elif key.data == "stderr":
                        diagnostic.extend(block[:max(0, MAX_COMMAND_DIAGNOSTIC - len(diagnostic))])
        if time.monotonic() >= work_deadline:
            raise UpdateError("release command timed out: " + name)
        if returncode:
            # Tool output may contain a credential. Only a known category is exposed.
            category = "not enough free space" if b"no space left on device" in diagnostic.lower() else (
                "exit status " + str(returncode)
            )
            raise UpdateError("release command failed: " + name + " (" + category + ")")
    except OSError as error:
        failure = UpdateError("release command could not finish: " + name)
        failure.__cause__ = error
    except BaseException as error:
        failure = error
    finally:
        cancellation_failed = False
        # Linux retains the waitable leader until the entire group is confirmed
        # ended. macOS never sends cancellation to a reaped leader's group.
        if process.returncode is None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            except OSError:
                cancellation_failed = True
        for stream in (process.stdin, process.stdout, process.stderr):
            if stream is not None:
                stream.close()
        cancellation_deadline = min(deadline, time.monotonic() + 1)
        try:
            if not hasattr(os, "waitid"):
                process.wait(timeout=max(0, cancellation_deadline - time.monotonic()))
            while command_group_running(process.pid, cancellation_deadline):
                if time.monotonic() >= cancellation_deadline:
                    raise CommandNotStopped(name, process)
                time.sleep(min(0.01, max(0, cancellation_deadline - time.monotonic())))
            process.wait(timeout=max(0, cancellation_deadline - time.monotonic()))
            if cancellation_failed:
                raise CommandNotStopped(name, process)
        except (OSError, subprocess.TimeoutExpired, UpdateError) as error:
            if isinstance(error, CommandNotStopped):
                raise
            raise CommandNotStopped(name, process) from error
        _completed_command(nonce)
    if failure is not None:
        raise failure
    if binary:
        return bytes(answer)
    try:
        return answer.decode("utf-8").strip()
    except UnicodeDecodeError as error:
        raise UpdateError("release command answer is not readable: " + name) from error


def run_command(command, timeout=300):
    _command_result(command, timeout, False)


def read_command(command, timeout=10):
    return _command_result(command, timeout, True)


def capture_command(command, timeout=30, *, input=b"", limit=65536):
    """Return bounded bytes after the complete owned process group has ended."""
    return _command_result(command, timeout, True, input_bytes=input,
                           output_limit=limit, answer_limit=limit, binary=True)


def run_installer(installer, source, *, root=UPDATE_ROOT, descriptor=9, prepare=None, timeout=900,
                  enable_passwordless_staging=False):
    """Own the complete shell session as well as independent nested leaf groups."""
    prepare = prepare or prepare_manual_install
    with manual_identity_operation(root, descriptor=descriptor):
        prepare(source, root=root, descriptor=descriptor)
        COMMAND_CONTEXT.lease_name = "installer-pending.json"
        # The shell must inherit the anchor in its documented descriptor slot.
        if descriptor != 9:
            raise UpdateError("the installer needs its inherited descriptor 9")
        command = ["/bin/sh", str(installer)]
        if enable_passwordless_staging:
            command.append("--enable-passwordless-staging")
        command.append(str(source))
        run_command(command, timeout=timeout)


PUBLIC_REQUEST = re.compile(r"[0-9a-f]{32}\Z")
RETRY_ID = re.compile(r"[0-9a-f]{64}\Z")
RETRY_PHASES = {"failed", "queued", "preparing", "stopping", "backing-up",
                "installing", "checking", "rolling-back"}
RETRY_PROBE_LOCK = threading.Lock()
RETRY_PROBE_PENDING = None


def _retry_identity(request, status):
    return hashlib.sha256(json.dumps({"request": request, "status": status},
                                     sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _operation_fields(record):
    public_id, kind = record.get("request_id"), record.get("request_kind")
    if not isinstance(public_id, str) or not PUBLIC_REQUEST.fullmatch(public_id) or kind not in ("install", "rollback"):
        return {"request_id": None, "request_kind": None, "request_version": None}
    return {"request_id": public_id, "request_kind": kind,
            "request_version": record.get("version", record.get("request_version")) or None}


def _identify_request(request):
    """Migrate an older retained operation only while its owner holds the lock."""
    if "request_id" not in request and "request_kind" not in request:
        request["request_id"] = secrets.token_hex(16)
        request["request_kind"] = "install" if request.get("version") else "rollback"
    fields = _operation_fields(request)
    if fields["request_id"] is None:
        raise UpdateError("the queued update identity is invalid")
    return fields


def _retry_info(root, status, query, *, budget=None, admission=False):
    """Inspect retained work without queueing or changing an update record."""
    global RETRY_PROBE_PENDING
    if status.get("phase") not in RETRY_PHASES or not (root / "request.json").exists():
        return None, None
    budget = budget or UpdateBudget()
    budget.deadline = min(budget.deadline, budget.started + 2)
    reason = "The failed update is still busy or could not be checked. Try again shortly."
    if not RETRY_PROBE_LOCK.acquire(blocking=False):
        return None, reason
    try:
        if RETRY_PROBE_PENDING is not None:
            process, record = RETRY_PROBE_PENDING
            if command_still_running(record, budget):
                raise CommandNotStopped("update status probe", process)
            process.wait(timeout=budget.limit(0.1))
            RETRY_PROBE_PENDING = None
        return _inspect_retry(root, status, query, budget, admission)
    except CommandNotStopped as error:
        RETRY_PROBE_PENDING = (error.process, pending_command(error.process))
        if admission:
            raise
        return None, reason
    except (OSError, ValueError, UpdateError, subprocess.TimeoutExpired):
        return None, reason
    finally:
        RETRY_PROBE_LOCK.release()


def _inspect_retry(root, status, query, budget, admission):
    try:
        budget.check()
        if _operation_busy(root, budget):
            raise UpdateError("an unfinished command still owns the operation")
        request = _recovery_record(root / "request.json")
        fields = _operation_fields(request)
        if (fields["request_id"] is None
                or fields["request_id"] != status.get("request_id")
                or fields["request_kind"] != status.get("request_kind")
                or type(request.get("rollback")) is not bool
                or (request.get("version") not in (None, "") and
                    (not isinstance(request["version"], str) or not VERSION.fullmatch(request["version"])))
                or (fields["request_kind"] == "install" and not request.get("version"))
                or ("attempt" in request and (not isinstance(request["attempt"], str)
                    or not PUBLIC_REQUEST.fullmatch(request["attempt"])))
                or ("restart_only" in request and type(request["restart_only"]) is not bool)):
            raise UpdateError("the failed update identity needs to be checked")
        if "command_pending" in request and command_still_running(request["command_pending"], budget):
            raise UpdateError("An update command has not stopped. Try again after it ends.")
        if request.get("rollback"):
            if not isinstance(request.get("recovery"), dict):
                raise UpdateError("the failed update recovery copy needs to be checked")
            recovery_backup(root, pinned=request["recovery"])
        fingerprint = _retry_identity(request, status)
        for _ in range(2 if admission else 1):
            answer = query(["systemctl", "show", "--all", "--property=ActiveState", "--property=MainPID",
                            "--property=ControlPID", "--property=TasksCurrent", "--property=Job",
                            "jittersim-update.service"], budget.limit(2))
            budget.check()
            if not isinstance(answer, str) or len(answer.encode("utf-8")) > MAX_COMMAND_ANSWER:
                raise UpdateError("The update service has not been confirmed stopped.")
            rows = answer.splitlines()
            if len(rows) != 5 or any("=" not in row for row in rows):
                raise UpdateError("The update service has not been confirmed stopped.")
            state = dict(row.split("=", 1) for row in rows)
            if (set(state) != {"ActiveState", "MainPID", "ControlPID", "TasksCurrent", "Job"}
                    or state["ActiveState"] not in ("inactive", "failed") or state["Job"] != ""
                    or any(state[name] != "0" for name in ("MainPID", "ControlPID", "TasksCurrent"))):
                raise UpdateError("The update service has not been confirmed stopped.")
            if fingerprint != _retry_identity(_recovery_record(root / "request.json"),
                                              _recovery_record(root / "status.json")):
                return None, "The update changed. Check its state before retrying."
        budget.check()
        return {"id": fingerprint, "request_id": fields["request_id"],
                "kind": fields["request_kind"], "version": fields["request_version"],
                "recovering": bool(request.get("rollback") or request.get("restart_only"))}, None
    except CommandNotStopped:
        raise
    except (OSError, ValueError, UpdateError):
        return None, "The failed update is still busy or could not be checked. Try again shortly."


def retry_update(retry_id, *, root=UPDATE_ROOT, code=CODE_ROOT, config=CONFIG_ROOT,
                 run=run_command, query=read_command):
    """Start exactly the failed retained operation after confirming physical stop."""
    if not isinstance(retry_id, str) or not RETRY_ID.fullmatch(retry_id):
        raise UpdateError("check the failed update again before retrying")
    with update_lock(root):
        status = read_json(root / "status.json")
        retry, reason = _retry_info(root, status, query, admission=True)
        if retry is None:
            raise UpdateError(reason or "there is no failed update to retry")
        if not secrets.compare_digest(retry["id"], retry_id):
            raise UpdateError("the failed update changed; check it again before retrying")
        request = _recovery_record(root / "request.json")
        current_id = _retry_identity(request, _recovery_record(root / "status.json"))
        if not secrets.compare_digest(current_id, retry_id):
            raise UpdateError("the failed update changed; check it again before retrying")
        fields = _identify_request(request)
        atomic_json(root / "status.json", {"phase": "queued", "message": "Retry queued. The dashboard will reconnect.", **fields})
        try:
            run(["systemctl", "start", "--no-block", "jittersim-update.service"], 10)
        except Exception as error:
            if isinstance(error, CommandNotStopped):
                request["command_pending"] = pending_command(error.process)
                atomic_json(root / "request.json", request)
            atomic_json(root / "status.json", {"phase": "failed", "message": "The retry could not be confirmed. Check the update state.", **fields})
            raise
        return update_status(root=root, code=code, config=config, query=query, retry_probe=False)


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
        request = {"version": release_version, "rollback": rollback,
                   "request_id": secrets.token_hex(16),
                   "request_kind": "rollback" if rollback else "install"}
        fields = _identify_request(request)
        if rollback:
            request["recovery"] = recovery_identity(root, recovery_backup(root))
        atomic_json(root / "request.json", request)
        atomic_json(
            root / "status.json",
            {"phase": "queued", "message": "Update queued. The dashboard will reconnect.", **fields},
        )
        try:
            run(["systemctl", "start", "--no-block", "jittersim-update.service"], 10)
        except Exception as error:
            if isinstance(error, CommandNotStopped):
                request["command_pending"] = pending_command(error.process)
                atomic_json(root / "request.json", request)
            atomic_json(
                root / "status.json",
                {"phase": "failed", "message": "The update service could not start.", **fields},
            )
            raise
        return update_status(root=root, code=code, config=config, retry_probe=False)


# The online release source. Only root runs it (see docs/implementation-plan-online-release-
# source-2026-10-03.md). The dashboard never sees the token or a download.

GITHUB_REPO = "smartinezai/jittersim"
GITHUB_API_HOST = "api.github.com"
GITHUB_ASSET_HOSTS = ("objects.githubusercontent.com", "release-assets.githubusercontent.com")
RELEASE_ASSETS = ("jittersim-release.tar.gz", "jittersim-release.tar.gz.sig")
MAX_API_ANSWER = 64 * 1024
MAX_REDIRECTS = 2
HTTP_TIMEOUT = 30
TOTAL_TIMEOUT = 5 * 60
MAX_TOKEN_INPUT = 1024
# A fine-grained token (github_pat_) or a classic one (ghp_).
TOKEN_SHAPE = re.compile(r"(?:github_pat_[A-Za-z0-9_]{22,255}|ghp_[A-Za-z0-9]{30,255})\Z")
ONLINE_FILE = "online.json"
ONLINE_REQUEST = "online-request.json"
ONLINE_SETTINGS = "online-settings.json"
ONLINE_SEEN = "online-seen.json"
# When the schedule's own check last ended. A check someone asked for does not change it.
ONLINE_DAILY = "online-daily.json"
ONLINE_LOCK = "online.lock"
# The dashboard asks GitHub at most this often unless the owner presses Check now.
CACHE_SECONDS = 10 * 60
# A check that has run this long without an answer is treated as failed.
STALE_SECONDS = TOTAL_TIMEOUT + 2 * 60
DAILY_SECONDS = 24 * 60 * 60
JOB = re.compile(r"[0-9a-f]{16}\Z")
SETTING_NAMES = ("daily_check", "install_automatically")
REDIRECTS = (301, 302, 303, 307, 308)


class OnlineError(UpdateError):
    """An online check stopped. The message is one plain line and never holds the token."""


class TokenError(OnlineError):
    """The token is missing or its file is not safe to use."""


class OnlineBusy(OnlineError):
    """Another online check is running."""


class GitHubSource:
    """Where releases come from. The defaults are the real GitHub. Tests give a fake one."""

    def __init__(self, api_host=GITHUB_API_HOST, port=443, repo=GITHUB_REPO,
                 asset_hosts=GITHUB_ASSET_HOSTS, context=None, timeout=HTTP_TIMEOUT,
                 total=TOTAL_TIMEOUT):
        self.api_host = api_host
        self.port = port
        self.repo = repo
        self.asset_hosts = tuple(asset_hosts)
        # The system CA store and the host name check.
        self.context = context if context is not None else ssl.create_default_context()
        self.timeout = timeout
        self.total = total

    def url(self, path):
        authority = self.api_host if self.port == 443 else f"{self.api_host}:{self.port}"
        return f"https://{authority}/repos/{self.repo}/{path}"


def default_source():
    return GitHubSource()


# The token


def token_is_set(config=CONFIG_ROOT):
    try:
        return stat.S_ISREG(os.lstat(config / TOKEN_NAME).st_mode)
    except OSError:
        return False


def set_token(text, *, config=CONFIG_ROOT):
    """Save a GitHub token for the online check: root only, replaced in one step."""
    token = text.strip() if len(text) <= MAX_TOKEN_INPUT else ""
    if not TOKEN_SHAPE.fullmatch(token):
        raise UpdateError(
            "that is not a GitHub token. A fine-grained token starts with github_pat_ and a "
            "classic one with ghp_. Paste only the token"
        )
    if not config.is_dir():
        raise UpdateError(f"{config} does not exist. Install JitterSim first")
    _replace_file(config, ".update-token.", config / TOKEN_NAME, token + "\n", 0o600)


def clear_token(*, config=CONFIG_ROOT):
    try:
        (config / TOKEN_NAME).unlink()
    except FileNotFoundError:
        raise UpdateError("no token is set") from None


def read_token(config=CONFIG_ROOT):
    """The saved token. Its file must be root's own, not a link and closed to others."""
    path = config / TOKEN_NAME
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        raise TokenError(
            "no GitHub token is set. Create a read-only token for the repository and save it "
            "with 'sudo jittersim update set-token'"
        ) from None
    except OSError:
        raise TokenError("the GitHub token file cannot be read") from None
    if not stat.S_ISREG(info.st_mode):
        raise TokenError(f"{path} must be a regular file, not a link")
    if info.st_uid != os.geteuid() or info.st_mode & 0o077:
        raise TokenError(f"{path} must belong to root and be readable by root only (mode 0600)")
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(descriptor, "rb") as handle:
            raw = handle.read(MAX_TOKEN_INPUT + 1)
        token = raw.decode("ascii").strip()
    except (OSError, UnicodeDecodeError):
        raise TokenError("the GitHub token file cannot be read") from None
    if len(raw) > MAX_TOKEN_INPUT or not TOKEN_SHAPE.fullmatch(token):
        raise TokenError("the saved GitHub token is damaged. Save it again with set-token")
    return token


# Talking to GitHub


def _left(deadline):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise OnlineError("the check took too long and was stopped")
    return remaining


def _web_failure(error, deadline):
    """A plain message for a failed connection. It never repeats what the error holds."""
    if isinstance(error, OnlineError):
        return error
    if time.monotonic() >= deadline:
        # The watchdog cut the connection or the time ran out in a read.
        return OnlineError("the check took too long and was stopped")
    if isinstance(error, ssl.SSLCertVerificationError):
        return OnlineError(
            "the certificate of GitHub could not be verified. Check the Pi's date and time"
        )
    if isinstance(error, ssl.SSLError):
        return OnlineError("the secure connection to GitHub failed")
    if isinstance(error, (socket.timeout, TimeoutError)):
        if time.monotonic() >= deadline:
            return OnlineError("the check took too long and was stopped")
        return OnlineError("GitHub did not answer in time")
    if isinstance(error, http.client.HTTPException):
        return OnlineError("GitHub sent an answer that was not valid")
    return OnlineError("Could not reach GitHub. Check the Pi's Ethernet connection")


def _request(source, url, headers, limit, deadline, destination=None):
    """One GET without following redirects. Returns (status, headers, body). With a
    destination the body goes into that new private file and body is None."""
    parts = urlsplit(url)
    path = parts.path + ("?" + parts.query if parts.query else "")
    connection = http.client.HTTPSConnection(
        parts.hostname, parts.port or 443, timeout=min(source.timeout, _left(deadline)),
        context=source.context,
    )

    def cut():
        # A server that sends one byte at a time never trips the per-read timeout and a read of
        # a whole block waits for the block. At the deadline the connection is shut down so
        # that every read in progress ends. Only the socket is shut down, never the TLS layer.
        try:
            socket.socket.shutdown(connection.sock, socket.SHUT_RDWR)
        except (OSError, TypeError, AttributeError):
            pass

    watchdog = threading.Timer(_left(deadline), cut)
    watchdog.daemon = True
    watchdog.start()
    try:
        connection.request("GET", path, headers=headers)
        response = connection.getresponse()
        answer = {key.lower(): value for key, value in response.getheaders()}
        if response.status in REDIRECTS or response.status >= 400:
            return response.status, answer, b""
        length = answer.get("content-length", "")
        if length.isdigit() and int(length) > limit:
            raise OnlineError("the file is too large")
        total = 0
        chunks = []
        output = None
        if destination is not None:
            output = os.fdopen(os.open(
                destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
                0o600), "wb")
        try:
            while True:
                if connection.sock is not None:
                    connection.sock.settimeout(min(source.timeout, _left(deadline)))
                block = response.read(65536)
                if not block:
                    break
                total += len(block)
                if total > limit:
                    raise OnlineError("the file is too large")
                if output is not None:
                    output.write(block)
                else:
                    chunks.append(block)
            if output is not None:
                output.flush()
                os.fsync(output.fileno())
        finally:
            if output is not None:
                output.close()
        _left(deadline)
        return response.status, answer, None if destination is not None else b"".join(chunks)
    except (OSError, http.client.HTTPException) as error:
        raise _web_failure(error, deadline) from None
    finally:
        watchdog.cancel()
        connection.close()


def _http_message(status, answer, what):
    if status == 429 or (status == 403 and answer.get("x-ratelimit-remaining") == "0"):
        return "GitHub is limiting requests right now. Try again later"
    if what == "file":
        return f"the release file could not be downloaded (HTTP {status})"
    if status in (401, 403):
        return (
            "GitHub refused the token. It may have expired or be wrong. Create a new one and "
            "save it with 'sudo jittersim update set-token'"
        )
    if status == 404:
        return (
            "GitHub has no release here or this token cannot see the repository. Check that "
            f"the token is for {GITHUB_REPO} with Contents read access"
        )
    return f"GitHub answered with an error (HTTP {status})"


def _redirect_target(source, current, location):
    """The URL a redirect may go to or an error. Only https, no login part, no other port and
    only a host on the allow list."""
    refusal = OnlineError(
        "GitHub redirected the download to a place JitterSim does not trust, so it was stopped"
    )
    if not location:
        raise refusal
    try:
        target = urlsplit(urljoin(current, location))
        port = target.port
        host = target.hostname
    except ValueError:
        raise refusal from None
    if (
        target.scheme != "https"
        or target.username is not None
        or target.password is not None
        or host not in source.asset_hosts
        or port not in (None, source.port)
        or (port is None and source.port != 443)
    ):
        raise refusal
    return target.geturl()


def _download(source, url, token, accept, limit, deadline, destination=None, what="file"):
    """GET a URL with the token and follow at most two redirects. The token goes only to the
    first URL (the API) and is dropped on every redirect."""
    headers = {
        "Authorization": "Bearer " + token,
        "Accept": accept,
        "User-Agent": "jittersim-update",
        "X-GitHub-Api-Version": "2022-11-28",
        "Connection": "close",
    }
    redirects = 0
    while True:
        status, answer, body = _request(source, url, headers, limit, deadline, destination)
        if status in REDIRECTS:
            if redirects >= MAX_REDIRECTS:
                raise OnlineError("GitHub sent too many redirects")
            url = _redirect_target(source, url, answer.get("location", ""))
            headers.pop("Authorization", None)
            redirects += 1
            continue
        if status != 200:
            raise OnlineError(_http_message(status, answer, what))
        return body


def _is_id(value):
    return type(value) is int and 0 < value < MAX_SEQUENCE


def _parse_release(body):
    """The tag, the ids and the sizes of the two assets of GitHub's latest release."""
    broken = OnlineError("GitHub's answer is not a release JitterSim understands")
    try:
        data = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise broken from None
    if (
        not isinstance(data, dict)
        or not isinstance(data.get("tag_name"), str)
        or not LABEL.fullmatch(data["tag_name"])
        or not _is_id(data.get("id"))
        or not isinstance(data.get("assets"), list)
        or not all(isinstance(asset, dict) for asset in data["assets"])
    ):
        raise broken
    if data.get("draft") is not False or data.get("prerelease") is not False:
        raise OnlineError("the newest GitHub release is not a final release")
    assets = data["assets"]
    names = sorted(str(asset.get("name")) for asset in assets)
    if names != sorted(RELEASE_ASSETS):
        raise OnlineError(
            "the newest GitHub release must hold exactly two files named "
            + " and ".join(RELEASE_ASSETS)
        )
    found = {}
    for asset in assets:
        size = asset.get("size")
        if not _is_id(asset.get("id")) or type(size) is not int or size < 0:
            raise broken
        found[asset["name"]] = (asset["id"], size)
    for name, limit, label in ((RELEASE_ASSETS[0], MAX_ARCHIVE, "archive"),
                               (RELEASE_ASSETS[1], MAX_SIGNATURE, "signature")):
        if found[name][1] > limit:
            raise OnlineError(f"the release {label} is too large")
    return {
        "tag": data["tag_name"], "id": data["id"],
        "archive": found[RELEASE_ASSETS[0]][0], "signature": found[RELEASE_ASSETS[1]][0],
        "fingerprint": f"{data['id']}:{found[RELEASE_ASSETS[0]][0]}:"
                       f"{found[RELEASE_ASSETS[1]][0]}:{data['tag_name']}",
    }


@contextmanager
def online_lock(root=UPDATE_ROOT):
    _private_folder(root)
    with (root / ONLINE_LOCK).open("a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise OnlineBusy("an online check is already running") from None
        yield


def _sentence(error):
    text = str(error)
    return text[:1].upper() + text[1:]


def _outcome(result, message, **more):
    return {"result": result, "message": message, "tag": None, "available": None,
            "contacted": False, **more}


def _check_online(root, code, config, source):
    # Only one check runs at a time (online_lock), so every online- folder is left over from
    # a check that systemd ended.
    for left in root.glob("online-*"):
        if left.is_dir() and not left.is_symlink():
            shutil.rmtree(left, ignore_errors=True)
    token = read_token(config)
    deadline = time.monotonic() + source.total
    body = _download(source, source.url("releases/latest"), token, "application/vnd.github+json",
                     MAX_API_ANSWER, deadline, what="answer")
    release = _parse_release(body)
    tag = release["tag"]
    if not re.fullmatch(r"v(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)", tag):
        raise OnlineError("the newest GitHub release does not have a stable version tag")
    installed = _record(code / ".jittersim-release.json")
    staged_record = _record(root / "staged" / ".jittersim-release.json")
    seen = _record(root / ONLINE_SEEN)
    base = {"tag": tag, "contacted": True}
    uptodate = _outcome(
        "up-to-date",
        f"Already up to date: the newest release on GitHub ({tag}) is not newer than the "
        "installed one.", **base,
    )
    if installed.get("source") == "github" and installed.get("tag") == tag:
        return uptodate
    if staged_record.get("source") == "github" and staged_record.get("tag") == tag:
        return _outcome(
            "staged", f"Release {staged_record.get('version')} ({tag}) is already staged and "
            "ready to install.", **{**base, "available": staged_record.get("version")},
        )
    if seen.get("fingerprint") == release["fingerprint"] and seen.get("outcome") == "not-newer":
        return uptodate
    work = Path(tempfile.mkdtemp(prefix="online-", dir=root))
    try:
        archive, signature = work / RELEASE_ASSETS[0], work / RELEASE_ASSETS[1]
        for number, target, limit, what in ((release["archive"], archive, MAX_ARCHIVE, "archive"),
                                            (release["signature"], signature, MAX_SIGNATURE,
                                             "signature")):
            _download(source, source.url(f"releases/assets/{number}"), token,
                      "application/octet-stream", limit, deadline, target, what="file")
        try:
            record = stage_release(archive, signature=signature, root=root, code=code,
                                   config=config, origin="github", tag=tag, version=tag[1:])
        except NotNewerError:
            atomic_json(root / ONLINE_SEEN, {"fingerprint": release["fingerprint"],
                                             "outcome": "not-newer"})
            return uptodate
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return _outcome(
        "staged",
        f"Release {record['version']} ({tag}) is staged and ready to install. Install it with "
        "'sudo jittersim update install' or in the dashboard.",
        **{**base, "available": record["version"]},
    )


def check_online(*, root=UPDATE_ROOT, code=CODE_ROOT, config=CONFIG_ROOT, source=None):
    """Look for the newest release on GitHub and stage it when a trusted key signed it and it
    is newer. Never raises: the answer says what happened in one plain line."""
    source = source if source is not None else default_source()
    try:
        with online_lock(root):
            return _check_online(root, code, config, source)
    except OnlineBusy as error:
        return {**_outcome("failed", _sentence(error)), "busy": True}
    except UpdateError as error:
        # stage_release holds no token. Its messages are plain lines.
        return _outcome("failed", _sentence(error), contacted=not isinstance(error, TokenError))
    except Exception as error:
        # Say what kind of failure it was but never what it holds.
        logging.getLogger(__name__).error("online check failed: %s", type(error).__name__)
        return _outcome("failed", "The check stopped on an unexpected error. See the update "
                        "service log")


# Jobs: how the dashboard asks for a check without ever handling the token


def online_settings(root=UPDATE_ROOT):
    """The two online settings. Both are off until the owner turns them on."""
    saved = _record(root / ONLINE_SETTINGS)
    return {name: saved.get(name) is True for name in SETTING_NAMES}


def set_online_settings(changes, *, root=UPDATE_ROOT):
    if (not isinstance(changes, dict) or not changes or set(changes) - set(SETTING_NAMES)
            or not all(isinstance(value, bool) for value in changes.values())):
        raise UpdateError("online settings are daily_check and install_automatically (true or false)")
    with update_lock(root):
        values = {**online_settings(root), **changes}
        atomic_json(root / ONLINE_SETTINGS, values)
    return values


def _view(record, now, cached=False):
    """The record without anything the dashboard has no use for. A check that never ended is
    shown as failed."""
    keys = ("job", "state", "requested_at", "checked_at", "result", "message", "available", "tag",
            "install_queued")
    view = {key: record.get(key) for key in keys}
    view["install_queued"] = record.get("install_queued") is True
    if record.get("state") == "running" and now - record.get("requested_at", 0) > STALE_SECONDS:
        view.update(state="done", result="failed",
                    message="The check did not finish. Try again.")
    view["cached"] = cached
    return view


def online_overview(job=None, *, root=UPDATE_ROOT, config=CONFIG_ROOT, now=time.time):
    """The state of the online check for the dashboard. With a job only that job."""
    record = _record(root / ONLINE_FILE)
    if not record:
        view = {"job": None, "state": "none", "message": "No online check has run yet."}
    elif job is not None and record.get("job") != job:
        view = {"job": job, "state": "unknown",
                "message": "This check is no longer tracked. Check again."}
    else:
        view = _view(record, now())
    view["settings"] = online_settings(root)
    view["token_set"] = token_is_set(config)
    return view


def new_job():
    return secrets.token_hex(8)


def request_online_check(*, force=False, root=UPDATE_ROOT, config=CONFIG_ROOT, run=run_command,
                         now=time.time, job_id=new_job, check_only=True):
    """Start one online check in the root fetch unit and return its job at once. Unless FORCE
    is set a check that ended less than ten minutes ago answers instead of a new one."""
    current = now()
    with update_lock(root):
        record = _record(root / ONLINE_FILE)
        if record.get("state") == "running" and current - record.get("requested_at", 0) <= STALE_SECONDS:
            return _view(record, current, cached=True)
        if (not force and record.get("state") == "done" and record.get("contacted") is True
                and isinstance(record.get("checked_at"), (int, float))
                and 0 <= current - record["checked_at"] < CACHE_SECONDS):
            return _view(record, current, cached=True)
        job = job_id()
        if not token_is_set(config):
            record = {"job": job, "state": "done", "requested_at": int(current),
                      "checked_at": int(current), "result": "failed", "contacted": False,
                      "message": "No GitHub token is set. Save one with "
                                 "'sudo jittersim update set-token' on the Pi."}
            atomic_json(root / ONLINE_FILE, record)
            return _view(record, current)
        record = {"job": job, "state": "running", "requested_at": int(current),
                  "checked_at": None, "result": None, "message": "Checking GitHub for a release."}
        # A check someone asked for only checks. Only the daily check may install by itself
        # (when Install automatically is on), so a manual check never interrupts the Pi.
        atomic_json(root / ONLINE_REQUEST, {"job": job, "requested_at": int(current),
                                            "check_only": check_only is not False})
        atomic_json(root / ONLINE_FILE, record)
        try:
            run(["systemctl", "start", "--no-block", FETCH_UNIT], 10)
        except Exception:
            (root / ONLINE_REQUEST).unlink(missing_ok=True)
            record.update(state="done", result="failed", checked_at=int(current), contacted=False,
                          message="The online check service could not start.")
            if check_only is False:
                atomic_json(root / ONLINE_DAILY, {"checked_at": int(current)})
            atomic_json(root / ONLINE_FILE, record)
        return _view(record, current)


def scheduled_online_check(*, root=UPDATE_ROOT, config=CONFIG_ROOT, run=run_command,
                           now=time.time):
    """Start the daily check when it is on, a token is saved and a day passed since the last
    one. Returns whether a check was started."""
    if not online_settings(root)["daily_check"] or not token_is_set(config):
        return False
    record = _record(root / ONLINE_FILE)
    current = now()
    if record.get("state") == "running" and current - record.get("requested_at", 0) <= STALE_SECONDS:
        return False
    try:
        last = _record(root / ONLINE_DAILY).get("checked_at")
    except UpdateError:
        last = None  # An unreadable marker must not stop the daily check for good.
    if isinstance(last, (int, float)) and 0 <= current - last < DAILY_SECONDS:
        return False
    started = request_online_check(force=True, root=root, config=config, run=run, now=now,
                                   check_only=False)
    return started.get("state") == "running"


def perform_online_check(job, *, root=UPDATE_ROOT, code=CODE_ROOT, config=CONFIG_ROOT, source=None,
                         run=run_command, now=time.time, auto_install=True, daily=False):
    """Run one check, record the answer under JOB and, when the owner turned Install
    automatically on, queue the install of a release that is staged and installable. DAILY
    says the schedule asked for it: only then does the daily timer start again from its end."""
    result = check_online(root=root, code=code, config=config, source=source)
    if result.get("busy"):
        return result
    queued = False
    message = result["message"]
    if result["result"] == "staged" and auto_install and online_settings(root)["install_automatically"]:
        status = update_status(root=root, code=code, config=config)
        seen = _record(root / ONLINE_SEEN)
        if status["installable"] and status["available"] != status["installed"]:
            tried = f"{status['available']}:{status.get('tag')}"
            if seen.get("auto") == tried:
                message += " It is not installed automatically because that failed before."
            else:
                try:
                    atomic_json(root / ONLINE_SEEN, {**seen, "auto": tried})
                    request_update(status["available"], root=root, code=code, config=config,
                                   run=run)
                    queued = True
                    message = (f"Release {status['available']} is staged and its install was "
                               "started (Install automatically is on).")
                except UpdateError as error:
                    message += " It was not installed automatically: " + str(error)
    finished = int(now())
    if daily:
        # The daily timer follows the schedule's own checks and nothing else, so checking by
        # hand every day cannot keep the one check that may install waiting. A schedule check
        # that never ended leaves it alone, so the next tick after STALE_SECONDS tries again.
        atomic_json(root / ONLINE_DAILY, {"checked_at": finished})
    record = {"job": job, "state": "done", "requested_at": finished, "checked_at": finished,
              "result": result["result"], "message": message, "available": result["available"],
              "tag": result["tag"], "contacted": result["contacted"], "install_queued": queued}
    previous = _record(root / ONLINE_FILE)
    if previous.get("job") == job:
        record["requested_at"] = previous.get("requested_at", finished)
    atomic_json(root / ONLINE_FILE, record)
    return record


def run_fetch_job(*, root=UPDATE_ROOT, code=CODE_ROOT, config=CONFIG_ROOT, source=None,
                  run=run_command, now=time.time):
    """The work of jittersim-update-fetch.service: take the request the helper wrote, check
    and record the result. Without a request it does nothing."""
    request = _record(root / ONLINE_REQUEST)
    job = request.get("job")
    if not isinstance(job, str) or not JOB.fullmatch(job):
        return None
    (root / ONLINE_REQUEST).unlink(missing_ok=True)
    scheduled = request.get("check_only") is False
    return perform_online_check(job, root=root, code=code, config=config, source=source, run=run,
                                now=now, auto_install=scheduled, daily=scheduled)


# A copied recovery manager must understand these records before they are published.
RECOVERY_FORMAT = 2
OPERATION_FORMAT = 2
ATTEMPT = re.compile(r"[0-9a-f]{32}\Z")
GENERATION = re.compile(r"backup-[0-9a-f]{32}\Z")
PREPARATION = re.compile(r"prepare-[0-9a-f]{32}\Z")
MAX_PREPARATION_BYTES = 512 * 1024 * 1024
MAX_PREPARATION_ENTRIES = 20000
MAX_PREPARED_FILE = 256 * 1024 * 1024
MAX_WHEELS = 64
MAX_SNAPSHOT_BYTES = 4 * 1024 * 1024 * 1024
MAX_SNAPSHOT_ENTRIES = 100000
MAX_TREE_DEPTH = 32
MAX_GENERATIONS = 16
SPACE_MARGIN = 100 * 1024 * 1024
WHEEL_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.+\-]{0,199}\.whl\Z")
COMMAND_CONTEXT = threading.local()


class UpdateBudget:
    """Share a finite allowance between preparation, installation and recovery."""

    def __init__(self):
        self.started = time.monotonic()
        self.deadline = self.started + 600
        self.recovery_deadline = self.started + 840

    def check(self):
        if time.monotonic() >= self.deadline:
            raise UpdateError("the update has no time remaining")

    def limit(self, seconds):
        self.check()
        return min(seconds, self.deadline - time.monotonic())

    def recover(self):
        self.deadline = self.recovery_deadline
        self.check()


def tree_entries(source, budget, *, links=False, ignore=(), max_bytes=None, max_entries=None, live=False):
    """Enumerate without following links and refuse unsafe or excessive contents."""
    maximum_bytes = MAX_SNAPSHOT_BYTES if max_bytes is None else max_bytes
    maximum_entries = MAX_SNAPSHOT_ENTRIES if max_entries is None else max_entries
    stack = [(Path(source), Path("."), 0)]
    count = total = 0
    discovered = 1
    while stack:
        budget.check()
        path, relative, depth = stack.pop()
        try:
            info = path.lstat()
        except FileNotFoundError:
            if live and relative != Path("."):
                continue
            raise
        count += 1
        if count > maximum_entries or depth > MAX_TREE_DEPTH:
            raise UpdateError("the recovery snapshot has too many entries or nested folders")
        if stat.S_ISLNK(info.st_mode):
            if not links or info.st_uid != os.geteuid():
                raise UpdateError("the recovery snapshot contains an unsafe link")
        elif stat.S_ISREG(info.st_mode):
            total += info.st_size
            if total > maximum_bytes:
                raise UpdateError("the recovery snapshot is too large")
        elif not stat.S_ISDIR(info.st_mode):
            raise UpdateError("the recovery snapshot contains a special file")
        yield path, relative, info
        if stat.S_ISDIR(info.st_mode):
            try:
                with os.scandir(path) as entries:
                    children = []
                    for entry in entries:
                        budget.check()
                        if relative == Path(".") and entry.name in ignore:
                            continue
                        if discovered >= maximum_entries:
                            raise UpdateError("the recovery snapshot has too many entries")
                        discovered += 1
                        children.append((Path(entry.path), relative / entry.name, depth + 1))
                stack.extend(sorted(children, reverse=True))
            except FileNotFoundError:
                if not live or relative == Path("."):
                    raise


def snapshot_size(source, budget, *, links=False, ignore=()):
    return sum(info.st_size for _, _, info in tree_entries(source, budget, links=links, ignore=ignore)
               if stat.S_ISREG(info.st_mode))


def snapshot_requirements(inputs, budget):
    total = count = 0
    for name, source in inputs:
        if not source.exists() and not source.is_symlink():
            continue
        ignore = PRESERVED_CONFIG if name == "config" else ("incoming-release",) if name == "data" else ()
        for _, _, info in tree_entries(source, budget, links=name in ("code", "command"), ignore=ignore):
            count += 1
            if stat.S_ISREG(info.st_mode):
                total += info.st_size
            if total > MAX_SNAPSHOT_BYTES or count > MAX_SNAPSHOT_ENTRIES:
                raise UpdateError("the complete recovery snapshot exceeds its size or entry allowance")
    return total


def _copy_snapshot_file(source, destination, info, budget):
    descriptor = os.open(source, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode) or (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino):
            raise UpdateError("a recovery input changed while it was being copied")
        remaining = info.st_size
        with os.fdopen(descriptor, "rb", closefd=False) as input_file, destination.open("xb") as output:
            while remaining:
                budget.check()
                block = input_file.read(min(65536, remaining))
                if not block:
                    raise UpdateError("a recovery input changed while it was being copied")
                output.write(block)
                remaining -= len(block)
            if input_file.read(1):
                raise UpdateError("a recovery input grew while it was being copied")
            output.flush()
            os.fsync(output.fileno())
            budget.check()
        destination.chmod(stat.S_IMODE(info.st_mode))
        os.utime(destination, ns=(info.st_atime_ns, info.st_mtime_ns), follow_symlinks=False)
        with destination.open("rb") as copied:
            os.fsync(copied.fileno())
        budget.check()
        current = os.fstat(descriptor)
        if (current.st_size, current.st_mtime_ns) != (info.st_size, info.st_mtime_ns):
            raise UpdateError("a recovery input changed while it was being copied")
    finally:
        os.close(descriptor)


def snapshot_tree(source, destination, budget, *, links=False, ignore=()):
    # Inspect the entire input before creating a partial destination.
    snapshot_size(source, budget, links=links, ignore=ignore)
    directories = []
    for path, relative, info in tree_entries(source, budget, links=links, ignore=ignore):
        target = destination if relative == Path(".") else destination / relative
        if stat.S_ISDIR(info.st_mode):
            target.mkdir(mode=0o700)
            directories.append((target, info))
        elif stat.S_ISLNK(info.st_mode):
            os.symlink(os.readlink(path), target)
        else:
            _copy_snapshot_file(path, target, info, budget)
    for path, info in reversed(directories):
        budget.check()
        path.chmod(stat.S_IMODE(info.st_mode))
        _fsync_folder(path)
        budget.check()


def tree_digest(source, budget, *, links=False, ignore=()):
    digest = hashlib.sha256()
    for path, relative, info in tree_entries(source, budget, links=links, ignore=ignore):
        digest.update(str(relative).encode("utf-8") + b"\0")
        digest.update(str(stat.S_IMODE(info.st_mode)).encode("ascii") + b"\0")
        if stat.S_ISLNK(info.st_mode):
            digest.update(b"link\0" + os.readlink(path).encode("utf-8") + b"\0")
        elif stat.S_ISREG(info.st_mode):
            descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
            try:
                opened = os.fstat(descriptor)
                if (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino):
                    raise UpdateError("a recovery input changed while it was being checked")
                remaining = info.st_size
                with os.fdopen(descriptor, "rb", closefd=False) as handle:
                    while remaining:
                        budget.check()
                        block = handle.read(min(65536, remaining))
                        if not block:
                            raise UpdateError("a recovery input changed while it was being checked")
                        digest.update(block)
                        remaining -= len(block)
                    if handle.read(1):
                        raise UpdateError("a recovery input grew while it was being checked")
                digest.update(b"\0")
            finally:
                os.close(descriptor)
        else:
            digest.update(b"directory\0")
    return digest.hexdigest()


def remove_tree(path, budget):
    # Bound traversal before cleanup and never follow a link into another tree.
    entries = list(tree_entries(path, budget, links=True))
    for entry, _, info in reversed(entries):
        budget.check()
        if stat.S_ISDIR(info.st_mode):
            entry.rmdir()
        else:
            entry.unlink()
    budget.check()


def _recovery_record(path):
    try:
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
            raise UpdateError("the recovery record must be private")
        value = read_json(path)
        if not isinstance(value, dict):
            raise UpdateError("the recovery record is invalid")
        return value
    except (OSError, ValueError) as error:
        raise UpdateError("the recovery record could not be read") from error


def recovery_backup(root, *, pinned=None):
    """Choose only the validated published copy, never an unrelated fallback."""
    selector = root / "backup-current.json"
    if pinned is None and not selector.exists() and not selector.is_symlink():
        return root / "backup"
    record = pinned if pinned is not None else _recovery_record(selector)
    name, attempt = record.get("name"), record.get("attempt")
    if name == "backup" and pinned is not None and attempt is None:
        backup = root / name
    elif (record.get("format") == RECOVERY_FORMAT and isinstance(name, str)
          and GENERATION.fullmatch(name) and isinstance(attempt, str) and ATTEMPT.fullmatch(attempt)
          and name == "backup-" + attempt):
        backup = root / name
    else:
        raise UpdateError("the recovery selector is invalid")
    try:
        info = backup.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or (info.st_mode & 0o077 and backup.name != "backup"):
            raise UpdateError("the recovery folder must be private")
        completed = _recovery_record(backup / "complete.json")
        if attempt is not None and (completed.get("attempt") != attempt
                                    or completed.get("format") != RECOVERY_FORMAT):
            raise UpdateError("the recovery copy belongs to another update")
        if record.get("completion") and hashlib.sha256((backup / "complete.json").read_bytes()).hexdigest() != record["completion"]:
            raise UpdateError("the pinned recovery copy changed")
        return backup
    except OSError as error:
        raise UpdateError("the selected recovery copy is missing") from error


def recovery_identity(root, backup):
    completed = _recovery_record(backup / "complete.json")
    return {"format": RECOVERY_FORMAT, "name": backup.name,
            "attempt": completed.get("attempt") if backup.name != "backup" else None,
            "completion": hashlib.sha256((backup / "complete.json").read_bytes()).hexdigest()}


def cleanup_generations(root, budget, *, candidate=None):
    selected = recovery_backup(root)
    generations = []
    preparations = []
    scanned = 0
    with os.scandir(root) as entries:
        for entry in entries:
            budget.check()
            scanned += 1
            if scanned > 64:
                raise UpdateError("too many update records need inspection")
            if entry.name.startswith("backup-") and entry.name != "backup-current.json":
                if not GENERATION.fullmatch(entry.name):
                    raise UpdateError("an unknown recovery folder needs inspection")
                generations.append(Path(entry.path))
            elif entry.name.startswith("prepare-"):
                if not PREPARATION.fullmatch(entry.name):
                    raise UpdateError("an unknown preparation folder needs inspection")
                preparations.append(Path(entry.path))
            if len(generations) + len(preparations) > MAX_GENERATIONS:
                raise UpdateError("too many update folders need inspection")
    legacy = root / "backup"
    if legacy.exists() or legacy.is_symlink():
        info = legacy.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o022:
            raise UpdateError("the legacy recovery folder is unsafe")
        generations.append(legacy)
    completed = []
    for path in generations:
        info = path.lstat()
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid()
                or info.st_mode & (0o022 if path.name == "backup" else 0o077)):
            raise UpdateError("a recovery folder is unsafe")
        if (path / "complete.json").exists():
            created = _recovery_record(path / "complete.json").get("created_at", 0)
            if type(created) is not int or created < 0:
                raise UpdateError("the recovery completion time is invalid")
            completed.append((created, path))
        elif path != candidate:
            remove_tree(path, budget)
    previous = next((path for _, path in sorted(completed, reverse=True) if path != selected), None)
    for _, path in completed:
        if path not in (selected, previous, candidate):
            remove_tree(path, budget)
    for path in preparations:
        remove_tree(path, budget)


def _check_preparation(path, budget):
    for _, _, info in tree_entries(path, budget, links=True, max_bytes=MAX_PREPARATION_BYTES,
                                  max_entries=MAX_PREPARATION_ENTRIES, live=True):
        if stat.S_ISREG(info.st_mode) and info.st_size > MAX_PREPARED_FILE:
            raise UpdateError("a prepared input is too large")


def prepared_wheels(path, record, budget):
    manifest = _recovery_record(path / "wheels.json")
    if manifest.get("commit") != record.get("commit") or manifest.get("version") != record.get("version"):
        raise UpdateError("prepared packages belong to another release")
    rows = manifest.get("wheels")
    if not isinstance(rows, list) or not 1 <= len(rows) <= MAX_WHEELS:
        raise UpdateError("the prepared package list is invalid")
    wheels = []
    names = set()
    for row in rows:
        budget.check()
        if (not isinstance(row, dict) or set(row) != {"name", "size", "sha256"}
                or not isinstance(row["name"], str) or not WHEEL_NAME.fullmatch(row["name"])
                or row["name"] in names or type(row["size"]) is not int
                or not 0 < row["size"] <= MAX_PREPARED_FILE
                or not isinstance(row["sha256"], str) or not re.fullmatch(r"[0-9a-f]{64}", row["sha256"])):
            raise UpdateError("the prepared package list is invalid")
        wheel = path / "wheels" / row["name"]
        info = wheel.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_size != row["size"]:
            raise UpdateError("a prepared package changed")
        digest = hashlib.sha256()
        with wheel.open("rb") as handle:
            while True:
                budget.check()
                block = handle.read(65536)
                if not block:
                    break
                digest.update(block)
        if digest.hexdigest() != row["sha256"]:
            raise UpdateError("a prepared package changed")
        names.add(row["name"])
        wheels.append(str(wheel))
    if {entry.name for entry in (path / "wheels").iterdir()} != names:
        raise UpdateError("the prepared package folder changed")
    return wheels


def offline_install(python, wheels, run, budget):
    run([str(python), "-m", "pip", "install", "--no-input", "--disable-pip-version-check",
         "--no-index", "--no-deps", "--force-reinstall", *wheels], budget.limit(300))
    budget.check()
    run([str(python), "-m", "pip", "check"], budget.limit(30))
    budget.check()
    run([str(python), "-I", "-c", IMPORT_CHECK], budget.limit(30))
    budget.check()


def prepare_install(staged, path, python, record, run, budget):
    """Finish builds and test the complete offline dependency set before stopping."""
    path.mkdir(mode=0o700)
    (path / "wheels").mkdir(mode=0o700)
    (path / "tmp").mkdir(mode=0o700)
    (path / "home").mkdir(mode=0o700)
    old_environment = getattr(COMMAND_CONTEXT, "environment", None)
    old_observe = getattr(COMMAND_CONTEXT, "observe", None)
    COMMAND_CONTEXT.environment = {"TMPDIR": str(path / "tmp"), "HOME": str(path / "home"),
                                   "PIP_NO_CACHE_DIR": "true"}
    COMMAND_CONTEXT.observe = lambda: _check_preparation(path, budget)
    try:
        locked_builder = staged / "deploy/raspberry-pi/prepare-locked-wheels.py"
        if locked_builder.is_file() and (locked_builder.parent / "release-locks").is_dir():
            run([str(python), "-I", str(locked_builder), "--source", str(staged),
                 "--output", str(path / "wheels")], budget.limit(300))
        else:
            if re.fullmatch(r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)", record.get("version", "")):
                raise UpdateError("a stable release must include its locked package preparation")
            run([str(python), "-m", "pip", "wheel", "--no-input", "--disable-pip-version-check",
                 "--no-cache-dir", "--wheel-dir", str(path / "wheels"), str(staged)], budget.limit(300))
        budget.check()
        _check_preparation(path, budget)
        rows = []
        for wheel in sorted((path / "wheels").iterdir()):
            budget.check()
            info = wheel.lstat()
            if not WHEEL_NAME.fullmatch(wheel.name) or not stat.S_ISREG(info.st_mode):
                raise UpdateError("package preparation produced an unexpected file")
            if len(rows) >= MAX_WHEELS or not 0 < info.st_size <= MAX_PREPARED_FILE:
                raise UpdateError("too many prepared packages or an oversized package")
            rows.append({"name": wheel.name, "size": info.st_size,
                         "sha256": hashlib.sha256(wheel.read_bytes()).hexdigest()})
        atomic_json(path / "wheels.json", {"commit": record["commit"], "version": record["version"], "wheels": rows})
        wheels = prepared_wheels(path, record, budget)
        environment = path / "verify"
        run([str(python), "-m", "venv", str(environment)], budget.limit(60))
        budget.check()
        offline_install(environment / "bin/python", wheels, run, budget)
        _check_preparation(path, budget)
        return wheels
    finally:
        COMMAND_CONTEXT.environment = old_environment
        COMMAND_CONTEXT.observe = old_observe


def compatible_manager(source):
    # Check syntax and the declared record format without executing future code.
    try:
        with source.open("rb") as handle:
            raw = handle.read(512 * 1024 + 1)
        if len(raw) > 512 * 1024:
            raise UpdateError("the release needs a compatible recovery manager")
        parsed = ast.parse(raw)
        declared = any(isinstance(node, ast.Assign)
                       and any(isinstance(target, ast.Name) and target.id == "RECOVERY_FORMAT"
                               for target in node.targets)
                       and isinstance(node.value, ast.Constant)
                       and type(node.value.value) is int and node.value.value == RECOVERY_FORMAT
                       for node in parsed.body)
        if not declared:
            raise UpdateError("the release needs a compatible recovery manager")
        operation_declared = any(isinstance(node, ast.Assign)
                                 and any(isinstance(target, ast.Name) and target.id == "OPERATION_FORMAT"
                                         for target in node.targets)
                                 and isinstance(node.value, ast.Constant)
                                 and type(node.value.value) is int and node.value.value == OPERATION_FORMAT
                                 for node in parsed.body)
        if not operation_declared:
            raise UpdateError("the release needs a compatible command ownership manager")
    except (OSError, SyntaxError, ValueError) as error:
        raise UpdateError("the release needs a runnable recovery manager") from error


def bootstrap_manager(source, destination, *, root=UPDATE_ROOT, descriptor=9):
    """Publish standalone recovery code while the manual installer holds its lock."""
    try:
        with manual_identity_operation(root, descriptor=descriptor):
            compatible_manager(source)
            safety = PanicProtection(source.parents[2])
            safety.inspect(source.parents[2])
            safety.retain(destination.with_name("jittersim-panic-guard.py"))
            install_manager(source, destination)
    except UpdateError as error:
        raise UpdateError("the installer command has no safe update ownership") from error


def check_operation(*, root=UPDATE_ROOT, descriptor=9):
    """Check the manual installer's inherited ownership before any mutation."""
    with manual_identity_operation(root, descriptor=descriptor):
        pass


WRITERS = (WATCHER, *reversed(SERVICES))


def writers_stopped(names, query, budget):
    for name in names:
        answer = query(["systemctl", "show", "--property=ActiveState", "--property=MainPID",
                        "--property=ControlPID", "--property=TasksCurrent", name], budget.limit(10))
        budget.check()
        state = dict(line.split("=", 1) for line in answer.splitlines() if "=" in line)
        if (state.get("ActiveState") not in ("inactive", "failed")
                or state.get("MainPID") != "0" or state.get("ControlPID") != "0"
                or state.get("TasksCurrent") not in ("0", "[not set]")):
            return False
    return True


def quiesce_manual_install(source, *, root=UPDATE_ROOT, descriptor=9, units=UNITS_ROOT,
                           run=run_command, query=read_command):
    """Provision current intent only after the installer's writers have ended."""
    with manual_identity_operation(root, descriptor=descriptor):
        if not _inherited_installer(_operation_record(root, "installer-pending.json")):
            raise UpdateError("the installer has no matching whole command ownership")
        budget = UpdateBudget()
        budget.deadline = min(budget.deadline, budget.started + 90)
        module = _panic_candidate(source)
        guard = module.PanicGuard()
        if guard.read() is None:
            guard.inspect_upgrade(module.PANIC_LEGACY_PATH)
        names = [name for name in WRITERS if (units / name).exists()]
        if names:
            run(["systemctl", "stop", *names], budget.limit(60))
        budget.check()
        if not writers_stopped(names, query, budget):
            raise UpdateError("the installer settings writers did not stop")
        budget.check()
        # Never publish the earlier preview. The real producer rereads latest
        # intent after stop so a concurrent Panic cannot be lost in migration.
        _panic_candidate(source)
        guard.provision_upgrade(module.PANIC_LEGACY_PATH)
        budget.check()


def validate_recovery(backup, budget):
    """Validate every input before deleting any part of the live installation."""
    try:
        completed = _recovery_record(backup / "complete.json")
        ownership = completed.get("ownership", {})
        if not isinstance(ownership, dict) or any(
            name not in ("code", "config", "data", "access")
            or not isinstance(pair, list) or len(pair) != 2
            or any(type(value) is not int or not 0 <= value < 2 ** 32 for value in pair)
            for name, pair in ownership.items()
        ):
            raise UpdateError("the recovery ownership record is invalid")
        names = completed.get("managed", ["units/" + unit for unit in LEGACY_UNITS])
        if not isinstance(names, list) or len(names) > len(UNITS) + 2 or len(set(names)) != len(names):
            raise UpdateError("the recovery file list is invalid")
        for name in names:
            if not isinstance(name, str) or not (name in ("dispatcher", "command")
                    or name.startswith("units/") and UNIT_NAME.fullmatch(name[6:])):
                raise UpdateError("the recovery copy names an unknown file")
        trees = ["code", "config", "data"]
        if (backup / "access").exists():
            trees.append("access")
        digests = {}
        for name in trees:
            if not (backup / name).is_dir() or (backup / name).is_symlink():
                raise UpdateError("a required recovery folder is missing")
            digests[name] = tree_digest(backup / name, budget, links=name == "code",
                                      ignore=PRESERVED_CONFIG if name == "config" else ())
        snapshot_size(backup, budget, links=True)
        if completed.get("format") == RECOVERY_FORMAT and completed.get("trees") != digests:
            raise UpdateError("the recovery snapshot changed")
        for name in names:
            saved = backup / name
            if saved.exists() or saved.is_symlink():
                digest = tree_digest(saved, budget, links=name == "command")
                if completed.get("format") == RECOVERY_FORMAT and completed.get("files", {}).get(name) != digest:
                    raise UpdateError("a managed recovery file changed")
            elif completed.get("format") == RECOVERY_FORMAT and name in completed.get("files", {}):
                raise UpdateError("a managed recovery file is missing")
        return completed
    except (OSError, ValueError, TypeError) as error:
        raise UpdateError("the recovery inputs could not be checked") from error


def pending_command(process):
    return {"group": process.pid, "boot": _current_boot()}


def _current_boot():
    path = Path("/proc/sys/kernel/random/boot_id")
    return path.read_text().strip() if path.exists() else None


def _same_boot(record):
    current_boot = _current_boot()
    saved_boot = record.get("boot")
    if ("boot" not in record or (saved_boot is not None
            and (not isinstance(saved_boot, str) or not re.fullmatch(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", saved_boot)))
            or (current_boot is not None and saved_boot is None)):
        raise UpdateError("the unfinished command boot record is invalid")
    return saved_boot == current_boot


def command_still_running(record, budget):
    # Read-only inspection cannot signal an unrelated reused process group.
    if (not isinstance(record, dict) or type(record.get("group")) is not int
            or not 0 < record["group"] < 2 ** 31):
        raise UpdateError("the unfinished command record is invalid")
    if not _same_boot(record):
        return False
    return command_group_running(record["group"], budget.deadline)


def healthy(*, config=CONFIG_ROOT, run=run_command, port=8080, budget=None, services=None):
    deadline = time.monotonic() + (budget.limit(10) if budget is not None else 10)

    def remaining():
        if budget is not None:
            budget.check()
        allowance = deadline - time.monotonic()
        if allowance <= 0:
            raise UpdateError("the dashboard health check timed out")
        return allowance

    class DeadlineReader(io.RawIOBase):
        def __init__(self, transport):
            super().__init__()
            self.transport = transport
            self.received = 0
            # HTTPConnection closes its socket early for HTTP/1.0 responses.
            # Retain the usual socket-file reference until the response closes.
            self.keep_open = transport.makefile("rb", buffering=0)

        def readable(self):
            return True

        def close(self):
            if not self.closed and hasattr(self, "keep_open"):
                self.keep_open.close()
            super().close()

        def readinto(self, buffer):
            self.transport.settimeout(remaining())
            size = self.transport.recv_into(buffer)
            self.received += size
            remaining()
            if self.received > 128 * 1024:
                raise UpdateError("the dashboard health response is too large")
            return size

    class DeadlineSocket:
        def __init__(self, transport):
            self.transport = transport

        def makefile(self, mode):
            return io.BufferedReader(DeadlineReader(self.transport))

    class DeadlineResponse(http.client.HTTPResponse):
        def __init__(self, transport, *args, **kwargs):
            super().__init__(DeadlineSocket(transport), *args, **kwargs)

    try:
        selected = services if services is not None else [
            name for name in SERVICES if name != "jittersim-adblocking.service"
            or (UNITS_ROOT / name).is_file()
        ]
        for service in selected:
            run(["systemctl", "is-active", "--quiet", service], min(5, remaining()))
            remaining()
        token = next(
            line.split("=", 1)[1].strip()
            for line in (config / "control.env").read_text().splitlines()
            if line.startswith("JITTERSIM_ADMIN_TOKEN=")
        )
        hostname = socket.gethostname().split(".")[0] + ".local"
        # The dashboard certificate is issued by the local authority. An older
        # self-signed one is its own authority.
        authority = config / "ca" / "ca.crt"
        saved = config / "trust.env"
        values = _certificate_settings(_certificate_bytes(saved)) if saved.exists() else {}
        if authority.exists():
            earlier = values.get("JITTERSIM_LOCAL_NAME", "")
            hostname = values.get("JITTERSIM_DASHBOARD_NAME") or (
                earlier if earlier.endswith(".local") else "netlab.local")
        elif "JITTERSIM_LOCAL_NAME" in values:
            hostname = values["JITTERSIM_LOCAL_NAME"]
        context = ssl.create_default_context(
            cafile=str(authority if authority.exists() else config / "dashboard.crt"))
        connection = http.client.HTTPSConnection(hostname, port, timeout=remaining(), context=context)
        connection.response_class = DeadlineResponse
        try:
            transport = socket.create_connection(("127.0.0.1", port), timeout=remaining())
            transport.settimeout(remaining())
            connection.sock = context.wrap_socket(transport, server_hostname=hostname)
            transport = connection.sock
            transport.settimeout(remaining())
            connection.request("GET", "/api/health", headers={"Authorization": "Bearer " + token})
            transport.settimeout(remaining())
            result = connection.getresponse()
            body = bytearray()
            while True:
                transport.settimeout(remaining())
                block = result.read1(min(4096, 65537 - len(body)))
                remaining()
                if not block:
                    break
                body.extend(block)
                if len(body) > 65536:
                    return False
                if getattr(result, "length", None) == 0:
                    break
            data = json.loads(body)
            remaining()
            return (result.status == 200 and data.get("status") == "ok"
                    and data.get("helper_reachable") is True)
        finally:
            connection.close()
    except (CommandNotStopped, OperationOwnershipUnknown):
        raise
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
    prepare=prepare_install,
):
    """Install the staged release with the release steps of install.sh.

    The steps that check the Pi run before anything stops. Every file the
    install replaces is in the recovery copy first.
    """
    budget = UpdateBudget()
    original_run, original_query = run, query

    def run(arguments, timeout):
        result = original_run(arguments, budget.limit(timeout))
        budget.check()
        return result

    def query(arguments, timeout):
        result = original_query(arguments, budget.limit(timeout))
        budget.check()
        return result

    health = health or (lambda: healthy(
        config=config, run=run, budget=budget,
        services=[name for name in SERVICES if name != "jittersim-adblocking.service"
                  or (units / name).is_file()],
    ))
    with update_lock(root, wait=budget.limit(15)):
        budget.check()
        request = read_json(root / "request.json")
        if not request:
            return
        fields = _identify_request(request)
        atomic_json(root / "request.json", request)
        previous_phase = read_json(root / "status.json").get("phase")
        backup = recovery_backup(root, pinned=request.get("recovery"))
        completed = _recovery_record(backup / "complete.json") if (backup / "complete.json").exists() else {}
        interrupted_current = (request.get("attempt") and completed.get("attempt") == request["attempt"]
                               and completed.get("target_version") == request.get("version"))
        interrupted_legacy = (not request.get("attempt") and backup.name == "backup"
                              and previous_phase in ("installing", "checking", "rolling-back")
                              and completed.get("target_version") == request.get("version"))
        if interrupted_current or interrupted_legacy:
            request["rollback"] = True
            request["recovery"] = recovery_identity(root, backup)
            atomic_json(root / "request.json", request)

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
                atomic_json(root / 'status.json', {'phase': 'failed', 'message': message, **fields})
                raise UpdateError(message) from error

        def progress(phase, message):
            if phase != "failed":
                budget.check()
            atomic_json(root / "status.json", {"phase": phase, "message": message, **fields})

        def present(names):
            return [name for name in names if (units / name).exists()]

        def prepare_name_service():
            """Limit Avahi like install.sh, before the firewall lets mDNS in.

            Avahi cannot limit its own publishing to one network. If it cannot be
            limited it is stopped, so it never announces the Pi on the test Wi-Fi
            with its own settings, and the name service is not started.
            """
            try:
                configure_dashboard_name(code, config)
                run(["systemctl", "restart", "avahi-daemon.service"], 120)
                return True
            except (CommandNotStopped, OperationOwnershipUnknown):
                raise
            except UpdateError:
                try:
                    run(["systemctl", "stop", "avahi-daemon.service", "avahi-daemon.socket"], 60)
                except (CommandNotStopped, OperationOwnershipUnknown):
                    raise
                except UpdateError:
                    pass
                return False

        def restart_services(prepare_name=False):
            """Run the systemctl steps of install.sh for the units installed now."""
            panic_safety.inspect(code)
            name_ready = prepare_name_service() if prepare_name else True
            try:
                wifi = query(
                    ["nmcli", "-g", "GENERAL.CONNECTION", "device", "show",
                     runtime_interfaces(config)[1]],
                    10,
                )
            except (CommandNotStopped, OperationOwnershipUnknown):
                raise
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
            if name_ready and present([NAME_UNIT]):
                for action, *names in NAME_STEPS:
                    try:
                        run(["systemctl", action, *names], 120)
                    except (CommandNotStopped, OperationOwnershipUnknown):
                        raise
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
            budget.check()
            descriptor, temporary = tempfile.mkstemp(prefix=".trust-", dir=destination.parent)
            os.close(descriptor)
            try:
                shutil.copy2(source, temporary)
                Path(temporary).chmod(0o600 if destination.name == TOKEN_NAME else 0o644)
                with open(temporary, "rb") as handle:
                    os.fsync(handle.fileno())
                os.replace(temporary, destination)
                _fsync_folder(destination.parent)
                budget.check()
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
            remove_tree(keep, budget)
            _fsync_folder(root)

        def restore():
            if not (backup / "complete.json").exists():
                raise UpdateError("the recovery copy is incomplete")
            completed = validate_recovery(backup, budget)
            panic_safety.inspect(backup / "code")
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
            certificate_deadline = time.monotonic() + CERTIFICATE_SECONDS
            transaction = hashlib.sha256(
                _certificate_bytes(backup / "complete.json") +
                str(request.get("version", "")).encode("utf-8")
            ).hexdigest()
            retained_certificate = prepare_certificate_restore(
                root, config, transaction, deadline=certificate_deadline, budget=budget
            )
            keep = keep_trust_settings()
            for name, destination in (("code", code), ("config", config), ("data", data)):
                if destination.exists():
                    remove_tree(destination, budget)
                preserved = PRESERVED_CONFIG + (
                    ".certificate-adoption", "ca", "dashboard.crt", "dashboard.key",
                    "dashboard-wifi.crt", "trust.env",
                ) if name == "config" else ()
                snapshot_tree(backup / name, destination, budget, links=name == "code",
                              ignore=preserved)
                if name in ownership:
                    uid, gid = ownership[name]
                    for entry, _, _ in tree_entries(destination, budget, links=name == "code"):
                        os.chown(entry, uid, gid, follow_symlinks=False)
            put_back_trust_settings(keep)
            restore_certificate_identity(
                root, config, retained_certificate, deadline=certificate_deadline, budget=budget
            )
            for name, destination in targets:
                budget.check()
                saved = backup / name
                if saved.is_symlink():
                    link_command(os.readlink(saved), destination)
                elif saved.exists():
                    if destination.is_symlink() or destination.exists():
                        destination.unlink()
                    _copy_snapshot_file(saved, destination, saved.lstat(), budget)
                elif destination.is_symlink() or destination.exists():
                    unit = name[6:] if name.startswith("units/") else None
                    if unit:
                        # The update added this unit. The running Wi-Fi and the
                        # update itself keep running.
                        keep = unit in (WIFI_UNIT, "jittersim-update.service")
                        try:
                            run(["systemctl", "disable", *([] if keep else ["--now"]), unit], 60)
                        except (CommandNotStopped, OperationOwnershipUnknown):
                            raise
                        except UpdateError:
                            pass
                    destination.unlink()
            if (backup / "access").exists():
                if access.exists():
                    remove_tree(access, budget)
                snapshot_tree(backup / "access", access, budget)
                if "access" in ownership:
                    uid, gid = ownership["access"]
                    for entry, _, _ in tree_entries(access, budget):
                        os.chown(entry, uid, gid, follow_symlinks=False)
            # The recovery manager is separate from the application. Rolling it
            # back could undo trust protections needed by later recovery attempts.
            restart_services()

        def wait_healthy():
            until = min(budget.deadline, time.monotonic() + 30)
            for _ in range(30):
                if time.monotonic() >= until:
                    return False
                budget.check()
                answer = health()
                budget.check()
                if time.monotonic() >= until:
                    return False
                if answer:
                    return True
                time.sleep(min(1, max(0, until - time.monotonic())))
            return False

        def wait_restarted_healthy():
            """Check health, then drop journal settings the restarted code lacks.

            Removing them restarts journald and may take 10 seconds, so it never
            runs while the services, and with them Panic, are stopped or unhealthy.
            After a failed check the removal stays pending: the kept recovery
            request runs it once a later attempt finds the services healthy.
            """
            healthy = wait_healthy()
            if healthy and not (code / "src/jittersim/journal_policy.py").is_file():
                configure_outage_journal(code, config, manager, run)
            return healthy

        if request.get("command_pending"):
            try:
                if command_still_running(request["command_pending"], budget):
                    raise UpdateError("an unfinished update command must stop before retry")
            except Exception:
                progress("failed", "An update command has not stopped. Recovery will wait for it.")
                raise
            request.pop("command_pending")
            atomic_json(root / "request.json", request)
        recovered = False
        keep_request = True
        stopped = bool(request.get("restart_only"))
        try:
            # Even preflight is supervised by the new durable command lease.
            compatible_manager(Path(__file__))
            if Path(__file__).resolve() != manager.resolve():
                install_manager(Path(__file__), manager)
            staged = root / "staged"
            panic_safety = PanicProtection(code if request.get("rollback") else staged,
                                           retained=manager.with_name("jittersim-panic-guard.py"))
            selected = (backup / "code",) if request.get("rollback") else (staged, code)
            panic_safety.inspect(*selected)
            panic_safety.retain(manager.with_name("jittersim-panic-guard.py"))
            if request.get("rollback"):
                if "recovery" not in request:
                    request["recovery"] = recovery_identity(root, backup)
                    atomic_json(root / "request.json", request)
                budget.recover()
                validate_recovery(backup, budget)
            else:
                if stopped:
                    # A previous invocation stopped writers before publishing a copy.
                    # Its old source is still live. Never restore an unrelated backup.
                    budget.recover()
                    restart_services()
                    if not wait_restarted_healthy():
                        raise UpdateError("previous services could not restart before retry")
                    request.pop("restart_only", None)
                    atomic_json(root / "request.json", request)
                    stopped = False
                    budget.deadline = min(budget.deadline, budget.started + 600)
                staged = root / "staged"
                record = _record(staged / ".jittersim-release.json")
                if record.get("version") != request.get("version"):
                    raise UpdateError("staged release changed")
                problem = staged_problem(record, trusted_keys(config), root=root, code=code)
                if problem:
                    raise UpdateError(problem)
                uplink, test_interface = runtime_interfaces(config)
                compatible_manager(staged / "src/jittersim/updates.py")
                progress("preparing", "Checking the Pi network and preparing the update.")
                run([PYTHON, str(staged / PREFLIGHT), "--uplink", uplink,
                     "--test-interface", test_interface], 60)
                run([PYTHON, str(staged / EGRESS_CHECK), test_interface], 60)
                roots = (("code", code), ("config", config), ("data", data), ("access", access))
                for name, source in roots[:3]:
                    if not source.is_dir() or source.is_symlink():
                        raise UpdateError("a required installation folder is missing or unsafe")
                managed = [("units/" + unit, units / unit) for unit in UNITS]
                managed += [("dispatcher", dispatcher), ("command", command)]
                required = snapshot_requirements([*roots, *managed], budget)
                if shutil.disk_usage(root).free < required + MAX_PREPARATION_BYTES + SPACE_MARGIN:
                    raise UpdateError("not enough free space for update and recovery")
                # Publish new record formats only after the installed recovery manager
                # can read them. The current fixed manager is also kept on rollback.
                if "attempt" not in request:
                    request["attempt"] = secrets.token_hex(16)
                if not isinstance(request["attempt"], str) or not ATTEMPT.fullmatch(request["attempt"]):
                    raise UpdateError("the queued preparation identity is invalid")
                atomic_json(root / "request.json", request)
                prepared = root / ("prepare-" + request["attempt"])
                candidate = root / ("backup-" + request["attempt"])
                # A complete but unpublished candidate still belongs to this
                # attempt. Never remove the selected or pinned recovery copy.
                if candidate == recovery_backup(root, pinned=request.get("recovery")):
                    raise UpdateError("the published recovery copy must be restored first")
                if (candidate / "complete.json").exists():
                    owned = _recovery_record(candidate / "complete.json")
                    if owned.get("attempt") != request["attempt"] or owned.get("target_version") != request.get("version"):
                        raise UpdateError("the unpublished recovery copy belongs to different work")
                for path in (prepared, candidate):
                    if path.exists() or path.is_symlink():
                        info = path.lstat()
                        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
                            raise UpdateError("the unfinished preparation folder is unsafe")
                        remove_tree(path, budget)
                cleanup_generations(root, budget)
                prepare(staged, prepared, code / ".venv/bin/python", record, run, budget)
                candidate.mkdir(mode=0o700)
                snapshot_tree(code, candidate / "code", budget, links=True)
                # Durable restart intent precedes the first possibly successful stop.
                request["restart_only"] = True
                atomic_json(root / "request.json", request)
            progress("stopping", "Stopping services before saving the settings.")
            stopped = True
            names = present(WRITERS)
            run(["systemctl", "stop", *names], 60)
            if not writers_stopped(names, query, budget):
                raise UpdateError("the settings writers did not stop")
            panic_safety.provision(*selected)
            if request.get("rollback"):
                progress("rolling-back", "Restoring the previous installation and settings.")
                restore()
                if not wait_restarted_healthy():
                    raise UpdateError("recovery health check failed")
                cleanup_generations(root, budget)
                recovered = True
                progress("rolled-back", "Previous installation restored and health check passed.")
                finish_certificate_recovery(root, budget=budget)
                keep_request = False
                return
            progress("backing-up", "Saving the stopped installation and settings.")
            mutable = (("config", config), ("data", data), ("access", access))
            immutable_size = snapshot_size(candidate / "code", budget, links=True)
            required = snapshot_requirements([("code", candidate / "code"), *mutable, *managed], budget)
            if shutil.disk_usage(root).free < required - immutable_size + SPACE_MARGIN:
                raise UpdateError("not enough free space for the final settings snapshot")
            for name, source in mutable:
                if source.exists():
                    snapshot_tree(source, candidate / name, budget,
                                  ignore=PRESERVED_CONFIG if name == "config" else
                                  ("incoming-release",) if name == "data" else ())
            (candidate / "units").mkdir(mode=0o700)
            managed = [("units/" + unit, units / unit) for unit in UNITS]
            managed += [("dispatcher", dispatcher), ("command", command)]
            files = {}
            for name, path in managed:
                budget.check()
                if path.is_symlink():
                    if name != "command" or path.lstat().st_uid != os.geteuid():
                        raise UpdateError("a managed recovery file contains an unsafe link")
                    os.symlink(os.readlink(path), candidate / name)
                elif path.exists():
                    _copy_snapshot_file(path, candidate / name, path.lstat(), budget)
                else:
                    continue
                files[name] = tree_digest(candidate / name, budget, links=name == "command")
            trees = {name: tree_digest(candidate / name, budget, links=name == "code",
                                      ignore=PRESERVED_CONFIG if name == "config" else ())
                     for name in ("code", "config", "data", "access")
                     if (candidate / name).exists()}
            snapshot_size(candidate, budget, links=True)
            _fsync_folder(candidate / "units")
            budget.check()
            atomic_json(candidate / "complete.json", {
                "format": RECOVERY_FORMAT, "attempt": request["attempt"],
                "created_at": int(time.time()), "target_version": request["version"],
                "managed": [name for name, _ in managed], "trees": trees, "files": files,
                "ownership": {name: [source.stat().st_uid, source.stat().st_gid]
                              for name, source in (("code", code), *mutable) if source.exists()},
            })
            budget.check()
            atomic_json(root / "backup-current.json", {
                "format": RECOVERY_FORMAT, "name": candidate.name, "attempt": request["attempt"],
            })
            backup = recovery_backup(root)
            request["recovery"] = recovery_identity(root, backup)
            atomic_json(root / "request.json", request)
            progress("installing", "Installing the prepared release.")
            wheels = prepared_wheels(prepared, record, budget)
            for name in ("src", "deploy"):
                if (code / name).exists():
                    remove_tree(code / name, budget)
                snapshot_tree(staged / name, code / name, budget)
            for name in ("pyproject.toml", "README.md", ".jittersim-release.json"):
                budget.check()
                if (code / name).exists():
                    (code / name).unlink()
                _copy_snapshot_file(staged / name, code / name, (staged / name).lstat(), budget)
            if (code / "build").exists():
                remove_tree(code / "build", budget)
            python = str(code / ".venv/bin/python")
            offline_install(python, wheels, run, budget)
            # The root helper runs this code, so remove writes by other users.
            for entry, _, info in tree_entries(code, budget, links=True):
                if not stat.S_ISLNK(info.st_mode):
                    entry.chmod(info.st_mode & ~0o022)
            run(["runuser", "-u", "jittersim", "--", python, "-I", "-c", IMPORT_CHECK], 120)
            run([python, str(code / BOOTSTRAP)], 60)
            # Like install.sh: keep the dashboard name setting and the local
            # certificate authority, and renew the dashboard certificates.
            prepare_dashboard_identity(code, config, budget)
            link_command(str(code / ".venv/bin/jittersim"), command)
            render_firewall(staged / FIREWALL_TEMPLATE, config / FIREWALL_RULES,
                            uplink, test_interface)
            dispatcher.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
            for source, destination, mode in release_files(code=code, units=units, dispatcher=dispatcher):
                budget.check()
                install_file(staged / source, destination, mode)
            compatibility = code / "deploy/raspberry-pi/homepage-profile-compat.py"
            if compatibility.exists():
                install_file(compatibility, manager.with_name("jittersim-profile-compat.py"), 0o644)
                dropins = units / "jittersim-dashboard.service.d"
                dropins.mkdir(mode=0o755, parents=True, exist_ok=True)
                install_file(code / "deploy/raspberry-pi/homepage-compat.conf",
                             dropins / "homepage-compat.conf", 0o644)
            progress("checking", "Restarting services and checking the dashboard and helper.")
            restart_services(prepare_name=True)
            if not wait_healthy():
                raise UpdateError("updated services failed their health check")
            configure_outage_journal(code, config, manager, run)
            _raise_floor(root, record["sequence"])
            compatible_manager(code / "src/jittersim/updates.py")
            install_manager(code / "src/jittersim/updates.py", manager)
            cleanup_generations(root, budget)
            progress("complete", "Release installed. Dashboard and helper health checks passed.")
            finish_certificate_recovery(config / ".certificate-adoption", budget=budget)
            keep_request = False
        except PanicProtectionError:
            progress("failed", "Panic protection needs management access. The current package and update intent are kept.")
            raise
        except OperationOwnershipUnknown:
            progress("failed", "An update command could not be confirmed. Recovery will wait for it.")
            raise
        except CommandNotStopped as error:
            retain_command(root, error)
            request["command_pending"] = pending_command(error.process)
            atomic_json(root / "request.json", request)
            progress("failed", "An update command has not stopped. Recovery will wait for it.")
            raise
        except Exception:
            # replace() may have succeeded even when its directory sync failed.
            # Choose the actual validated, request-matching publication.
            needs_recovery = bool(request.get("rollback"))
            try:
                actual = recovery_backup(root, pinned=request.get("recovery"))
                completed = _recovery_record(actual / "complete.json") if (actual / "complete.json").exists() else {}
                if request.get("attempt") and completed.get("attempt") == request["attempt"]:
                    backup = actual
                    request["recovery"] = recovery_identity(root, backup)
                    needs_recovery = True
                budget.recover()
                if needs_recovery:
                    request["rollback"] = True
                    atomic_json(root / "request.json", request)
                    progress("rolling-back", "Update failed. Restoring the recovery copy.")
                    validate_recovery(backup, budget)
                    names = present(WRITERS)
                    run(["systemctl", "stop", *names], 60)
                    if not writers_stopped(names, query, budget):
                        raise UpdateError("the settings writers did not stop before recovery")
                    restore()
                    recovered = wait_restarted_healthy()
                elif stopped:
                    restart_services()
                    recovered = wait_restarted_healthy()
                else:
                    recovered = True
                if recovered and (needs_recovery or stopped):
                    cleanup_generations(root, budget)
            except OperationOwnershipUnknown:
                progress("failed", "A recovery command could not be confirmed. Recovery will wait for it.")
                raise
            except CommandNotStopped as error:
                retain_command(root, error)
                request["command_pending"] = pending_command(error.process)
                atomic_json(root / "request.json", request)
                recovered = False
            except Exception:
                recovered = False
            keep_request = bool((needs_recovery or stopped or request.get("command_pending")) and not recovered)
            if recovered and needs_recovery:
                message = "Update failed. Previous installation restored and checked."
            elif recovered and stopped:
                message = "Update stopped. Previous installation restarted and checked."
            elif not stopped:
                message = "Update stopped before it changed anything. Check the update service log."
            else:
                message = "Update or recovery failed. Check the update service log."
            progress("rolled-back" if recovered and needs_recovery else "failed", message)
            if recovered and needs_recovery:
                finish_certificate_recovery(root, budget=budget)
            raise
        finally:
            if not keep_request:
                (root / "request.json").unlink(missing_ok=True)
                _fsync_folder(root)



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


def _read_token_input():
    """The token from standard input only. On a terminal nothing is shown while it is typed."""
    stream = getattr(sys.stdin, "buffer", None)
    if sys.stdin.isatty():
        print("Paste the GitHub token, then press Ctrl-D. It is not shown.", file=sys.stderr)
    echo_off = None
    try:
        import termios

        if sys.stdin.isatty():
            echo_off = termios.tcgetattr(sys.stdin.fileno())
            changed = list(echo_off)
            changed[3] &= ~termios.ECHO
            termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, changed)
    except (ImportError, OSError):
        echo_off = None
    try:
        raw = stream.read(MAX_TOKEN_INPUT + 1) if stream else sys.stdin.read(
            MAX_TOKEN_INPUT + 1).encode()
    finally:
        if echo_off is not None:
            import termios

            termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, echo_off)
    try:
        return raw.decode("ascii")
    except UnicodeDecodeError:
        return ""


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
    commands.add_parser("bootstrap-manager", help=argparse.SUPPRESS)
    commands.add_parser("check-operation", help=argparse.SUPPRESS)
    installer_prepare = commands.add_parser("prepare-installer", help=argparse.SUPPRESS)
    installer_prepare.add_argument("source", type=Path)
    manager_check = commands.add_parser("check-manager", help=argparse.SUPPRESS)
    manager_check.add_argument("source", type=Path)
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
        "set-token", help="save the read-only GitHub token of the online check (root)",
        description="Read a fine-grained GitHub token (this repository, Contents read-only) "
        "from standard input and save it for root only. The token is never printed.",
    )
    commands.add_parser("clear-token", help="remove the saved GitHub token (root)")
    commands.add_parser(
        "check-online", help="look for a newer signed release on GitHub and stage it (root)",
        description="Ask GitHub for the newest release and stage it when a trusted key signed "
        "it and it is newer than the installed one.",
    )
    commands.add_parser("fetch", help="run the online check the dashboard asked for (service)")
    commands.add_parser(
        "enable-passwordless-staging",
        help="let the jittersim login stage and install signed releases without a password",
    )
    commands.add_parser(
        "disable-passwordless-staging", help="ask for the sudo password again (root)"
    )
    args = parser.parse_args(argv)
    name = parser.prog
    if args.command not in ("list-keys", "check-manager") and not running_as_root():
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
        elif args.command == "check-manager":
            compatible_manager(args.source)
        elif args.command == "bootstrap-manager":
            bootstrap_manager(Path(__file__), MANAGER_PATH)
        elif args.command == "check-operation":
            check_operation()
        elif args.command == "prepare-installer":
            quiesce_manual_install(args.source)
        elif args.command == "apply":
            apply_update(root=UPDATE_ROOT, code=CODE_ROOT, config=CONFIG_ROOT)
        elif args.command == "set-token":
            set_token(_read_token_input(), config=CONFIG_ROOT)
            print("The GitHub token is saved. Only root can read it. Check with "
                  "'sudo jittersim update check --online'.")
        elif args.command == "clear-token":
            clear_token(config=CONFIG_ROOT)
            print("The GitHub token is removed.")
        elif args.command == "check-online":
            result = perform_online_check(
                new_job(), root=UPDATE_ROOT, code=CODE_ROOT, config=CONFIG_ROOT,
                source=default_source(), auto_install=False,
            )
            if result["result"] == "failed":
                raise UpdateError(result["message"])
            print(result["message"])
        elif args.command == "fetch":
            result = run_fetch_job(root=UPDATE_ROOT, code=CODE_ROOT, config=CONFIG_ROOT,
                                   source=default_source())
            print(result["message"] if result else "No online check was requested.")
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
        if args.command not in ("apply", "fetch", "bootstrap-manager"):
            print("Nothing was changed.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
