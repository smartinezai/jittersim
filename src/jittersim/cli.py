"""Command-line entry point for safe policy simulation."""

import argparse
import shlex
import sys
from pathlib import Path
from typing import Optional, Sequence

from jittersim.config.models import Device, Impairment, Profile
from jittersim.engine.compiler import compile_plan
from jittersim.networking.commands import Command
from jittersim.networking.helper import HelperClient, HelperClientError
from jittersim.networking.renderer import render_command_plan
from jittersim.networking.topology import Topology
from jittersim.networking.wifi import DEFAULT_WIFI_NAME
from jittersim.networking.wifi_security import SECURITY_MODES


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Render a jittersim policy without changing the host network."
    )
    parser.add_argument("--uplink", default="eth0", help="Pi interface facing the router")
    parser.add_argument("--test-interface", default="wlan0", help="interface facing test devices")
    parser.add_argument("--device-id", default="demo-device")
    parser.add_argument("--device-ip", default="192.168.50.10")
    parser.add_argument("--delay-ms", type=int, default=3_000)
    parser.add_argument("--jitter-ms", type=int, default=700)
    parser.add_argument("--loss-percent", type=int, default=10)
    parser.add_argument("--bandwidth-kbit", type=int, default=2_000)
    return parser


def _format_command(command: Command) -> str:
    return " ".join(shlex.quote(argument) for argument in command.argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Render a deliberately inconvenient demo profile without applying it."""

    raw_arguments = list(sys.argv[1:] if argv is None else argv)
    if raw_arguments and raw_arguments[0] == "wifi":
        return _wifi_command(raw_arguments[1:])
    parser = _build_parser()
    parser.epilog = "Restore the test Wi-Fi name with 'jittersim wifi reset'."
    arguments = parser.parse_args(argv)
    profile = Profile(
        id="demo",
        name="Demo impairment",
        upload=Impairment(
            delay_ms=arguments.delay_ms,
            jitter_ms=arguments.jitter_ms,
            loss_percent=arguments.loss_percent,
            bandwidth_kbit=arguments.bandwidth_kbit,
        ),
        download=Impairment(
            delay_ms=arguments.delay_ms,
            jitter_ms=arguments.jitter_ms,
            loss_percent=arguments.loss_percent,
            bandwidth_kbit=arguments.bandwidth_kbit,
        ),
    )
    device = Device(
        id=arguments.device_id,
        name=arguments.device_id,
        ipv4_address=arguments.device_ip,
        profile_id=profile.id,
    )
    plan = compile_plan(devices=(device,), profiles={profile.id: profile})
    commands = render_command_plan(
        plan=plan,
        topology=Topology(
            uplink_interface=arguments.uplink,
            test_interface=arguments.test_interface,
        ),
    )

    print("Simulation mode. No commands are being executed.")
    for command in commands:
        if command.ignore_failure:
            print("(ignore failure)", end=" ")
        print("$", _format_command(command))
        if command.stdin:
            print(command.stdin, end="")
    return 0


def _wifi_command(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(prog="jittersim wifi")
    commands = parser.add_subparsers(dest="wifi_command", required=True)
    reset = commands.add_parser("reset", help="restore the name and keep the password")
    reset.add_argument("--yes", action="store_true", help="confirm without a prompt")
    reset.add_argument("--helper-socket", type=Path, default=Path("/run/jittersim/helper.sock"))
    arguments = parser.parse_args(argv)
    if not arguments.yes:
        try:
            answer = input("Restore the Wi-Fi name to JitterSim and disconnect test clients? "
                           "The password stays the same. Type yes to continue: ")
        except EOFError:
            answer = ""
        if answer.strip().lower() != "yes":
            print("Wi-Fi name reset cancelled.")
            return 0
    try:
        result = HelperClient(socket_path=arguments.helper_socket).reset_wifi_name()
        if result.get("ssid") != DEFAULT_WIFI_NAME or result.get("security") not in SECURITY_MODES:
            raise HelperClientError("The helper did not confirm the expected Wi-Fi settings.")
    except HelperClientError as error:
        print(f"Wi-Fi name reset failed: {error}", file=sys.stderr)
        print("Use the Pi's management connection or a local terminal. "
              "Check sudo systemctl status jittersim-helper.service "
              "and sudo nmcli connection show jittersim-ap.", file=sys.stderr)
        return 1
    print("Wi-Fi name restored to JitterSim with " + result["security"] + ". "
          "Reconnect test devices using the same password.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
