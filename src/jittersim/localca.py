"""A small local certificate authority for the dashboard's HTTPS certificate.

The authority is created once and kept. It permits local name suffixes and
the configured network address ranges. The dashboard certificate is issued from it and is
renewed without changing the authority. Everything here calls the `openssl`
program and needs no root rights by itself.
"""

import argparse
import contextvars
import fcntl
import functools
import hashlib
import ipaddress
import os
import plistlib
import re
import secrets
import shutil
import socket
import ssl
import stat
import subprocess
import sys
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional, Sequence, Tuple

from .localname import (
    DEFAULT_LOCAL_NAME,
    GATEWAY_ADDRESS,
    LEGACY_LOCAL_NAME,
    _legacy_name,
    compatibility_names,
    read_local_name,
    validate_local_name,
)

CA_DAYS = 7300
LEAF_DAYS = 365
RENEW_SECONDS = 30 * 24 * 3600
TEST_NETWORK = "192.168.50.0/24"
# Names shown on devices that trust the authority. They stay neutral.
CA_NAME = "Local Dashboard Authority"
PROFILE_NAME = "Local Dashboard Certificate"
_PRIVATE_BLOCKS = ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")
# The authority vouches for whole suffixes, not for one name. The dashboard
# name is a setting, and a new name must not need a new authority, because every
# device that trusts the authority would have to install it again. `.local` is
# the neutral dashboard name, `home.arpa` its earlier test Wi-Fi form.
_NAME_SUFFIXES = ("local", "home.arpa")
_CRYPTO_DEADLINE = contextvars.ContextVar("certificate_deadline", default=None)
MAX_CERTIFICATE_BYTES = 64 * 1024
CRYPTO_SECONDS = 60.0


class TrustError(Exception):
    """The certificate files could not be prepared."""


def _propagate_special_update_error(error):
    from .updates import UpdateError
    if isinstance(error, UpdateError) and type(error) is not UpdateError:
        raise error


def _bounded_crypto(function):
    @functools.wraps(function)
    def bounded(*args, **kwargs):
        from .updates import _CERTIFICATE_TIME, _CERTIFICATE_RUN
        parent = _CERTIFICATE_TIME.get()
        budget = kwargs.get("budget") or (parent[1] if parent else None)
        now = time.monotonic()
        deadline = min(_CRYPTO_DEADLINE.get() or now + CRYPTO_SECONDS,
                       parent[0] if parent else now + CRYPTO_SECONDS)
        if budget is not None:
            budget.check()
            deadline = min(deadline, now + budget.limit(CRYPTO_SECONDS))
        deadline_token = _CRYPTO_DEADLINE.set(deadline)
        timing_token = _CERTIFICATE_TIME.set((deadline, budget))
        runner_token = _CERTIFICATE_RUN.set(kwargs.get("run") or _CERTIFICATE_RUN.get())
        try:
            _remaining()
            result = function(*args, **kwargs)
            _remaining()
            return result
        finally:
            _CERTIFICATE_RUN.reset(runner_token)
            _CERTIFICATE_TIME.reset(timing_token)
            _CRYPTO_DEADLINE.reset(deadline_token)
    return bounded


def _remaining() -> float:
    from .updates import _certificate_check
    _certificate_check()
    deadline = _CRYPTO_DEADLINE.get()
    remaining = CRYPTO_SECONDS if deadline is None else deadline - time.monotonic()
    if remaining <= 0:
        raise TrustError("certificate verification exceeded its time allowance")
    return remaining


def _serialized_prepare(function):
    @functools.wraps(function)
    def checked(config_dir, *args, **kwargs):
        config_dir.mkdir(parents=True, exist_ok=True)
        _check_directory(config_dir)
        lock = os.open(config_dir / ".certificate-prepare.lock",
                       os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0) |
                       getattr(os, "O_NONBLOCK", 0), 0o600)
        try:
            info = os.fstat(lock)
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or
                    stat.S_IMODE(info.st_mode) != 0o600):
                raise TrustError("the certificate preparation lock is not private")
            while True:
                _remaining()
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    time.sleep(min(0.02, _remaining()))
            return function(config_dir, *args, **kwargs)
        finally:
            os.close(lock)
    return checked


