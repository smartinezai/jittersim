"""A small local certificate authority for the dashboard's HTTPS certificate.

The authority is created once and kept. It may only vouch for the dashboard
names and the two address ranges in use, so a device that trusts it cannot be
fooled about other sites. The dashboard certificate is issued from it and is
renewed without changing the authority. Everything here calls the `openssl`
program and needs no root rights by itself.
"""

import argparse
import hashlib
import ipaddress
import os
import plistlib
import re
import secrets
import shutil
import socket
import ssl
import subprocess
import sys
import tempfile
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional, Sequence, Tuple

from .localname import DEFAULT_LOCAL_NAME, GATEWAY_ADDRESS, read_local_name, validate_local_name

CA_DAYS = 7300
LEAF_DAYS = 365
RENEW_SECONDS = 30 * 24 * 3600
TEST_NETWORK = "192.168.50.0/24"
# Names shown on devices that trust the authority. They stay neutral.
CA_NAME = "Local Dashboard Authority"
PROFILE_NAME = "Local Dashboard Certificate"
_PRIVATE_BLOCKS = ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")
_NAME_SUFFIXES = ("local", "home.arpa")


class TrustError(Exception):
    """The certificate files could not be prepared."""


@dataclass
class LeafResult:
    reissued: bool
    notes: Tuple[str, ...] = field(default_factory=tuple)


def _openssl(*arguments: str, check: bool = True, data: Optional[bytes] = None):
    try:
        completed = subprocess.run(
            ("openssl",) + arguments, input=data, capture_output=True, timeout=60)
    except (OSError, subprocess.SubprocessError) as error:
        raise TrustError("openssl could not run: %s" % error) from error
    if check and completed.returncode != 0:
        raise TrustError("openssl %s failed: %s" % (
            arguments[0], completed.stderr.decode("utf-8", "replace").strip()[:200]))
    return completed


def _text(path: Path) -> str:
    return _openssl("x509", "-in", str(path), "-noout", "-text").stdout.decode()


def _replace(source: Path, destination: Path, mode: int) -> None:
    os.chmod(source, mode)
    os.replace(source, destination)


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
        completed = _openssl("x509", "-in", str(path), "-noout", "-pubkey", check=False)
    else:
        completed = _openssl("pkey", "-in", str(path), "-pubout", check=False)
    return completed.stdout.decode() if completed.returncode == 0 else ""


def authority_valid(directory: Path) -> bool:
    """True when the saved authority is usable, constrained and matches its key."""
    certificate, key = directory / "ca.crt", directory / "ca.key"
    try:
        if not certificate.is_file() or not key.is_file():
            return False
        text = _text(certificate)
        if "CA:TRUE" not in text or "Name Constraints" not in text or "prime256v1" not in text:
            return False
        if _openssl("x509", "-in", str(certificate), "-noout", "-checkend", "86400",
                    check=False).returncode != 0:
            return False
        public = _public_key(certificate, True)
        return bool(public) and public == _public_key(key, False)
    except (TrustError, OSError):
        return False


def ensure_authority(directory: Path, *, ethernet_ip: Optional[str] = None) -> bool:
    """Create the authority when it is missing or invalid. True when created."""
    if authority_valid(directory):
        return False
    directory.mkdir(parents=True, exist_ok=True)
    os.chmod(directory, 0o700)
    with tempfile.TemporaryDirectory(dir=str(directory)) as scratch:
        scratch_path = Path(scratch)
        key, certificate, config = (
            scratch_path / "ca.key", scratch_path / "ca.crt", scratch_path / "ca.cnf")
        _openssl("ecparam", "-name", "prime256v1", "-genkey", "-noout", "-out", str(key))
        os.chmod(key, 0o600)
        config.write_text(_authority_config(ethernet_ip))
        _openssl("req", "-new", "-x509", "-key", str(key), "-sha256", "-days", str(CA_DAYS),
                 "-config", str(config), "-out", str(certificate))
        _replace(key, directory / "ca.key", 0o600)
        _replace(certificate, directory / "ca.crt", 0o644)
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


def ensure_leaf(
    authority: Path,
    certificate: Path,
    key: Path,
    wifi_certificate: Path,
    *,
    names: Sequence[str],
    addresses: Sequence[str],
    neutral_names: Sequence[str] = (DEFAULT_LOCAL_NAME,),
    neutral_addresses: Sequence[str] = (GATEWAY_ADDRESS,),
    days: int = LEAF_DAYS,
) -> LeafResult:
    """Issue the dashboard certificates from the authority when they need it.

    The full certificate names every way to reach the dashboard. The Wi-Fi
    certificate names only the neutral name and the gateway address, so test
    Wi-Fi devices learn nothing else before they sign in. Both share one key.
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
    wifi_names = [validate_local_name(name) for name in neutral_names]
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
        rb"-----BEGIN CERTIFICATE-----.+?-----END CERTIFICATE-----", certificate.read_bytes(), re.S)
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


def prepare(config_dir: Path, *, host_name: str, neutral_name: str,
            ethernet_ip: Optional[str]) -> Tuple[bool, LeafResult]:
    """Do the installer's work: keep the authority, renew the certificates."""
    neutral = validate_local_name(neutral_name)
    created = ensure_authority(config_dir / "ca", ethernet_ip=ethernet_ip)
    addresses = [GATEWAY_ADDRESS]
    if ethernet_ip and ethernet_ip != "127.0.0.1":
        addresses.insert(0, ethernet_ip)
    names = [host_name, "jittersim.local", neutral]
    result = ensure_leaf(
        config_dir / "ca", config_dir / "dashboard.crt", config_dir / "dashboard.key",
        config_dir / "dashboard-wifi.crt",
        names=names, addresses=addresses, neutral_names=(neutral,),
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
            try:
                shutil.chown(path, user="root", group=group if owner_group == "jittersim" else "root")
            except (LookupError, OSError):
                pass


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Prepare the dashboard certificates.")
    commands = parser.add_subparsers(dest="command", required=True)
    ensure = commands.add_parser("ensure")
    ensure.add_argument("--config-dir", type=Path, default=Path("/etc/jittersim"))
    ensure.add_argument("--host-name", default=None, help="default: this Pi's name plus .local")
    ensure.add_argument("--neutral-name", default=None, help="default: the saved dashboard name")
    ensure.add_argument("--ethernet-ip", default=None, help="default: found from the route")
    ensure.add_argument("--group", default=None, help="group that reads the dashboard key")
    arguments = parser.parse_args(argv)
    host_name = arguments.host_name or socket.gethostname().split(".")[0].lower() + ".local"
    neutral = arguments.neutral_name or read_local_name(arguments.config_dir / "trust.env")
    ethernet = detect_ethernet_ip() if arguments.ethernet_ip is None else arguments.ethernet_ip
    try:
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


if __name__ == "__main__":
    sys.exit(main())
