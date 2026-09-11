"""Unix-socket boundary for the privileged networking process."""

import argparse
import json
import logging
import os
from pathlib import Path
import socket
import socketserver
import stat
from typing import Any, Dict, Optional, Sequence

from jittersim.engine.codec import PlanCodecError, plan_from_payload, plan_to_payload
from jittersim.engine.plan import NetworkPlan

from .applier import ApplyError, NetworkApplier
from .executor import CommandExecutionError, SubprocessExecutor
from .topology import Topology


LOGGER = logging.getLogger(__name__)
MAX_MESSAGE_BYTES = 256 * 1024


class HelperError(RuntimeError):
    """Raised when the helper boundary cannot be started or used safely."""


class HelperClientError(RuntimeError):
    """Raised when a helper request cannot be completed."""


class HelperService:
    """Dispatch validated plan and reset requests to one network applier."""

    def __init__(self, applier: NetworkApplier) -> None:
        self._applier = applier

    def handle_payload(self, payload: Any) -> Dict[str, Any]:
        """Handle one decoded request without exposing command execution details."""

        if not isinstance(payload, dict):
            return {"ok": False, "error": "request must be an object"}
        action = payload.get("action")
        if action == "ping":
            return {"ok": True, "action": "ping"}
        if action == "reset":
            return self._handle_reset()
        if action == "apply":
            return self._handle_apply(payload.get("plan"))
        return {"ok": False, "error": "unknown action"}

    def _handle_apply(self, payload: Any) -> Dict[str, Any]:
        try:
            plan = plan_from_payload(payload)
        except PlanCodecError:
            return {"ok": False, "error": "invalid network plan"}
        try:
            report = self._applier.apply(plan=plan)
        except ApplyError as error:
            LOGGER.exception("network plan apply failed")
            return {
                "ok": False,
                "error": "network plan apply failed",
                "cleanup_failed": error.reset_error is not None,
            }
        return {"ok": True, "action": "apply", "commands_run": report.commands_run}

    def _handle_reset(self) -> Dict[str, Any]:
        try:
            commands_run = self._applier.reset()
        except CommandExecutionError:
            LOGGER.exception("network reset failed")
            return {"ok": False, "error": "network reset failed"}
        return {"ok": True, "action": "reset", "commands_run": commands_run}


class HelperRequestHandler(socketserver.StreamRequestHandler):
    """Read one newline-delimited JSON request from a Unix socket client."""

    server: "UnixHelperServer"

    def handle(self) -> None:
        line = self.rfile.readline(MAX_MESSAGE_BYTES + 1)
        if not line:
            return
        if len(line) > MAX_MESSAGE_BYTES:
            self._send_response({"ok": False, "error": "request is too large"})
            return
        try:
            payload = json.loads(line.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._send_response({"ok": False, "error": "request must be valid JSON"})
            return
        self._send_response(self.server.service.handle_payload(payload))

    def _send_response(self, payload: Dict[str, Any]) -> None:
        encoded = (json.dumps(payload, sort_keys=True) + "\n").encode("utf-8")
        self.wfile.write(encoded)


class UnixHelperServer(socketserver.UnixStreamServer):
    """Serve the helper protocol through a filesystem-protected Unix socket."""

    def __init__(self, socket_path: Path, service: HelperService) -> None:
        self.socket_path = socket_path
        self.service = service
        self._prepare_socket_path()
        try:
            super().__init__(str(socket_path), HelperRequestHandler)
            os.chmod(socket_path, 0o660)
        except OSError as error:
            self._remove_socket_path()
            raise HelperError("could not create the helper socket") from error

    def server_close(self) -> None:
        """Close the socket and remove it only when it is still a socket."""

        super().server_close()
        self._remove_socket_path()

    def _prepare_socket_path(self) -> None:
        if not self.socket_path.exists() and not self.socket_path.is_symlink():
            return
        try:
            mode = self.socket_path.lstat().st_mode
        except OSError as error:
            raise HelperError("could not inspect the helper socket path") from error
        if not stat.S_ISSOCK(mode):
            raise HelperError("helper socket path already exists and is not a socket")
        try:
            self.socket_path.unlink()
        except OSError as error:
            raise HelperError("could not remove the stale helper socket") from error

    def _remove_socket_path(self) -> None:
        try:
            if stat.S_ISSOCK(self.socket_path.lstat().st_mode):
                self.socket_path.unlink()
        except FileNotFoundError:
            pass


class HelperClient:
    """Send bounded requests to a local privileged helper."""

    def __init__(self, *, socket_path: Path, timeout_seconds: float = 5.0) -> None:
        if timeout_seconds <= 0 or timeout_seconds > 60:
            raise ValueError("timeout_seconds must be greater than 0 and no more than 60")
        self.socket_path = socket_path
        self.timeout_seconds = timeout_seconds

    def ping(self) -> Dict[str, Any]:
        """Check whether the helper is accepting requests."""

        return self._request({"action": "ping"})

    def apply(self, *, plan: NetworkPlan) -> Dict[str, Any]:
        """Ask the helper to apply one validated network plan."""

        return self._request({"action": "apply", "plan": plan_to_payload(plan)})

    def reset(self) -> Dict[str, Any]:
        """Ask the helper to remove project-owned network state."""

        return self._request({"action": "reset"})

    def _request(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        encoded = (json.dumps(payload, sort_keys=True) + "\n").encode("utf-8")
        if len(encoded) > MAX_MESSAGE_BYTES:
            raise HelperClientError("helper request is too large")
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
                connection.settimeout(self.timeout_seconds)
                connection.connect(str(self.socket_path))
                with connection.makefile("rwb") as stream:
                    stream.write(encoded)
                    stream.flush()
                    response = stream.readline(MAX_MESSAGE_BYTES + 1)
        except OSError as error:
            raise HelperClientError("could not contact the networking helper") from error
        if not response or len(response) > MAX_MESSAGE_BYTES:
            raise HelperClientError("helper returned an invalid response")
        try:
            decoded = json.loads(response.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise HelperClientError("helper returned invalid JSON") from error
        if not isinstance(decoded, dict):
            raise HelperClientError("helper returned an invalid response object")
        if not decoded.get("ok", False):
            raise HelperClientError(str(decoded.get("error", "helper request failed")))
        return decoded


def run_helper(
    *,
    socket_path: Path,
    topology: Topology,
    timeout_seconds: float = 10.0,
    enable_hardware: bool = False,
) -> None:
    """Run the helper only after an explicit hardware-mode opt-in."""

    if not enable_hardware:
        raise HelperError("hardware mode requires --enable-hardware")
    service = HelperService(
        NetworkApplier(
            executor=SubprocessExecutor(timeout_seconds=timeout_seconds),
            topology=topology,
        )
    )
    with UnixHelperServer(socket_path, service) as server:
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the jittersim privileged helper.")
    parser.add_argument("--socket", type=Path, required=True)
    parser.add_argument("--uplink", required=True)
    parser.add_argument("--test-interface", required=True)
    parser.add_argument("--timeout-seconds", type=float, default=10.0)
    parser.add_argument("--enable-hardware", action="store_true")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Parse helper options and start the explicit hardware process."""

    arguments = _build_parser().parse_args(argv)
    run_helper(
        socket_path=arguments.socket,
        topology=Topology(
            uplink_interface=arguments.uplink,
            test_interface=arguments.test_interface,
        ),
        timeout_seconds=arguments.timeout_seconds,
        enable_hardware=arguments.enable_hardware,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