def _regular_bytes(path: Path) -> bytes:
    from .updates import CertificateMaterial
    _remaining()
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    parent = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) |
                     getattr(os, "O_NOFOLLOW", 0))
    try:
        directory = os.fstat(parent)
        if directory.st_uid not in (0, os.geteuid()) or directory.st_mode & 0o022:
            raise TrustError("certificate material must be in an administrator-owned directory")
        descriptor = os.open(path.name, flags, dir_fd=parent)
        try:
            info = os.fstat(descriptor)
            if (not stat.S_ISREG(info.st_mode) or info.st_size > MAX_CERTIFICATE_BYTES or
                    info.st_uid not in (0, os.geteuid()) or info.st_mode & 0o022 or
                    (path.name == "ca.key" and info.st_mode & 0o077)):
                raise TrustError("certificate material must be a bounded administrator-owned regular file")
            with os.fdopen(descriptor, "rb", closefd=False) as handle:
                content = CertificateMaterial(handle.read(MAX_CERTIFICATE_BYTES + 1))
            if len(content) > MAX_CERTIFICATE_BYTES:
                raise TrustError("certificate material is too large")
            _remaining()
            return content
        finally:
            os.close(descriptor)
    finally:
        os.close(parent)


def _sync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _check_directory(path: Path) -> None:
    info = path.lstat()
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid not in (0, os.geteuid()) or
            info.st_mode & 0o022):
        raise TrustError("certificate settings must be in an administrator-owned directory")


@dataclass
class LeafResult:
    reissued: bool
    notes: Tuple[str, ...] = field(default_factory=tuple)


def _openssl(*arguments: str, check: bool = True, data: Optional[bytes] = None):
    from .updates import CommandNotStopped, UpdateError, capture_command, _CERTIFICATE_RUN
    try:
        run = _CERTIFICATE_RUN.get() or capture_command
        output = run(("openssl",) + arguments, _remaining(), input=data or b"", limit=65536)
        _remaining()
    except CommandNotStopped:
        raise
    except (OSError, subprocess.SubprocessError, UpdateError) as error:
        _propagate_special_update_error(error)
        _remaining()
        if not check:
            return subprocess.CompletedProcess(("openssl",) + arguments, 1, b"", b"")
        raise TrustError("openssl %s could not finish" % arguments[0]) from error
    return subprocess.CompletedProcess(("openssl",) + arguments, 0, output, b"")


def _text(path: Path) -> str:
    return _openssl("x509", "-noout", "-text", data=_regular_bytes(path)).stdout.decode()


def _replace(source: Path, destination: Path, mode: int) -> None:
    os.chmod(source, mode)
    with source.open("rb") as handle:
        os.fsync(handle.fileno())
    os.replace(source, destination)
    _sync_directory(destination.parent)


def uplink_range(ethernet_ip: Optional[str]) -> str:
    """The private block that holds the Ethernet address, or a likely default."""
    try:
        address = ipaddress.ip_address(ethernet_ip or "")
    except ValueError:
        return "192.168.0.0/16"
    for block in _PRIVATE_BLOCKS:
        if address in ipaddress.ip_network(block):
            return block
    if address.is_loopback or address.version != 4:
        return "192.168.0.0/16"
    return str(ipaddress.ip_network(str(address) + "/24", strict=False))


def _constraint(network: str) -> str:
    parsed = ipaddress.ip_network(network)
    return "IP:%s/%s" % (parsed.network_address, parsed.netmask)


def _authority_config(ethernet_ip: Optional[str]) -> str:
    permitted = ["DNS:" + suffix for suffix in _NAME_SUFFIXES]
    permitted += [_constraint(TEST_NETWORK), _constraint(uplink_range(ethernet_ip))]
    return "\n".join([
        "[req]", "distinguished_name=dn", "x509_extensions=ext", "prompt=no",
        "[dn]", "CN=%s %s" % (CA_NAME, secrets.token_hex(4)),
        "[ext]",
        "basicConstraints=critical,CA:TRUE,pathlen:0",
        "keyUsage=critical,keyCertSign,cRLSign",
        "subjectKeyIdentifier=hash",
        "authorityKeyIdentifier=keyid:always",
        "nameConstraints=critical," + ",".join("permitted;" + item for item in permitted),
        "",
    ])


