"""Small read-only HTTP server for the jittersim control plane."""

import argparse
from dataclasses import asdict
import json
import logging
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any, Dict, Optional, Sequence, Tuple
from urllib.parse import urlsplit

from jittersim.config.models import Device, Impairment, Profile
from jittersim.config.storage import ConfigRepository, DatabasePath, StorageError
from jittersim.engine.compiler import PlanError
from jittersim.engine.plan import NetworkPlan
from jittersim.engine.service import compile_stored_plan


LOGGER = logging.getLogger(__name__)


DASHBOARD_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>jittersim</title>
  <style>
    :root { color-scheme: light dark; font-family: system-ui, sans-serif; }
    body { margin: 2rem auto; max-width: 58rem; padding: 0 1rem; }
    code, pre { font-family: ui-monospace, SFMono-Regular, monospace; }
    pre { overflow-x: auto; padding: 1rem; border: 1px solid #8886; border-radius: 0.5rem; }
    .notice { padding: 0.75rem 1rem; border-left: 0.3rem solid #cc8b00; }
  </style>
</head>
<body>
  <h1>jittersim</h1>
  <p class="notice">Read-only inspection mode. No network commands can be run from this page.</p>
  <h2>Profiles</h2>
  <pre id="profiles">Loading...</pre>
  <h2>Devices</h2>
  <pre id="devices">Loading...</pre>
  <h2>Compiled plan</h2>
  <pre id="plan">Loading...</pre>
  <script>
    async function show(endpoint, elementId, key) {
      const response = await fetch(endpoint, { cache: "no-store" });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || response.statusText);
      document.getElementById(elementId).textContent = JSON.stringify(data[key], null, 2);
    }

    Promise.all([
      show("/api/profiles", "profiles", "profiles"),
      show("/api/devices", "devices", "devices"),
      show("/api/plan", "plan", "plan")
    ]).catch((error) => {
      document.getElementById("plan").textContent = error.message;
    });
  </script>
</body>
</html>
"""


def _impairment_payload(impairment: Impairment) -> Dict[str, Any]:
    return asdict(impairment)


def _profile_payload(profile: Profile) -> Dict[str, Any]:
    return {
        "id": profile.id,
        "name": profile.name,
        "upload": _impairment_payload(profile.upload),
        "download": _impairment_payload(profile.download),
        "random_seed": profile.random_seed,
    }


def _device_payload(device: Device) -> Dict[str, Any]:
    return {
        "id": device.id,
        "name": device.name,
        "ipv4_address": str(device.ipv4_address),
        "profile_id": device.profile_id,
    }


def _plan_payload(plan: NetworkPlan) -> Dict[str, Any]:
    return {
        "mark_rules": [
            {
                "device_id": rule.device_id,
                "ipv4_address": str(rule.ipv4_address),
                "mark": rule.mark,
            }
            for rule in plan.mark_rules
        ],
        "queues": [
            {
                "device_id": queue.device_id,
                "direction": queue.direction.value,
                "mark": queue.mark,
                "impairment": _impairment_payload(queue.impairment),
            }
            for queue in plan.queues
        ],
    }


def _json_bytes(payload: Dict[str, Any]) -> bytes:
    return (json.dumps(payload, sort_keys=True) + "\n").encode("utf-8")


class ControlServer(HTTPServer):
    """Serve read-only configuration and plan data from one repository."""

    allow_reuse_address = True

    def __init__(self, server_address: Tuple[str, int], repository: ConfigRepository) -> None:
        self.repository = repository
        super().__init__(server_address, ControlRequestHandler)

    def response_for(self, path: str) -> Tuple[int, str, bytes]:
        """Build the response body for a supported GET path."""

        if path == "/":
            return HTTPStatus.OK, "text/html; charset=utf-8", DASHBOARD_HTML.encode("utf-8")
        if path == "/api/health":
            return HTTPStatus.OK, "application/json", _json_bytes(
                {"status": "ok", "mode": "read-only"}
            )
        if path == "/api/profiles":
            profiles = [_profile_payload(profile) for profile in self.repository.list_profiles()]
            return HTTPStatus.OK, "application/json", _json_bytes({"profiles": profiles})
        if path == "/api/devices":
            devices = [_device_payload(device) for device in self.repository.list_devices()]
            return HTTPStatus.OK, "application/json", _json_bytes({"devices": devices})
        if path == "/api/plan":
            return HTTPStatus.OK, "application/json", _json_bytes(
                {"plan": _plan_payload(compile_stored_plan(repository=self.repository))}
            )
        return HTTPStatus.NOT_FOUND, "application/json", _json_bytes({"error": "not found"})


class ControlRequestHandler(BaseHTTPRequestHandler):
    """Handle the read-only control-plane routes."""

    server: ControlServer

    def do_GET(self) -> None:
        path = urlsplit(self.path).path
        try:
            status, content_type, body = self.server.response_for(path)
        except (PlanError, StorageError):
            LOGGER.exception("could not build control-plane response for %s", path)
            status = HTTPStatus.INTERNAL_SERVER_ERROR
            content_type = "application/json"
            body = _json_bytes({"error": "stored configuration could not be read"})
        self._send_response(status=status, content_type=content_type, body=body)

    def do_POST(self) -> None:
        self._send_response(
            status=HTTPStatus.METHOD_NOT_ALLOWED,
            content_type="application/json",
            body=_json_bytes({"error": "state-changing actions are not enabled"}),
        )

    def do_PUT(self) -> None:
        self.do_POST()

    def do_DELETE(self) -> None:
        self.do_POST()

    def log_message(self, format_string: str, *args: Any) -> None:
        LOGGER.info("%s - %s", self.address_string(), format_string % args)

    def _send_response(self, *, status: int, content_type: str, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)


def run_server(*, database_path: DatabasePath, host: str = "127.0.0.1", port: int = 8080) -> None:
    """Run the read-only dashboard until the process receives Ctrl-C."""

    with ConfigRepository(database_path) as repository:
        with ControlServer((host, port), repository) as server:
            LOGGER.info("jittersim dashboard listening on %s:%s", host, port)
            try:
                server.serve_forever()
            except KeyboardInterrupt:
                pass


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the read-only jittersim dashboard.")
    parser.add_argument("--database", type=Path, default=Path("jittersim.db"))
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Parse server options and start the read-only dashboard."""

    arguments = _build_parser().parse_args(argv)
    run_server(database_path=arguments.database, host=arguments.host, port=arguments.port)
    return 0
