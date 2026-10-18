"""Commands that run on your Mac or another computer against the Pi's HTTPS dashboard.

`jittersim connect`, `status`, `profiles`, `traffic`, `wifi show` and `impact` read and change
the Pi through the same verified dashboard API as the browser. They ask for the admin token
with a hidden prompt every time and never store it. Each command registers its parser and
its handler below.
"""

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence

from jittersim import dashboard_rules as rules
from jittersim import impact_rules
from jittersim.config.models import normalize_domain
from jittersim.remote_client import (
    CA_CERT_ENV,
    EXAMPLE_URL,
    TOKEN_ENV,
    UNDERSTOOD_MESSAGE,
    URL_ENV,
    Connection,
    DashboardClient,
    RemoteError,
    config_path,
    checked_certificate,
    hidden_prompt,
    js_number,
    printable,
    read_token,
    resolve_connection,
    validate_url,
    write_config,
)

# Options that would put a secret on the command line. `--password` alone is the flag of
# `wifi show` that asks to see the Wi-Fi password and `--password=VALUE` is refused.
SECRET_OPTIONS = ("--token", "--admin-token", "--router-password", "--secret")
SECRET_PREFIXES = tuple(option + "=" for option in SECRET_OPTIONS) + ("--password=",)
ACTION_TIMEOUT_SECONDS = 90.0

WHERE_REMOTE = (
    "Runs on your Mac or any computer that reaches the Pi dashboard over HTTPS. "
    "It does not need sudo."
)
TOKEN_HELP = (
    "The admin token is asked with a hidden prompt every time. For scripts the command reads "
    f"{TOKEN_ENV} when it is set: other programs that can read your environment can then read "
    "the token too and typing it inline leaves it in your shell history. The token is never "
    "accepted as an argument and never saved."
)
CONNECTION_HELP = (
    f"The dashboard address and certificate come from --url and --ca-cert, then {URL_ENV} and "
    f"{CA_CERT_ENV}, then the file saved by 'jittersim connect'."
)

Handler = Callable[[argparse.Namespace], int]
PARSERS: Dict[str, Callable[[], argparse.ArgumentParser]] = {}
HANDLERS: Dict[str, Handler] = {}
GROUPS: Dict[str, Dict[str, Any]] = {}


