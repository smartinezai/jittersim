"""The one neutral address that opens the dashboard on every network.

The Pi announces this name with mDNS on the home router's network and on the
test Wi-Fi, so a browser keeps one address (and one remembered sign-in) whichever
network the computer is on. It is one label followed by `.local` and never
contains the product name, because anyone on the test Wi-Fi can read it.
"""

import argparse
import os
import re
import shutil
import stat
import sys
import tempfile
from pathlib import Path
from typing import Optional, Tuple

DEFAULT_LOCAL_NAME = "netlab.local"
LOCAL_NAME_SUFFIX = ".local"
# The earlier test Wi-Fi name. The Pi still answers it from its own resolver and
# the certificate still names it, so bookmarks made with it keep working.
# `home.arpa` is reserved for private home networks (RFC 8375).
LEGACY_LOCAL_NAME = "pi.home.arpa"
GATEWAY_ADDRESS = "192.168.50.1"
CONFIG_DIR = Path("/etc/jittersim")
CONFIG_FILE = CONFIG_DIR / "trust.env"
SHARED_DNS_DIR = Path("/etc/NetworkManager/dnsmasq-shared.d")
SHARED_DNS_FILE = "jittersim-dashboard-name.conf"
SHARED_DHCP_FILE = "jittersim-shared-dhcp.conf"
CONFIG_KEY = "JITTERSIM_DASHBOARD_NAME"
# The key an earlier build of this feature used. It is still read when valid.
EARLIER_CONFIG_KEY = "JITTERSIM_LOCAL_NAME"
_LABEL = re.compile(r"[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?")


def validate_local_name(value: str) -> str:
    """Return the normalised name or raise ValueError saying what is wrong."""
    name = str(value).strip().lower().rstrip(".")
    if not name.endswith(LOCAL_NAME_SUFFIX) or len(name) <= len(LOCAL_NAME_SUFFIX):
        raise ValueError("the dashboard name must be one word followed by " + LOCAL_NAME_SUFFIX)
    label = name[: -len(LOCAL_NAME_SUFFIX)]
    if not _LABEL.fullmatch(label):
        raise ValueError(
            "the dashboard name must be one word of letters, digits and hyphens "
            "(at most 63) followed by " + LOCAL_NAME_SUFFIX)
    if "jittersim" in name.replace("-", ""):
        # Anyone on the test Wi-Fi can read this name before signing in.
        raise ValueError("the dashboard name must stay neutral")
    return name


def _saved_values(path: Path) -> dict:
    values = {}
    for line in _read_text(path).splitlines():
        key, separator, value = line.partition("=")
        if separator:
            if key.strip() in (CONFIG_KEY, EARLIER_CONFIG_KEY) and key.strip() in values:
                raise ValueError("the saved dashboard name is repeated")
            values[key.strip()] = value.strip().strip("'\"")
    return values


def _read_text(path: Path) -> str:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) |
                         getattr(os, "O_NONBLOCK", 0))
    try:
        info = os.fstat(descriptor)
        if (not stat.S_ISREG(info.st_mode) or info.st_size > 64 * 1024 or
                info.st_uid not in (0, os.geteuid()) or info.st_mode & 0o022):
            raise ValueError("the saved dashboard name must be a bounded administrator-owned file")
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            content = handle.read(64 * 1024 + 1)
        if len(content) > 64 * 1024:
            raise ValueError("the saved dashboard name is too large")
        return content.decode("utf-8")
    finally:
        os.close(descriptor)


def _legacy_name(value: str) -> str:
    name = str(value).strip().lower().rstrip(".")
    if name.endswith(".local"):
        return validate_local_name(name)
    if not name.endswith(".home.arpa"):
        raise ValueError("the saved legacy name must end in .home.arpa or .local")
    label = name[:-len(".home.arpa")]
    if not _LABEL.fullmatch(label) or "jittersim" in label.replace("-", ""):
        raise ValueError("the saved legacy dashboard name is invalid")
    return name


def _names(path: Path) -> Tuple[str, Tuple[str, ...]]:
    try:
        values = _saved_values(path)
    except FileNotFoundError:
        return DEFAULT_LOCAL_NAME, (LEGACY_LOCAL_NAME,)
    earlier = _legacy_name(values[EARLIER_CONFIG_KEY]) if EARLIER_CONFIG_KEY in values else None
    if CONFIG_KEY in values:
        name = validate_local_name(values[CONFIG_KEY])
    elif earlier and earlier.endswith(".local"):
        name = earlier
    else:
        name = DEFAULT_LOCAL_NAME
    aliases = tuple(dict.fromkeys(item for item in (earlier, LEGACY_LOCAL_NAME)
                                  if item and item != name))
    return name, aliases


def read_local_name(path: Optional[Path] = None) -> str:
    """Keep a configured name or use the default only when its setting is absent."""
    return _names(path or CONFIG_FILE)[0]


def compatibility_names(path: Optional[Path] = None) -> Tuple[str, ...]:
    """Existing neutral aliases retained for DNS and certificate compatibility."""
    return _names(path or CONFIG_FILE)[1]


def resolver_names(name: str, aliases: Optional[Tuple[str, ...]] = None) -> Tuple[str, ...]:
    """The names the Pi's own resolvers answer with the test Wi-Fi address."""
    kept = compatibility_names() if aliases is None else aliases
    return tuple(dict.fromkeys((validate_local_name(name),) + tuple(_legacy_name(item) for item in kept)))