def _public_key(path: Path, certificate: bool) -> str:
    if certificate:
        completed = _openssl("x509", "-noout", "-pubkey", check=False,
                             data=_regular_bytes(path))
    else:
        completed = _openssl("pkey", "-pubout", check=False, data=_regular_bytes(path))
    return completed.stdout.decode() if completed.returncode == 0 else ""


@_bounded_crypto
def authority_valid(directory: Path) -> bool:
    """True when the saved authority is usable, constrained and matches its key."""
    from .updates import UpdateError, verify_certificate_identity
    certificate, key = directory / "ca.crt", directory / "ca.key"
    try:
        if not stat.S_ISDIR(directory.lstat().st_mode):
            return False
        return verify_certificate_identity(
            {"ca/ca.crt": _regular_bytes(certificate), "ca/ca.key": _regular_bytes(key)},
            authority_only=True, deadline=_CRYPTO_DEADLINE.get(),
        ) == "ca"
    except (TrustError, OSError, UpdateError) as error:
        _propagate_special_update_error(error)
        return False


@_bounded_crypto
def ensure_authority(directory: Path, *, ethernet_ip: Optional[str] = None) -> bool:
    """Keep a valid existing authority or publish one complete fresh authority."""
    directory.parent.mkdir(parents=True, exist_ok=True)
    _check_directory(directory.parent)
    lock_path = directory.parent / ".ca-create.lock"
    lock = os.open(lock_path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        info = os.fstat(lock)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
            raise TrustError("certificate creation lock must be private and owned by its administrator")
        while True:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                time.sleep(min(0.02, _remaining()))
        if os.path.lexists(directory):
            if authority_valid(directory):
                return False
            raise TrustError("the existing certificate authority is damaged or incomplete; preserve it for recovery")
        return _create_authority(directory, ethernet_ip)
    finally:
        os.close(lock)


def _create_authority(directory: Path, ethernet_ip: Optional[str]) -> bool:
    with tempfile.TemporaryDirectory(prefix=".ca-new-", dir=str(directory.parent)) as scratch:
        scratch_path = Path(scratch)
        candidate = scratch_path / "authority"
        candidate.mkdir(mode=0o700)
        key, certificate, config = (
            candidate / "ca.key", candidate / "ca.crt", scratch_path / "ca.cnf")
        _openssl("ecparam", "-name", "prime256v1", "-genkey", "-noout", "-out", str(key))
        os.chmod(key, 0o600)
        config.write_text(_authority_config(ethernet_ip))
        _openssl("req", "-new", "-x509", "-key", str(key), "-sha256", "-days", str(CA_DAYS),
                 "-config", str(config), "-out", str(certificate))
        os.chmod(certificate, 0o644)
        if not authority_valid(candidate):
            raise TrustError("the new certificate authority could not be verified")
        for path in (key, certificate):
            with path.open("rb") as handle:
                os.fsync(handle.fileno())
        _sync_directory(candidate)
        os.rename(candidate, directory)
        _sync_directory(directory.parent)
    return True


def _permitted(directory: Path) -> Tuple[Tuple[str, ...], Tuple[ipaddress.IPv4Network, ...]]:
    text = _text(directory / "ca.crt")
    names = tuple(re.findall(r"DNS:([A-Za-z0-9.-]+)", text.split("Name Constraints")[-1]))
    networks = tuple(
        ipaddress.ip_network("%s/%s" % pair)
        for pair in re.findall(r"IP:([\d.]+)/([\d.]+)", text))
    return names, networks


def _name_allowed(name: str, suffixes: Iterable[str]) -> bool:
    return any(name == suffix or name.endswith("." + suffix) for suffix in suffixes)


def _neutral_name(value: str) -> str:
    """The dashboard name, or the earlier test Wi-Fi name that is still served."""
    name = str(value).strip().lower().rstrip(".")
    return _legacy_name(name) if name.endswith(".home.arpa") else validate_local_name(name)


def _alternative_names(certificate: Path) -> Optional[set]:
    try:
        text = _text(certificate)
    except TrustError:
        return None
    match = re.search(r"Subject Alternative Name:[^\n]*\n\s*([^\n]+)", text)
    if not match:
        return set()
    return {item.strip() for item in match.group(1).split(",")}


def _current(authority: Path, certificate: Path, key: Path, wanted: set) -> bool:
    if not certificate.is_file() or _alternative_names(certificate) != wanted:
        return False
    if _openssl("verify", "-CAfile", str(authority / "ca.crt"), str(certificate),
                check=False).returncode != 0:
        return False
    if _openssl("x509", "-in", str(certificate), "-noout", "-checkend", str(RENEW_SECONDS),
                check=False).returncode != 0:
        return False
    return _public_key(certificate, True) == _public_key(key, False) != ""


def _issue(authority: Path, key: Path, target: Path, names: Sequence[str],
           addresses: Sequence[str], days: int, scratch: Path) -> None:
    alternatives = ["DNS:" + name for name in names] + ["IP:" + item for item in addresses]
    extensions = scratch / (target.name + ".cnf")
    extensions.write_text("\n".join([
        "basicConstraints=critical,CA:FALSE",
        "keyUsage=critical,digitalSignature",
        "extendedKeyUsage=serverAuth",
        "subjectKeyIdentifier=hash",
        "authorityKeyIdentifier=keyid",
        "subjectAltName=" + ",".join(alternatives),
        "",
    ]))
    request = scratch / (target.name + ".csr")
    _openssl("req", "-new", "-key", str(key), "-subj", "/CN=" + (names[0] if names else addresses[0]),
             "-out", str(request))
    issued = scratch / (target.name + ".new")
    _openssl("x509", "-req", "-in", str(request), "-CA", str(authority / "ca.crt"),
             "-CAkey", str(authority / "ca.key"), "-sha256", "-days", str(days),
             "-set_serial", str(secrets.randbits(63) | 1), "-extfile", str(extensions),
             "-out", str(issued))


@_bounded_crypto
def ensure_leaf(
    authority: Path,
    certificate: Path,
    key: Path,
    wifi_certificate: Path,
    *,
    names: Sequence[str],
    addresses: Sequence[str],
    neutral_names: Sequence[str] = (DEFAULT_LOCAL_NAME, LEGACY_LOCAL_NAME),
    neutral_addresses: Sequence[str] = (GATEWAY_ADDRESS,),
    days: int = LEAF_DAYS,
) -> LeafResult:
    """Issue the dashboard certificates from the authority when they need it.

    The full certificate names every way to reach the dashboard. The Wi-Fi
    certificate names only the neutral names and the gateway address, so test
    Wi-Fi devices learn nothing else before they sign in. Both share one key
    and both are issued again when the dashboard name changes.
    """
    suffixes, networks = _permitted(authority)
    notes = []
    clean_names = []
    for name in names:
        name = name.strip().lower().rstrip(".")
        if not _name_allowed(name, suffixes):
            raise TrustError("the authority cannot vouch for %s" % name)
        if name not in clean_names:
            clean_names.append(name)

    def usable(addresses_in: Iterable[str]) -> list:
        kept = []
        for item in addresses_in:
            try:
                parsed = ipaddress.ip_address(item)
            except ValueError:
                notes.append("%s is not an address and was left out" % item)
                continue
            if not any(parsed in network for network in networks if network.version == parsed.version):
                notes.append("%s is outside the ranges the authority covers and was left out" % item)
            elif item not in kept:
                kept.append(item)
        return kept

    full_addresses = usable(addresses)
    wifi_names = []
    for name in neutral_names:
        name = _neutral_name(name)
        if not _name_allowed(name, suffixes):
            raise TrustError("the authority cannot vouch for %s" % name)
        if name not in wifi_names:
            wifi_names.append(name)
    wifi_addresses = usable(neutral_addresses)
    wanted_full = {"DNS:" + name for name in clean_names} | {"IP Address:" + a for a in full_addresses}
    wanted_wifi = {"DNS:" + name for name in wifi_names} | {"IP Address:" + a for a in wifi_addresses}

    key_ok = key.is_file() and "prime256v1" in _openssl(
        "pkey", "-in", str(key), "-noout", "-text", check=False).stdout.decode()
    need_full = not key_ok or not _current(authority, certificate, key, wanted_full)
    need_wifi = not key_ok or not _current(authority, wifi_certificate, key, wanted_wifi)
    if not need_full and not need_wifi:
        return LeafResult(False, tuple(notes))
    certificate.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=str(certificate.parent)) as scratch:
        scratch_path = Path(scratch)
        working_key = key
        if not key_ok:
            working_key = scratch_path / "dashboard.key"
            _openssl("ecparam", "-name", "prime256v1", "-genkey", "-noout", "-out", str(working_key))
            os.chmod(working_key, 0o600)
        if need_full:
            _issue(authority, working_key, certificate, clean_names, full_addresses, days, scratch_path)
        if need_wifi:
            _issue(authority, working_key, wifi_certificate, wifi_names, wifi_addresses, days, scratch_path)
        if not key_ok:
            _replace(working_key, key, 0o600)
        if need_full:
            _replace(scratch_path / (certificate.name + ".new"), certificate, 0o644)
        if need_wifi:
            _replace(scratch_path / (wifi_certificate.name + ".new"), wifi_certificate, 0o644)
    return LeafResult(True, tuple(notes))


