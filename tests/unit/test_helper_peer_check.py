"""Audit A6: only root may change saved dashboard access through the helper socket."""

import inspect
import io
import json
import os
import re
import socket
import threading
import types
import unittest
from unittest import mock

from jittersim.networking import helper
from jittersim.networking.helper import HelperRequestHandler, HelperService

DASHBOARD_UID = 1000
ROOT_ONLY = ("local-add", "local-remove")


def every_helper_action():
    """Every action name HelperService.handle_payload and the socket handler know."""
    source = "".join(inspect.getsource(method) for method in (
        HelperService.handle_payload, HelperService._handle_payload,
        HelperRequestHandler._handle_request))
    names = set(re.findall(r'action == "([a-z-]+)"', source))
    for group in re.findall(r"action in \(([^)]*)\)", source):
        names.update(re.findall(r'"([a-z-]+)"', group))
    names.update(helper.WRITE_ACTIONS, helper.RELEASE_ACTIONS, helper.WIFI_ACTIONS,
                 helper.ROUTER_ACTIONS, helper.READ_ONLY_ACTIONS)
    return sorted(names)


def dashboard_access_operations():
    source = inspect.getsource(HelperService)
    names = set(re.findall(r'operation == "([a-z-]+)"', source))
    for group in re.findall(r"operation in \(([^)]*)\)", source):
        names.update(re.findall(r'"([a-z-]+)"', group))
    return sorted(names)


class PeerCheckTests(unittest.TestCase):
    def send(self, payload, uid):
        service = mock.Mock()
        service.handle_payload.return_value = {"ok": True, "devices": []}
        handler = object.__new__(HelperRequestHandler)
        handler.server = types.SimpleNamespace(
            policy_only=False, service=service, operation_lock=threading.Lock(),
            release_stage_lock=threading.Lock(), release_lock=threading.Lock())
        handler.request = mock.Mock()
        handler.rfile = io.BytesIO(json.dumps(payload).encode() + b"\n")
        handler.wfile = io.BytesIO()
        with mock.patch.object(helper, "_peer_uid", return_value=uid) as reader:
            handler._handle_request()
        return json.loads(handler.wfile.getvalue()), service.handle_payload, reader

    def test_root_may_add_and_remove(self) -> None:
        for operation in ROOT_ONLY:
            answer, service, _ = self.send(
                {"action": "dashboard-access", "operation": operation}, uid=0)
            self.assertTrue(answer["ok"], operation)
            service.assert_called_once()

    def test_other_and_unknown_callers_are_refused_add_and_remove(self) -> None:
        for uid in (DASHBOARD_UID, None):
            for operation in ROOT_ONLY:
                payload = {"action": "dashboard-access", "operation": operation,
                           "mac_address": "02:00:00:00:00:01", "name": "secret-name"}
                answer, service, _ = self.send(payload, uid=uid)
                self.assertEqual(answer, {"ok": False, "reason": "root-required",
                                          "error": "Run this command as root on the Pi."})
                service.assert_not_called()

    def test_dashboard_user_may_still_list(self) -> None:
        for uid in (DASHBOARD_UID, None):
            answer, service, _ = self.send(
                {"action": "dashboard-access", "operation": "local-list"}, uid=uid)
            self.assertTrue(answer["ok"])
            service.assert_called_once()

    def test_dashboard_user_keeps_every_other_action_and_operation(self) -> None:
        # Panic (normal-access, emergency-status, emergency-protected-sync) included.
        payloads = [{"action": action} for action in every_helper_action()
                    if action not in ("homepage-snapshot", "homepage-confirm")]
        payloads += [{"action": "dashboard-access", "operation": operation}
                     for operation in dashboard_access_operations() if operation not in ROOT_ONLY]
        self.assertIn({"action": "normal-access"}, payloads)
        self.assertIn({"action": "dashboard-access", "operation": "emergency-status"}, payloads)
        for payload in payloads:
            with self.subTest(payload=payload):
                answer, service, reader = self.send(payload, uid=DASHBOARD_UID)
                self.assertTrue(answer["ok"])
                service.assert_called_once_with(payload)
                reader.assert_not_called()


class PeerUidTests(unittest.TestCase):
    def test_reads_the_peer_of_a_real_socket_or_reports_unknown(self) -> None:
        left, right = socket.socketpair(socket.AF_UNIX)
        with left, right:
            expected = os.getuid() if hasattr(socket, "SO_PEERCRED") else None
            self.assertEqual(helper._peer_uid(left), expected)

    def test_a_failed_read_is_unknown(self) -> None:
        connection = mock.Mock()
        connection.getsockopt.side_effect = OSError("closed")
        self.assertIsNone(helper._peer_uid(connection))


if __name__ == "__main__":
    unittest.main()
