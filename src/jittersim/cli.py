"""Command-line entry point for policy simulation and emergency control."""

import argparse
import json
import os
import shlex
import sqlite3
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from typing import Optional, Sequence

from jittersim.config.models import AccessOptions, Device, Impairment, Profile
from jittersim.config.storage import ConfigRepository, StorageError
from jittersim.engine.access import build_access_plan
from jittersim.engine.compiler import compile_plan
from jittersim.measure import main as measure_main
from jittersim.networking.commands import Command
from jittersim.networking.helper import HelperClient, HelperClientError
from jittersim.networking.renderer import render_command_plan
from jittersim.networking.topology import Topology
from jittersim.networking.wifi import DEFAULT_WIFI_NAME
from jittersim.networking.wifi_security import SECURITY_MODES

CONTROL_COMMANDS = ("reset", "normal-access", "status")
MEASUREMENT_COMMANDS = ("measure", "compare")
LOG_SERVICES = {
    "access-boot": "jittersim-access-boot.service",
    "ap": "jittersim-ap.service",
    "browser-proxy": "jittersim-browser-proxy.service",
    "control-firewall": "jittersim-control-firewall.service",
    "dashboard": "jittersim-dashboard.service",
    "dns": "jittersim-dns.service",
    "helper": "jittersim-helper.service",
    "service-watch": "jittersim-service-watch.service",
    "update": "jittersim-update.service",
}
MAX_LOG_LINES = 200
MAX_LOG_OUTPUT_CHARS = 32_000
REMOTE_COMMANDS = ("connect", "profiles", "traffic", "impact", "remote")
# `jittersim update` commands that the root update manager runs in this process.
LOCAL_UPDATE_COMMANDS = {
    "stage": "verify a signed release archive and stage it (root)",
    "trust-key": "trust one ed25519 release signing key (root)",
    "untrust-key": "stop trusting a release signing key (root)",
    "list-keys": "show the trusted release signing keys",
    "enable-passwordless-staging": "let the jittersim login stage signed releases "
                                   "without a password (root)",
    "disable-passwordless-staging": "ask for the sudo password again (root)",
}
UPDATE_SERVICE = "jittersim-update.service"
UPDATE_WAIT_SECONDS = 20 * 60


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Render a jittersim policy without changing the host network. "
            "Use 'jittersim reset' or 'jittersim status' for emergency control, "
            "and 'jittersim measure' or 'jittersim compare' on a test device."
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
        "reset": "Remove experiment effects while preserving household access rules.",
        "normal-access": "Stop all profiles and household restrictions while keeping saved settings.",
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


def _add_local_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--database", type=Path, default=Path("/var/lib/jittersim/jittersim.db")
    )
    parser.add_argument(
        "--helper-socket", type=Path, default=Path("/run/jittersim/helper.sock")
    )
    parser.add_argument("--timeout-seconds", type=float, default=15.0)


def _parse_log_lines(value: str) -> int:
    try:
        lines = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("line count must be a whole number") from error
    if not 1 <= lines <= MAX_LOG_LINES:
        raise argparse.ArgumentTypeError(f"line count must be from 1 to {MAX_LOG_LINES}")
    return lines


