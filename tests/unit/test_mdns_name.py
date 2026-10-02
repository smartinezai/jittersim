"""The Pi announces one neutral name on the router network and on the test Wi-Fi.

Avahi publishes an address record on every network unless a record is bound to
one interface. The publisher therefore binds each record to its own interface,
so a device on either network gets the address that works for it and nothing
else about the Pi.
"""

import json
import tempfile
import unittest
from pathlib import Path

from jittersim import mdnsname
from jittersim.mdnsname import (
    COLLISION,
    ESTABLISHED,
    FAILURE,
    REGISTERING,
    AvahiCollision,
    AvahiError,
    Publisher,
)

DEBIAN_CONFIG = """\
# This file is part of avahi.
[server]
#host-name=foo
#domain-name=local
use-ipv4=yes
use-ipv6=yes
#allow-interfaces=eth0
#deny-interfaces=eth1
#enable-dbus=yes

[wide-area]
enable-wide-area=yes

[publish]
#disable-publishing=no
#disable-user-service-publishing=no
#publish-addresses=yes
#publish-hinfo=yes
#publish-workstation=yes
#publish-domain=yes

[reflector]
#enable-reflector=no

[rlimits]
#rlimit-core=0
"""


def active(text: str) -> dict:
    """The active settings of a config file as {(section, key): value}."""
    values, section = {}, None
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1]
        elif "=" in line:
            key, _, value = line.partition("=")
            values[(section, key.strip())] = value.strip()
    return values


class AvahiConfigTests(unittest.TestCase):
    def test_publishing_is_limited_to_what_the_publisher_adds(self) -> None:
        values = active(mdnsname.render_avahi_config(DEBIAN_CONFIG))

        for key, wanted in (
            ("publish-workstation", "no"),
            ("publish-addresses", "no"),
            ("publish-hinfo", "no"),
            ("publish-domain", "no"),
            ("disable-user-service-publishing", "yes"),
            ("disable-publishing", "no"),
        ):
            self.assertEqual(values[("publish", key)], wanted, key)
        self.assertEqual(values[("reflector", "enable-reflector")], "no")
        self.assertEqual(values[("server", "enable-dbus")], "yes")
        self.assertEqual(values[("server", "use-ipv4")], "yes")

    def test_other_settings_and_comments_are_kept(self) -> None:
        text = mdnsname.render_avahi_config(DEBIAN_CONFIG)

        self.assertEqual(active(text)[("wide-area", "enable-wide-area")], "yes")
        self.assertEqual(active(text)[("server", "use-ipv6")], "yes")
        for line in ("# This file is part of avahi.", "#host-name=foo", "[rlimits]"):
            self.assertIn(line, text)

    def test_the_result_is_stable_when_applied_again(self) -> None:
        once = mdnsname.render_avahi_config(DEBIAN_CONFIG, ("eth0", "wlan0"))
        self.assertEqual(mdnsname.render_avahi_config(once, ("eth0", "wlan0")), once)

    def test_values_that_would_block_publishing_are_replaced(self) -> None:
        text = DEBIAN_CONFIG.replace("use-ipv4=yes", "use-ipv4=no").replace(
            "#disable-publishing=no", "disable-publishing=yes").replace(
            "#publish-workstation=yes", "publish-workstation=yes").replace(
            "#enable-reflector=no", "enable-reflector=yes")

        values = active(mdnsname.render_avahi_config(text))

        self.assertEqual(values[("server", "use-ipv4")], "yes")
        self.assertEqual(values[("publish", "disable-publishing")], "no")
        self.assertEqual(values[("publish", "publish-workstation")], "no")
        self.assertEqual(values[("reflector", "enable-reflector")], "no")

    def test_missing_sections_are_added(self) -> None:
        values = active(mdnsname.render_avahi_config("[server]\nuse-ipv4=yes\n"))

        self.assertEqual(values[("publish", "publish-addresses")], "no")
        self.assertEqual(values[("reflector", "enable-reflector")], "no")

    def test_interface_limits_never_hide_either_network(self) -> None:
        limited = DEBIAN_CONFIG.replace("#allow-interfaces=eth0", "allow-interfaces=eth0").replace(
            "#deny-interfaces=eth1", "deny-interfaces=eth1,wlan0,docker0")

        values = active(mdnsname.render_avahi_config(limited, ("eth0", "wlan0")))

        self.assertEqual(values[("server", "allow-interfaces")], "eth0,wlan0")
        # Other networks stay hidden. Ours are taken off the list.
        self.assertEqual(values[("server", "deny-interfaces")], "eth1,docker0")
        everything = DEBIAN_CONFIG.replace("#deny-interfaces=eth1", "deny-interfaces=wlan0")
        self.assertNotIn(("server", "deny-interfaces"),
                         active(mdnsname.render_avahi_config(everything, ("eth0", "wlan0"))))
        # Without a limit nothing is added.
        self.assertNotIn(("server", "allow-interfaces"),
                         active(mdnsname.render_avahi_config(DEBIAN_CONFIG, ("eth0", "wlan0"))))


class AvahiFilesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.config = self.root / "avahi-daemon.conf"
        self.services = self.root / "services"
        self.services.mkdir()
        self.config.write_text(DEBIAN_CONFIG)
        for name in ("ssh.service", "sftp-ssh.service"):
            (self.services / name).write_text("<service-group>%s</service-group>" % name)
        (self.services / "notes.txt").write_text("keep")

    def configure(self, **options):
        return mdnsname.configure_avahi(self.config, self.services, **options)

    def test_configure_saves_the_original_once_and_switches_off_other_services(self) -> None:
        self.assertTrue(self.configure(interfaces=("eth0", "wlan0")))

        backup = self.root / "avahi-daemon.conf.jittersim-original"
        self.assertEqual(backup.read_text(), DEBIAN_CONFIG)
        self.assertEqual(active(self.config.read_text())[("publish", "publish-workstation")], "no")
        self.assertEqual(sorted(path.name for path in self.services.glob("*.service")), [])
        self.assertTrue((self.services / "ssh.service.jittersim-disabled").exists())
        self.assertTrue((self.services / "notes.txt").exists())

        self.assertFalse(self.configure(interfaces=("eth0", "wlan0")))
        self.assertEqual(backup.read_text(), DEBIAN_CONFIG)

    def test_a_service_added_later_is_switched_off_on_the_next_run(self) -> None:
        self.configure()
        (self.services / "printer.service").write_text("<service-group/>")

        self.assertTrue(self.configure())

        self.assertFalse((self.services / "printer.service").exists())
        self.assertTrue((self.services / "printer.service.jittersim-disabled").exists())

    def test_restore_puts_back_the_original_settings_and_services(self) -> None:
        self.configure(interfaces=("eth0", "wlan0"))

        self.assertTrue(mdnsname.restore_avahi(self.config, self.services))

        self.assertEqual(self.config.read_text(), DEBIAN_CONFIG)
        self.assertTrue((self.services / "ssh.service").exists())
        self.assertTrue((self.services / "sftp-ssh.service").exists())
        self.assertFalse(list(self.services.glob("*.jittersim-disabled")))
        self.assertFalse((self.root / "avahi-daemon.conf.jittersim-original").exists())
        self.assertFalse(mdnsname.restore_avahi(self.config, self.services))

    def test_restore_does_not_overwrite_a_service_the_user_added_back(self) -> None:
        self.configure()
        (self.services / "ssh.service").write_text("new copy")

        mdnsname.restore_avahi(self.config, self.services)

        self.assertEqual((self.services / "ssh.service").read_text(), "new copy")

    def test_a_missing_avahi_is_reported_not_created(self) -> None:
        self.config.unlink()

        with self.assertRaises(mdnsname.ConfigureError):
            self.configure()

        self.assertFalse(self.config.exists())
        self.assertTrue((self.services / "ssh.service").exists())

    def test_the_command_line_reports_changed_and_unchanged(self) -> None:
        arguments = ["configure", "--config", str(self.config), "--services", str(self.services),
                     "--uplink", "eth0", "--test-interface", "wlan0"]
        out = []
        self.assertEqual(mdnsname.main(arguments, out.append), 0)
        self.assertEqual(mdnsname.main(arguments, out.append), 0)
        self.assertEqual(out, ["changed", "unchanged"])
        self.assertEqual(mdnsname.main(["restore", "--config", str(self.config),
                                        "--services", str(self.services)], out.append), 0)
        self.assertEqual(out[-1], "restored")
        self.assertEqual(mdnsname.main(["configure", "--config", str(self.root / "gone.conf"),
                                        "--services", str(self.services)], out.append), 1)