class _Parser(argparse.ArgumentParser):
    """argparse without echoing what it did not understand: that could be a pasted secret.

    Abbreviations are off so `--pass=VALUE` is never read as `--password` with a value that
    argparse would then repeat in its error.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        kwargs.setdefault("allow_abbrev", False)
        super().__init__(*args, **kwargs)

    def error(self, message: str) -> None:
        if message.startswith("unrecognized arguments"):
            message = "unrecognized arguments (not shown here)"
        message = re.sub(
            r"invalid choice: .*? \(choose from", "invalid choice (choose from", message
        )
        message = re.sub(
            r"ignored explicit argument .*", "ignored explicit argument (not shown here)",
            message, flags=re.S,
        )
        super().error(printable(message))


def _epilog() -> str:
    return "\n\n".join((WHERE_REMOTE, CONNECTION_HELP, TOKEN_HELP))


def _parser(prog: str, description: str) -> argparse.ArgumentParser:
    parser = _Parser(
        prog=prog,
        description=description,
        epilog=_epilog(),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    connection = parser.add_argument_group("connection")
    connection.add_argument("--url", help=f"dashboard address such as {EXAMPLE_URL} (https only)")
    connection.add_argument(
        "--ca-cert", type=Path,
        help="the Pi's dashboard certificate, a copy of /etc/jittersim/dashboard.crt",
    )
    parser.add_argument("--json", action="store_true", help="print the answer as JSON")
    return parser


def register(
    name: str, build: Callable[[], argparse.ArgumentParser]
) -> Callable[[Handler], Handler]:
    """Add a command: `name` is its words such as "profiles list"."""

    def decorate(handler: Handler) -> Handler:
        PARSERS[name] = build
        HANDLERS[name] = handler
        return handler

    return decorate


def register_group(name: str, description: str, choices: Sequence[str]) -> None:
    """A command that only has subcommands, such as `jittersim profiles`."""

    GROUPS[name] = {"description": description, "choices": tuple(choices)}


def _secret_argument(argv: Sequence[str]) -> bool:
    return any(
        argument in SECRET_OPTIONS or argument.startswith(SECRET_PREFIXES) for argument in argv
    )


def _out(text: str = "") -> None:
    """Every line for the terminal passes here so dashboard names cannot carry escapes."""

    print(printable(text))


def _err(text: str) -> None:
    print(printable(text), file=sys.stderr)


def _json_character(character: str) -> str:
    if character == "\n" or printable(character) == character:
        return character
    code = ord(character)
    if code > 0xFFFF:
        code -= 0x10000
        return "\\u%04x\\u%04x" % (0xD800 + (code >> 10), 0xDC00 + (code & 0x3FF))
    return "\\u%04x" % code


def _print_json(value: Any) -> None:
    """JSON with readable letters but every control, bidi or invisible character escaped."""

    text = json.dumps(value, indent=2, ensure_ascii=False)
    # Outside strings the JSON text has only spaces and new lines. Inside strings json.dumps
    # already escaped the C0 controls so what is left (C1, bidi and format characters) is
    # escaped here. The result is the same JSON value.
    print("".join(_json_character(character) for character in text))


def _client(arguments: argparse.Namespace) -> DashboardClient:
    # The address and certificate are checked before anything secret is asked for.
    connection = resolve_connection(arguments.url, arguments.ca_cert)
    return DashboardClient(connection, read_token())


def _require(value: Any, kind: type) -> Any:
    if not isinstance(value, kind):
        raise RemoteError(UNDERSTOOD_MESSAGE)
    return value


# --- section: connect ---------------------------------------------------------------------


def _connect_parser() -> argparse.ArgumentParser:
    parser = _parser(
        "jittersim connect",
        "Save the dashboard address and certificate path for the other remote commands. "
        "The file ~/.config/jittersim/cli.json is readable only by you and never holds a token.",
    )
    parser.add_argument(
        "--no-check", action="store_true",
        help="save without checking the certificate against the dashboard now",
    )
    return parser


@register("connect", _connect_parser)
def _connect(arguments: argparse.Namespace) -> int:
    if not arguments.url or not arguments.ca_cert:
        raise RemoteError(
            f"Give both --url (for example {EXAMPLE_URL}) and --ca-cert.", exit_code=2
        )
    url = validate_url(arguments.url)
    certificate = checked_certificate(arguments.ca_cert)
    connection = Connection(url, certificate.resolve())
    checked = False
    if not arguments.no_check:
        # The health check needs no token: it only proves the certificate and the address.
        health = DashboardClient(connection, None).get("/api/health")
        if health.get("status") not in ("ok", "degraded"):
            raise RemoteError(UNDERSTOOD_MESSAGE)
        checked = True
    path = config_path()
    write_config(path, connection)
    if arguments.json:
        _print_json({
            "url": connection.url, "ca_cert": str(connection.ca_cert), "config": str(path),
            "checked": checked,
        })
        return 0
    if checked:
        _out(f"The certificate matches the dashboard at {connection.url}.")
    _out(f"Saved the dashboard address and certificate path in {path}.")
    _out("The admin token is never saved. Each command asks for it.")
    return 0


# --- end: connect


# --- section: status ----------------------------------------------------------------------

LOCAL_STATUS_OPTIONS = ("--database", "--helper-socket", "--timeout-seconds")

def remote_status_wanted(argv: Sequence[str], environ: Optional[Mapping[str, str]] = None) -> bool:
    """Decide whether `jittersim status` reads the dashboard or the local Pi helper.

    Local options keep the Pi's own status. Remote options or a saved or exported dashboard
    address choose the dashboard.
    """

    environ = os.environ if environ is None else environ
    if _secret_argument(argv):
        # The remote command refuses it without repeating it. argparse would print it.
        return True
    names = {argument.split("=", 1)[0] for argument in argv if argument.startswith("-")}
    if names & set(LOCAL_STATUS_OPTIONS):
        return False
    if names:
        # Remote options, --help and anything unknown go to the remote parser: it explains
        # both ways and never repeats an option it does not know.
        return True
    return bool(environ.get(URL_ENV)) or config_path(environ).exists()


def _status_parser() -> argparse.ArgumentParser:
    return _parser(
        "jittersim status",
        "Show the release the Pi runs, whether JitterSim is running, the general profile and "
        "the connected devices that are affected right now with the profile each one gets. "
        "Protected devices are never listed. On the Pi without a saved connection "
        "'sudo jittersim status' shows the local helper status instead (options --database, "
        "--helper-socket and --timeout-seconds).",
    )


def _general_status(view_text: str, general: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    profile = (general or {}).get("profile")
    if not profile:
        return None
    return {
        "id": profile.get("id"),
        "name": profile.get("name") or profile.get("id"),
        "status": view_text.rsplit(" · ", 1)[-1],
    }


def _release(client: DashboardClient) -> Dict[str, Any]:
    """The release label the Pi runs and a staged one, as Setup shows them."""

    try:
        data = client.get("/api/updates")
    except RemoteError:
        # An older dashboard has no release controls. The rest of the status still counts.
        return {"installed": None, "staged": None,
                "message": "The dashboard does not report its release."}
    installed = data.get("installed")
    if not isinstance(installed, str) or not installed:
        message = data.get("message")
        return {"installed": None, "staged": None,
                "message": message if isinstance(message, str) and message else None}
    staged = data.get("available")
    return {"installed": installed,
            "staged": staged if isinstance(staged, str) and staged != installed else None,
            "message": None}


def _forced_window_notice(client: DashboardClient) -> Optional[str]:
    """The sentence the dashboard shows while a forced window keeps the router's Wi-Fi off."""

    try:
        data = client.get("/api/forced-windows")
    except RemoteError as error:
        if error.status in (401, 403):
            raise
        # An older dashboard has no forced windows. The rest of the status still counts.
        return None
    notice = data.get("notice")
    return notice if isinstance(notice, str) and notice else None


def _release_text(release: Dict[str, Any]) -> str:
    if release["installed"] is None:
        return "Release on the Pi: unknown" + (
            f" ({release['message']})" if release["message"] else "")
    text = f"Release on the Pi: {release['installed']}"
    return text + (f". Staged and ready to install: {release['staged']}"
                   if release["staged"] else "")