def _build_operations_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="jittersim",
        description="Headless status, access, update, persistence and recovery commands.",
    )
    commands = parser.add_subparsers(dest="operation", required=True)

    access = commands.add_parser("access", help="read or apply saved access rules")
    access_commands = access.add_subparsers(dest="access_command", required=True)
    for name in ("status", "apply"):
        command = access_commands.add_parser(name)
        _add_local_options(command)
    chance = access_commands.add_parser("dns-failure", help="show or set the DNS error chance")
    chance_commands = chance.add_subparsers(dest="chance_command", required=True)
    show_chance = chance_commands.add_parser("show")
    _add_local_options(show_chance)
    set_chance = chance_commands.add_parser("set")
    set_chance.add_argument("percent", type=int, help="whole number from 0 to 100")
    set_chance.add_argument(
        "--apply", action="store_true", help="apply all saved access rules after saving"
    )
    set_chance.add_argument(
        "--yes", action="store_true", help="confirm a 100% failure chance without a prompt"
    )
    _add_local_options(set_chance)

    updates = commands.add_parser("update", help="check and manage signed staged releases")
    update_commands = updates.add_subparsers(dest="update_command", required=True)
    for name in ("check", "install", "rollback"):
        command = update_commands.add_parser(name)
        if name != "check":
            command.add_argument(
                "--yes", action="store_true", help="confirm without an interactive prompt"
            )
        if name == "install":
            command.add_argument(
                "--wait", action="store_true",
                help="as root: wait until the update service installed and checked the release",
            )
        _add_local_options(command)
    # Listed for --help only: main() hands these to the update manager before parsing.
    for name, text in LOCAL_UPDATE_COMMANDS.items():
        update_commands.add_parser(name, help=text, add_help=False)

    persistence = commands.add_parser(
        "persistence", help="show or change profile recovery after gateway outages"
    )
    persistence_commands = persistence.add_subparsers(
        dest="persistence_command", required=True
    )
    show_persistence = persistence_commands.add_parser("show")
    _add_local_options(show_persistence)
    set_persistence = persistence_commands.add_parser("set")
    set_persistence.add_argument("mode", choices=("on", "off"))
    _add_local_options(set_persistence)

    diagnostics = commands.add_parser(
        "diagnostics", help="show a bounded service and access summary"
    )
    _add_local_options(diagnostics)
    logs = commands.add_parser("logs", help="show bounded logs for a JitterSim service")
    logs.add_argument("service", choices=tuple(LOG_SERVICES))
    logs.add_argument(
        "--lines", type=_parse_log_lines, default=80,
        help=f"number of lines to show, from 1 to {MAX_LOG_LINES} (default: 80)",
    )
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


def _reset(arguments: argparse.Namespace, *, all_restrictions: bool = False) -> int:
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
        if all_restrictions and repository is not None:
            try:
                settings = repository.access_settings()
                repository.save_access_policies(settings.mac_policy, settings.website_policy,
                                                 options=replace(settings.access_options, enabled=False))
            except (StorageError, sqlite3.Error):
                print("warning: the disabled state could not be saved. Retry before restarting the dashboard.", file=sys.stderr)
        client = HelperClient(
            socket_path=arguments.helper_socket, timeout_seconds=arguments.timeout_seconds
        )
        try:
            client.normal_access() if all_restrictions else client.reset()
        except HelperClientError as error:
            _record(repository, "error", "failure")
            print(f"Reset failed: {error}", file=sys.stderr)
            return 1
        _record(repository, "reset", "success")
    finally:
        if repository is not None:
            repository.close()
    print("Normal access restored. Profiles and household restrictions are off." if all_restrictions else
          "Network reset. Experiment effects are off. Household access rules remain active.")
    return 0


def _status(arguments: argparse.Namespace) -> int:
    repository = _open_repository(arguments.database)
    if repository is None:
        print(f"Database: not readable at {arguments.database}")
        print("On your Mac use 'jittersim connect --url https://PI:8080 --ca-cert PATH' once "
              "and then 'jittersim status'.", file=sys.stderr)
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
    try:
        gateway = client.gateway_status()
    except HelperClientError as error:
        print(f"Gateway status: unavailable ({error})")
        return 1
    print("Gateway: " + gateway.get("gateway_state", "unknown"))
    print(
        "Persistence mode: "
        + ("on" if gateway.get("mode_enabled") else "off")
    )
    if gateway.get("outage_stop_latched"):
        print("Profiles: stopped after a gateway outage. Apply after the gateway recovers.")
    return 0


def _persistence_status(arguments) -> int:
    repository = _open_repository(arguments.database)
    if repository is None:
        print(f"Settings database is not readable at {arguments.database}", file=sys.stderr)
        return 1
    try:
        saved = repository.get_persist_profiles_after_gateway_outage()
    except (StorageError, sqlite3.Error) as error:
        print(f"Could not read gateway persistence: {error}", file=sys.stderr)
        return 1
    finally:
        repository.close()
    print("Saved persistence mode: " + ("on" if saved else "off"))
    client = HelperClient(
        socket_path=arguments.helper_socket, timeout_seconds=arguments.timeout_seconds
    )
    try:
        gateway = client.gateway_status()
    except HelperClientError as error:
        print(f"Live gateway status: unavailable ({error})", file=sys.stderr)
        return 1
    print("Gateway: " + gateway["gateway_state"])
    print("Helper mode: " + ("on" if gateway["mode_enabled"] else "off"))
    if gateway["outage_stop_latched"]:
        print("Profiles are stopped. Use Apply after the gateway recovers.")
    if gateway["reset_pending"]:
        print("Network reset is still retrying. Check 'jittersim logs helper'.")
    if gateway["recovery_pending"]:
        print("The gateway is back. The dashboard will restore the active plan.")
    if gateway["state_error"]:
        print("Gateway state could not be saved safely.", file=sys.stderr)
        return 1
    return 0


