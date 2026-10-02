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
import sys
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
    for line in path.read_text().splitlines():
        key, separator, value = line.partition("=")
        if separator:
            values[key.strip()] = value.strip().strip("'\"")
    return values


def read_local_name(path: Optional[Path] = None) -> str:
    """The configured name, or the default when the file is missing or invalid."""
    try:
        values = _saved_values(path or CONFIG_FILE)
    except OSError:
        return DEFAULT_LOCAL_NAME
    for key in (CONFIG_KEY, EARLIER_CONFIG_KEY):
        if key in values:
            try:
                return validate_local_name(values[key])
            except ValueError:
                if key == CONFIG_KEY:
                    break
    return DEFAULT_LOCAL_NAME


def resolver_names(name: str) -> Tuple[str, ...]:
    """The names the Pi's own resolvers answer with the test Wi-Fi address."""
    return (validate_local_name(name), LEGACY_LOCAL_NAME)


def render_shared_dnsmasq(name: str) -> str:
    """Answer the name from the resolver NetworkManager shares on the test Wi-Fi.

    That resolver serves test devices whenever access rules are off. With rules
    on, the filtered resolver adds the same record itself and forwards the
    rest here.
    """
    return "".join("address=/%s/%s\n" % (item, GATEWAY_ADDRESS) for item in resolver_names(name))


def _write(path: Path, content: str, mode: int, group: Optional[str]) -> bool:
    """Write the file when it differs. True when it changed."""
    try:
        changed = path.read_text() != content
    except OSError:
        changed = True
    if changed:
        temporary = path.with_name("." + path.name + ".new")
        temporary.write_text(content)
        os.replace(temporary, path)
    os.chmod(path, mode)
    if group and hasattr(os, "geteuid") and os.geteuid() == 0:
        try:
            shutil.chown(path, user="root", group=group)
        except (LookupError, OSError):
            pass
    return changed


def _with_name(existing: str, name: str) -> str:
    """The file text with the name set under the current key, other lines kept."""
    lines, placed = [], False
    for line in existing.splitlines():
        key = line.partition("=")[0].strip()
        if key in (CONFIG_KEY, EARLIER_CONFIG_KEY):
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
        existing = config_file.read_text()
    except OSError:
        existing = ""
    _write(config_file, _with_name(existing, name), 0o640, group)
    return name


def install_config(
    config_dir: Path = CONFIG_DIR,
    dns_dir: Path = SHARED_DNS_DIR,
    group: Optional[str] = None,
) -> Tuple[str, bool]:
    """Keep the name setting and publish it to the shared test Wi-Fi resolver.

    Returns the name and whether the resolver record changed. The resolver
    reads its record when the test Wi-Fi starts, so a change needs a restart of
    that connection.
    """
    config_file = config_dir / "trust.env"
    if not config_file.exists():
        _write(config_file, "%s=%s\n" % (CONFIG_KEY, DEFAULT_LOCAL_NAME), 0o640, group)
    name = read_local_name(config_file)
    try:
        saved = config_file.read_text()
    except OSError:
        saved = ""
    if not any(line.partition("=")[0].strip() == CONFIG_KEY for line in saved.splitlines()):
        # Move a name saved under the earlier key (or none) to the current key.
        _write(config_file, _with_name(saved, name), 0o640, group)
    dns_dir.mkdir(parents=True, exist_ok=True)
    changed = _write(dns_dir / SHARED_DNS_FILE, render_shared_dnsmasq(name), 0o644, group)
    return name, changed


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