def certificate_download(certificate: Path) -> bytes:
    """The authority certificate as a plain PEM file for the .crt download."""
    match = re.search(
        rb"-----BEGIN CERTIFICATE-----.+?-----END CERTIFICATE-----", _regular_bytes(certificate), re.S)
    if match is None:
        raise TrustError("the authority certificate is missing")
    return match.group(0) + b"\n"


def apple_profile(certificate: Path) -> bytes:
    """An Apple configuration profile that installs the authority as a trusted root."""
    der = ssl.PEM_cert_to_DER_cert(certificate_download(certificate).decode("ascii"))
    seed = hashlib.sha256(der).hexdigest()
    profile = {
        "PayloadType": "Configuration",
        "PayloadVersion": 1,
        "PayloadIdentifier": "local.dashboard.authority",
        "PayloadUUID": str(uuid.uuid5(uuid.NAMESPACE_URL, "profile:" + seed)).upper(),
        "PayloadDisplayName": PROFILE_NAME,
        "PayloadDescription": "Lets this device open the local dashboard without a warning.",
        "PayloadOrganization": "Local network",
        "PayloadRemovalDisallowed": False,
        "PayloadContent": [{
            "PayloadType": "com.apple.security.root",
            "PayloadVersion": 1,
            "PayloadIdentifier": "local.dashboard.authority.certificate",
            "PayloadUUID": str(uuid.uuid5(uuid.NAMESPACE_URL, "certificate:" + seed)).upper(),
            "PayloadDisplayName": PROFILE_NAME,
            "PayloadContent": der,
        }],
    }
    return plistlib.dumps(profile, fmt=plistlib.FMT_XML)