def _set_persistence_mode(arguments) -> int:
    enabled = arguments.mode == "on"
    repository = _open_repository(arguments.database)
    if repository is None:
        print(f"Settings database is not readable at {arguments.database}", file=sys.stderr)
        return 1
    try:
        version = repository.get_runtime_state().configuration_version
        repository.set_persist_profiles_after_gateway_outage(
            enabled,
            expected_version=version,
        )
    except (StorageError, sqlite3.Error, ValueError) as error:
        print(f"Could not save gateway persistence: {error}", file=sys.stderr)
        return 1
    finally:
        repository.close()
    print(f"Saved persistence mode: {arguments.mode}")
    client = HelperClient(
        socket_path=arguments.helper_socket, timeout_seconds=arguments.timeout_seconds
    )
    try:
        state = client.set_gateway_persistence(enabled=enabled)
    except HelperClientError as error:
        print(f"The setting is saved but the helper did not receive it: {error}", file=sys.stderr)
        return 1
    print("Gateway: " + state["gateway_state"])
    if state["outage_stop_latched"]:
        print("Profiles remain stopped. Use Apply after the gateway recovers.")
    return 0


def _confirm(message: str, yes: bool) -> bool:
    if yes:
        return True
    try:
        answer = input(message + " Type yes to continue: ")
    except EOFError:
        answer = ""
    return answer.strip().lower() == "yes"


def _access_snapshot(arguments):
    repository = _open_repository(arguments.database)
    if repository is None:
        raise StorageError(f"settings database is not readable at {arguments.database}")
    try:
        settings = repository.access_settings()
        saved_percent = settings.access_options.dns_failure_percent
        saved_enabled = settings.access_options.enabled
        saved_policy = json.loads(json.dumps(build_access_plan(settings).payload()))
    finally:
        repository.close()
    client = HelperClient(
        socket_path=arguments.helper_socket, timeout_seconds=arguments.timeout_seconds
    )
    try:
        state = client.access_status()
        helper_error = None
    except HelperClientError as error:
        state = None
        helper_error = str(error)
    policy = (state or {}).get("policy") or {}
    options = policy.get("options") or {}
    return saved_percent, saved_enabled, saved_policy, state, options, helper_error


def _policy_definition(policy):
    if not isinstance(policy, dict):
        return None
    return {
        **{key: value for key, value in policy.items() if key != "clients"},
        "clients": [
            {key: value for key, value in client.items() if key != "address"}
            for client in policy.get("clients", [])
        ],
    }


def _print_access_status(arguments) -> int:
    try:
        (
            saved_percent,
            saved_enabled,
            saved_policy,
            state,
            options,
            helper_error,
        ) = _access_snapshot(arguments)
    except (StorageError, sqlite3.Error, HelperClientError, ValueError) as error:
        print(f"Access status unavailable: {error}", file=sys.stderr)
        return 1
    applied_percent = options.get("dns_failure_percent")
    applied_enabled = bool(options.get("enabled"))
    print("Saved household rules: " + ("enabled" if saved_enabled else "disabled"))
    print(f"Saved DNS failure chance: {saved_percent}%")
    if state is None:
        print("Applied household rules: unavailable")
        print(f"Networking helper: not answering ({helper_error})", file=sys.stderr)
        return 1
    if applied_percent is None:
        print("Applied household rules: unavailable")
    else:
        print("Applied household rules: " + ("enabled" if applied_enabled else "disabled"))
        print(f"Applied DNS failure chance: {applied_percent}%")
        if applied_enabled:
            print(f"Effective DNS failure chance: {applied_percent}%")
        else:
            print("Effective DNS failure chance: off while household rules are disabled")
    if state.get("error"):
        print(f"Access helper: {state['error']}")
        return 1
    matches = _policy_definition(saved_policy) == _policy_definition(state.get("policy"))
    print("Saved rules match applied rules." if matches and not state.get("error") else
          "Saved rules differ from applied rules. Run 'jittersim access apply' to apply them.")
    return 0


