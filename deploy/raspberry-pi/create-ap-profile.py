#!/usr/bin/env python3
"""Create only a new inactive secured AP without exposing its password."""

import argparse
import os
import re
import runpy
import selectors
import subprocess
import sys
import time
import uuid
from pathlib import Path

AP_NAME = "jittersim-ap"
PENDING_PREFIX = "jittersim-initial-ap-pending:"
TIMEOUT = 3
MAX_PROFILE_BYTES = 16384
PASSWORD_VARIABLES = ("JITTERSIM_AP_PASSWORD", "ap_password", "jittersim_initial_ap_secret")


class ProfileError(RuntimeError):
    """A private profile operation could not be confirmed."""


def read_bounded(stream, limit, deadline):
    result = bytearray()
    with selectors.DefaultSelector() as selector:
        selector.register(stream, selectors.EVENT_READ)
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not selector.select(remaining):
                raise ProfileError("private operation timed out")
            chunk = os.read(stream.fileno(), limit + 1 - len(result))
            if not chunk:
                return bytes(result)
            result.extend(chunk)
            if len(result) > limit:
                raise ProfileError("private input exceeded its limit")


def command(arguments, *, private_input=None, expected=None, limit=0):
    process = None
    failed = False
    result = b""
    deadline = time.monotonic() + TIMEOUT
    environment = {key: value for key, value in os.environ.items()
                   if key not in PASSWORD_VARIABLES}
    environment.update(HOME="/dev/null", XDG_CACHE_HOME="/dev/null")
    try:
        process = subprocess.Popen(
            ("nmcli", *arguments),
            stdin=subprocess.PIPE if private_input is not None else subprocess.DEVNULL,
            stdout=subprocess.PIPE if expected is not None or limit else subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, env=environment,
            close_fds=True,
        )
        if private_input is not None:
            process.communicate(input=private_input, timeout=max(0.001, deadline - time.monotonic()))
        elif expected is not None or limit:
            result = read_bounded(process.stdout, len(expected) if expected is not None else limit,
                                  deadline)
            process.wait(timeout=max(0.001, deadline - time.monotonic()))
        else:
            process.wait(timeout=max(0.001, deadline - time.monotonic()))
        failed = process.returncode != 0 or (expected is not None and result != expected)
    except (OSError, subprocess.TimeoutExpired, ProfileError):
        failed = True
    finally:
        if process is not None:
            try:
                if process.poll() is None:
                    process.kill()
            except OSError:
                failed = True
            try:
                process.wait(timeout=1)
            except (OSError, subprocess.TimeoutExpired):
                failed = True
            for stream in (process.stdin, process.stdout):
                if stream is not None:
                    try:
                        stream.close()
                    except (OSError, ValueError):
                        failed = True
    if failed:
        raise ProfileError("private profile operation failed")
    return result if expected is None else b""


def profile_state(interface):
    listing = command(("--escape", "no", "--terse", "--fields", "NAME,UUID",
                       "connection", "show"), limit=MAX_PROFILE_BYTES).decode("utf-8")
    matches = []
    for line in listing.splitlines():
        name, separator, identifier = line.rpartition(":")
        if not separator or str(uuid.UUID(identifier)) != identifier:
            raise ProfileError("profile list is ambiguous")
        if name == AP_NAME:
            matches.append(identifier)
    if not matches:
        return "absent"
    if len(matches) != 1:
        raise ProfileError("profile name is ambiguous")
    identifier = matches[0]
    fields = ("connection.type", "connection.interface-name", "connection.autoconnect",
              "802-11-wireless.mode", "802-11-wireless-security.key-mgmt", "connection.stable-id")
    values = command(("--escape", "no", "-g", ",".join(fields), "connection", "show",
                      "uuid", identifier), limit=4096).decode("utf-8").splitlines()
    if len(values) != len(fields) or values[0] != "802-11-wireless" or values[1] != interface:
        raise ProfileError("profile ownership could not be confirmed")
    if values[5].startswith(PENDING_PREFIX):
        if (values[5] != PENDING_PREFIX + identifier or values[2:5] != ["no", "ap", "sae"]):
            raise ProfileError("incomplete profile ownership could not be confirmed")
        raise ProfileError("Initial Wi-Fi setup is incomplete. Use management access to "
                           "inspect the inactive profile before running the installer again.")
    return "present"


