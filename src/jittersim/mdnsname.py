"""Announce the one neutral dashboard name on the router network and the test Wi-Fi.

Avahi publishes an address record on every network unless the record is bound to
one interface. Its command line tools and its static hosts file cannot bind a
record, so this module talks to Avahi over D-Bus, where each record names the
interface it belongs to. The Ethernet record carries the current Ethernet
address and the test Wi-Fi record carries 192.168.50.1, so a device on either
network gets the address that works for it and learns nothing else about the Pi.

Avahi's own publishing (host name, services, workstation record) is switched off
because Avahi cannot limit it to one network. Existing certificate aliases can
still be announced on Ethernet. Standard library only: the publisher
runs with the system Python, which provides the `dbus` module.

Everything here is optional. If Avahi or D-Bus is missing the dashboard keeps
working by its IP address and the problem is written to a status file that the
dashboard and `jittersim diagnostics` show in plain words.
"""

import argparse
import contextvars
import functools
import ipaddress
import json
import math
import os
import re
import signal
import socket
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from .localname import GATEWAY_ADDRESS, _read_text, compatibility_names, read_local_name

STATUS_FILE = Path("/run/jittersim-mdns/status.json")
RUNTIME_ENV = Path("/etc/jittersim/runtime.env")
TRUST_ENV = Path("/etc/jittersim/trust.env")
AVAHI_CONFIG = Path("/etc/avahi/avahi-daemon.conf")
AVAHI_SERVICES = Path("/etc/avahi/services")
ORIGINAL_SUFFIX = ".jittersim-original"
DISABLED_SUFFIX = ".jittersim-disabled"

# Avahi entry group states (AvahiEntryGroupState).
UNCOMMITED, REGISTERING, ESTABLISHED, COLLISION, FAILURE = 0, 1, 2, 3, 4
# Publish flags and protocol numbers of the Avahi D-Bus API.
PUBLISH_NO_REVERSE = 16
PROTOCOL_IPV4 = 0

POLL_SECONDS = 5.0
RETRY_SECONDS = 60.0
STATUS_REFRESH_SECONDS = 30.0
STATUS_STALE_SECONDS = 90.0
MAX_STATUS_BYTES = 32 * 1024
STATES = ("published", "partial", "waiting", "conflict", "unavailable")
_STEP_DEADLINE = contextvars.ContextVar("mdns_step_deadline", default=None)
DBUS_SECONDS = 2.0
STEP_SECONDS = 10.0

_INTERFACE = re.compile(r"[A-Za-z0-9_.:][A-Za-z0-9_.:-]{0,14}")
_HOST_LABEL = re.compile(r"[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?")
LINK_NAMES = {"ethernet": "Ethernet (router network)", "test": "test Wi-Fi"}


class AvahiError(Exception):
    """Avahi or D-Bus could not do what was asked."""


class AvahiCollision(AvahiError):
    """Another device already uses the name."""


class ConfigureError(Exception):
    """The Avahi settings could not be changed."""


def _dbus_timeout():
    deadline = _STEP_DEADLINE.get()
    remaining = DBUS_SECONDS if deadline is None else deadline - time.monotonic()
    if remaining <= 0:
        raise AvahiError("the name announcement exceeded its time allowance")
    return min(DBUS_SECONDS, remaining)


def _bounded_step(function):
    @functools.wraps(function)
    def checked(*args, **kwargs):
        parent = _STEP_DEADLINE.get()
        token = _STEP_DEADLINE.set(min(parent, time.monotonic() + STEP_SECONDS)
                                  if parent is not None else time.monotonic() + STEP_SECONDS)
        try:
            return function(*args, **kwargs)
        finally:
            _STEP_DEADLINE.reset(token)
    return checked


# ---------------------------------------------------------------------------
# Avahi settings
# ---------------------------------------------------------------------------

# What this feature needs from avahi-daemon.conf. Avahi cannot limit its own
# publishing to one network, so it publishes nothing by itself. The publisher
# adds the records, each bound to its own interface.
_REQUIRED = (
    ("server", "use-ipv4", "yes"),
    ("server", "enable-dbus", "yes"),
    ("publish", "disable-publishing", "no"),
    ("publish", "disable-user-service-publishing", "yes"),
    ("publish", "publish-workstation", "no"),
    ("publish", "publish-addresses", "no"),
    ("publish", "publish-hinfo", "no"),
    ("publish", "publish-domain", "no"),
    ("reflector", "enable-reflector", "no"),
)
_SECTION = re.compile(r"\s*\[([A-Za-z0-9_-]+)\]\s*$")
_NOTE = "# Set by JitterSim. The original file is avahi-daemon.conf" + ORIGINAL_SUFFIX


