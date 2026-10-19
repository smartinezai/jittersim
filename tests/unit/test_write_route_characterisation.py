"""Characterisation test of the dashboard's write routes.

Every write path (and a few that do not exist) is sent to
ControlServer.write_response_for with three small payloads, each on a fresh
server with and without a helper. The answers are compared with
tests/unit/write_route_characterisation.json: the status, the content type,
the top-level JSON keys and every error message, or the exception raised. It
was recorded before the write router became a route table, so a refactor of
the routing must keep it green.

A new route does not need a new entry. To record the corpus again after a wanted
change, run:

    JITTERSIM_RECORD_ROUTES=1 python -m pytest tests/unit/test_write_route_characterisation.py
"""

import json
import os
import unittest
from pathlib import Path

from jittersim.api.server import WRITE_PATHS, ControlServer
from jittersim.config.models import Device, Impairment, Profile
from jittersim.config.storage import ConfigRepository
from tests.unit.test_api import FakeHelper

SNAPSHOT = Path(__file__).with_name("write_route_characterisation.json")
EXTRA_PATHS = ("/api/nope", "/api/profiles/", "/api/router/nope", "/api/routerx", "/api/delete")
PAYLOADS = {
    "empty": {},
    "unknown-id": {"id": "nobody"},
    "known-ids": {"id": "alice", "device_id": "alice", "profile_id": "slow", "group_id": "nobody",
                  "name": "Characterised"},
}


def _summary(answer):
    status, content_type, body = answer
    result = {"status": int(status), "content_type": content_type}
    if body and content_type.startswith("application/json"):
        data = json.loads(body)
        if isinstance(data, dict):
            result["keys"] = sorted(data)
            if "error" in data:
                result["error"] = data["error"]
        else:
            result["json"] = type(data).__name__
    return result


class WriteRouteCharacterisationTests(unittest.TestCase):
    maxDiff = None

    def _answer(self, mode, path, payload):
        repository = ConfigRepository(":memory:")
        repository.save_profile(Profile(id="slow", name="Slow", upload=Impairment(delay_ms=3_000)))
        repository.save_device(Device(id="alice", name="Alice's laptop",
                                      ipv4_address="192.168.50.10", profile_id="slow"))
        server = ControlServer(("127.0.0.1", 0), repository)
        if mode == "helper":
            server.helper_client = FakeHelper()
        try:
            return _summary(server.write_response_for(path, dict(payload)))
        except Exception as error:  # the exception is part of the recorded answer
            return {"raises": type(error).__name__, "message": str(error)}
        finally:
            server.server_close()
            repository.close()

    def _observe(self):
        observed = {}
        for mode in ("configuration-only", "helper"):
            for path in sorted(WRITE_PATHS) + list(EXTRA_PATHS):
                for name, payload in PAYLOADS.items():
                    observed[f"{mode} {path} {name}"] = self._answer(mode, path, payload)
        return observed

    def test_every_write_route_answers_as_recorded(self):
        observed = self._observe()
        if os.environ.get("JITTERSIM_RECORD_ROUTES") == "1":
            # One request per line keeps the file short and its diffs readable.
            SNAPSHOT.write_text("{\n" + ",\n".join(
                json.dumps(key) + ": " + json.dumps(observed[key], sort_keys=True)
                for key in sorted(observed)) + "\n}\n")
            self.skipTest("write route answers recorded")
        recorded = json.loads(SNAPSHOT.read_text())
        # Routes added later answer with their own tests: only the recorded corpus is compared.
        compared = {key: observed.get(key) for key in recorded}
        self.assertEqual(compared, recorded)


if __name__ == "__main__":
    unittest.main()