@_bounded_crypto
@_serialized_prepare
def prepare(config_dir: Path, *, host_name: str, neutral_name: str,
            ethernet_ip: Optional[str], budget=None, run=None,
            allow_adoption=True) -> Tuple[bool, LeafResult]:
    """Do the installer's work: keep the authority, renew the certificates."""
    neutral = validate_local_name(neutral_name)
    config_dir.mkdir(parents=True, exist_ok=True)
    _check_directory(config_dir)
    from .updates import (
        UpdateError, _certificate_live_files, _certificate_directory, _certificate_pointer,
        publish_certificate_adoption,
        verify_certificate_identity,
    )
    addresses = [GATEWAY_ADDRESS]
    if ethernet_ip and ethernet_ip != "127.0.0.1":
        addresses.insert(0, ethernet_ip)
    try:
        if not allow_adoption and os.path.lexists(config_dir / ".certificate-adoption"):
            root = _certificate_directory(config_dir / ".certificate-adoption", private=True)
            namespace = _certificate_directory(root / "certificate-recovery", private=True)
            pointer = _certificate_pointer(namespace)
            if pointer and pointer["active"]:
                raise TrustError("finish certificate adoption through the verified installer before changing the dashboard name")
        if os.path.lexists(config_dir / ".certificate-adoption/certificate-recovery/current.json"):
            if publish_certificate_adoption(config_dir, required_names=(neutral,),
                                            required_addresses=(GATEWAY_ADDRESS,), deadline=_CRYPTO_DEADLINE.get()):
                alternatives = _alternative_names(config_dir / "dashboard.crt") or ()
                notes = (() if not ethernet_ip or "IP Address:" + ethernet_ip in alternatives else
                         ("The changed Ethernet IP address will be refreshed after the owned adoption passes health.",))
                return False, LeafResult(True, notes)
    except (UpdateError, OSError) as error:
        _propagate_special_update_error(error)
        raise TrustError("the earlier certificate adoption must finish with its owned identity") from error
    old_names = set()
    files = None
    kind = None
    if not os.path.lexists(config_dir / "ca"):
        try:
            files = _certificate_live_files(config_dir)
            kind = verify_certificate_identity(files, deadline=_CRYPTO_DEADLINE.get())
        except (UpdateError, OSError) as error:
            _propagate_special_update_error(error)
            raise TrustError("the existing dashboard identity must be verified and retained before adoption") from error
    if os.path.lexists(config_dir / "dashboard.crt"):
        old_names = {item[4:] for item in (_alternative_names(config_dir / "dashboard.crt") or ())
                     if item.startswith("DNS:")}
        if os.path.lexists(config_dir / "dashboard-wifi.crt"):
            old_names -= {item[4:] for item in (_alternative_names(config_dir / "dashboard-wifi.crt") or ())
                          if item.startswith("DNS:")}
    aliases = compatibility_names(config_dir / "trust.env")
    if os.path.lexists(config_dir / "trust.env"):
        saved_name = read_local_name(config_dir / "trust.env")
        if saved_name != neutral:
            aliases = tuple(dict.fromkeys((saved_name,) + aliases))
    names = sorted(old_names | {neutral} | set(aliases))
    if kind == "legacy":
        if not allow_adoption:
            raise TrustError("adopt the legacy certificate through the verified installer before changing the dashboard name")
        with tempfile.TemporaryDirectory(prefix=".certificate-adoption-stage-", dir=config_dir) as temporary:
            stage = Path(temporary)
            if "trust.env" in files:
                (stage / "trust.env").write_bytes(files["trust.env"])
                (stage / "trust.env").chmod(0o600)
            ensure_authority(stage / "ca", ethernet_ip=ethernet_ip)
            result = ensure_leaf(stage / "ca", stage / "dashboard.crt", stage / "dashboard.key",
                                 stage / "dashboard-wifi.crt", names=names, addresses=addresses,
                                 neutral_names=(neutral,) + aliases, neutral_addresses=(GATEWAY_ADDRESS,))
            try:
                publish_certificate_adoption(config_dir, _certificate_live_files(stage),
                                             required_names=(neutral,), required_addresses=addresses,
                                             deadline=_CRYPTO_DEADLINE.get())
            except (UpdateError, OSError) as error:
                _propagate_special_update_error(error)
                raise TrustError("the owned certificate adoption could not be published") from error
            return True, result
    created = ensure_authority(config_dir / "ca", ethernet_ip=ethernet_ip)
    result = ensure_leaf(
        config_dir / "ca", config_dir / "dashboard.crt", config_dir / "dashboard.key",
        config_dir / "dashboard-wifi.crt",
        names=names, addresses=addresses, neutral_names=(neutral,) + aliases,
        neutral_addresses=(GATEWAY_ADDRESS,))
    return created, result