def _set_dns_failure(arguments) -> int:
    try:
        options = AccessOptions(dns_failure_percent=arguments.percent)
    except ValueError as error:
        print(str(error), file=sys.stderr)
        return 2
    if arguments.percent == 100 and not _confirm(
        "100% makes every test-network DNS lookup fail.", arguments.yes
    ):
        print("Cancelled. No settings were changed.")
        return 0
    repository = _open_repository(arguments.database)
    if repository is None:
        print(f"Settings database is not readable at {arguments.database}", file=sys.stderr)
        return 1
    try:
        settings = repository.access_settings()
        version = repository.get_runtime_state().configuration_version
        repository.save_access_policies(
            settings.mac_policy,
            settings.website_policy,
            blocks=settings.site_blocks,
            options=replace(
                settings.access_options,
                dns_failure_percent=options.dns_failure_percent,
            ),
            expected_version=version,
        )
        enabled = settings.access_options.enabled
    except (StorageError, sqlite3.Error, ValueError) as error:
        print(f"Could not save DNS failure chance: {error}", file=sys.stderr)
        return 1
    finally:
        repository.close()
    print(f"Saved DNS failure chance: {arguments.percent}%")
    if not enabled:
        print("Household access rules are disabled, so this setting is not active.")
    if not arguments.apply:
        print("Apply the saved setting with 'jittersim access apply'.")
        return 0
    return _apply_access(arguments)


def _apply_access(arguments) -> int:
    repository = _open_repository(arguments.database)
    if repository is None:
        print(f"Settings database is not readable at {arguments.database}", file=sys.stderr)
        return 1
    client = HelperClient(
        socket_path=arguments.helper_socket, timeout_seconds=arguments.timeout_seconds
    )
    try:
        revision = client.control_revision()
    except HelperClientError as error:
        repository.close()
        print(f"Access apply failed: {error}", file=sys.stderr)
        return 1
    try:
        plan = build_access_plan(repository.access_settings())
    except (StorageError, sqlite3.Error, ValueError) as error:
        print(f"Saved access rules are not valid: {error}", file=sys.stderr)
        return 1
    finally:
        repository.close()
    try:
        result = client.apply_access(plan, control_revision=revision)
    except HelperClientError as error:
        print(f"Access apply failed: {error}", file=sys.stderr)
        return 1
    print("Saved access rules applied.")
    print(f"DNS failure chance: {plan.options.dns_failure_percent}%")
    if result.get("error"):
        print(result["error"], file=sys.stderr)
        return 1
    return 0


def _release_status(arguments):
    client = HelperClient(
        socket_path=arguments.helper_socket, timeout_seconds=arguments.timeout_seconds
    )
    return client.release_status()


def _wait_for_update() -> int:
    """Join the queued run of the one-shot update service and report how it ended."""
    from jittersim.updates import update_status

    print("Waiting for the update service to install and check the release.", flush=True)
    try:
        subprocess.run(
            ["systemctl", "start", UPDATE_SERVICE], check=False, timeout=UPDATE_WAIT_SECONDS,
            stdin=subprocess.DEVNULL,
        )
        final = update_status()
    except (OSError, subprocess.SubprocessError, ValueError) as error:
        print(f"Could not follow the update: {error}. Check 'jittersim update check'.",
              file=sys.stderr)
        return 1
    print(f"Installed: {final.get('installed', 'unknown')}")
    print(f"Update state: {final.get('message', 'unknown')}")
    return 0 if final.get("phase") == "complete" else 1