@register("status", _status_parser)
def _status(arguments: argparse.Namespace) -> int:
    client = _client(arguments)
    health = client.get("/api/health")
    state = _require(client.get("/api/state").get("state"), dict)
    general = client.get("/api/general-profile")
    try:
        connected = client.get("/api/connected-devices")
    except RemoteError as error:
        if error.status in (401, 403):
            raise
        # Like the page: the device list is unavailable and the rest of the status still shows.
        connected = {"available": False, "devices": []}
    profiles = _require(client.get("/api/profiles").get("profiles"), list)
    lists = _require(client.get("/api/service-lists").get("service_lists"), list)
    release = _release(client)
    window_notice = _forced_window_notice(client)
    clients = _require(connected.get("devices") or [], list)
    clients_available = (connected.get("available") is not False
                         and connected.get("association_status_available") is not False)
    observed = state.get("observed_state")
    running = state.get("running") is True
    view = rules.sidebar_status(
        {
            "adminTokenVerified": True,
            "loaded": {name: True for name in rules.SIDEBAR_INPUTS},
            "runtimeState": observed,
            "running": running,
            "lastAction": state.get("last_action"),
            "deviceOnly": state.get("device_only"),
            "everyoneExceptions": state.get("everyone_exceptions"),
            "generalState": general,
            "profiles": profiles,
            "clients": clients,
            "clientsAvailable": clients_available,
            "serviceLists": lists,
        },
        limit=None,
    )
    summary = rules.runtime_status_text(
        observed,
        running,
        {
            "lastAction": state.get("last_action"),
            "deviceOnly": state.get("device_only"),
            "everyoneExceptions": state.get("everyone_exceptions"),
            "helperConfigured": health.get("helper_configured") is not False,
        },
    )
    protected = sum(1 for item in clients if isinstance(item, dict) and item.get("unaffected"))
    enabled_lists = [
        item.get("name") for item in lists
        if view["lists"]["text"] and isinstance(item, dict) and item.get("enabled")
    ]
    if arguments.json:
        _print_json({
            "dashboard": client.url,
            "running": running,
            "state": observed,
            "summary": summary["text"],
            "last_action": state.get("last_action"),
            "last_result": state.get("last_result"),
            "general_profile": _general_status(view["general"]["text"], general),
            "devices_summary": view["devices"]["summary"],
            "connected_devices_available": clients_available,
            "affected_devices": view["devices"]["items"],
            "service_lists": enabled_lists,
            "protected_connected_devices": protected,
            "release": {"installed": release["installed"], "staged": release["staged"]},
            "forced_window_notice": window_notice,
        })
        return 0
    _out(f"Dashboard: {client.url}")
    _out(_release_text(release))
    _out(summary["text"])
    if window_notice:
        _out(window_notice)
    if state.get("last_action"):
        result = f" ({state['last_result']})" if state.get("last_result") else ""
        _out(f"Last action: {state['last_action']}{result}")
    _out(view["general"]["text"])
    items = view["devices"]["items"]
    _out(view["devices"]["summary"] + (":" if items else ""))
    for item in items:
        _out(f"  {item['name']}" + (f": {item['profile']}" if item.get("profile") else ""))
    if view["lists"]["text"]:
        _out(view["lists"]["text"])
    if protected:
        _out(
            "1 protected device stays untouched" if protected == 1
            else f"{protected} protected devices stay untouched"
        )
    return 0


# --- end: status


# --- section: profiles --------------------------------------------------------------------

register_group(
    "profiles",
    "List the saved profiles or apply one to every connected device.",
    ("list", "apply-all", "stop-all"),
)


def _profiles_list_parser() -> argparse.ArgumentParser:
    return _parser(
        "jittersim profiles list",
        "List the saved profiles with a one-line summary and mark the general profile.",
    )


def _apply_all_parser() -> argparse.ArgumentParser:
    parser = _parser(
        "jittersim profiles apply-all",
        "Make one saved profile the general profile for every connected device. "
        "Protected devices stay untouched.",
    )
    parser.add_argument("profile_id", metavar="PROFILE_ID")
    return parser


def _stop_all_parser() -> argparse.ArgumentParser:
    return _parser(
        "jittersim profiles stop-all",
        "Stop the general profile. Saved device rules resume and protected devices stay "
        "untouched.",
    )


@register("profiles list", _profiles_list_parser)
def _profiles_list(arguments: argparse.Namespace) -> int:
    client = _client(arguments)
    profiles = _require(client.get("/api/profiles").get("profiles"), list)
    general = client.get("/api/general-profile")
    general_id = (general.get("profile") or {}).get("id")
    badge = rules.general_badge(general)
    rows = [
        {
            "id": profile.get("id"),
            "name": profile.get("name"),
            "summary": rules.profile_summary(profile),
            "general": profile.get("id") == general_id,
        }
        for profile in profiles
    ]
    if arguments.json:
        _print_json({
            "profiles": rows,
            "general_profile_id": general_id,
            "general_badge": badge if general_id else None,
        })
        return 0
    if not rows:
        _out("No saved profiles yet. Create one in the dashboard.")
        return 0
    for row in rows:
        _out(f"{row['name']} ({row['id']})" + (f"  {badge}" if row["general"] else ""))
        _out(f"  {row['summary']}")
    return 0


def _general_profile_change(arguments: argparse.Namespace, *, stop: bool) -> int:
    client = _client(arguments)
    path = "/api/general-profile/stop" if stop else "/api/general-profile"
    payload: Dict[str, Any] = {} if stop else {"profile_id": arguments.profile_id}
    result = client.post(path, payload, timeout=ACTION_TIMEOUT_SECONDS)
    general = result.get("general_profile") or {}
    if stop:
        # Without a saved general profile the dashboard answers without applying anything.
        lines = (
            ["General profile stopped. Saved device rules resumed."] if "applied" in result
            else ["No general profile was set. Nothing changed."]
        )
        lines.append("Protected devices stay untouched.")
    else:
        lines = ["General profile applied. Protected devices remain unaffected."]
        profile = general.get("profile") or {}
        if profile:
            lines.append(
                "General profile: " + str(profile.get("name") or profile.get("id"))
                + " · " + rules.general_badge(general).split(" · ")[-1]
            )
    warning = result.get("warning")
    if arguments.json:
        _print_json({
            "message": " ".join(lines),
            "general_profile": general.get("profile"),
            "active": general.get("active") is True,
            "warning": warning,
        })
    else:
        for line in lines:
            _out(line)
    if isinstance(warning, str) and warning:
        _err(warning)
    return 0


@register("profiles apply-all", _apply_all_parser)
def _apply_all(arguments: argparse.Namespace) -> int:
    return _general_profile_change(arguments, stop=False)


