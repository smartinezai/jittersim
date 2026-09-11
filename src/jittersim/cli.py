"""Command-line entry point for safe policy simulation."""

import argparse
import shlex
import sys
from typing import Optional, Sequence

from jittersim.config.models import Device, Impairment, Profile
from jittersim.engine.compiler import compile_plan
from jittersim.networking.commands import Command
from jittersim.networking.renderer import render_command_plan
from jittersim.networking.topology import Topology


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

    parser = _build_parser()
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


if __name__ == "__main__":
    sys.exit(main())