class FakeGroup:
    def __init__(self, bus):
        self.bus = bus
        self.records = []
        self.committed = False
        self.freed = False
        self.state_value = ESTABLISHED

    def add_address(self, index, name, address):
        error = self.bus.refuse.get(name)
        if error is not None:
            raise error
        self.records.append((index, name, address))

    def commit(self):
        self.committed = True

    def state(self):
        if self.bus.broken:
            raise AvahiError("gone")
        return self.bus.states.get(self.records[0][1:], self.state_value) if self.records \
            else self.state_value

    def free(self):
        self.freed = True


class FakeBus:
    def __init__(self):
        self.groups = []
        self.states = {}
        self.refuse = {}
        self.broken = False

    def new_group(self):
        if self.broken:
            raise AvahiError("Avahi is not answering")
        group = FakeGroup(self)
        self.groups.append(group)
        return group

    def live(self):
        return [record for group in self.groups if not group.freed for record in group.records]


class PublisherTests(unittest.TestCase):
    INDEXES = {"eth0": 2, "wlan0": 3}

    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.status_path = Path(self.directory.name) / "run" / "status.json"
        self.bus = FakeBus()
        self.addresses = {"eth0": "192.168.0.23", "wlan0": "192.168.50.1"}
        self.now = [1000.0]
        self.connects = 0
        self.publisher = self.make()

    def make(self, **options):
        def connect():
            self.connects += 1
            if self.bus.broken:
                raise AvahiError("Avahi is not answering")
            return self.bus

        settings = dict(
            name="netlab.local", uplink="eth0", test_interface="wlan0",
            extra_names=("pi-lab.local", "jittersim.local"),
            connect=connect,
            address_of=lambda kind, interface: self.addresses.get(interface),
            index_of=lambda interface: self.INDEXES.get(interface),
            status_path=self.status_path, clock=lambda: self.now[0])
        settings.update(options)
        return Publisher(**settings)

    def neutral(self):
        return sorted(r for r in self.bus.live() if r[1] == "netlab.local")

    def test_each_network_gets_its_own_address_bound_to_its_own_interface(self) -> None:
        status = self.publisher.step()

        self.assertEqual(self.neutral(), [
            (2, "netlab.local", "192.168.0.23"),
            (3, "netlab.local", "192.168.50.1"),
        ])
        self.assertEqual(status["state"], "published")
        self.assertEqual({link["interface"]: link["address"] for link in status["links"]},
                         {"eth0": "192.168.0.23", "wlan0": "192.168.50.1"})

    def test_nothing_is_ever_published_for_all_interfaces(self) -> None:
        self.publisher.step()

        for index, _name, _address in self.bus.live():
            self.assertGreater(index, 0)

    def test_the_test_wifi_gets_only_the_one_name_and_never_the_ethernet_address(self) -> None:
        self.publisher.step()

        on_wifi = [r for r in self.bus.live() if r[0] == 3]
        self.assertEqual(on_wifi, [(3, "netlab.local", "192.168.50.1")])
        for _index, _name, address in on_wifi:
            self.assertNotEqual(address, "192.168.0.23")

    def test_older_names_are_published_on_ethernet_only(self) -> None:
        self.publisher.step()

        extras = sorted(r for r in self.bus.live() if r[1] != "netlab.local")
        self.assertEqual(extras, [
            (2, "jittersim.local", "192.168.0.23"),
            (2, "pi-lab.local", "192.168.0.23"),
        ])

    def test_a_new_ethernet_address_replaces_only_the_ethernet_record(self) -> None:
        self.publisher.step()
        wifi_group = next(g for g in self.bus.groups if g.records and g.records[0][0] == 3)
        self.addresses["eth0"] = "192.168.0.77"

        status = self.publisher.step()

        self.assertEqual(self.neutral(), [
            (2, "netlab.local", "192.168.0.77"),
            (3, "netlab.local", "192.168.50.1"),
        ])
        self.assertFalse(wifi_group.freed)
        self.assertEqual(status["state"], "published")
        self.assertTrue(all(r[2] == "192.168.0.77" for r in self.bus.live() if r[0] == 2))

    def test_an_unchanged_address_is_not_published_again(self) -> None:
        self.publisher.step()
        count = len(self.bus.groups)

        self.publisher.step()
        self.publisher.step()

        self.assertEqual(len(self.bus.groups), count)

    def test_losing_the_ethernet_address_drops_its_records_and_keeps_the_wifi_one(self) -> None:
        self.publisher.step()
        self.addresses["eth0"] = None

        status = self.publisher.step()

        self.assertEqual(self.neutral(), [(3, "netlab.local", "192.168.50.1")])
        self.assertEqual(status["state"], "partial")
        self.assertIn("Ethernet", status["message"])
        self.assertFalse([r for r in self.bus.live() if r[0] == 2])

    def test_the_wifi_record_follows_the_access_point(self) -> None:
        self.addresses["wlan0"] = None
        self.assertEqual(self.publisher.step()["state"], "partial")
        self.assertEqual(self.neutral(), [(2, "netlab.local", "192.168.0.23")])

        self.addresses["wlan0"] = "192.168.50.1"
        self.assertEqual(self.publisher.step()["state"], "published")
        self.assertEqual(len(self.neutral()), 2)

    def test_no_address_anywhere_publishes_nothing_and_waits(self) -> None:
        self.addresses.clear()

        status = self.publisher.step()

        self.assertEqual(self.bus.live(), [])
        self.assertEqual(status["state"], "waiting")

    def test_an_interface_that_does_not_exist_yet_is_waited_for(self) -> None:
        publisher = self.make(index_of=lambda interface: self.INDEXES.get(interface)
                              if interface == "eth0" else None)

        status = publisher.step()

        self.assertEqual(self.neutral(), [(2, "netlab.local", "192.168.0.23")])
        self.assertEqual(status["state"], "partial")

    def test_a_name_conflict_is_reported_with_a_plain_fix_and_never_renamed(self) -> None:
        self.bus.states[("netlab.local", "192.168.0.23")] = COLLISION

        status = self.publisher.step()

        self.assertEqual(status["state"], "conflict")
        self.assertIn("netlab.local", status["message"])
        self.assertIn("already", status["message"])
        self.assertIn("dashboard-name set", status["fix"])
        self.assertEqual({link["interface"]: link["state"] for link in status["links"]},
                         {"eth0": "conflict", "wlan0": "published"})
        # Nothing was published under another name such as netlab-2.local.
        self.assertFalse([r for r in self.bus.live() if "-2" in r[1]])

    def test_a_refused_registration_is_a_conflict_too(self) -> None:
        self.bus.refuse["netlab.local"] = AvahiCollision("name in use")

        status = self.publisher.step()

        self.assertEqual(status["state"], "conflict")
        self.assertIn("dashboard-name set", status["fix"])

    def test_a_conflict_is_tried_again_after_a_while_and_clears_when_gone(self) -> None:
        self.bus.states[("netlab.local", "192.168.0.23")] = COLLISION
        self.assertEqual(self.publisher.step()["state"], "conflict")
        count = len(self.bus.groups)

        self.now[0] += 5
        self.publisher.step()
        self.assertEqual(len(self.bus.groups), count)

        del self.bus.states[("netlab.local", "192.168.0.23")]
        self.now[0] += mdnsname.RETRY_SECONDS + 1
        status = self.publisher.step()

        self.assertGreater(len(self.bus.groups), count)
        self.assertEqual(status["state"], "published")

    def test_a_conflict_on_an_older_name_does_not_hide_the_neutral_name(self) -> None:
        self.bus.states[("pi-lab.local", "192.168.0.23")] = COLLISION

        status = self.publisher.step()

        self.assertEqual(status["state"], "published")
        self.assertEqual(len(self.neutral()), 2)
        names = {item["name"]: item["state"] for item in status["extra"]}
        self.assertEqual(names["pi-lab.local"], "conflict")
        self.assertEqual(names["jittersim.local"], "published")

    def test_a_failed_group_is_reported_and_tried_again(self) -> None:
        self.bus.states[("netlab.local", "192.168.50.1")] = FAILURE

        status = self.publisher.step()

        wifi = [link for link in status["links"] if link["interface"] == "wlan0"][0]
        self.assertEqual(wifi["state"], "failed")
        self.assertEqual(status["state"], "unavailable")

    def test_registering_is_not_an_error(self) -> None:
        self.bus.states[("netlab.local", "192.168.0.23")] = REGISTERING

        status = self.publisher.step()

        self.assertEqual(status["state"], "published")
        self.assertEqual([l["state"] for l in status["links"] if l["interface"] == "eth0"],
                         ["registering"])

    def test_losing_avahi_is_reported_and_the_records_return_when_it_does(self) -> None:
        self.publisher.step()
        self.bus.broken = True

        status = self.publisher.step()

        self.assertEqual(status["state"], "unavailable")
        self.assertIn("IP address", status["fix"] + status["message"])
        self.assertIn("avahi-daemon", status["fix"])

        self.bus.broken = False
        self.bus.groups.clear()
        status = self.publisher.step()

        self.assertEqual(status["state"], "published")
        self.assertEqual(len(self.neutral()), 2)

    def test_starting_without_avahi_is_not_a_crash(self) -> None:
        self.bus.broken = True

        status = self.publisher.step()

        self.assertEqual(status["state"], "unavailable")
        self.assertEqual(self.bus.live(), [])

    def test_the_status_file_is_written_atomically_and_readable_by_the_dashboard(self) -> None:
        self.publisher.step()

        mode = self.status_path.stat().st_mode & 0o777
        saved = json.loads(self.status_path.read_text())
        self.assertEqual(mode, 0o644)
        self.assertEqual(saved["name"], "netlab.local")
        self.assertEqual(saved["state"], "published")
        self.assertEqual(saved["updated"], 1000)
        self.assertFalse(list(self.status_path.parent.glob("*.tmp")))
        self.assertNotIn("jittersim", json.dumps(saved["links"]).lower())

    def test_the_status_file_is_rewritten_only_when_something_changed_or_is_old(self) -> None:
        self.publisher.step()
        first = self.status_path.stat().st_mtime_ns
        self.now[0] += 5
        self.publisher.step()
        self.assertEqual(self.status_path.stat().st_mtime_ns, first)
        self.assertEqual(json.loads(self.status_path.read_text())["updated"], 1000)

        self.now[0] += mdnsname.STATUS_REFRESH_SECONDS + 1
        self.publisher.step()
        self.assertEqual(json.loads(self.status_path.read_text())["updated"],
                         int(self.now[0]))

    def test_stopping_frees_every_record(self) -> None:
        self.publisher.step()

        self.publisher.close()

        self.assertEqual(self.bus.live(), [])