def _update_command(arguments) -> int:
    if arguments.update_command == "install" and arguments.wait and os.geteuid() != 0:
        print("--wait needs root: run 'sudo jittersim update install --yes --wait'.",
              file=sys.stderr)
        return 1
    try:
        status = _release_status(arguments)
    except HelperClientError as error:
        print(f"Release status unavailable: {error}", file=sys.stderr)
        return 1
    if arguments.update_command == "check":
        print(f"Installed: {status.get('installed', 'unknown')}")
        print(f"Staged: {status.get('available') or 'none'}")
        if status.get("available"):
            print(f"Signed by: {status.get('signed_by') or 'nobody'}")
            if status.get("installable"):
                print("Ready to install: yes")
            else:
                print(f"Ready to install: no. {status.get('reason') or ''}".rstrip())
        if "trusted_keys" in status:
            print(f"Trusted signing keys: {status['trusted_keys']}")
        print(f"Update phase: {status.get('phase', 'unknown')}")
        print(f"Update state: {status.get('message', 'unknown')}")
        print(
            "This checks signed releases staged on the Pi. "
            "It does not check the private repository online."
        )
        return 0
    if arguments.update_command == "install":
        version = status.get("available")
        if not version or version == status.get("installed"):
            print("No newer staged release is ready to install.", file=sys.stderr)
            return 1
        if status.get("installable") is False:
            print(f"The staged release {version} cannot be installed: "
                  f"{status.get('reason') or 'stage a signed release'}", file=sys.stderr)
            return 1
        confirmed = _confirm(
            f"Install {version}? The dashboard and test network will pause briefly.",
            arguments.yes,
        )
        if not confirmed:
            print("Cancelled. The staged release was not installed.")
            return 0
        try:
            HelperClient(
                socket_path=arguments.helper_socket,
                timeout_seconds=arguments.timeout_seconds,
            ).install_release(version=version)
        except HelperClientError as error:
            print(f"Install request failed: {error}", file=sys.stderr)
            return 1
        if arguments.wait:
            return _wait_for_update()
        print(f"Installation of {version} was queued. Check with 'jittersim update check'.")
        return 0
    if not status.get("rollback_available"):
        print("No previous installation is available for recovery.", file=sys.stderr)
        return 1
    if not _confirm(
        "Restore the previous installation and its settings backup?",
        arguments.yes,
    ):
        print("Cancelled. The previous installation was not restored.")
        return 0
    try:
        HelperClient(
            socket_path=arguments.helper_socket,
            timeout_seconds=arguments.timeout_seconds,
        ).install_release(version=None, rollback=True)
    except HelperClientError as error:
        print(f"Rollback request failed: {error}", file=sys.stderr)
        return 1
    print("Restore request queued. The dashboard will reconnect when recovery finishes.")
    return 0


def _service_states():
    units = tuple(LOG_SERVICES.values())
    try:
        result = subprocess.run(
            ["systemctl", "is-active", *units],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
            stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.SubprocessError):
        return {name: "unavailable" for name in LOG_SERVICES}
    states = result.stdout.splitlines()
    return {
        name: states[index].strip() if index < len(states) else "unavailable"
        for index, name in enumerate(LOG_SERVICES)
    }


def _diagnostics(arguments) -> int:
    repository = _open_repository(arguments.database)
    if repository is None:
        print(f"Settings database: unreadable at {arguments.database}")
    else:
        try:
            settings = repository.access_settings()
            print("Settings database: readable")
            print(
                "Saved access: "
                + ("enabled" if settings.access_options.enabled else "disabled")
                + f", DNS failure {settings.access_options.dns_failure_percent}%"
            )
        except (StorageError, sqlite3.Error, ValueError):
            print("Settings database: error reading settings")
        finally:
            repository.close()
    client = HelperClient(
        socket_path=arguments.helper_socket, timeout_seconds=arguments.timeout_seconds
    )
    try:
        client.ping()
        print("Networking helper: answering")
    except HelperClientError:
        print("Networking helper: not answering")
    try:
        state = client.access_status()
        policy = state.get("policy") or {}
        options = policy.get("options") or {}
        if options:
            print(
                "Applied access: "
                + ("enabled" if options.get("enabled") else "disabled")
                + f", DNS failure {options.get('dns_failure_percent', 0)}%"
            )
        else:
            print("Applied access: unavailable")
    except HelperClientError:
        print("Applied access: unavailable")
    print("Services:")
    service_states = _service_states()
    for short_name in LOG_SERVICES:
        print(f"  {short_name}: {service_states[short_name]}")
    return 0


def _show_logs(arguments) -> int:
    unit = LOG_SERVICES[arguments.service]
    command = [
        "journalctl", "--quiet", "--unit", unit, "--lines", str(arguments.lines),
        "--no-pager", "--output=short-iso",
    ]
    try:
        result = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
            stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.SubprocessError) as error:
        print(f"Could not read {unit} logs: {error}", file=sys.stderr)
        return 1
    output = result.stdout[:MAX_LOG_OUTPUT_CHARS]
    clipped = len(result.stdout) > MAX_LOG_OUTPUT_CHARS
    if output:
        sys.stdout.write(output)
        if not output.endswith("\n"):
            sys.stdout.write("\n")
    if result.returncode:
        print(
            f"journalctl could not read {unit} logs. "
            "Check service name and permissions.",
            file=sys.stderr,
        )
        return 1
    if clipped:
        print("Output clipped at 32,000 characters.", file=sys.stderr)
    return 0


