import json

from jittersim.config.exemptions import UnaffectedDevice
from jittersim.config.models import Profile
from jittersim.config.storage import ConfigRepository
from jittersim.engine.compiler import compile_plan
from jittersim.engine.plan import GeneralProfileRule
from jittersim.networking.applier import NetworkApplier
from jittersim.networking.commands import CommandResult
from jittersim.networking.helper import HelperService
from jittersim.networking.homepage import helper_confirm, helper_snapshot
from jittersim.networking.topology import Topology

MAC = "02:00:00:00:00:02"
PAYLOAD = {
    "action": "homepage-snapshot",
    "peer": "192.168.50.2",
    "host": "example.com",
    "addresses": ["93.184.216.34"],
}


class Executor:
    def __init__(self):
        self.commands = []
        self.after_route = None
        self.state = ["REACHABLE"]
        self.station = MAC
        self.gateway = None

    def run(self, command):
        self.commands.append(command)
        if command.executable == "iw":
            return CommandResult(0, stdout="Station " + self.station)
        if "route" in command.arguments:
            if self.after_route:
                self.after_route()
            route = {"dev": "wlan0"}
            if self.gateway:
                route["gateway"] = self.gateway
            return CommandResult(0, stdout=json.dumps([route]))
        return CommandResult(
            0,
            stdout=json.dumps(
                [{"dst": "192.168.50.2", "dev": "wlan0", "lladdr": MAC, "state": self.state}]
            ),
        )


def setup(tmp_path):
    database = tmp_path / "config.db"
    with ConfigRepository(database):
        pass
    executor = Executor()
    service = HelperService(
        NetworkApplier(executor=executor, topology=Topology("eth0", "wlan0")),
        homepage_database=database,
    )
    service._active_plan = compile_plan(
        devices=(),
        profiles={"p": Profile("p", "P", homepage_bounce_percent=100)},
        general_profile=GeneralProfileRule("p"),
    )
    return service, executor, database


def test_fresh_identity_commands_share_a_deadline_and_do_not_cache(tmp_path):
    service, executor, _ = setup(tmp_path)
    first = helper_snapshot(service, PAYLOAD)
    assert first["chance"] == 100
    assert helper_confirm(service, {"receipt": first["receipt"]})["verified"]
    assert len(executor.commands) == 3
    assert all(0 < command.timeout_seconds <= 0.45 for command in executor.commands)
    executor.state = ["STALE"]
    assert not helper_snapshot(service, PAYLOAD)["verified"]
    assert len(executor.commands) == 5


def test_protection_write_during_identity_disables_and_confirmation_rechecks(tmp_path):
    service, executor, database = setup(tmp_path)
    first = helper_snapshot(service, PAYLOAD)

    def protect():
        with ConfigRepository(database) as repository:
            repository.save_unaffected_device(UnaffectedDevice(MAC, "Phone"))

    executor.after_route = protect
    assert not helper_snapshot(service, PAYLOAD)["verified"]
    assert not helper_confirm(service, {"receipt": first["receipt"]})["verified"]


def test_reset_during_identity_and_routed_peer_are_refused(tmp_path):
    service, executor, _ = setup(tmp_path)
    executor.after_route = lambda: setattr(service, "_active_plan", None)
    assert not helper_snapshot(service, PAYLOAD)["verified"]
    service, executor, _ = setup(tmp_path)
    executor.gateway = "192.168.50.1"
    assert not helper_snapshot(service, PAYLOAD)["verified"]


def test_busy_write_refused_without_identity_work(tmp_path):
    service, executor, _ = setup(tmp_path)
    with service._network_lock:
        assert not helper_snapshot(service, PAYLOAD)["verified"]
    assert not executor.commands


def test_saved_profile_edit_does_not_replace_applied_chance(tmp_path):
    service, _, database = setup(tmp_path)
    with ConfigRepository(database) as repository:
        repository.save_profile(Profile("p", "P", homepage_bounce_percent=0))
    assert helper_snapshot(service, PAYLOAD)["chance"] == 100


def test_policy_socket_saturation_leaves_reset_control_capacity(tmp_path):
    import threading
    import time
    from concurrent.futures import ThreadPoolExecutor

    from jittersim.networking.helper import HelperClient, UnixHelperServer

    service, executor, _ = setup(tmp_path)
    entered = threading.Event()
    release = threading.Event()
    original = executor.run

    def slow(command):
        if "route" in command.arguments:
            entered.set()
            release.wait(2)
        return original(command)

    executor.run = slow
    service._applier.reset = lambda: 0
    import tempfile
    from pathlib import Path
    temporary = tempfile.TemporaryDirectory(prefix="hpol-", dir="/tmp")
    main_socket = Path(temporary.name) / "h.sock"
    policy_socket = Path(temporary.name) / "h.sock.homepage"
    with (
        UnixHelperServer(main_socket, service) as control,
        UnixHelperServer(policy_socket, service, policy_only=True) as policy_server,
    ):
        workers = [
            threading.Thread(target=server.serve_forever, daemon=True)
            for server in (control, policy_server)
        ]
        for worker in workers:
            worker.start()
        client = HelperClient(socket_path=main_socket, timeout_seconds=0.5)
        with ThreadPoolExecutor(max_workers=2) as pool:
            requests = [
                pool.submit(
                    client.homepage_snapshot,
                    peer="192.168.50.2",
                    host="example.com",
                    addresses=("93.184.216.34",),
                )
                for _ in range(2)
            ]
            try:
                assert entered.wait(1)
                started = time.monotonic()
                assert client.reset()["ok"]
                assert time.monotonic() - started < 0.25
                assert client.ping()["ok"]
            finally:
                release.set()
                for request in requests:
                    result = request.result(1)
                    assert not result["verified"]
                control.shutdown()
                policy_server.shutdown()
        for worker in workers:
            worker.join(1)
    temporary.cleanup()
