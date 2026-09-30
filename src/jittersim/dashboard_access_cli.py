"""Dashboard enrollment from a Mac and local recovery on the Pi."""

import argparse
import getpass
import json
import os
import ssl
import sys
import warnings
from pathlib import Path
from typing import Sequence
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, HTTPSHandler, Request, build_opener

from jittersim.networking.dashboard_access import AccessError, normalize_mac, validate_label
from jittersim.networking.helper import HelperClient, HelperClientError


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def register_self(url: str, certificate: Path, name: str) -> dict:
    """Send a token only to the specified certificate-verified HTTPS endpoint."""
    parsed = urlsplit(url)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
            or parsed.path not in ("", "/") or parsed.query or parsed.fragment):
        raise AccessError("Use the Pi's HTTPS dashboard URL without a path or credentials.")
    name = validate_label(name)
    context = ssl.create_default_context(cafile=str(certificate))
    with warnings.catch_warnings():
        warnings.simplefilter("error", getpass.GetPassWarning)
        token = getpass.getpass("JitterSim admin token (hidden): ").strip()
    if not token or any(ord(character) < 33 or ord(character) > 126 for character in token):
        raise AccessError("Enter a valid JitterSim admin token.")
    request = Request(url.rstrip("/") + "/api/dashboard-access/register-self",
                      data=json.dumps({"name": name}).encode(), method="POST",
                      headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"})
    opener = build_opener(_NoRedirect(), HTTPSHandler(context=context))
    with opener.open(request, timeout=10) as response:
        body = response.read(64 * 1024 + 1)
    if len(body) > 64 * 1024:
        raise AccessError("The dashboard response was too large.")
    result = json.loads(body)
    if not isinstance(result, dict) or result.get("allowed") is not True:
        raise AccessError("The dashboard did not confirm saved access.")
    return result


def main(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(prog="jittersim dashboard-access")
    commands = parser.add_subparsers(dest="command", required=True)
    enroll = commands.add_parser("allow-self", help="allow this Mac or computer over HTTPS")
    enroll.add_argument("--url", required=True, help="https://Pi-address:8080")
    enroll.add_argument("--ca-cert", required=True, type=Path, help="trusted Pi dashboard certificate")
    enroll.add_argument("--name", default="")
    for command in ("add", "remove", "list", "status"):
        local = commands.add_parser(command, help="Pi root terminal recovery")
        local.add_argument("--helper-socket", type=Path, default=Path("/run/jittersim/helper.sock"))
        if command in ("add", "remove"):
            local.add_argument("mac_address")
        if command == "add":
            local.add_argument("--name", default="")
    arguments = parser.parse_args(argv)
    try:
        if arguments.command == "allow-self":
            register_self(arguments.url, arguments.ca_cert, arguments.name)
            print("This device has saved dashboard access. Its internet traffic still follows profiles.")
            return 0
        if os.geteuid() != 0:
            raise AccessError("Run this recovery command with sudo on the Pi. "
                              "On your Mac use dashboard-access allow-self instead.")
        mac = normalize_mac(arguments.mac_address) if hasattr(arguments, "mac_address") else None
        operation = "local-list" if arguments.command == "status" else "local-" + arguments.command
        result = HelperClient(socket_path=arguments.helper_socket).dashboard_access(
            operation, mac_address=mac, name=validate_label(getattr(arguments, "name", "")))
        print(json.dumps(result["devices"], indent=2))
        return 0
    except HTTPError as error:
        print(f"Dashboard registration failed (HTTP {error.code}). Check the admin token "
              "and connect directly to the Pi Wi-Fi.", file=sys.stderr)
    except (AccessError, HelperClientError) as error:
        print(f"Dashboard access failed: {error}", file=sys.stderr)
    except (OSError, URLError, ValueError,
            getpass.GetPassWarning, EOFError, KeyboardInterrupt):
        print("Dashboard access failed. Check the URL, trusted Pi certificate and admin token. "
              "Use a terminal that supports a hidden password prompt.", file=sys.stderr)
    return 1