def render_shared_dnsmasq(name: str, aliases: Optional[Tuple[str, ...]] = None) -> str:
    """Answer the name from the resolver NetworkManager shares on the test Wi-Fi.

    That resolver serves test devices whenever access rules are off. With rules
    on, the filtered resolver adds the same record itself and forwards the
    rest here.
    """
    return "".join("address=/%s/%s\n" % (item, GATEWAY_ADDRESS) for item in resolver_names(name, aliases))


def render_shared_dhcp() -> str:
    """Settings for the DHCP server NetworkManager runs on the test Wi-Fi.

    That dnsmasq is the only DHCP server on the test Wi-Fi. Without
    `dhcp-authoritative` it silently ignores a device that asks again for an
    address it has no lease for. After the Pi copies the router's Wi-Fi name
    that is every household device: each one asks for its router address
    (192.168.0.x), gets no answer and may keep that address, associated but
    without internet. Authoritative mode refuses it at once (DHCPNAK) so the
    device asks for a test Wi-Fi address, and it confirms a test Wi-Fi address
    whose lease record was lost.
    """
    return ("# Written by JitterSim (jittersim.localname). NetworkManager reads it when\n"
            "# the test Wi-Fi starts. Its dnsmasq is the only DHCP server there.\n"
            "dhcp-authoritative\n")


def _write(path: Path, content: str, mode: int, group: Optional[str]) -> bool:
    """Write the file when it differs. True when it changed."""
    try:
        changed = _read_text(path) != content
    except FileNotFoundError:
        changed = True
    if changed:
        descriptor, temporary = tempfile.mkstemp(prefix="." + path.name + ".", dir=path.parent)
        try:
            with os.fdopen(descriptor, "w") as handle:
                os.fchmod(handle.fileno(), mode)
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            if group and os.geteuid() == 0:
                shutil.chown(temporary, user="root", group=group)
            os.replace(temporary, path)
            directory = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            Path(temporary).unlink(missing_ok=True)
    os.chmod(path, mode)
    if group and hasattr(os, "geteuid") and os.geteuid() == 0:
        shutil.chown(path, user="root", group=group)
    return changed


def _with_name(existing: str, name: str) -> str:
    """The file text with the name set under the current key, other lines kept."""
    lines, placed = [], False
    for line in existing.splitlines():
        key = line.partition("=")[0].strip()
        if key == CONFIG_KEY:
            if not placed:
                lines.append("%s=%s" % (CONFIG_KEY, name))
                placed = True
            continue
        lines.append(line)
    if not placed:
        lines.append("%s=%s" % (CONFIG_KEY, name))
    return "\n".join(lines) + "\n"


def set_local_name(value: str, config_dir: Path = CONFIG_DIR, group: Optional[str] = None) -> str:
    """Validate and save the dashboard name. Nothing is saved when it is invalid."""
    name = validate_local_name(value)
    config_file = config_dir / "trust.env"
    try:
        existing = _read_text(config_file)
        _names(config_file)
    except FileNotFoundError:
        existing = ""
    _write(config_file, _with_name(existing, name), 0o640, group)
    return name


def install_config(
    config_dir: Path = CONFIG_DIR,
    dns_dir: Path = SHARED_DNS_DIR,
    group: Optional[str] = None,
) -> Tuple[str, bool]:
    """Keep the name setting and publish it to the shared test Wi-Fi resolver.

    Also keeps the test Wi-Fi DHCP settings (`render_shared_dhcp`). Returns the
    name and whether the resolver record or the DHCP settings changed. The
    resolver reads both when the test Wi-Fi starts, so a change needs a restart
    of that connection.
    """
    config_file = config_dir / "trust.env"
    if not os.path.lexists(config_file):
        _write(config_file, "%s=%s\n" % (CONFIG_KEY, DEFAULT_LOCAL_NAME), 0o640, group)
    name = read_local_name(config_file)
    saved = _read_text(config_file)
    if not any(line.partition("=")[0].strip() == CONFIG_KEY for line in saved.splitlines()):
        # Keep the earlier alias while adding the canonical announcement name.
        _write(config_file, _with_name(saved, name), 0o640, group)
    dns_dir.mkdir(parents=True, exist_ok=True)
    changed = _write(dns_dir / SHARED_DNS_FILE,
                     render_shared_dnsmasq(name, compatibility_names(config_file)), 0o644, group)
    dhcp_changed = _write(dns_dir / SHARED_DHCP_FILE, render_shared_dhcp(), 0o644, group)
    return name, changed or dhcp_changed


def main(argv: Optional[list] = None) -> int:
    """Small commands for the installer and the dashboard update."""
    parser = argparse.ArgumentParser(description="The one dashboard name.")
    parser.add_argument("command", choices=("name", "dnsmasq", "install"))
    parser.add_argument("--file", type=Path, default=CONFIG_FILE)
    parser.add_argument("--config-dir", type=Path, default=CONFIG_DIR)
    parser.add_argument("--dns-dir", type=Path, default=SHARED_DNS_DIR)
    parser.add_argument("--group", default=None)
    arguments = parser.parse_args(argv)
    if arguments.command == "install":
        name, changed = install_config(arguments.config_dir, arguments.dns_dir, arguments.group)
        sys.stdout.write("%s\n%s\n" % (name, "changed" if changed else "unchanged"))
        return 0
    name = read_local_name(arguments.file)
    sys.stdout.write(name + "\n" if arguments.command == "name" else render_shared_dnsmasq(name))
    return 0


if __name__ == "__main__":
    sys.exit(main())
