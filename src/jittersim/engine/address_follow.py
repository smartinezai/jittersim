"""Notice a saved device that came back with a new private Wi-Fi address.

Phones and laptops use private (locally administered) Wi-Fi addresses that can
change. A saved device is matched by its address, so after a change it shows up
as a new unknown client and escapes its profile. This module compares the
evidence the Pi already has and suggests a move only when exactly one saved
device fits one new address. The rules are simple on purpose so the dashboard
can show why it suggests something. See
docs/implementation-plan-follow-address-changes-2026-10-17.md.

Everything here is pure: no database, no helper and no clock.
"""

import re
from dataclasses import dataclass
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

# Points for each rule. Ask needs ASK_POINTS and an identity signal.
POINTS_HOSTNAME = 4
POINTS_GENERIC_HOSTNAME = 1
POINTS_NAME = 2
POINTS_IPV4 = 2
POINTS_TIMING = 1
ASK_POINTS = 3
# The old address must have left at most this long before the new one joined.
TIMING_WINDOW_SECONDS = 15 * 60
# Sightings are written every 30 seconds or so. Within this margin the old address
# may still look present after the new one joined without both being online.
OVERLAP_TOLERANCE_SECONDS = 90
# A saved device not seen for this long gets no timing point and loses its host name memory.
SIGHTING_RETENTION_SECONDS = 30 * 24 * 60 * 60
# Automatic needs the new address to stay connected this long first.
STABLE_SECONDS = 60

IDENTITY_REASONS = frozenset(("hostname", "generic_hostname", "name", "ipv4"))

_MAC = re.compile(r"[0-9a-f]{2}(?::[0-9a-f]{2}){5}\Z")
# Host names that many devices send, so an equal one says little about identity.
_GENERIC_NAMES = re.compile(
    r"(?:"
    r"i?phone|ipad|ipod|iwatch|watch|android|localhost|unknown|none|espressif|esp|"
    r"mac|macbook|macbook-?air|macbook-?pro|imac|mac-?mini|mac-?studio|"
    r"laptop|desktop|pc|computer|tablet|phone|mobile|device|client|host|"
    r"galaxy[a-z0-9-]*|samsung[a-z0-9-]*|pixel[a-z0-9-]*|redmi[a-z0-9-]*|xiaomi[a-z0-9-]*|"
    r"oneplus[a-z0-9-]*|huawei[a-z0-9-]*|oppo[a-z0-9-]*|vivo[a-z0-9-]*|motorola[a-z0-9-]*|moto[a-z0-9-]*|"
    r"nokia[a-z0-9-]*|honor[a-z0-9-]*|realme[a-z0-9-]*|sm-[a-z0-9]+|"
    r"kindle|echo|amazon[a-z0-9-]*|fire-?tv[a-z0-9-]*|roku[a-z0-9-]*|chromecast[a-z0-9-]*|"
    r"google-?home[a-z0-9-]*|nest[a-z0-9-]*|apple-?tv|appletv|playstation[a-z0-9-]*|xbox[a-z0-9-]*|"
    r"windows|win[a-z0-9-]*|desktop-[a-z0-9]+|laptop-[a-z0-9]+|chromebook|raspberrypi"
    r")\Z"
)
# Names made of a generic word and a number or random letters, such as "android-1a2b3c4d".
_GENERIC_SUFFIX = re.compile(r"(?P<base>.+?)(?:[-_ ]?\(?\d{1,3}\)?|[-_][0-9a-f]{6,})\Z")
_RANDOM_LOOKING = re.compile(r"[0-9a-f]{12,}\Z|[0-9a-f-]{16,}\Z")


@dataclass(frozen=True)
class SavedIdentity:
    """A saved address that has something worth carrying to a new address."""

    mac_address: str
    name: str
    # The saved device with a profile assignment, if any.
    device_id: Optional[str] = None
    # The saved device's IPv4 address, else the last one seen.
    ipv4_address: Optional[str] = None
    protected: bool = False
    labelled: bool = False
    # The last DHCP host name this address sent, if it was seen.
    hostname: str = ""
    last_seen: Optional[int] = None


@dataclass(frozen=True)
class Arrival:
    """A connected Wi-Fi client that no saved entry knows."""

    mac_address: str
    hostname: str = ""
    ipv4_address: Optional[str] = None
    # Seconds since it joined, from the station list. None when unknown.
    connected_seconds: Optional[int] = None
    # True only when a reachable neighbour entry confirms the IPv4 address. A lease
    # or an old neighbour entry can be stale, so it earns no point.
    ipv4_verified: bool = False