@register("profiles stop-all", _stop_all_parser)
def _stop_all(arguments: argparse.Namespace) -> int:
    return _general_profile_change(arguments, stop=True)


# --- end: profiles


# --- section: traffic ---------------------------------------------------------------------

SCAN_TIMEOUT_SECONDS = 120.0
# The page's ENFORCE_NOTE word for word. Panic also turns household blocking off.
ENFORCE_NOTE = (
    "It applies on its own while profiles are running. "
    "After Reset or Panic choose Start. "
    "After Panic also turn household blocking back on."
)
KIND_TAGS = {
    "app": "app",
    "system": "system",
    "tracker": "tracker",
    "iot": "smart-home",
    "cdn": "CDN",
    "unknown": "unknown",
}


def _traffic_parser() -> argparse.ArgumentParser:
    parser = _parser(
        "jittersim traffic",
        "Show the names each test device looked up (Traffic discovery). Names are grouped by "
        "app or site. Labels come from a built-in table and 'seen with' is a guess. Use "
        "'jittersim traffic block DOMAIN' or 'jittersim traffic unblock DOMAIN' to change the "
        "global block list.",
    )
    parser.add_argument("--device", metavar="NAME_OR_IP", help="only this device")
    parser.add_argument(
        "--scan", action="store_true",
        help="sample DNS lookups on the test Wi-Fi first (takes a few seconds)",
    )
    parser.add_argument("--expand", action="store_true", help="list every name of each group")
    parser.add_argument("--blocked", action="store_true", help="only globally blocked names")
    return parser


def _block_parser(word: str, text: str) -> Callable[[], argparse.ArgumentParser]:
    def build() -> argparse.ArgumentParser:
        parser = _parser(
            f"jittersim traffic {word}",
            f"{text} the global block list. The change is saved and applies on its own while "
            "JitterSim is running. While household access rules are on a listed name also "
            "blocks every name under it (example.com also blocks www.example.com). Unblock "
            "takes the exact name that was blocked.",
        )
        parser.add_argument("domain", metavar="DOMAIN")
        return parser

    return build


def _label_text(parts: Dict[str, Any]) -> str:
    pieces = []
    if parts["showName"]:
        pieces.append(parts["labelName"])
    if parts["showCategory"]:
        pieces.append(parts["category"])
    # Like the page: a row of a group shows no label line when it would only repeat the header.
    if not pieces:
        return ""
    text = " · ".join(pieces)
    tag = KIND_TAGS.get(parts["kind"], "")
    return f"[{tag}] {text}" if tag else text


