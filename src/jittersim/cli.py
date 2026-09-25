"""Command-line entry point for policy simulation and emergency control."""

import argparse
import shlex
import sqlite3
import sys
from pathlib import Path
from typing import Optional, Sequence

from jittersim.config.models import Device, Impairment, Profile
from jittersim.config.storage import ConfigRepository, StorageError
from jittersim.engine.compiler import compile_plan
from jittersim.networking.commands import Command
from jittersim.networking.helper import HelperClient, HelperClientError
from jittersim.networking.renderer import render_command_plan
from jittersim.networking.topology import Topology

CONTROL_COMMANDS = ("reset", "status")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Render a jittersim policy without changing the host network. "
            "Use 'jittersim reset' or 'jittersim status' for emergency control."
        )
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


def _build_control_parser(command: str) -> argparse.ArgumentParser:
    descriptions = {
        "reset": "Remove every JitterSim network change, even when the dashboard is down.",
        "status": "Show the last recorded network action and whether the helper answers.",
    }
    parser = argparse.ArgumentParser(prog=f"jittersim {command}", description=descriptions[command])
    parser.add_argument(
        "--database", type=Path, default=Path("/var/lib/jittersim/jittersim.db")
    )
    parser.add_argument(
        "--helper-socket", type=Path, default=Path("/run/jittersim/helper.sock")
    )
    parser.add_argument("--timeout-seconds", type=float, default=15.0)
    return parser


def _open_repository(database: Path) -> Optional[ConfigRepository]:
    # Never create a new database here: a missing file means the wrong path.
    if not database.is_file():
        return None
    try:
        return ConfigRepository(database)
    except (StorageError, sqlite3.Error):
        return None


def _record(
    repository: Optional[ConfigRepository], observed_state: str, result: Optional[str]
) -> None:
    if repository is None:
        return
    try:
        repository.update_runtime_state(
            observed_state=observed_state,
            last_action="reset",
            last_result=result,
            last_error=None if result != "failure" else "network helper reset failed",
        )
    except (StorageError, sqlite3.Error):
        print("warning: could not record the reset in the database", file=sys.stderr)


def _reset(arguments: argparse.Namespace) -> int:
    repository = _open_repository(arguments.database)
    if repository is None:
        print(
            f"warning: could not record the reset in {arguments.database}; "
            "the dashboard may apply the plan again when it restarts",
            file=sys.stderr,
        )
    try:
        # Record the reset first. A running dashboard sees it and stops
        # applying scheduled changes.
        _record(repository, "resetting", None)
        client = HelperClient(
            socket_path=arguments.helper_socket, timeout_seconds=arguments.timeout_seconds
        )
        try:
            client.reset()
        except HelperClientError as error:
            _record(repository, "error", "failure")
            print(f"Reset failed: {error}", file=sys.stderr)
            return 1
        _record(repository, "reset", "success")
    finally:
        if repository is not None:
            repository.close()
    print("Network reset. Test devices use the network without JitterSim changes.")
    return 0


def _status(arguments: argparse.Namespace) -> int:
    repository = _open_repository(arguments.database)
    if repository is None:
        print(f"Database: not readable at {arguments.database}")
    else:
        try:
            state = repository.get_runtime_state()
        finally:
            repository.close()
        action = state.last_action or "none"
        result = f" ({state.last_result})" if state.last_result else ""
        print(f"Last network action: {action}{result}")
        print(f"Network state: {state.observed_state}")
        print(f"Settings version: {state.configuration_version}")
    client = HelperClient(
        socket_path=arguments.helper_socket, timeout_seconds=arguments.timeout_seconds
    )
    try:
        client.ping()
    except HelperClientError as error:
        print(f"Helper: not answering ({error})")
        return 1
    print("Helper: answering")
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Run a control command, or render a demo profile without applying it."""

    raw_arguments = list(sys.argv[1:] if argv is None else argv)
    if raw_arguments and raw_arguments[0] in CONTROL_COMMANDS:
        command = raw_arguments[0]
        arguments = _build_control_parser(command).parse_args(raw_arguments[1:])
        return _reset(arguments) if command == "reset" else _status(arguments)
    if raw_arguments and raw_arguments[0] == "simulate":
        raw_arguments = raw_arguments[1:]
    return _simulate(raw_arguments)


def _simulate(argv: Sequence[str]) -> int:
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