def detect_ethernet_ip() -> str:
    """The address this Pi uses to reach the internet (no packet is sent)."""
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("1.1.1.1", 53))
        return probe.getsockname()[0]
    except OSError:
        return ""
    finally:
        probe.close()


def set_permissions(config_dir: Path, group: Optional[str]) -> None:
    """Only root reads the authority key. The dashboard group reads the rest."""
    modes = (
        (config_dir / "ca", 0o750, "jittersim"), (config_dir / "ca" / "ca.key", 0o600, "root"),
        (config_dir / "ca" / "ca.crt", 0o644, "root"), (config_dir / "dashboard.key", 0o640, "jittersim"),
        (config_dir / "dashboard.crt", 0o644, "root"), (config_dir / "dashboard-wifi.crt", 0o644, "root"),
    )
    for path, mode, owner_group in modes:
        os.chmod(path, mode)
        if group and hasattr(os, "geteuid") and os.geteuid() == 0:
            shutil.chown(path, user="root", group=group if owner_group == "jittersim" else "root")


def main(argv: Optional[Sequence[str]] = None, *, operation=None, update_root=None) -> int:
    parser = argparse.ArgumentParser(description="Prepare the dashboard certificates.")
    commands = parser.add_subparsers(dest="command", required=True)
    finish = commands.add_parser("finish-adoption")
    finish.add_argument("--config-dir", type=Path, default=Path("/etc/jittersim"))
    recover = commands.add_parser("recover-adoption")
    recover.add_argument("--config-dir", type=Path, default=Path("/etc/jittersim"))
    health = commands.add_parser("check-health")
    health.add_argument("--config-dir", type=Path, default=Path("/etc/jittersim"))
    ensure = commands.add_parser("ensure")
    ensure.add_argument("--config-dir", type=Path, default=Path("/etc/jittersim"))
    ensure.add_argument("--host-name", default=None, help="default: this Pi's name plus .local")
    ensure.add_argument("--neutral-name", default=None, help="default: the saved dashboard name")
    ensure.add_argument("--ethernet-ip", default=None, help="default: found from the route")
    ensure.add_argument("--group", default=None, help="group that reads the dashboard key")
    arguments = parser.parse_args(argv)
    from . import updates
    ownership = operation or getattr(updates, "manual_identity_operation", None)
    try:
        if ownership is None:
            raise updates.UpdateError("the installed update manager cannot safely own a certificate operation")
        with ownership(update_root or updates.UPDATE_ROOT):
            return _execute(arguments)
    except (TrustError, updates.UpdateError, ValueError, OSError) as error:
        _propagate_special_update_error(error)
        print("certificate operation did not finish: %s" % error, file=sys.stderr)
        return 1