def _setting(line: str) -> Optional[Tuple[str, str]]:
    stripped = line.strip()
    if not stripped or stripped.startswith(("#", ";")) or "=" not in stripped:
        return None
    key, _, value = stripped.partition("=")
    return key.strip(), value.strip()


def _sections(lines: List[str]) -> Dict[str, Tuple[int, int]]:
    """Each section's header line index and the index where the next section begins."""
    found, headers = {}, []
    for index, line in enumerate(lines):
        match = _SECTION.match(line)
        if match:
            headers.append((match.group(1), index))
    for number, (name, start) in enumerate(headers):
        end = headers[number + 1][1] if number + 1 < len(headers) else len(lines)
        found.setdefault(name, (start, end))
    return found


def _find(lines: List[str], section: str, key: str) -> Optional[int]:
    bounds = _sections(lines).get(section)
    if bounds is None:
        return None
    for index in range(bounds[0] + 1, bounds[1]):
        item = _setting(lines[index])
        if item is not None and item[0] == key:
            return index
    return None


def _put(lines: List[str], section: str, key: str, value: str) -> None:
    index = _find(lines, section, key)
    if index is not None:
        if _setting(lines[index])[1] == value:
            return
        lines[index:index + 1] = [_NOTE, "%s=%s" % (key, value)]
        return
    bounds = _sections(lines).get(section)
    if bounds is None:
        if lines and lines[-1].strip():
            lines.append("")
        lines.extend(["[%s]" % section, _NOTE, "%s=%s" % (key, value)])
        return
    lines[bounds[0] + 1:bounds[0] + 1] = [_NOTE, "%s=%s" % (key, value)]


def _interface_lists(lines: List[str], interfaces: Sequence[str]) -> None:
    """Keep both networks visible to Avahi without touching other limits."""
    wanted = [item for item in interfaces if item]
    if not wanted:
        return
    allow = _find(lines, "server", "allow-interfaces")
    if allow is not None:
        listed = [item.strip() for item in _setting(lines[allow])[1].split(",") if item.strip()]
        merged = listed + [item for item in wanted if item not in listed]
        if merged != listed:
            lines[allow:allow + 1] = [_NOTE, "allow-interfaces=" + ",".join(merged)]
    deny = _find(lines, "server", "deny-interfaces")
    if deny is not None:
        listed = [item.strip() for item in _setting(lines[deny])[1].split(",") if item.strip()]
        kept = [item for item in listed if item not in wanted]
        if kept != listed:
            lines[deny:deny + 1] = [_NOTE, "deny-interfaces=" + ",".join(kept)] if kept else []


def render_avahi_config(text: str, interfaces: Sequence[str] = ()) -> str:
    """The Avahi settings with publishing limited to the records this module adds."""
    lines = text.splitlines()
    for section, key, value in _REQUIRED:
        _put(lines, section, key, value)
    _interface_lists(lines, interfaces)
    return "\n".join(lines) + "\n"


def _write_text(path: Path, content: str, mode: int) -> None:
    temporary = path.with_name(path.name + ".jittersim-new")
    temporary.write_text(content)
    os.chmod(temporary, mode)
    os.replace(temporary, path)


def configure_avahi(
    config_path: Path = AVAHI_CONFIG,
    services_dir: Path = AVAHI_SERVICES,
    interfaces: Sequence[str] = (),
) -> bool:
    """Limit Avahi's own publishing. True when a setting or a service file changed.

    The original settings are saved once next to the file and every service file
    Avahi would announce (SSH, SFTP, printers) is renamed so Avahi ignores it.
    `restore_avahi` undoes both.
    """
    try:
        current = config_path.read_text()
    except OSError as error:
        raise ConfigureError("Avahi is not installed (%s)" % config_path) from error
    changed = False
    backup = config_path.with_name(config_path.name + ORIGINAL_SUFFIX)
    wanted = render_avahi_config(current, interfaces)
    if wanted != current:
        try:
            if not backup.exists():
                backup.write_text(current)
                os.chmod(backup, 0o644)
            mode = config_path.stat().st_mode & 0o777
            _write_text(config_path, wanted, mode)
        except OSError as error:
            raise ConfigureError("the Avahi settings could not be saved: %s" % error) from error
        changed = True
    try:
        for service in sorted(services_dir.glob("*.service")):
            target = service.with_name(service.name + DISABLED_SUFFIX)
            if service.is_file() and not target.exists():
                os.replace(service, target)
                changed = True
    except OSError as error:
        raise ConfigureError("the Avahi service files could not be switched off: %s" % error) from error
    return changed