def _run_operations(raw_arguments: Sequence[str]) -> int:
    arguments = _build_operations_parser().parse_args(raw_arguments)
    if arguments.operation == "access":
        if arguments.access_command == "status":
            return _print_access_status(arguments)
        if arguments.access_command == "apply":
            return _apply_access(arguments)
        if arguments.chance_command == "show":
            return _print_access_status(arguments)
        return _set_dns_failure(arguments)
    if arguments.operation == "update":
        return _update_command(arguments)
    if arguments.operation == "persistence":
        if arguments.persistence_command == "show":
            return _persistence_status(arguments)
        return _set_persistence_mode(arguments)
    if arguments.operation == "diagnostics":
        return _diagnostics(arguments)
    return _show_logs(arguments)


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Run a control command, or render a demo profile without applying it."""

    raw_arguments = list(sys.argv[1:] if argv is None else argv)
    if raw_arguments and raw_arguments[0] in MEASUREMENT_COMMANDS:
        return measure_main(raw_arguments)
    if raw_arguments and raw_arguments[0] in REMOTE_COMMANDS:
        from jittersim.remote_cli import main as remote_main

        return remote_main(raw_arguments)
    if raw_arguments and raw_arguments[0] == "status":
        from jittersim.remote_cli import main as remote_main
        from jittersim.remote_cli import remote_status_wanted

        if remote_status_wanted(raw_arguments[1:]):
            return remote_main(raw_arguments)
    if raw_arguments and raw_arguments[0] in CONTROL_COMMANDS:
        command = raw_arguments[0]
        arguments = _build_control_parser(command).parse_args(raw_arguments[1:])
        return (_reset(arguments, all_restrictions=command == "normal-access")
                if command != "status" else _status(arguments))
    if raw_arguments[:1] == ["update"] and raw_arguments[1:2] and (
        raw_arguments[1] in LOCAL_UPDATE_COMMANDS
    ):
        from jittersim.updates import main as manager_main

        return manager_main(raw_arguments[1:], prog="jittersim update")
    if raw_arguments and raw_arguments[0] in (
        "access", "update", "persistence", "diagnostics", "logs"
    ):
        return _run_operations(raw_arguments)
    if raw_arguments and raw_arguments[0] in ("-h", "--help"):
        parser = _build_parser()
        parser.epilog = (
            "Headless commands on the Pi: access status, access apply, access dns-failure, "
            "persistence show, persistence set on/off, diagnostics, logs, "
            "update check, update install, update rollback, update stage, update trust-key "
            "and wifi reset. "
            "Commands for your Mac through the Pi's HTTPS dashboard: connect, status, "
            "profiles list/apply-all/stop-all, traffic, wifi show and impact. "
            "Run one with --help for details."
        )
        parser.print_help()
        return 0
    if raw_arguments and raw_arguments[0] == "simulate":
        raw_arguments = raw_arguments[1:]
    return _simulate(raw_arguments)


def reset_main(argv: Optional[Sequence[str]] = None) -> int:
    """Run `jittersim reset` under its older name, `jittersim-reset`."""

    return main(["reset", *(sys.argv[1:] if argv is None else argv)])


def _simulate(argv: Sequence[str]) -> int:
    """Render a deliberately inconvenient demo profile without applying it."""

    raw_arguments = list(sys.argv[1:] if argv is None else argv)
    if raw_arguments and raw_arguments[0] == "wifi":
        return _wifi_command(raw_arguments[1:])
    if raw_arguments and raw_arguments[0] == "dashboard-access":
        from jittersim.dashboard_access_cli import main as access_main

        return access_main(raw_arguments[1:])
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
    if list(argv[:1]) == ["show"]:
        from jittersim.remote_cli import main as remote_main

        return remote_main(["wifi", *argv])
    parser = argparse.ArgumentParser(
        prog="jittersim wifi",
        description="'wifi reset' runs on the Pi with sudo and talks to the local helper. "
                    "'wifi show' runs on your Mac through the Pi's HTTPS dashboard.",
    )
    commands = parser.add_subparsers(dest="wifi_command", required=True)
    commands.add_parser(
        "show", add_help=False,
        help="on your Mac: show the router's own Wi-Fi name (see 'jittersim wifi show --help')",
    )
    reset = commands.add_parser(
        "reset", help="on the Pi: restore the test Wi-Fi name and keep the password"
    )
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