def _execute(arguments) -> int:
    if arguments.command in ("finish-adoption", "recover-adoption", "check-health"):
        from .updates import UpdateError, recover_incomplete_certificate_adoption
        try:
            if arguments.command == "recover-adoption":
                if recover_incomplete_certificate_adoption(arguments.config_dir):
                    complete_adoption(arguments.config_dir)
            elif arguments.command == "check-health":
                complete_adoption(arguments.config_dir, force_health=True)
            elif complete_adoption(arguments.config_dir):
                print("completed")
        except (TrustError, UpdateError, ValueError, OSError) as error:
            _propagate_special_update_error(error)
            print("certificate adoption did not finish: %s" % error, file=sys.stderr)
            return 1
        return 0
    try:
        host_name = arguments.host_name or socket.gethostname().split(".")[0].lower() + ".local"
        neutral = arguments.neutral_name or read_local_name(arguments.config_dir / "trust.env")
        ethernet = detect_ethernet_ip() if arguments.ethernet_ip is None else arguments.ethernet_ip
        created, result = prepare(
            arguments.config_dir, host_name=host_name,
            neutral_name=neutral, ethernet_ip=ethernet or None)
        set_permissions(arguments.config_dir, arguments.group)
    except (TrustError, ValueError, OSError) as error:
        print("certificate setup failed: %s" % error, file=sys.stderr)
        return 1
    print("authority: %s" % ("created" if created else "kept"))
    print("dashboard certificate: %s" % ("issued" if result.reissued else "kept"))
    for note in result.notes:
        print("note: " + note)
    return 0


@_bounded_crypto
def complete_adoption(config_dir, *, force_health=False, health=None, budget=None, run=None):
    """Retire a manual installation transition only after actual dashboard health."""
    from .updates import (
        UpdateError, _certificate_directory, _certificate_pointer, healthy,
        finish_certificate_recovery, _CERTIFICATE_TIME, run_command,
    )
    root = config_dir / ".certificate-adoption"
    retained = None
    if os.path.lexists(root):
        _certificate_directory(root, private=True)
        namespace = _certificate_directory(root / "certificate-recovery", private=True)
        retained = _certificate_pointer(namespace)
        if retained is not None and not retained["active"]:
            retained = None
    if retained is None and not force_health:
        return False
    try:
        _remaining()
        inherited_budget = _CERTIFICATE_TIME.get()[1]
        check_health = health or (lambda: healthy(config=config_dir, run=run or run_command,
                                                  budget=inherited_budget))
        if not check_health():
            raise TrustError("the owned certificate adoption has not passed health")
        _remaining()
        if retained is not None:
            finish_certificate_recovery(root, retained=retained, deadline=_CRYPTO_DEADLINE.get())
        return retained is not None
    except UpdateError as error:
        _propagate_special_update_error(error)
        raise TrustError("the owned certificate adoption has not completed") from error


if __name__ == "__main__":
    sys.exit(main())
