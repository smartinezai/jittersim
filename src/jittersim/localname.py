"""The neutral address that test Wi-Fi devices use to reach the dashboard."""

import argparse
import os
import re
import shutil
import sys
from pathlib import Path
from typing import Optional, Tuple

DEFAULT_LOCAL_NAME = "pi.home.arpa"
# `home.arpa` is reserved for private home networks (RFC 8375), so the name can
# never collide with a public site. The local certificate authority is limited
# to the same suffix.
LOCAL_NAME_SUFFIX = ".home.arpa"
GATEWAY_ADDRESS = "192.168.50.1"
CONFIG_DIR = Path("/etc/jittersim")
CONFIG_FILE = CONFIG_DIR / "trust.env"
SHARED_DNS_DIR = Path("/etc/NetworkManager/dnsmasq-shared.d")
SHARED_DNS_FILE = "jittersim-dashboard-name.conf"
CONFIG_KEY = "JITTERSIM_LOCAL_NAME"
_LABEL = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")


def validate_local_name(value: str) -> str:
    """Return the normalised name or raise ValueError saying what is wrong."""
    name = str(value).strip().lower().rstrip(".")
    if not name.endswith(LOCAL_NAME_SUFFIX) or len(name) > 253:
        raise ValueError("the dashboard name must end with " + LOCAL_NAME_SUFFIX)
    labels = name.split(".")
    if len(labels) < 3 or not all(_LABEL.match(label) for label in labels):
        raise ValueError("the dashboard name has an invalid part")
    if "jittersim" in name:
        # Anyone on the test Wi-Fi can read this name before signing in.
        raise ValueError("the dashboard name must stay neutral")
    return name


def read_local_name(path: Optional[Path] = None) -> str:
    """The configured name, or the default when the file is missing or invalid."""
    try:
        for line in (path or CONFIG_FILE).read_text().splitlines():
            key, separator, value = line.partition("=")
            if separator and key.strip() == CONFIG_KEY:
                return validate_local_name(value.strip().strip("'\""))
    except (OSError, ValueError):
        pass
    return DEFAULT_LOCAL_NAME


def render_shared_dnsmasq(name: str) -> str:
    """Answer the name from the resolver NetworkManager shares on the test Wi-Fi.

    That resolver serves test devices whenever access rules are off. With rules
    on, the filtered resolver adds the same record itself and forwards the
    rest here.
    """
    return "address=/%s/%s\n" % (validate_local_name(name), GATEWAY_ADDRESS)


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
    dns_dir.mkdir(parents=True, exist_ok=True)
    changed = _write(dns_dir / SHARED_DNS_FILE, render_shared_dnsmasq(name), 0o644, group)
    return name, changed


def main(argv: Optional[list] = None) -> int:
    """Small commands for the installer and the dashboard update."""
    parser = argparse.ArgumentParser(description="Dashboard name for the test Wi-Fi.")
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