def restore_avahi(
    config_path: Path = AVAHI_CONFIG, services_dir: Path = AVAHI_SERVICES
) -> bool:
    """Put back the Avahi settings and service files as they were before."""
    restored = False
    backup = config_path.with_name(config_path.name + ORIGINAL_SUFFIX)
    if backup.exists():
        try:
            _write_text(config_path, backup.read_text(), config_path.stat().st_mode & 0o777
                        if config_path.exists() else 0o644)
            backup.unlink()
        except OSError as error:
            raise ConfigureError("the Avahi settings could not be restored: %s" % error) from error
        restored = True
    try:
        for disabled in sorted(services_dir.glob("*" + DISABLED_SUFFIX)):
            original = disabled.with_name(disabled.name[: -len(DISABLED_SUFFIX)])
            if not original.exists():
                os.replace(disabled, original)
                restored = True
    except OSError as error:
        raise ConfigureError("the Avahi service files could not be restored: %s" % error) from error
    return restored


# ---------------------------------------------------------------------------
# Addresses
# ---------------------------------------------------------------------------

Runner = Callable[[Sequence[str]], str]


def _run(command: Sequence[str]) -> str:
    try:
        done = subprocess.run(
            list(command), capture_output=True, text=True, timeout=5,
            stdin=subprocess.DEVNULL, check=False)
    except (OSError, subprocess.SubprocessError):
        return ""
    return done.stdout if done.returncode == 0 else ""


def _publishable(value: str) -> Optional[str]:
    """The IPv4 address when it is one a device on the network can reach."""
    try:
        address = ipaddress.IPv4Address(value)
    except ValueError:
        return None
    if address.is_loopback or address.is_link_local or address.is_unspecified or address.is_multicast:
        return None
    return str(address)


def ethernet_address(interface: str, run: Runner = _run) -> Optional[str]:
    """The Ethernet interface's current IPv4 address, or None while it has none."""
    if not _INTERFACE.fullmatch(interface or ""):
        return None
    route = re.search(
        r"\bsrc (\S+)", run(["ip", "-4", "route", "get", "1.1.1.1", "oif", interface]))
    if route and _publishable(route.group(1)):
        return route.group(1)
    listing = run(["ip", "-4", "-o", "addr", "show", "dev", interface, "scope", "global"])
    for found in re.finditer(r"\binet (\d+\.\d+\.\d+\.\d+)/", listing):
        if _publishable(found.group(1)):
            return found.group(1)
    return None


def access_point_address(interface: str, run: Runner = _run) -> Optional[str]:
    """The gateway address, once the test Wi-Fi interface carries it."""
    if not _INTERFACE.fullmatch(interface or ""):
        return None
    listing = run(["ip", "-4", "-o", "addr", "show", "dev", interface])
    return GATEWAY_ADDRESS if re.search(
        r"\binet %s/" % re.escape(GATEWAY_ADDRESS), listing) else None



def _address_of(kind: str, interface: str) -> Optional[str]:
    return ethernet_address(interface) if kind == "ethernet" else access_point_address(interface)