def _name_row(observation: Dict[str, Any], group: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if group is None:
        parts = rules.row_label_parts(observation)
    else:
        parts = rules.row_label_parts(
            observation, group["name"], rules.group_categories_uniform(group)
        )
    hint = rules.domain_hint(observation)
    label = observation.get("label") or {}
    return {
        "domain": observation.get("domain"),
        "query_count": observation.get("query_count"),
        "queries": rules.query_count_text(observation.get("query_count")),
        "label": {key: label.get(key) for key in ("name", "category", "kind")},
        "label_text": _label_text(parts),
        "kind": KIND_TAGS.get(label.get("kind"), label.get("kind")),
        "seen_with": list(observation.get("seen_with") or []),
        "seen_with_text": hint["text"],
        "blocked": observation.get("globally_blocked") is True,
    }


def _traffic_device(device: Dict[str, Any], blocked_only: bool) -> Optional[Dict[str, Any]]:
    domains = [item for item in device.get("domains") or [] if isinstance(item, dict)]
    if blocked_only:
        domains = [item for item in domains if item.get("globally_blocked") is True]
        if not domains:
            return None
    groups = []
    for group in rules.group_observations(domains):
        names = [_name_row(item, None if group["single"] else group) for item in group["names"]]
        groups.append({
            "name": group["name"],
            "kind": KIND_TAGS.get(group["kind"], group["kind"]),
            "single": group["single"],
            "names_count": len(group["names"]),
            "queries": rules.query_count_text(group["totalQueries"]),
            "total_queries": group["totalQueries"],
            "blocked_text": rules.group_blocked_text(group),
            "names": names,
        })
    return {
        "name": rules.device_display_name(device),
        "ipv4_address": device.get("ipv4_address"),
        "groups": groups,
    }


def _print_name(row: Dict[str, Any], indent: str) -> None:
    pieces = [row["domain"]]
    if row["label_text"]:
        pieces.append(row["label_text"])
    pieces.append(row["queries"])
    if row["blocked"]:
        pieces.append("Blocked")
    if row["seen_with_text"]:
        pieces.append(row["seen_with_text"])
    _out(indent + "  ".join(str(piece) for piece in pieces))


def _cleared_list_message(data: Dict[str, Any]) -> Optional[str]:
    """The dashboard's own sentence when an upgrade cleared the list and no name came back yet."""

    cleared = data.get("list_cleared")
    message = cleared.get("message") if isinstance(cleared, dict) else None
    return message if isinstance(message, str) and message.strip() else None


@register("traffic", _traffic_parser)
def _traffic(arguments: argparse.Namespace) -> int:
    client = _client(arguments)
    scan = None
    if arguments.scan:
        if not arguments.json:
            print("Sampling DNS queries from the test network...", file=sys.stderr)
        scan = client.post("/api/observations/scan", {}, timeout=SCAN_TIMEOUT_SECONDS)
    data = client.get("/api/observations")
    if not data.get("available"):
        raise RemoteError(str(data.get("message") or "Traffic discovery is unavailable."))
    devices = [item for item in _require(data.get("devices") or [], list) if isinstance(item, dict)]
    if arguments.device:
        wanted = arguments.device.strip().lower()
        devices = [
            item for item in devices
            if wanted in (
                str(item.get("ipv4_address") or "").lower(),
                rules.device_display_name(item).lower(),
            )
        ]
        if not devices:
            raise RemoteError(f"No device named or at {arguments.device} has traffic in the list.")
    shown = [
        result for result in (_traffic_device(item, arguments.blocked) for item in devices)
        if result is not None
    ]
    if arguments.json:
        _print_json({
            "scan": {"captured_observations": scan.get("captured_observations")} if scan else None,
            "total_domains": data.get("total_domains"),
            "list_cleared": data.get("list_cleared") if _cleared_list_message(data) else None,
            "devices": shown,
            "note": "Labels come from a built-in table. seen_with is a guess.",
        })
        return 0
    if scan is not None:
        _out(
            f"Traffic scan complete: {scan.get('captured_observations')} domain observations "
            "captured."
        )
    if not shown:
        _out(
            "No globally blocked names in the list." if arguments.blocked
            else _cleared_list_message(data) or "No DNS observations yet. Connect a device and run a scan."
        )
        return 0
    _out(
        f"{data.get('total_domains', 0)} domains. Labels come from a built-in table. "
        "'Seen with' is a guess."
    )
    for device in shown:
        _out("")
        _out(f"{device['name']}  ({device['ipv4_address']})")
        for group in device["groups"]:
            if group["single"]:
                _print_name(group["names"][0], "  ")
                continue
            pieces = [group["name"]]
            if group["kind"]:
                pieces.append(f"[{group['kind']}]")
            pieces += [rules.group_names_text(group["names_count"]), group["queries"]]
            if group["blocked_text"]:
                pieces.append(group["blocked_text"])
            _out("  " + "  ".join(str(piece) for piece in pieces))
            if arguments.expand:
                for row in group["names"]:
                    _print_name(row, "      ")
    if not arguments.expand and any(
        not group["single"] for device in shown for group in device["groups"]
    ):
        _out("")
        _out("Add --expand to list every name of a group.")
    return 0


def _traffic_block(arguments: argparse.Namespace, *, block: bool) -> int:
    try:
        domain = normalize_domain(arguments.domain)
    except ValueError as error:
        raise RemoteError("That is not a valid DNS name.", exit_code=2) from error
    client = _client(arguments)
    before = _blocked_domains(client.get("/api/policies"))
    if (domain in before) == block:
        # Nothing to save: say so instead of claiming a change.
        message = (
            f"{domain} is already on the global block list. Nothing changed." if block
            else f"{domain} was not on the global block list. Nothing changed."
        )
        saved = False
    else:
        path = "/api/observations/" + ("add-global-block" if block else "remove-global-block")
        result = client.post(path, {"domain": domain})
        listed = _blocked_domains(result.get("policies") or {})
        if (domain in listed) != block:
            raise RemoteError(
                "The dashboard did not confirm the change to the global block list.")
        message = (
            f"Saved: added {domain} to the global block list. " if block
            else f"Saved: removed {domain} from the global block list. "
        ) + ENFORCE_NOTE
        saved = True
    if arguments.json:
        _print_json({"domain": domain, "blocked": block, "saved": saved, "message": message})
    else:
        _out(message)
    return 0


def _blocked_domains(policies: Dict[str, Any]) -> List[str]:
    listed = (policies.get("websites") or {}).get("blocked_domains")
    if not isinstance(listed, list):
        raise RemoteError(UNDERSTOOD_MESSAGE)
    return listed


@register("traffic block", _block_parser("block", "Add one name to"))
def _block(arguments: argparse.Namespace) -> int:
    return _traffic_block(arguments, block=True)


@register("traffic unblock", _block_parser("unblock", "Remove one name from"))
def _unblock(arguments: argparse.Namespace) -> int:
    return _traffic_block(arguments, block=False)


# --- end: traffic


# --- section: wifi ------------------------------------------------------------------------

ROUTER_TIMEOUT_SECONDS = 45.0


def _wifi_show_parser() -> argparse.ArgumentParser:
    parser = _parser(
        "jittersim wifi show",
        "Show the router's own Wi-Fi name, read live from the router through the dashboard. "
        "The router admin password is asked with a hidden prompt and is never shown or saved. "
        "'jittersim wifi reset' is different: it runs on the Pi with sudo.",
    )
    parser.add_argument(
        "--password", action="store_true",
        help="also show the router's Wi-Fi password (the terminal keeps it in its scrollback)",
    )
    return parser


@register("wifi show", _wifi_show_parser)
def _wifi_show(arguments: argparse.Namespace) -> int:
    client = _client(arguments)
    password = hidden_prompt("Router admin password (hidden): ")
    if not password:
        raise RemoteError("Enter the router admin password.")
    client.add_secret(password)
    result = client.post(
        "/api/router/wifi-credentials",
        {"admin_password": password, "include_password": bool(arguments.password)},
        timeout=ROUTER_TIMEOUT_SECONDS,
    )
    found = _require(result.get("result"), dict)
    ssid = _require(found.get("ssid"), str)
    wifi_password = _require(found.get("password"), str) if arguments.password else None
    if arguments.password:
        print(
            "Warning: the router Wi-Fi password is shown below and stays in this terminal's "
            "scrollback.",
            file=sys.stderr,
        )
    if arguments.json:
        value = {"ssid": ssid}
        if arguments.password:
            value["password"] = wifi_password
        _print_json(value)
        return 0
    _out(f"Router Wi-Fi name: {ssid}")
    if arguments.password:
        _out(f"Router Wi-Fi password: {wifi_password}")
    return 0


# --- end: wifi


# --- section: impact ----------------------------------------------------------------------

DEFAULT_IMPACT_WAIT = 10
# The rules compare samples at most a minute apart. Two slow answers must still fit.
MAX_IMPACT_WAIT = 50
PAGE_WINDOW_WORDS = "in the last minute"


def _wait_seconds(value: str) -> int:
    try:
        seconds = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("the wait must be a whole number of seconds") from error
    if not 1 <= seconds <= MAX_IMPACT_WAIT:
        raise argparse.ArgumentTypeError(f"the wait must be from 1 to {MAX_IMPACT_WAIT} seconds")
    return seconds


def _impact_parser() -> argparse.ArgumentParser:
    parser = _parser(
        "jittersim impact",
        "Show the Impact check: for each affected device and direction the set and actual "
        "loss, the speed cap and rate, the queue wait and a verdict. The command takes two "
        "samples of the Pi's queue counters --wait seconds apart and judges them with the "
        "dashboard's rules. The verdicts name the time it measured such as 'in the last 10 "
        "seconds' where the page says 'in the last minute'.",
    )
    parser.add_argument(
        "--wait", type=_wait_seconds, default=DEFAULT_IMPACT_WAIT, metavar="SECONDS",
        help=f"seconds between the two samples, 1 to {MAX_IMPACT_WAIT} "
        f"(default {DEFAULT_IMPACT_WAIT})",
    )
    return parser


def _impact_sample(client: DashboardClient) -> Dict[str, Any]:
    data = client.get("/api/impact")
    if data.get("available") is not True:
        raise RemoteError(str(data.get("message") or "Impact is not available."))
    if isinstance(data.get("sampled_at"), bool) or not isinstance(
        data.get("sampled_at"), (int, float)
    ):
        raise RemoteError(UNDERSTOOD_MESSAGE)
    _require(data.get("queues"), list)
    return data


def _window_words(seconds: float) -> str:
    whole = max(1, int(seconds + 0.5))
    return "in the last " + ("1 second" if whole == 1 else f"{whole} seconds")


def _for_window(text: str, seconds: float) -> str:
    """The page judges the last minute. The command judges only the time it measured."""

    return text.replace(PAGE_WINDOW_WORDS, _window_words(seconds))


@register("impact", _impact_parser)
def _impact(arguments: argparse.Namespace) -> int:
    client = _client(arguments)
    first = _impact_sample(client)
    if not arguments.json:
        _err(f"Measuring for {arguments.wait} seconds...")
    time.sleep(arguments.wait)
    second = _impact_sample(client)
    # The measured time is what the Pi's own clock says passed between its two samples.
    gap = second["sampled_at"] - first["sampled_at"]
    if gap <= 0:
        raise RemoteError(
            "The Pi's clock did not move forward between the two samples. Run the command again."
        )
    if gap > impact_rules.IMPACT_WINDOW_SECONDS:
        raise RemoteError(
            f"The two samples were {js_number(round(gap, 1))} seconds apart: more than the "
            "minute the counters can be compared over. Run the command again with a shorter "
            "--wait."
        )
    rows = impact_rules.two_sample_rows(first, second)
    for row in rows:
        row["verdict_text"] = _for_window(impact_rules.verdict_label(row["verdict"]), gap)
        row["lines"] = [_for_window(line, gap) for line in row["lines"]]
    protected = [str(name) for name in second.get("protected") or []]
    window = round(gap, 1)
    if arguments.json:
        _print_json({
            "window_seconds": float(window),
            "rows": [
                {
                    "name": row["name"],
                    "direction": row["direction"],
                    "verdict": row["verdict"],
                    "verdict_text": row["verdict_text"],
                    "lines": row["lines"] if row["verdict"] != "collecting" else [],
                    "configured": row["configured"],
                    "offered": row["offered"],
                    "packets": row["packets"],
                    "drops": row["drops"],
                    "loss_percent": float(row["lossPercent"]),
                    "rate_kbit": row["rateKbit"],
                    "peak_kbit": row["peakKbit"],
                    "cap_reached": row["capReached"],
                    "queue_wait_ms": row["queueWaitMs"],
                }
                for row in rows
            ],
            "protected": protected,
        })
        return 0
    if not rows:
        _out(impact_rules.IMPACT_NOTHING_SLOWED)
    for row in rows:
        direction = "Upload" if row["direction"] == "upload" else "Download"
        _out(f"{row['name']} · {direction} · {row['verdict_text']}")
        if row["verdict"] != "collecting":
            for line in row["lines"]:
                _out(f"  {line}")
    if any(row["verdict"] == "collecting" for row in rows):
        # The gap is inside the window so only new counters explain a missing comparison.
        _out("A queue was replaced or changed between the two samples. Run the command again.")
    if protected:
        _out("Protected (not affected): " + ", ".join(protected))
    _out(f"Measured over {js_number(window)} seconds. " + impact_rules.IMPACT_NOTE)
    return 0


# --- end: impact


# --- section: remote update --------------------------------------------------------------

DEFAULT_ARCHIVE = Path("jittersim-release.tar.gz")


def _remote_update_parser() -> argparse.ArgumentParser:
    parser = _parser(
        "jittersim remote update",
        "Deploy a signed release through the Pi's HTTPS dashboard when SSH is not possible, "
        "for example from the Pi's test Wi-Fi. 'status' shows the release the Pi runs and a "
        "staged one. 'upload' sends a release archive and its .sig file (build them with "
        "deploy/raspberry-pi/build-release.sh REF OUTPUT KEY): the Pi stages it only when a key "
        "it trusts signed it and it is newer than what it ran before. 'install' installs the "
        "staged release, and with --wait waits until the Pi runs it. 'check' asks the Pi to look "
        "for a newer signed release on GitHub with its own read-only token. 'now' checks, shows "
        "the release, asks and installs it, then waits until the Pi runs it (a failed health "
        "check restores the previous installation on the Pi by itself).",
    )
    parser.add_argument("action", choices=("status", "check", "now", "upload", "install"))
    parser.add_argument("--archive", type=Path, default=DEFAULT_ARCHIVE,
                        help=f"upload: the release archive (default: {DEFAULT_ARCHIVE})")
    parser.add_argument("--signature", type=Path,
                        help="upload: its signature (default: the archive name plus .sig)")
    parser.add_argument("--wait", type=float, default=None, metavar="SECONDS",
                        help="install: wait up to this long until the Pi runs the release "
                        f"(now: default {NOW_WAIT_SECONDS:.0f})")
    parser.add_argument("--yes", action="store_true", help="now: install without asking")
    return parser


NOW_WAIT_SECONDS = 900.0
CHECK_WAIT_SECONDS = 420.0
ACTIVE_PHASES = ("queued", "preparing", "stopping", "backing-up", "installing", "checking",
                 "rolling-back")


class _Report:
    """Progress text for people. With --json nothing is printed until the one final answer."""

    def __init__(self, quiet: bool) -> None:
        self.quiet = quiet

    def line(self, text: str) -> None:
        if not self.quiet:
            _out(text)


def _online_check(client: DashboardClient) -> Dict[str, Any]:
    """Ask the Pi to check GitHub and wait for the answer. The Pi uses its own token."""

    view = _require(client.post("/api/updates/check-online", {"force": True},
                                timeout=ACTION_TIMEOUT_SECONDS), dict)
    job = view.get("job")
    deadline = time.monotonic() + CHECK_WAIT_SECONDS
    while view.get("state") == "running":
        if not isinstance(job, str) or not re.fullmatch(r"[0-9a-f]{16}", job):
            raise RemoteError(UNDERSTOOD_MESSAGE)
        if time.monotonic() >= deadline:
            raise RemoteError("The Pi's online check did not finish. Check again later with: "
                              "jittersim remote update status")
        time.sleep(2)
        view = _require(client.get("/api/updates/check-online/" + job), dict)
    return view


def _follow(client: DashboardClient, request_id: Optional[str], staged: str, wait: float,
            report: _Report) -> Dict[str, Any]:
    """Follow one install until it ends. Success only when that request reached `complete` and
    the Pi runs STAGED. A restore is claimed only when the Pi reports `rolled-back`."""

    deadline = time.monotonic() + max(0.0, wait)
    last = None
    while True:
        time.sleep(5)
        try:
            now = client.get("/api/updates")
        except RemoteError:
            now = {}  # The dashboard restarts during the install.
        phase = now.get("phase")
        if now and phase != last:
            report.line(f"Pi: {printable(phase or 'unknown')}. {printable(now.get('message') or '')}".rstrip())
            last = phase
        ours = request_id is None or now.get("request_id") == request_id
        if now and now.get("request_id") and not ours:
            raise RemoteError("Another update replaced this one on the Pi. Check with: "
                              "jittersim remote update status")
        if ours and phase == "complete":
            if now.get("installed") == staged:
                return {"result": "installed", "installed": staged, "request_id": request_id}
            raise RemoteError(f"The update finished but the Pi runs {printable(now.get('installed'))}, "
                              f"not {printable(staged)}.")
        if ours and phase == "rolled-back":
            raise RemoteError(f"Release {printable(staged)} failed on the Pi and the Pi confirmed that "
                              f"the previous installation ({printable(now.get('installed') or 'unknown')}) "
                              "was restored.")
        if ours and phase == "failed":
            raise RemoteError("The update failed and recovery is not confirmed: "
                              f"{printable(now.get('message') or 'no details')}. Check the Pi.")
        if time.monotonic() >= deadline:
            raise RemoteError(f"The update of {printable(staged)} has not finished yet. "
                              "Check again with: jittersim remote update status")


def _install_and_wait(client: DashboardClient, staged: str, wait: float,
                      report: _Report) -> Dict[str, Any]:
    queued = _require(client.post("/api/updates/install", {"version": staged, "confirm": True},
                                  timeout=ACTION_TIMEOUT_SECONDS), dict)
    report.line(f"Install of release {printable(staged)} queued.")
    request_id = queued.get("request_id")
    request_id = request_id if isinstance(request_id, str) else None
    if wait <= 0:
        return {"result": "queued", "request_id": request_id, "version": staged}
    result = _follow(client, request_id, staged, wait, report)
    report.line(f"The Pi now runs release {printable(staged)} and passed its health check.")
    return result


def _release_lines(data: Dict[str, Any]) -> None:
    installed, staged = data.get("installed"), data.get("available")
    _out(f"Release on the Pi: {printable(installed) if installed else 'unknown'}")
    _out(f"Staged release: {printable(staged) if staged else 'none'}")
    if staged and data.get("installable") is False and data.get("reason"):
        _out(f"It cannot be installed yet: {printable(data['reason'])}")
    elif data.get("message"):
        _out(printable(data["message"]))


@register("remote update", _remote_update_parser)
def _remote_update(arguments: argparse.Namespace) -> int:
    if arguments.action == "now" and arguments.json and not arguments.yes:
        _err("'now --json' cannot ask a question. Add --yes.")
        return 2
    client = _client(arguments)
    if arguments.action == "status":
        data = client.get("/api/updates")
        if arguments.json:
            _print_json(data)
        else:
            _release_lines(data)
        return 0
    if arguments.action == "upload":
        signature = arguments.signature or arguments.archive.with_name(
            arguments.archive.name + ".sig")
        data = client.upload_release(arguments.archive, signature)
        staged = data.get("staged_version") or data.get("available")
        if not isinstance(staged, str) or not staged:
            raise RemoteError(UNDERSTOOD_MESSAGE)
        if arguments.json:
            _print_json(data)
        else:
            _out(f"The Pi checked the signature and staged release {printable(staged)}.")
            _out("Install it with: jittersim remote update install --wait 600")
        return 0
    report = _Report(arguments.json)
    wait = arguments.wait
    if arguments.action == "now":
        wait = NOW_WAIT_SECONDS if wait is None else wait
        status = client.get("/api/updates")
        if status.get("phase") in ACTIVE_PHASES:
            # Already installing, for example after the daily check. Follow it, do not ask.
            version = status.get("request_version") or status.get("available")
            report.line(f"An update to {printable(version or 'a new release')} is already running "
                        "on the Pi. Following it.")
            result = _follow(client, status.get("request_id"), version, wait, report)
            return _finish(arguments, report, result, f"The Pi now runs release {printable(version)}.")
    if arguments.action in ("check", "now"):
        view = _online_check(client)
        if view.get("result") == "failed":
            raise RemoteError("The Pi could not check GitHub: "
                              f"{printable(view.get('message') or 'no details')}. Nothing was installed.")
        if arguments.action == "check":
            release = client.get("/api/updates")
            if arguments.json:
                _print_json({"check": view, "release": release})
            else:
                _out(printable(view.get("message") or ""))
                _release_lines(release)
            return 0
        report.line(printable(view.get("message") or ""))
        status = client.get("/api/updates")
        if view.get("install_queued") is True or status.get("phase") in ACTIVE_PHASES:
            # Install automatically is on and the Pi already started the install.
            version = status.get("request_version") or status.get("available")
            report.line("Install automatically is on, so the Pi already started installing "
                        f"{printable(version or 'the release')}. Following it.")
            result = _follow(client, status.get("request_id"), version, wait, report)
            return _finish(arguments, report, result, "")
    else:
        status = client.get("/api/updates")
    staged = status.get("available")
    if arguments.action == "now" and (not staged or staged == status.get("installed")):
        return _finish(arguments, report, {"result": "no-newer-release",
                                           "installed": status.get("installed")},
                       f"No newer release. The Pi runs {printable(status.get('installed') or 'an unknown release')}.")
    if not isinstance(staged, str) or not staged:
        raise RemoteError("No release is staged on the Pi. Upload one first.")
    if status.get("installable") is False:
        raise RemoteError(
            printable(status.get("reason") or "The staged release cannot be installed."))
    if arguments.action == "now":
        if (status.get("phase") in ("failed", "rolled-back") and status.get("request_kind") == "install"
                and status.get("request_version") == staged and arguments.yes):
            raise RemoteError(f"Release {printable(staged)} failed on the Pi before. Run without "
                              "--yes to try it once more.")
        report.line(f"Release on the Pi: {printable(status.get('installed') or 'unknown')}")
        report.line(f"New release: {printable(staged)}, signed by "
                    f"{printable(status.get('signed_by') or 'unknown')}")
        if isinstance(status.get("notes"), str):
            for line in status["notes"].splitlines()[:60]:
                report.line("  " + line)
        if not arguments.yes and not _ask(f"Install {staged} on the Pi now?"):
            _out("Cancelled. Nothing was installed.")
            return 0
    result = _install_and_wait(client, staged, wait or 0.0, report)
    return _finish(arguments, report, result, "")


def _finish(arguments: argparse.Namespace, report: _Report, result: Dict[str, Any],
            text: str) -> int:
    if arguments.json:
        _print_json(result)
    elif text:
        report.line(text)
    return 0


def _ask(question: str) -> bool:
    try:
        answer = input(printable(question) + " Type yes to continue: ")
    except EOFError:
        return False
    return answer.strip().lower() == "yes"


# --- end: remote update


def _command_words(argv: List[str]) -> List[str]:
    """Return the registered command name followed by the arguments for it."""

    command = argv[0]
    rest = argv[1:]
    if command in GROUPS:
        group = GROUPS[command]
        if not rest or rest[0] not in group["choices"]:
            parser = _Parser(prog=f"jittersim {command}", description=group["description"],
                             epilog=_epilog())
            commands = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")
            for choice in group["choices"]:
                commands.add_parser(choice, add_help=False)
            parser.parse_args(rest)
        return [command + " " + rest[0], *rest[1:]]
    if rest[:1] and f"{command} {rest[0]}" in HANDLERS:
        return [f"{command} {rest[0]}", *rest[1:]]
    return [command, *rest]


def main(argv: Sequence[str]) -> int:
    """Run one remote command. Exit 0 on success, 1 on failure and 2 on a usage error."""

    argv = list(argv)
    if _secret_argument(argv):
        print(
            "Secrets are never accepted as arguments. The command asks for them with a hidden "
            f"prompt. Scripts may set {TOKEN_ENV} for the admin token.",
            file=sys.stderr,
        )
        return 2
    words = _command_words(argv)
    if words[0] not in PARSERS:
        print("Unknown remote command. Run 'jittersim --help'.", file=sys.stderr)
        return 2
    arguments = PARSERS[words[0]]().parse_args(words[1:])
    try:
        return HANDLERS[words[0]](arguments)
    except RemoteError as error:
        return _failed(arguments, error.message, error.exit_code)
    except KeyboardInterrupt:
        return _failed(arguments, "Cancelled.", 1)
    except (KeyError, TypeError, ValueError, AttributeError, IndexError, RecursionError):
        return _failed(arguments, UNDERSTOOD_MESSAGE, 1)
    except OSError as error:
        return _failed(arguments, f"The command failed: {error.strerror or 'system error'}.", 1)


def _failed(arguments: argparse.Namespace, message: str, code: int) -> int:
    """With --json a failure is JSON on stdout too so scripts always read one shape."""

    if getattr(arguments, "json", False):
        _print_json({"error": message, "exit_code": code})
    else:
        _err(message)
    return code
