import http.client
import json
import threading
from queue import Queue

from jittersim.api.homepage import PairingStore
from jittersim.api.server import ControlServer
from jittersim.config.storage import ConfigRepository


class Resolver:
    def resolve(self, _):
        return ("93.184.216.34",)

    def close(self):
        pass


class Helper:
    pass


def test_admin_enrollment_is_authenticated_and_rejects_cross_site_requests(tmp_path):
    ready = Queue()

    def serve():
        with ConfigRepository(tmp_path / "db") as repository:
            with ControlServer(("127.0.0.1", 0), repository, admin_token="admin") as server:
                server.homepage_store = PairingStore(Helper(), resolver=Resolver())
                ready.put(server)
                server.serve_forever()

    worker = threading.Thread(target=serve, daemon=True)
    worker.start()
    server = ready.get(timeout=2)

    def request(token, origin):
        connection = http.client.HTTPConnection(*server.server_address, timeout=2)
        try:
            connection.request(
                "POST",
                "/api/homepage/pairing-code",
                json.dumps({"extension_id": "a" * 32, "pages": ["https://example.org/article"]}),
                {
                    "Authorization": "Bearer " + token,
                    "Content-Type": "application/json",
                    "Origin": origin,
                },
            )
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()

    try:
        assert request("wrong", "http://127.0.0.1:" + str(server.server_port))[0] == 401
        assert request("admin", "https://other.example.org")[0] == 403
        status, result = request("admin", "http://127.0.0.1:" + str(server.server_port))
        assert status == 200 and len(result["code"]) == 32 and result["expires_in"] == 120
    finally:
        server.shutdown()
        worker.join(2)