def _index_of(interface: str) -> Optional[int]:
    try:
        return socket.if_nametoindex(interface)
    except (OSError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Avahi over D-Bus
# ---------------------------------------------------------------------------

class DbusAvahi:
    """A connection to avahi-daemon. Records are freed when the connection closes."""

    SERVICE = "org.freedesktop.Avahi"

    def __init__(self) -> None:
        try:
            import dbus
        except ImportError as error:
            raise AvahiError("the python3-dbus package is not installed") from error
        self._dbus = dbus
        try:
            self._bus = dbus.SystemBus()
            self._server_call("GetVersionString")
        except dbus.DBusException as error:
            raise AvahiError("Avahi is not answering on D-Bus") from error

    def _server_call(self, method):
        return self._bus.call_blocking(self.SERVICE, "/", self.SERVICE + ".Server",
                                       method, "", (), timeout=_dbus_timeout())

    def new_group(self) -> "_DbusGroup":
        try:
            path = self._server_call("EntryGroupNew")
        except self._dbus.DBusException as error:
            raise AvahiError("Avahi refused a new record group") from error
        return _DbusGroup(self._dbus, self._bus, path)


class _DbusGroup:
    def __init__(self, dbus_module, bus, path) -> None:
        self._dbus = dbus_module
        self._bus = bus
        self._path = path

    def _call(self, method: str, *arguments, signature=""):
        try:
            return self._bus.call_blocking(
                DbusAvahi.SERVICE, self._path, DbusAvahi.SERVICE + ".EntryGroup",
                method, signature, arguments, timeout=_dbus_timeout())
        except self._dbus.DBusException as error:
            if "Collision" in (error.get_dbus_name() or ""):
                raise AvahiCollision("the name is already in use") from error
            raise AvahiError("Avahi refused the request") from error

    def add_address(self, index: int, name: str, address: str) -> None:
        dbus = self._dbus
        # An explicit interface keeps the record on that network only. The flag
        # leaves out the reverse (PTR) record, which would name the address too.
        self._call(
            "AddAddress", dbus.Int32(index), dbus.Int32(PROTOCOL_IPV4),
            dbus.UInt32(PUBLISH_NO_REVERSE), name, address, signature="iiuss")

    def commit(self) -> None:
        self._call("Commit")

    def state(self) -> int:
        return int(self._call("GetState"))

    def free(self) -> None:
        try:
            self._call("Free")
        except AvahiError:
            pass


# ---------------------------------------------------------------------------
# The publisher
# ---------------------------------------------------------------------------

class _Record:
    """One name on one interface, held in its own Avahi record group."""

    def __init__(self, link: str, interface: str, name: str, address: str, now: float) -> None:
        self.link, self.interface, self.name, self.address = link, interface, name, address
        self.group = None
        self.state = "registering"
        self.since = now


def fix_for_conflict(name: str) -> str:
    return ("Choose another name: sudo jittersim dashboard-name set <other>.local "
            "(or change the name on the other device that uses %s)." % name)


class Publisher:
    """Keeps one record per network, each bound to its own interface."""

    def __init__(
        self,
        *,
        name: str,
        uplink: str,
        test_interface: str,
        extra_names: Sequence[str] = (),
        connect: Callable[[], object] = DbusAvahi,
        address_of: Callable[[str, str], Optional[str]] = _address_of,
        index_of: Callable[[str], Optional[int]] = _index_of,
        status_path: Path = STATUS_FILE,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.name = name
        self._links = (("ethernet", uplink), ("test", test_interface))
        self._extra = tuple(item for item in extra_names if item and item != name)
        if len(self._extra) > 32:
            raise ValueError("too many legacy dashboard aliases")
        self._connect = connect
        self._address_of = address_of
        self._index_of = index_of
        self._status_path = status_path
        self._clock = clock
        self._bus = None
        self._records: Dict[Tuple[str, str], _Record] = {}
        self._written: Optional[str] = None
        self._written_at = 0.0

    # -- one pass ----------------------------------------------------------

    @_bounded_step
    def step(self) -> dict:
        """Compare the wanted records with the published ones, fix them, report."""
        now = self._clock()
        addresses = {
            kind: (self._address_of(kind, interface)
                   if self._index_of(interface) is not None else None)
            for kind, interface in self._links
        }
        try:
            if self._bus is None:
                self._bus = self._connect()
            self._reconcile(addresses, now)
            self._poll(now)
            status = self._status(addresses, now, None)
        except AvahiError as error:
            self._forget()
            status = self._status(addresses, now, str(error))
        self._write(status, now)
        return status

    def _wanted(self, addresses: Dict[str, Optional[str]]) -> Dict[Tuple[str, str], Tuple[str, str]]:
        wanted = {}
        for kind, interface in self._links:
            address = addresses.get(kind)
            if address is None:
                continue
            wanted[(interface, self.name)] = (kind, address)
            if kind == "ethernet":
                # Older names are announced on Ethernet only, never on the test Wi-Fi.
                for extra in self._extra:
                    wanted[(interface, extra)] = (kind, address)
        return wanted

    def _reconcile(self, addresses: Dict[str, Optional[str]], now: float) -> None:
        wanted = self._wanted(addresses)
        for key in list(self._records):
            record = self._records[key]
            expired = (record.state in ("conflict", "failed")
                       and now - record.since >= RETRY_SECONDS)
            if key not in wanted or wanted[key][1] != record.address or expired:
                self._drop(key)
        for (interface, name), (kind, address) in wanted.items():
            if (interface, name) not in self._records:
                self._publish(kind, interface, name, address, now)

    def _publish(self, kind: str, interface: str, name: str, address: str, now: float) -> None:
        record = _Record(kind, interface, name, address, now)
        self._records[(interface, name)] = record
        index = self._index_of(interface)
        if index is None:
            record.state = "waiting"
            return
        group = self._bus.new_group()
        try:
            group.add_address(index, name, address)
            group.commit()
        except AvahiCollision:
            group.free()
            record.state = "conflict"
            return
        except AvahiError:
            group.free()
            raise
        record.group = group

    def _drop(self, key: Tuple[str, str]) -> None:
        record = self._records.pop(key)
        if record.group is not None:
            record.group.free()

    def _poll(self, now: float) -> None:
        for record in self._records.values():
            if record.group is None:
                continue
            state = record.group.state()
            if state == ESTABLISHED:
                new = "published"
            elif state in (UNCOMMITED, REGISTERING):
                new = "registering"
            elif state == COLLISION:
                new = "conflict"
            else:
                new = "failed"
            if new != record.state:
                record.state, record.since = new, now

    def _forget(self) -> None:
        for record in self._records.values():
            if record.group is not None:
                try:
                    record.group.free()
                except AvahiError:
                    pass
        self._records.clear()
        self._bus = None

    @_bounded_step
    def close(self) -> None:
        """Free every record, so the name is withdrawn from both networks."""
        self._forget()

    # -- the report --------------------------------------------------------

    def _status(self, addresses: Dict[str, Optional[str]], now: float,
                problem: Optional[str]) -> dict:
        links, extra = [], []
        for kind, interface in self._links:
            record = self._records.get((interface, self.name))
            address = addresses.get(kind)
            state = "waiting" if address is None else (
                record.state if record is not None else "unavailable")
            links.append({"link": kind, "interface": interface, "address": address,
                          "state": state})
        for (interface, name), record in sorted(self._records.items()):
            if name != self.name:
                extra.append({"name": name, "interface": interface, "state": record.state})
        status = {"name": self.name, "links": links, "extra": extra,
                  "updated": int(now), "fix": None}
        states = [link["state"] for link in links]
        networks = [LINK_NAMES[link["link"]] for link in links if link["state"] == "published"]
        missing = [LINK_NAMES[link["link"]] for link in links if link["state"] != "published"]
        if problem is not None:
            status.update(
                state="unavailable",
                message="Avahi is not answering (%s), so %s is not announced. "
                        "The dashboard still opens by its IP address." % (problem, self.name),
                fix="Run: sudo systemctl status avahi-daemon.service jittersim-mdns.service")
        elif "conflict" in states:
            where = " and ".join(
                LINK_NAMES[link["link"]] for link in links if link["state"] == "conflict")
            status.update(
                state="conflict",
                message="Another device on the %s already uses %s, so it is not announced "
                        "there. The dashboard still opens by its IP address." % (where, self.name),
                fix=fix_for_conflict(self.name))
        elif "failed" in states:
            status.update(
                state="unavailable",
                message="Avahi could not publish %s. The dashboard still opens by its IP "
                        "address." % self.name,
                fix="Run: sudo systemctl restart avahi-daemon.service jittersim-mdns.service")
        elif not networks:
            status.update(
                state="waiting",
                message="Waiting for an address or name registration on Ethernet and on "
                        "the test Wi-Fi before announcing %s." % self.name)
        elif missing:
            status.update(
                state="partial",
                message="%s is announced on the %s. The %s is waiting for an address or name registration."
                        % (self.name, " and ".join(networks), " and ".join(missing)))
        else:
            status.update(
                state="published",
                message="%s opens the dashboard on Ethernet (%s) and on the test Wi-Fi (%s)."
                        % (self.name, links[0]["address"], links[1]["address"]))
        return status

    def _write(self, status: dict, now: float) -> None:
        comparable = json.dumps({k: v for k, v in status.items() if k != "updated"},
                                sort_keys=True)
        if comparable == self._written and now - self._written_at < STATUS_REFRESH_SECONDS:
            return
        try:
            self._status_path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                    "w", dir=str(self._status_path.parent), suffix=".tmp", delete=False) as handle:
                handle.write(json.dumps(status, sort_keys=True) + "\n")
                temporary = Path(handle.name)
            os.chmod(temporary, 0o644)
            os.replace(temporary, self._status_path)
        except OSError:
            return
        self._written, self._written_at = comparable, now


def run_forever(
    publisher: Publisher,
    *,
    poll: float = POLL_SECONDS,
    sleep: Callable[[float], None] = time.sleep,
    stopped: Callable[[], bool] = lambda: False,
    woken: Callable[[], bool] = lambda: False,
) -> None:
    """Run until stopped. A wake-up (SIGHUP from the network hook) checks again at once."""
    try:
        while not stopped():
            publisher.step()
            waited = 0.0
            while waited < poll and not stopped() and not woken():
                sleep(0.5)
                waited += 0.5
    finally:
        publisher.close()


# ---------------------------------------------------------------------------
# What the dashboard and the command line show
# ---------------------------------------------------------------------------

def stopped_report(name: str) -> dict:
    return {
        "state": "stopped", "name": name, "links": [], "extra": [], "updated": 0,
        "message": "The dashboard name is not being announced because the name service "
                   "is not running. The dashboard still opens by its IP address.",
        "fix": "Run: sudo systemctl status jittersim-mdns.service",
    }


def read_status(path: Path = STATUS_FILE, now: Optional[float] = None,
                name: Optional[str] = None) -> dict:
    """The publisher's last report, or a 'stopped' report when it is missing or old."""
    fallback = stopped_report(name or read_local_name(TRUST_ENV))
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) |
                             getattr(os, "O_NONBLOCK", 0))
        try:
            info = os.fstat(descriptor)
            if (not stat.S_ISREG(info.st_mode) or info.st_size > MAX_STATUS_BYTES or
                    info.st_uid not in (0, os.geteuid()) or info.st_mode & 0o022):
                return fallback
            raw = os.read(descriptor, MAX_STATUS_BYTES + 1)
        finally:
            os.close(descriptor)
        if len(raw) > MAX_STATUS_BYTES:
            return fallback
        data = json.loads(raw)
    except (OSError, ValueError, RecursionError):
        return fallback
    if (not isinstance(data, dict) or data.get("state") not in STATES
            or type(data.get("updated")) not in (int, float)
            or not 0 <= data["updated"] <= 2**53
            or not math.isfinite(data["updated"])
            or not isinstance(data.get("name"), str)
            or not isinstance(data.get("links", []), list)
            or not isinstance(data.get("extra", []), list)):
        return fallback
    if (time.time() if now is None else now) - data["updated"] > STATUS_STALE_SECONDS:
        return fallback
    links, extra = data.get("links", [])[:4], data.get("extra", [])[:8]
    for item in links:
        if (not isinstance(item, dict) or item.get("link") not in ("ethernet", "test") or
                not isinstance(item.get("interface"), str) or
                not isinstance(item.get("state"), str) or
                (item.get("address") is not None and not isinstance(item["address"], str))):
            return fallback
    for item in extra:
        if not isinstance(item, dict) or any(not isinstance(item.get(field), str)
                                             for field in ("name", "interface", "state")):
            return fallback
    return {
        "state": data["state"],
        "name": data["name"],
        "message": str(data.get("message") or "")[:400],
        "fix": str(data["fix"])[:400] if data.get("fix") else None,
        "links": links,
        "extra": extra,
        "updated": int(data["updated"]),
    }