def validate_input(interface, ssid, password):
    if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,15}", interface):
        raise ProfileError("invalid test interface")
    if not 1 <= len(ssid.encode("utf-8")) <= 32 or any(ord(c) < 32 or ord(c) == 127 for c in ssid):
        raise ProfileError("invalid initial network name")
    if not 8 <= len(password) <= 63 or any(value < 32 or value > 126 for value in password):
        raise ProfileError("invalid initial password")


def check_fresh_settings(identifier, interface, marker):
    fields = ("connection.uuid", "connection.type", "connection.interface-name",
              "connection.autoconnect", "802-11-wireless.mode", "802-11-wireless-security.key-mgmt",
              "802-11-wireless-security.proto", "802-11-wireless-security.pairwise",
              "802-11-wireless-security.group", "802-11-wireless-security.pmf", "connection.stable-id")
    expected = "\n".join((identifier, "802-11-wireless", interface, "no", "ap", "sae",
                          "rsn", "ccmp", "ccmp", "3", marker)) + "\n"
    command(("--escape", "no", "-g", ",".join(fields), "connection", "show", "uuid", identifier),
            expected=expected.encode())


def create_profile(interface, ssid, password):
    validate_input(interface, ssid, password)
    if profile_state(interface) != "absent":
        raise ProfileError("an existing profile must be preserved")
    identifier = str(uuid.uuid4())
    marker = PENDING_PREFIX + identifier
    failed = False
    try:
        command(("connection", "add", "type", "wifi", "ifname", interface,
                 "con-name", AP_NAME, "ssid", ssid, "connection.uuid", identifier,
                 "connection.autoconnect", "no", "802-11-wireless.mode", "ap",
                 "802-11-wireless-security.key-mgmt", "sae", "802-11-wireless-security.proto", "rsn",
                 "802-11-wireless-security.pairwise", "ccmp", "802-11-wireless-security.group", "ccmp",
                 "802-11-wireless-security.pmf", "3", "connection.stable-id", marker))
        check_fresh_settings(identifier, interface, marker)
        command(("--colors", "no", "connection", "edit", "uuid", identifier),
                private_input=b"set 802-11-wireless-security.psk\n" + password
                + b"\nsave persistent\nquit\n")
        command(("--escape", "no", "--show-secrets", "-g", "802-11-wireless-security.psk",
                 "connection", "show", "uuid", identifier), expected=password + b"\n")
        command(("connection", "modify", "uuid", identifier, "connection.stable-id", ""))
        check_fresh_settings(identifier, interface, "")
    except (OSError, ValueError, ProfileError):
        failed = True
    if failed:
        try:
            command(("--wait", "3", "connection", "delete", "uuid", identifier))
        except ProfileError:
            raise ProfileError("Initial Wi-Fi setup failed and cleanup could not be confirmed. "
                               "Use management access to inspect the inactive profile.") from None
        raise ProfileError("Initial Wi-Fi setup failed. The new profile was removed.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("probe", "create"))
    parser.add_argument("--interface", required=True)
    parser.add_argument("--ssid", default="TestNet")
    arguments = parser.parse_args()
    try:
        lock = runpy.run_path(str(Path(__file__).with_name("install-lock.py")))
        lock["verify_lock"]()
        if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,15}", arguments.interface):
            raise ProfileError("invalid test interface")
        if arguments.action == "probe":
            print(profile_state(arguments.interface))
            return 0
        supplied = read_bounded(sys.stdin.buffer, 64, time.monotonic() + TIMEOUT)
        if not supplied.endswith(b"\n"):
            raise ProfileError("initial password input is incomplete")
        create_profile(arguments.interface, arguments.ssid, supplied[:-1])
        return 0
    except Exception:
        print("Initial Wi-Fi setup could not be confirmed. Use management access to "
              "inspect the network profile and update status.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
