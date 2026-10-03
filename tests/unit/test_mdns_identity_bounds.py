"""Keep fresh mDNS names neutral and bound actual D-Bus adapter calls."""

import sys
import json
import time
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from jittersim import localca, mdnsname
from tests.unit.test_mdns_name import FakeBus


@pytest.mark.parametrize("field,value", [
    ("updated", float("nan")), ("updated", float("inf")), ("updated", True),
    ("updated", 10**400), ("links", None), ("links", "invalid"), ("links", {}),
    ("extra", None), ("extra", "invalid"), ("extra", {}),
    ("links", [{"link": []}]), ("extra", [{"name": []}]),
])
def test_malformed_status_returns_the_safe_fallback_to_trust_consumers(tmp_path, field, value):
    from jittersim.api.trust import read_trust_api, dashboard_name_report
    path = tmp_path / "status.json"
    data = {"state": "published", "name": "netlab.local", "updated": time.time(),
            "links": [], "extra": []}
    data[field] = value
    path.write_text(json.dumps(data))
    server = SimpleNamespace(name_status_path=path, local_name="netlab.local",
                             server_address=("127.0.0.1", 8080), trust_ca_path=None)
    status, _, body = read_trust_api(server, "/api/trust")
    assert status == 200
    assert json.loads(body)["announce"]["state"] == "stopped"
    assert dashboard_name_report(server)["state"] == "stopped"


@pytest.mark.parametrize("kind", ["link", "fifo"])
def test_status_reader_refuses_links_and_special_files_without_waiting(tmp_path, kind):
    import os
    path = tmp_path / "status.json"
    if kind == "link":
        target = tmp_path / "other"
        target.write_text(json.dumps({"state": "published", "name": "netlab.local", "updated": 1}))
        path.symlink_to(target)
    else:
        os.mkfifo(path, 0o600)
    assert mdnsname.read_status(path, now=2, name="netlab.local")["state"] == "stopped"


def test_deeply_nested_damaged_status_returns_the_safe_fallback(tmp_path):
    path = tmp_path / "status.json"
    path.write_text("[" * 5000 + "0" + "]" * 5000)
    assert mdnsname.read_status(path, name="netlab.local")["state"] == "stopped"


def test_registration_is_reported_ready_only_after_both_interfaces_are_established(tmp_path):
    bus = FakeBus()
    bus.states[("netlab.local", "192.168.0.20")] = mdnsname.REGISTERING
    bus.states[("netlab.local", "192.168.50.1")] = mdnsname.REGISTERING
    indexes = {"eth0": 2, "wlan0": 3}
    addresses = {"eth0": "192.168.0.20", "wlan0": "192.168.50.1"}
    publisher = mdnsname.Publisher(name="netlab.local", uplink="eth0", test_interface="wlan0",
                                  extra_names=(), connect=lambda: bus,
                                  index_of=indexes.get, address_of=lambda kind, link: addresses[link],
                                  status_path=tmp_path / "status.json")
    try:
        waiting = publisher.step()
        assert waiting["state"] == "waiting"
        assert "opens the dashboard" not in waiting["message"]
        bus.states[("netlab.local", "192.168.0.20")] = mdnsname.ESTABLISHED
        partial = publisher.step()
        assert partial["state"] == "partial"
        assert "Ethernet" in partial["message"]
        assert "test Wi-Fi" in partial["message"]
        assert "waiting" in partial["message"].lower()
        bus.states[("netlab.local", "192.168.50.1")] = mdnsname.ESTABLISHED
        complete = publisher.step()
        assert complete["state"] == "published"
    finally:
        publisher.close()


def test_fresh_certificate_does_not_publish_a_host_or_product_alias(tmp_path):
    (tmp_path / "trust.env").write_text("JITTERSIM_DASHBOARD_NAME=netlab.local\n")
    localca.prepare(tmp_path, host_name="old-pi.local", neutral_name="netlab.local", ethernet_ip="192.168.1.20")
    assert mdnsname._extra_names("netlab.local", tmp_path / "trust.env") == ()


def test_only_existing_local_certificate_aliases_stay_on_ethernet(tmp_path):
    (tmp_path / "trust.env").write_text("JITTERSIM_DASHBOARD_NAME=netlab.local\n")
    localca.prepare(tmp_path, host_name="old-pi.local", neutral_name="netlab.local", ethernet_ip="192.168.1.20")
    localca.ensure_leaf(tmp_path / "ca", tmp_path / "dashboard.crt", tmp_path / "dashboard.key",
                        tmp_path / "dashboard-wifi.crt",
                        names=("netlab.local", "old-pi.local", "jittersim.local"),
                        addresses=("192.168.50.1",),
                        neutral_names=("netlab.local", "pi.home.arpa"))
    assert set(mdnsname._extra_names("netlab.local", tmp_path / "trust.env")) == {"old-pi.local", "jittersim.local"}


def test_runtime_settings_links_are_refused_instead_of_defaulting_interfaces(tmp_path):
    saved = tmp_path / "runtime.env"
    original = tmp_path / "other.env"
    original.write_text("JITTERSIM_UPLINK=eth9\n")
    saved.symlink_to(original)
    with pytest.raises(OSError):
        mdnsname._environment(saved)


def test_actual_adapter_calls_have_explicit_signatures_and_total_deadline():
    calls = []

    class BusError(Exception):
        def get_dbus_name(self):
            return "org.freedesktop.Avahi.Error.CollisionError"

    class Bus:
        def call_blocking(self, service, path, interface, method, signature, arguments, **kwargs):
            calls.append((method, signature, arguments, kwargs["timeout"]))
            if method == "EntryGroupNew":
                return "/group/1"
            return 2 if method == "GetState" else None

    module = SimpleNamespace(SystemBus=Bus, DBusException=BusError, Int32=int, UInt32=int)
    with patch.dict(sys.modules, {"dbus": module}):
        adapter = mdnsname.DbusAvahi()
        group = adapter.new_group()
        group.add_address(2, "netlab.local", "192.168.0.5")
        group.commit()
        assert group.state() == 2
        token = mdnsname._STEP_DEADLINE.set(time.monotonic() + 0.05)
        try:
            group.state()
            assert 0 < calls[-1][-1] <= 0.05
        finally:
            mdnsname._STEP_DEADLINE.reset(token)
        count = len(calls)
        token = mdnsname._STEP_DEADLINE.set(time.monotonic() - 1)
        try:
            with pytest.raises(mdnsname.AvahiError, match="time allowance"):
                group.state()
        finally:
            mdnsname._STEP_DEADLINE.reset(token)
        assert len(calls) == count
        group.free()
    add = next(call for call in calls if call[0] == "AddAddress")
    assert add[1:3] == ("iiuss", (2, 0, 16, "netlab.local", "192.168.0.5"))
    assert all(0 < call[-1] <= 2 for call in calls)