def summary_lines(status: dict) -> List[str]:
    """Plain sentences for the command line: what is wrong first, then the networks."""
    lines = [status.get("message") or "No report.", "  name: %s" % status.get("name")]
    for link in status.get("links", []):
        kind = LINK_NAMES.get(link.get("link"), str(link.get("link")))
        lines.append("  %s: %s%s" % (
            kind, link.get("address") or "no address",
            "" if link.get("address") is None else " (%s)" % link.get("state")))
    if status.get("fix"):
        lines.append(status["fix"])
    return lines


# ---------------------------------------------------------------------------
# Command line
# ---------------------------------------------------------------------------

def _environment(path: Path) -> Dict[str, str]:
    values: Dict[str, str] = {}
    try:
        for line in _read_text(path).splitlines():
            key, separator, value = line.partition("=")
            if separator:
                values[key.strip()] = value.strip().strip("'\"")
    except FileNotFoundError:
        pass
    return values


def _extra_names(name: str, trust_env: Path = TRUST_ENV) -> Tuple[str, ...]:
    names = list(compatibility_names(trust_env))
    certificate = trust_env.parent / "dashboard.crt"
    if os.path.lexists(certificate):
        from .localca import _regular_bytes
        from .updates import _certificate_names
        result = subprocess.run(["openssl", "x509", "-noout", "-text"],
                                input=_regular_bytes(certificate), capture_output=True, timeout=5)
        if result.returncode or len(result.stdout) > 65536 or len(result.stderr) > 65536:
            raise ValueError("the existing dashboard certificate aliases could not be verified")
        names.extend(_certificate_names(result.stdout.decode("utf-8")))
    # home.arpa aliases belong to the unicast resolver. Only .local is mDNS.
    return tuple(dict.fromkeys(item for item in names if item.endswith(".local") and item != name))