@dataclass(frozen=True)
class Evidence:
    points: int
    reasons: Tuple[str, ...]
    # Strong enough for an automatic profile move (still subject to the caller's limits).
    strong: bool


@dataclass(frozen=True)
class Suggestion:
    old: SavedIdentity
    new: Arrival
    evidence: Evidence


def is_private_address(mac_address: str) -> bool:
    """True for a valid single-device address with the locally administered bit."""

    mac = str(mac_address or "").lower()
    if not _MAC.fullmatch(mac):
        return False
    first = int(mac[:2], 16)
    return bool(first & 2) and not first & 1


def normalize_hostname(hostname: object) -> str:
    """The host name to compare, or "" when there is none worth comparing."""

    if not isinstance(hostname, str):
        return ""
    value = hostname.strip().casefold()
    if not value or value == "*" or len(value) > 64 or any(ord(char) < 32 for char in value):
        return ""
    return value


def is_generic_hostname(hostname: object) -> bool:
    """True when many devices could send this host name."""

    value = normalize_hostname(hostname)
    if len(value) < 3 or _RANDOM_LOOKING.fullmatch(value.replace(":", "")):
        return True
    value = value.replace("_", "-").replace(" ", "-").replace("'", "").replace("’", "")
    if _GENERIC_NAMES.fullmatch(value):
        return True
    suffix = _GENERIC_SUFFIX.fullmatch(value)
    return bool(suffix and _GENERIC_NAMES.fullmatch(suffix.group("base").rstrip("-")))


def _letters(value: str) -> str:
    return "".join(char for char in value.casefold() if char.isalnum())


def joined_at(arrival: Arrival, now: int) -> Optional[int]:
    if arrival.connected_seconds is None or arrival.connected_seconds < 0:
        return None
    return now - arrival.connected_seconds


def score(old: SavedIdentity, new: Arrival, *, now: int) -> Optional[Evidence]:
    """The evidence that `new` is `old` with a new address, or None when it cannot be."""

    if old.mac_address == new.mac_address:
        return None
    reasons: List[str] = []
    points = 0
    new_host = normalize_hostname(new.hostname)
    old_host = normalize_hostname(old.hostname)
    generic = is_generic_hostname(new_host)
    if new_host and new_host == old_host:
        if generic:
            points += POINTS_GENERIC_HOSTNAME
            reasons.append("generic_hostname")
        else:
            points += POINTS_HOSTNAME
            reasons.append("hostname")
    saved_name = _letters(old.name)
    if new_host and not generic and len(saved_name) >= 4 and _letters(new_host) == saved_name:
        points += POINTS_NAME
        reasons.append("name")
    if (
        new.ipv4_verified
        and new.ipv4_address
        and old.ipv4_address
        and new.ipv4_address == old.ipv4_address
    ):
        points += POINTS_IPV4
        reasons.append("ipv4")
    joined = joined_at(new, now)
    if joined is not None and old.last_seen is not None and old.last_seen <= now:
        if old.last_seen > joined + OVERLAP_TOLERANCE_SECONDS:
            # Both were online together, so they are two devices.
            return None
        if joined - old.last_seen <= TIMING_WINDOW_SECONDS and now - old.last_seen <= (
            SIGHTING_RETENTION_SECONDS
        ):
            points += POINTS_TIMING
            reasons.append("timing")
    strong = "hostname" in reasons and ("ipv4" in reasons or "timing" in reasons)
    return Evidence(points, tuple(reasons), strong)


def qualifies(evidence: Optional[Evidence], *, protected: bool = False) -> bool:
    """Enough for Ask: the points and at least one signal about the device itself.

    A protected device also needs a distinctive host name, because a wrong
    protection move exempts a stranger from every profile.
    """

    if evidence is None or evidence.points < ASK_POINTS:
        return False
    if protected and not {"hostname", "name"}.intersection(evidence.reasons):
        return False
    return bool(IDENTITY_REASONS.intersection(evidence.reasons))