class AddressLookupTests(unittest.TestCase):
    def runner(self, outputs):
        calls = []

        def run(command):
            calls.append(command)
            return outputs.get(tuple(command), "")

        run.calls = calls
        return run

    def test_the_ethernet_address_is_the_route_source_on_that_interface(self) -> None:
        run = self.runner({
            ("ip", "-4", "route", "get", "1.1.1.1", "oif", "eth0"):
                "1.1.1.1 via 192.168.0.1 dev eth0 src 192.168.0.23 uid 0\n    cache\n"})

        self.assertEqual(mdnsname.ethernet_address("eth0", run), "192.168.0.23")

    def test_the_interface_address_is_the_fallback_without_a_route(self) -> None:
        run = self.runner({
            ("ip", "-4", "-o", "addr", "show", "dev", "eth0", "scope", "global"):
                "2: eth0    inet 10.4.5.6/24 brd 10.4.5.255 scope global dynamic eth0\\ "})

        self.assertEqual(mdnsname.ethernet_address("eth0", run), "10.4.5.6")

    def test_loopback_link_local_and_garbage_are_never_published(self) -> None:
        for output in ("1.1.1.1 dev lo src 127.0.0.1", "x dev eth0 src 169.254.3.4",
                       "x dev eth0 src not-an-address", "x dev eth0 src 0.0.0.0", ""):
            with self.subTest(output=output):
                run = self.runner({
                    ("ip", "-4", "route", "get", "1.1.1.1", "oif", "eth0"): output})
                self.assertIsNone(mdnsname.ethernet_address("eth0", run))

    def test_the_access_point_address_is_the_gateway_address_when_the_interface_has_it(self) -> None:
        listing = "3: wlan0    inet 192.168.50.1/24 brd 192.168.50.255 scope global noprefixroute wlan0"
        run = self.runner({("ip", "-4", "-o", "addr", "show", "dev", "wlan0"): listing})
        self.assertEqual(mdnsname.access_point_address("wlan0", run), "192.168.50.1")

        run = self.runner({("ip", "-4", "-o", "addr", "show", "dev", "wlan0"):
                           "3: wlan0    inet 192.168.0.9/24 scope global wlan0"})
        self.assertIsNone(mdnsname.access_point_address("wlan0", run))
        self.assertIsNone(mdnsname.access_point_address("wlan0", self.runner({})))

    def test_odd_interface_names_never_reach_a_command(self) -> None:
        run = self.runner({})
        for bad in ("", "eth0; reboot", "a" * 16, "-x"):
            self.assertIsNone(mdnsname.ethernet_address(bad, run))
            self.assertIsNone(mdnsname.access_point_address(bad, run))
        self.assertEqual(run.calls, [])


class StatusReportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "status.json"

    def test_a_missing_file_means_the_publisher_is_not_running(self) -> None:
        status = mdnsname.read_status(self.path)

        self.assertEqual(status["state"], "stopped")
        self.assertIn("systemctl status jittersim-mdns", status["fix"])
        self.assertIn("IP address", status["message"])

    def test_a_stale_file_is_not_trusted(self) -> None:
        self.path.write_text(json.dumps({"state": "published", "name": "netlab.local",
                                         "updated": 100, "links": [], "extra": []}))

        self.assertEqual(mdnsname.read_status(self.path, now=100 + 3600)["state"], "stopped")
        self.assertEqual(mdnsname.read_status(self.path, now=130)["state"], "published")

    def test_damaged_or_hostile_files_are_ignored(self) -> None:
        for content in ("not json", "[]", '{"state": 5}', '{"state": "weird", "updated": 1}',
                        "x" * 100000):
            with self.subTest(content=content[:20]):
                self.path.write_text(content)
                self.assertEqual(mdnsname.read_status(self.path, now=2)["state"], "stopped")

    def test_the_summary_lines_name_the_address_and_each_network(self) -> None:
        self.path.write_text(json.dumps({
            "state": "published", "name": "netlab.local", "updated": 1,
            "message": "ok", "fix": None,
            "links": [{"link": "ethernet", "interface": "eth0", "address": "192.168.0.23",
                       "state": "published"},
                      {"link": "test", "interface": "wlan0", "address": "192.168.50.1",
                       "state": "published"}],
            "extra": []}))

        lines = mdnsname.summary_lines(mdnsname.read_status(self.path, now=2))

        text = "\n".join(lines)
        self.assertIn("netlab.local", text)
        self.assertIn("192.168.0.23", text)
        self.assertIn("192.168.50.1", text)

    def test_a_conflict_summary_ends_with_the_fix(self) -> None:
        self.path.write_text(json.dumps({
            "state": "conflict", "name": "netlab.local", "updated": 1,
            "message": "Another device already uses netlab.local on the router network.",
            "fix": "Pick another name: sudo jittersim dashboard-name set <name>.local",
            "links": [], "extra": []}))

        lines = mdnsname.summary_lines(mdnsname.read_status(self.path, now=2))

        self.assertIn("already uses", lines[0])
        self.assertIn("dashboard-name set", lines[-1])


if __name__ == "__main__":
    unittest.main()