def _publish_command(arguments: argparse.Namespace) -> int:
    runtime = _environment(arguments.runtime_env)
    uplink = runtime.get("JITTERSIM_UPLINK", "eth0")
    test_interface = runtime.get("JITTERSIM_TEST_INTERFACE", "wlan0")
    name = read_local_name(arguments.trust_env)
    publisher = Publisher(
        name=name, uplink=uplink, test_interface=test_interface,
        extra_names=_extra_names(name, arguments.trust_env), status_path=arguments.status)
    flags = {"stop": False, "wake": False}

    def on_stop(*_ignored) -> None:
        flags["stop"] = True

    def on_wake(*_ignored) -> None:
        flags["wake"] = True

    signal.signal(signal.SIGTERM, on_stop)
    signal.signal(signal.SIGINT, on_stop)
    signal.signal(signal.SIGHUP, on_wake)

    def woken() -> bool:
        wake, flags["wake"] = flags["wake"], False
        return wake

    run_forever(publisher, stopped=lambda: flags["stop"], woken=woken)
    return 0


def main(argv: Optional[Sequence[str]] = None, out: Callable[[str], None] = print) -> int:
    parser = argparse.ArgumentParser(
        prog="python3 -m jittersim.mdnsname", description="The dashboard name on every network.")
    commands = parser.add_subparsers(dest="command", required=True)
    publish = commands.add_parser("publish", help="run the publisher (a systemd service)")
    publish.add_argument("--runtime-env", type=Path, default=RUNTIME_ENV)
    publish.add_argument("--trust-env", type=Path, default=TRUST_ENV)
    publish.add_argument("--status", type=Path, default=STATUS_FILE)
    configure = commands.add_parser("configure", help="limit Avahi's own publishing")
    restore = commands.add_parser("restore", help="put the Avahi settings back")
    for command in (configure, restore):
        command.add_argument("--config", type=Path, default=AVAHI_CONFIG)
        command.add_argument("--services", type=Path, default=AVAHI_SERVICES)
    configure.add_argument("--uplink", default="")
    configure.add_argument("--test-interface", default="")
    status = commands.add_parser("status", help="show whether the name is announced")
    status.add_argument("--status", type=Path, default=STATUS_FILE)
    arguments = parser.parse_args(argv)
    if arguments.command == "publish":
        return _publish_command(arguments)
    if arguments.command == "status":
        for line in summary_lines(read_status(arguments.status)):
            out(line)
        return 0
    try:
        if arguments.command == "configure":
            changed = configure_avahi(
                arguments.config, arguments.services, (arguments.uplink, arguments.test_interface))
            out("changed" if changed else "unchanged")
        else:
            out("restored" if restore_avahi(arguments.config, arguments.services) else "unchanged")
    except ConfigureError as error:
        print("Avahi settings: %s" % error, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