def find_suggestions(
    identities: Sequence[SavedIdentity],
    arrivals: Sequence[Arrival],
    *,
    present: Iterable[str],
    now: int,
    dismissed: Iterable[Tuple[str, str]] = (),
    vetoed: Iterable[Tuple[str, str]] = (),
) -> List[Suggestion]:
    """Pair each new address with the one saved device it fits, never guessing.

    A saved device takes part only while its address is absent. A new address
    takes part only when it is private and no saved entry knows it. A pair is
    suggested only when that saved device is the only one over the threshold
    for that new address and that new address is the only one over the
    threshold for that saved device. A dismissed pair still counts for this
    uniqueness, so dismissing one of two candidates does not turn the other
    into a guess. A vetoed pair was seen online at the same time, so it is two
    devices and takes no part at all.
    """

    present_set = set(present)
    known = {identity.mac_address for identity in identities}
    absent = [identity for identity in identities if identity.mac_address not in present_set]
    candidates = [
        arrival
        for arrival in arrivals
        if is_private_address(arrival.mac_address) and arrival.mac_address not in known
    ]
    never = set(vetoed)
    matches: Dict[Tuple[str, str], Evidence] = {}
    by_new: Dict[str, List[str]] = {}
    by_old: Dict[str, List[str]] = {}
    for arrival in candidates:
        for identity in absent:
            if (identity.mac_address, arrival.mac_address) in never:
                continue
            evidence = score(identity, arrival, now=now)
            if not qualifies(evidence, protected=identity.protected):
                continue
            matches[(identity.mac_address, arrival.mac_address)] = evidence
            by_new.setdefault(arrival.mac_address, []).append(identity.mac_address)
            by_old.setdefault(identity.mac_address, []).append(arrival.mac_address)
    skip = set(dismissed)
    identity_by_mac = {identity.mac_address: identity for identity in absent}
    suggestions = []
    for arrival in candidates:
        olds = by_new.get(arrival.mac_address, [])
        if len(olds) != 1:
            continue
        old_mac = olds[0]
        if by_old.get(old_mac) != [arrival.mac_address] or (old_mac, arrival.mac_address) in skip:
            continue
        suggestions.append(
            Suggestion(identity_by_mac[old_mac], arrival, matches[(old_mac, arrival.mac_address)])
        )
    return sorted(suggestions, key=lambda item: (item.old.name.casefold(), item.new.mac_address))


def build_identities(
    *,
    devices: Iterable[Tuple[str, str, str, Optional[str]]],
    protected: Mapping[str, str],
    labels: Mapping[str, str],
    sightings: Mapping[str, Tuple[str, Optional[str], int]],
) -> List[SavedIdentity]:
    """Merge saved devices, protection and names by MAC address.

    `devices` holds (device ID, name, IPv4 address, MAC) for saved devices,
    `protected` and `labels` map a MAC to its name and `sightings` maps a MAC
    to (host name, IPv4 address, last seen).
    """

    merged: Dict[str, Dict[str, object]] = {}
    for device_id, name, ipv4_address, mac in devices:
        if not mac:
            continue
        merged.setdefault(mac, {}).update(device_id=device_id, device_name=name, ipv4=ipv4_address)
    for mac, name in protected.items():
        merged.setdefault(mac, {}).update(protected=True, protected_name=name)
    for mac, name in labels.items():
        merged.setdefault(mac, {}).update(labelled=True, label=name)
    identities = []
    for mac, item in merged.items():
        hostname, seen_ipv4, last_seen = sightings.get(mac, ("", None, None))
        name = str(
            item.get("label") or item.get("protected_name") or item.get("device_name") or mac
        )
        identities.append(
            SavedIdentity(
                mac_address=mac,
                name=name,
                device_id=item.get("device_id"),
                ipv4_address=item.get("ipv4") or seen_ipv4,
                protected=bool(item.get("protected")),
                labelled=bool(item.get("labelled")),
                hostname=hostname or "",
                last_seen=last_seen,
            )
        )
    return sorted(identities, key=lambda identity: identity.mac_address)


def describe(evidence: Evidence, *, hostname: str = "") -> List[str]:
    """Plain sentences for the dashboard, one per rule that matched."""

    sentences = {
        "hostname": f'It sends the same device name "{hostname}".',
        "generic_hostname": (
            f'It sends the same common device name "{hostname}", which many devices use.'
        ),
        "name": "Its device name matches the saved name.",
        "ipv4": "It got the same IP address.",
        "timing": "It joined shortly after the old address left.",
    }
    return [sentences[reason] for reason in evidence.reasons if reason in sentences]
