"""Commands that run on your Mac or another computer against the Pi's HTTPS dashboard.

`jittersim connect`, `status`, `profiles`, `traffic`, `wifi show` and `impact` read and change
the Pi through the same verified dashboard API as the browser. They ask for the admin token
with a hidden prompt every time and never store it. Each command registers its parser and
its handler below.
"""

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Sequence

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
    """argparse without echoing what it did not understand: that could be a pasted secret."""

    def error(self, message: str) -> None:
        if message.startswith("unrecognized arguments"):
            message = "unrecognized arguments (not shown here)"
        message = re.sub(
            r"invalid choice: .*? \(choose from", "invalid choice (choose from", message
        )
        super().error(message)


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
    print(text)


def _print_json(value: Any) -> None:
    print(json.dumps(value, indent=2, ensure_ascii=False))


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
    certificate = arguments.ca_cert.expanduser()
    if not certificate.is_file():
        raise RemoteError(f"The certificate file {certificate} does not exist.", exit_code=2)
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
        print(error.message, file=sys.stderr)
        return error.exit_code
    except KeyboardInterrupt:
        print("Cancelled.", file=sys.stderr)
        return 1
    except (KeyError, TypeError, ValueError, AttributeError, IndexError):
        print(UNDERSTOOD_MESSAGE, file=sys.stderr)
        return 1
    except OSError as error:
        print(f"The command failed: {error.strerror or 'system error'}.", file=sys.stderr)
        return 1
