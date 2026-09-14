"""Small HTTP server for the jittersim control plane."""

import argparse
from dataclasses import asdict
import json
import hmac
import logging
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, HTTPServer
import os
from pathlib import Path
from typing import Any, Dict, Optional, Sequence, Tuple
from urllib.parse import urlsplit

from jittersim.config.models import Device, Impairment, Profile, RuntimeState
from jittersim.config.storage import ConfigRepository, DatabasePath, StorageError
from jittersim.engine.compiler import PlanError
from jittersim.engine.codec import plan_to_payload
from jittersim.engine.service import compile_stored_plan
from jittersim.networking.helper import HelperClient, HelperClientError


LOGGER = logging.getLogger(__name__)
MAX_JSON_BODY_BYTES = 64 * 1024
WRITE_PATHS = frozenset(("/api/profiles", "/api/devices", "/api/apply", "/api/reset"))
ACTION_PATHS = frozenset(("/api/apply", "/api/reset"))


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
    .card { border: 1px solid #8886; border-radius: 0.5rem; margin: 1rem 0; padding: 1rem; }
    .fields {
      display: grid;
      gap: 0.75rem;
      grid-template-columns: repeat(auto-fit, minmax(12rem, 1fr));
    }
    label { display: grid; gap: 0.25rem; }
    input { box-sizing: border-box; max-width: 100%; padding: 0.4rem; }
    button { margin-top: 1rem; padding: 0.45rem 0.8rem; }
    #message { min-height: 1.5rem; }
  </style>
</head>
<body>
  <h1>jittersim</h1>
  <p class="notice">
    Configuration mode. These forms update SQLite only. No network commands can be run from this
    page.
  </p>
  <section class="card">
    <h2>Control token</h2>
    <label>Bearer token (kept in this page only)
      <input id="admin-token" type="password" autocomplete="off">
    </label>
  </section>
  <section class="card">
    <h2>Network actions</h2>
    <p>Actions call the helper only when the server was started with a helper socket.</p>
    <button id="apply-button" type="button">Apply current configuration</button>
    <button id="reset-button" type="button">Reset network</button>
  </section>
  <section class="card">
    <h2>Save profile</h2>
    <form id="profile-form">
      <div class="fields">
        <label>Profile ID
          <input id="profile-id" required pattern="[a-z0-9][a-z0-9_-]{0,63}">
        </label>
        <label>Name <input id="profile-name" required></label>
      </div>
      <h3>Upload</h3>
      <div class="fields">
        <label>Delay (ms) <input id="upload-delay" type="number" min="0" value="0" required></label>
        <label>Jitter (ms)
          <input id="upload-jitter" type="number" min="0" value="0" required>
        </label>
        <label>Loss (%)
          <input id="upload-loss" type="number" min="0" max="100" value="0" required>
        </label>
        <label>Bandwidth (kbit/s, optional)
          <input id="upload-bandwidth" type="number" min="1">
        </label>
      </div>
      <h3>Download</h3>
      <div class="fields">
        <label>Delay (ms)
          <input id="download-delay" type="number" min="0" value="0" required>
        </label>
        <label>Jitter (ms)
          <input id="download-jitter" type="number" min="0" value="0" required>
        </label>
        <label>Loss (%)
          <input id="download-loss" type="number" min="0" max="100" value="0" required>
        </label>
        <label>Bandwidth (kbit/s, optional)
          <input id="download-bandwidth" type="number" min="1">
        </label>
      </div>
      <button type="submit">Save profile</button>
    </form>
  </section>
  <section class="card">
    <h2>Assign a device</h2>
    <form id="device-form">
      <div class="fields">
        <label>Device ID <input id="device-id" required pattern="[a-z0-9][a-z0-9_-]{0,63}"></label>
        <label>Name <input id="device-name" required></label>
        <label>Reserved IPv4 address
          <input id="device-ip" required placeholder="192.168.50.10">
        </label>
        <label>Profile ID <input id="device-profile" required></label>
      </div>
      <button type="submit">Save device</button>
    </form>
  </section>
  <p id="message" role="status"></p>
  <h2>Profiles</h2>
  <pre id="profiles">Loading...</pre>
  <h2>Devices</h2>
  <pre id="devices">Loading...</pre>
  <h2>Compiled plan</h2>
  <pre id="plan">Loading...</pre>
  <h2>Runtime state</h2>
  <pre id="state">Loading...</pre>
  <script>
    const element = (id) => document.getElementById(id);

    function optionalNumber(id) {
      const value = element(id).value.trim();
      return value === "" ? null : Number(value);
    }

    function impairment(prefix) {
      return {
        delay_ms: Number(element(prefix + "-delay").value),
        jitter_ms: Number(element(prefix + "-jitter").value),
        loss_percent: Number(element(prefix + "-loss").value),
        bandwidth_kbit: optionalNumber(prefix + "-bandwidth")
      };
    }

    async function request(path, method, body) {
      const headers = {};
      const token = element("admin-token").value.trim();
      if (token) headers.Authorization = "Bearer " + token;
      if (body !== undefined) headers["Content-Type"] = "application/json";
      const response = await fetch(path, {
        method: method,
        headers: headers,
        body: body === undefined ? undefined : JSON.stringify(body),
        cache: "no-store"
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || response.statusText);
      return data;
    }

    async function show(endpoint, elementId, key) {
      const data = await request(endpoint, "GET");
      element(elementId).textContent = JSON.stringify(data[key], null, 2);
    }

    async function refresh() {
      await Promise.all([
        show("/api/profiles", "profiles", "profiles"),
        show("/api/devices", "devices", "devices"),
        show("/api/plan", "plan", "plan"),
        show("/api/state", "state", "state")
      ]);
    }

    function showMessage(message) {
      element("message").textContent = message;
    }

    async function runAction(path, successMessage) {
      try {
        await request(path, "POST", {});
        showMessage(successMessage);
        await refresh();
      } catch (error) {
        showMessage(error.message);
      }
    }

    element("apply-button").addEventListener("click", () => {
      runAction("/api/apply", "Configuration applied.");
    });

    element("reset-button").addEventListener("click", () => {
      runAction("/api/reset", "Network reset requested.");
    });

    element("profile-form").addEventListener("submit", async (event) => {
      event.preventDefault();
      try {
        await request("/api/profiles", "POST", {
          id: element("profile-id").value,
          name: element("profile-name").value,
          upload: impairment("upload"),
          download: impairment("download")
        });
        showMessage("Profile saved.");
        await refresh();
      } catch (error) {
        showMessage(error.message);
      }
    });

    element("device-form").addEventListener("submit", async (event) => {
      event.preventDefault();
      try {
        await request("/api/devices", "POST", {
          id: element("device-id").value,
          name: element("device-name").value,
          ipv4_address: element("device-ip").value,
          profile_id: element("device-profile").value
        });
        showMessage("Device saved.");
        await refresh();
      } catch (error) {
        showMessage(error.message);
      }
    });

    refresh().catch((error) => {
      showMessage(error.message);
      element("plan").textContent = error.message;
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


def _runtime_state_payload(state: RuntimeState) -> Dict[str, Any]:
    return {
        "configuration_version": state.configuration_version,
        "observed_state": state.observed_state,
        "last_action": state.last_action,
        "last_result": state.last_result,
        "last_error": state.last_error,
    }


def _json_bytes(payload: Dict[str, Any]) -> bytes:
    return (json.dumps(payload, sort_keys=True) + "\n").encode("utf-8")


class ControlServer(HTTPServer):
    """Serve configuration and plan data from one repository."""

    allow_reuse_address = True

    def __init__(
        self,
        server_address: Tuple[str, int],
        repository: ConfigRepository,
        admin_token: Optional[str] = None,
        helper_client: Optional[HelperClient] = None,
    ) -> None:
        if admin_token == "":
            raise ValueError("admin_token must not be empty")
        self.repository = repository
        self.admin_token = admin_token
        self.helper_client = helper_client
        super().__init__(server_address, ControlRequestHandler)

    def response_for(self, path: str) -> Tuple[int, str, bytes]:
        """Build the response body for a supported GET path."""

        if path == "/":
            return HTTPStatus.OK, "text/html; charset=utf-8", DASHBOARD_HTML.encode("utf-8")
        if path == "/api/health":
            return HTTPStatus.OK, "application/json", _json_bytes(
                {
                    "status": "ok",
                    "mode": "configuration-only",
                    "helper_configured": self.helper_client is not None,
                }
            )
        if path == "/api/profiles":
            profiles = [_profile_payload(profile) for profile in self.repository.list_profiles()]
            return HTTPStatus.OK, "application/json", _json_bytes({"profiles": profiles})
        if path == "/api/devices":
            devices = [_device_payload(device) for device in self.repository.list_devices()]
            return HTTPStatus.OK, "application/json", _json_bytes({"devices": devices})
        if path == "/api/plan":
            return HTTPStatus.OK, "application/json", _json_bytes(
                {"plan": plan_to_payload(compile_stored_plan(repository=self.repository))}
            )
        if path == "/api/state":
            return HTTPStatus.OK, "application/json", _json_bytes(
                {"state": _runtime_state_payload(self.repository.get_runtime_state())}
            )
        return HTTPStatus.NOT_FOUND, "application/json", _json_bytes({"error": "not found"})

    def write_response_for(self, path: str, payload: Dict[str, Any]) -> Tuple[int, str, bytes]:
        """Handle one supported configuration or network action request."""

        if path == "/api/profiles":
            try:
                profile = _profile_from_payload(payload)
            except (KeyError, TypeError, ValueError):
                return HTTPStatus.BAD_REQUEST, "application/json", _json_bytes(
                    {"error": "invalid profile configuration"}
                )
            self.repository.save_profile(profile)
            return HTTPStatus.OK, "application/json", _json_bytes(
                {"profile": _profile_payload(profile)}
            )

        if path == "/api/devices":
            try:
                device = _device_from_payload(payload)
            except (KeyError, TypeError, ValueError):
                return HTTPStatus.BAD_REQUEST, "application/json", _json_bytes(
                    {"error": "invalid device configuration"}
                )
            self.repository.save_device(device)
            return HTTPStatus.OK, "application/json", _json_bytes(
                {"device": _device_payload(device)}
            )

        if path == "/api/apply":
            return self._apply_configuration()

        if path == "/api/reset":
            return self._reset_network()

        return HTTPStatus.NOT_FOUND, "application/json", _json_bytes({"error": "not found"})

    def _apply_configuration(self) -> Tuple[int, str, bytes]:
        if self.helper_client is None:
            return HTTPStatus.SERVICE_UNAVAILABLE, "application/json", _json_bytes(
                {"error": "network helper is not configured"}
            )
        try:
            plan = compile_stored_plan(repository=self.repository)
        except (PlanError, StorageError):
            return HTTPStatus.CONFLICT, "application/json", _json_bytes(
                {"error": "stored configuration cannot be applied"}
            )
        self.repository.update_runtime_state(
            observed_state="applying",
            last_action="apply",
            last_result=None,
            last_error=None,
        )
        try:
            result = self.helper_client.apply(plan=plan)
        except HelperClientError as error:
            LOGGER.error("network helper apply failed: %s", error)
            state = self.repository.update_runtime_state(
                observed_state="error",
                last_action="apply",
                last_result="failure",
                last_error="network helper apply failed",
            )
            return HTTPStatus.BAD_GATEWAY, "application/json", _json_bytes(
                {"error": "network helper apply failed", "state": _runtime_state_payload(state)}
            )
        state = self.repository.update_runtime_state(
            observed_state="applied",
            last_action="apply",
            last_result="success",
            last_error=None,
        )
        return HTTPStatus.OK, "application/json", _json_bytes(
            {"result": result, "state": _runtime_state_payload(state)}
        )

    def _reset_network(self) -> Tuple[int, str, bytes]:
        if self.helper_client is None:
            return HTTPStatus.SERVICE_UNAVAILABLE, "application/json", _json_bytes(
                {"error": "network helper is not configured"}
            )
        self.repository.update_runtime_state(
            observed_state="resetting",
            last_action="reset",
            last_result=None,
            last_error=None,
        )
        try:
            result = self.helper_client.reset()
        except HelperClientError as error:
            LOGGER.error("network helper reset failed: %s", error)
            state = self.repository.update_runtime_state(
                observed_state="error",
                last_action="reset",
                last_result="failure",
                last_error="network helper reset failed",
            )
            return HTTPStatus.BAD_GATEWAY, "application/json", _json_bytes(
                {"error": "network helper reset failed", "state": _runtime_state_payload(state)}
            )
        state = self.repository.update_runtime_state(
            observed_state="reset",
            last_action="reset",
            last_result="success",
            last_error=None,
        )
        return HTTPStatus.OK, "application/json", _json_bytes(
            {"result": result, "state": _runtime_state_payload(state)}
        )

    def is_authorized(self, authorization_header: Optional[str]) -> bool:
        """Return whether an Authorization header matches the configured admin token."""

        return _authorization_matches(
            expected_token=self.admin_token,
            authorization_header=authorization_header,
        )


class ControlRequestHandler(BaseHTTPRequestHandler):
    """Handle the control-plane routes."""

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
        self._handle_write()

    def do_PUT(self) -> None:
        self._handle_write()

    def do_DELETE(self) -> None:
        self._send_response(
            status=HTTPStatus.METHOD_NOT_ALLOWED,
            content_type="application/json",
            body=_json_bytes({"error": "delete actions are not enabled"}),
        )

    def log_message(self, format_string: str, *args: Any) -> None:
        LOGGER.info("%s - %s", self.address_string(), format_string % args)

    def _handle_write(self) -> None:
        path = urlsplit(self.path).path
        if path not in WRITE_PATHS:
            self._send_response(
                status=HTTPStatus.NOT_FOUND,
                content_type="application/json",
                body=_json_bytes({"error": "not found"}),
            )
            return
        if self.server.admin_token is None:
            self._send_response(
                status=HTTPStatus.SERVICE_UNAVAILABLE,
                content_type="application/json",
                body=_json_bytes({"error": "state-changing actions are disabled"}),
            )
            return
        if not self.server.is_authorized(self.headers.get("Authorization")):
            self._send_response(
                status=HTTPStatus.UNAUTHORIZED,
                content_type="application/json",
                body=_json_bytes({"error": "valid bearer authentication is required"}),
                headers={"WWW-Authenticate": "Bearer"},
            )
            return

        try:
            payload = self._read_json_body(required=path not in ACTION_PATHS)
            status, content_type, body = self.server.write_response_for(path, payload)
        except RequestError as error:
            status = HTTPStatus.BAD_REQUEST
            content_type = "application/json"
            body = _json_bytes({"error": str(error)})
        except StorageError:
            LOGGER.exception("could not persist control-plane request for %s", path)
            status = HTTPStatus.CONFLICT
            content_type = "application/json"
            body = _json_bytes({"error": "configuration could not be saved"})
        self._send_response(status=status, content_type=content_type, body=body)

    def _read_json_body(self, *, required: bool = True) -> Dict[str, Any]:
        length_header = self.headers.get("Content-Length")
        if length_header is None:
            if not required:
                return {}
            raise RequestError("request body is required")
        try:
            length = int(length_header)
        except ValueError as error:
            raise RequestError("request body length is invalid") from error
        if length < 0 or length > MAX_JSON_BODY_BYTES:
            raise RequestError("request body is too large")
        if length == 0 and not required:
            return {}
        raw_body = self.rfile.read(length)
        if len(raw_body) != length:
            raise RequestError("request body is incomplete")
        try:
            payload = json.loads(raw_body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise RequestError("request body must be valid JSON") from error
        if not isinstance(payload, dict):
            raise RequestError("request body must contain a JSON object")
        return payload

    def _send_response(
        self,
        *,
        status: int,
        content_type: str,
        body: bytes,
        headers: Optional[Dict[str, str]] = None,
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)


class RequestError(ValueError):
    """Raised when an HTTP request body cannot be safely decoded."""


def _impairment_from_payload(payload: Any) -> Impairment:
    if payload is None:
        return Impairment()
    if not isinstance(payload, dict):
        raise ValueError("impairment must be an object")
    return Impairment(**payload)


def _profile_from_payload(payload: Dict[str, Any]) -> Profile:
    return Profile(
        id=payload["id"],
        name=payload["name"],
        upload=_impairment_from_payload(payload.get("upload")),
        download=_impairment_from_payload(payload.get("download")),
        random_seed=payload.get("random_seed"),
    )


def _device_from_payload(payload: Dict[str, Any]) -> Device:
    return Device(
        id=payload["id"],
        name=payload["name"],
        ipv4_address=payload["ipv4_address"],
        profile_id=payload["profile_id"],
    )


def _authorization_matches(
    *, expected_token: Optional[str], authorization_header: Optional[str]
) -> bool:
    if expected_token is None or authorization_header is None:
        return False
    scheme, separator, token = authorization_header.partition(" ")
    return (
        separator == " "
        and scheme.lower() == "bearer"
        and hmac.compare_digest(token, expected_token)
    )


def run_server(
    *,
    database_path: DatabasePath,
    host: str = "127.0.0.1",
    port: int = 8080,
    admin_token: Optional[str] = None,
    helper_socket_path: Optional[Path] = None,
    helper_timeout_seconds: float = 5.0,
) -> None:
    """Run the dashboard until the process receives Ctrl-C."""

    helper_client = (
        None
        if helper_socket_path is None
        else HelperClient(
            socket_path=helper_socket_path,
            timeout_seconds=helper_timeout_seconds,
        )
    )
    with ConfigRepository(database_path) as repository:
        with ControlServer(
            (host, port),
            repository,
            admin_token=admin_token,
            helper_client=helper_client,
        ) as server:
            LOGGER.info("jittersim dashboard listening on %s:%s", host, port)
            try:
                server.serve_forever()
            except KeyboardInterrupt:
                pass


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the jittersim dashboard.")
    parser.add_argument("--database", type=Path, default=Path("jittersim.db"))
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--helper-socket", type=Path)
    parser.add_argument("--helper-timeout-seconds", type=float, default=5.0)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Parse server options and start the dashboard."""

    arguments = _build_parser().parse_args(argv)
    run_server(
        database_path=arguments.database,
        host=arguments.host,
        port=arguments.port,
        admin_token=os.environ.get("JITTERSIM_ADMIN_TOKEN"),
        helper_socket_path=arguments.helper_socket,
        helper_timeout_seconds=arguments.helper_timeout_seconds,
    )
    return 0
