import plistlib
import re
import shutil
import socket
import ssl
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from jittersim import localca

HAVE_OPENSSL = shutil.which("openssl") is not None
NAMES = ("pi-lab.local", "jittersim.local", "netlab.local", "pi.home.arpa")


def run(*arguments: str) -> str:
    return subprocess.run(arguments, check=True, capture_output=True, text=True).stdout


def certificate_text(path: Path) -> str:
    return run("openssl", "x509", "-in", str(path), "-noout", "-text")


def alternative_names(path: Path) -> set:
    text = certificate_text(path)
    match = re.search(r"Subject Alternative Name:[^\n]*\n\s*([^\n]+)", text)
    return {item.strip() for item in match.group(1).split(",")}


def lifetime_days(path: Path) -> int:
    text = certificate_text(path)
    start = re.search(r"Not Before\s*:\s*(.+)", text).group(1).strip()
    end = re.search(r"Not After\s*:\s*(.+)", text).group(1).strip()
    from datetime import datetime
    pattern = "%b %d %H:%M:%S %Y %Z"
    return (datetime.strptime(end, pattern) - datetime.strptime(start, pattern)).days


@unittest.skipUnless(HAVE_OPENSSL, "openssl is required")
class LocalAuthorityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.ca = self.root / "ca"
        self.cert = self.root / "dashboard.crt"
        self.key = self.root / "dashboard.key"
        self.wifi_cert = self.root / "dashboard-wifi.crt"

    def authority(self, ethernet_ip: str = "192.168.1.20") -> bool:
        return localca.ensure_authority(self.ca, ethernet_ip=ethernet_ip)

    def leaf(self, **overrides):
        options = dict(
            names=NAMES,
            addresses=("192.168.1.20", "192.168.50.1"),
            neutral_names=("netlab.local", "pi.home.arpa"),
            neutral_addresses=("192.168.50.1",),
        )
        options.update(overrides)
        return localca.ensure_leaf(
            self.ca, self.cert, self.key, self.wifi_cert, **options)

    def test_creates_a_constrained_ec_authority_with_a_private_key(self) -> None:
        self.assertTrue(self.authority())

        text = certificate_text(self.ca / "ca.crt")
        self.assertIn("prime256v1", text)
        self.assertIn("CA:TRUE", text)
        self.assertIn("Certificate Sign", text)
        self.assertIn("Name Constraints", text)
        self.assertIn("DNS:local", text)
        self.assertIn("DNS:home.arpa", text)
        self.assertIn("IP:192.168.50.0/255.255.255.0", text)
        self.assertIn("IP:192.168.0.0/255.255.0.0", text)
        self.assertGreater(lifetime_days(self.ca / "ca.crt"), 3650)
        self.assertEqual((self.ca / "ca.key").stat().st_mode & 0o777, 0o600)
        self.assertNotIn("jittersim", text.lower())

    def test_keeps_a_valid_authority_and_refuses_a_broken_one(self) -> None:
        self.authority()
        original = (self.ca / "ca.crt").read_bytes()

        self.assertFalse(self.authority(ethernet_ip="10.1.2.3"))
        self.assertEqual((self.ca / "ca.crt").read_bytes(), original)

        (self.ca / "ca.key").unlink()
        with self.assertRaises(localca.TrustError):
            self.authority()
        self.assertEqual((self.ca / "ca.crt").read_bytes(), original)

        (self.ca / "ca.crt").write_text("not a certificate")
        with self.assertRaises(localca.TrustError):
            self.authority()
        self.assertEqual((self.ca / "ca.crt").read_text(), "not a certificate")

    def test_an_authority_with_a_mismatched_key_is_invalid(self) -> None:
        self.authority()
        other = self.root / "other"
        localca.ensure_authority(other, ethernet_ip="192.168.1.20")
        shutil.copy(other / "ca.key", self.ca / "ca.key")

        self.assertFalse(localca.authority_valid(self.ca))

    def test_issues_a_short_lived_leaf_with_the_expected_names(self) -> None:
        self.authority()

        result = self.leaf()

        self.assertTrue(result.reissued)
        self.assertEqual(
            alternative_names(self.cert),
            {"DNS:pi-lab.local", "DNS:jittersim.local", "DNS:netlab.local", "DNS:pi.home.arpa",
             "IP Address:192.168.1.20", "IP Address:192.168.50.1"},
        )
        self.assertLessEqual(lifetime_days(self.cert), 397)
        self.assertIn("prime256v1", certificate_text(self.cert))
        self.assertIn("TLS Web Server Authentication", certificate_text(self.cert))
        self.assertNotIn("CA:TRUE", certificate_text(self.cert))
        run("openssl", "verify", "-CAfile", str(self.ca / "ca.crt"), str(self.cert))
        self.assertEqual(self.key.stat().st_mode & 0o777, 0o600)

    def test_the_wifi_leaf_names_nothing_but_the_neutral_names_and_gateway(self) -> None:
        self.authority()
        self.leaf()

        self.assertEqual(
            alternative_names(self.wifi_cert),
            {"DNS:netlab.local", "DNS:pi.home.arpa", "IP Address:192.168.50.1"},
        )
        self.assertNotIn("jittersim", certificate_text(self.wifi_cert).lower())
        self.assertNotIn("pi-lab", certificate_text(self.wifi_cert))
        run("openssl", "verify", "-CAfile", str(self.ca / "ca.crt"), str(self.wifi_cert))
        public = run("openssl", "x509", "-in", str(self.wifi_cert), "-noout", "-pubkey")
        self.assertEqual(public, run("openssl", "x509", "-in", str(self.cert), "-noout", "-pubkey"))

    def test_leaf_is_reissued_only_when_something_changed(self) -> None:
        self.authority()
        self.leaf()
        first = self.cert.read_bytes()
        key = self.key.read_bytes()

        self.assertFalse(self.leaf().reissued)
        self.assertEqual(self.cert.read_bytes(), first)

        self.assertTrue(self.leaf(addresses=("192.168.1.99", "192.168.50.1")).reissued)
        self.assertIn("IP Address:192.168.1.99", alternative_names(self.cert))
        self.assertEqual(self.key.read_bytes(), key)

        # Changing the dashboard name reissues both certificates under the same key.
        self.assertTrue(self.leaf(
            addresses=("192.168.1.99", "192.168.50.1"),
            names=("pi-lab.local", "jittersim.local", "dash.local", "pi.home.arpa"),
            neutral_names=("dash.local", "pi.home.arpa")).reissued)
        for certificate in (self.cert, self.wifi_cert):
            self.assertIn("DNS:dash.local", alternative_names(certificate))
            self.assertNotIn("DNS:netlab.local", alternative_names(certificate))
            run("openssl", "verify", "-CAfile", str(self.ca / "ca.crt"), str(certificate))
        self.assertEqual(self.key.read_bytes(), key)

    def test_leaf_is_reissued_inside_the_renewal_window(self) -> None:
        self.authority()
        self.leaf(days=20)
        stale = self.cert.read_bytes()

        self.assertTrue(self.leaf().reissued)
        self.assertNotEqual(self.cert.read_bytes(), stale)
        self.assertFalse(self.leaf().reissued)

    def test_an_old_self_signed_certificate_is_migrated(self) -> None:
        self.authority()
        subprocess.run(
            ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-sha256", "-nodes",
             "-days", "825", "-keyout", str(self.key), "-out", str(self.cert),
             "-subj", "/CN=pi-lab.local"],
            check=True, capture_output=True)
        old = self.cert.read_bytes()

        self.assertTrue(self.leaf().reissued)
        self.assertNotEqual(self.cert.read_bytes(), old)
        run("openssl", "verify", "-CAfile", str(self.ca / "ca.crt"), str(self.cert))
        self.assertIn("prime256v1", certificate_text(self.cert))

    def test_a_leaf_signed_by_a_replaced_authority_is_reissued(self) -> None:
        self.authority()
        self.leaf()
        shutil.rmtree(self.ca)
        self.authority()

        self.assertTrue(self.leaf().reissued)
        run("openssl", "verify", "-CAfile", str(self.ca / "ca.crt"), str(self.cert))

    def test_an_address_outside_the_authority_ranges_is_left_out(self) -> None:
        self.authority(ethernet_ip="192.168.1.20")

        result = self.leaf(addresses=("10.9.9.9", "192.168.50.1"))

        self.assertNotIn("IP Address:10.9.9.9", alternative_names(self.cert))
        self.assertIn("IP Address:192.168.50.1", alternative_names(self.cert))
        self.assertTrue(any("10.9.9.9" in note for note in result.notes))
        run("openssl", "verify", "-CAfile", str(self.ca / "ca.crt"), str(self.cert))

    def test_a_name_outside_the_authority_is_refused(self) -> None:
        self.authority()

        with self.assertRaises(localca.TrustError):
            self.leaf(names=("example.com",))

    def test_the_authority_cannot_vouch_for_other_names(self) -> None:
        self.authority()
        rogue_key = self.root / "rogue.key"
        rogue_request = self.root / "rogue.csr"
        rogue_cert = self.root / "rogue.crt"
        extensions = self.root / "rogue.cnf"
        extensions.write_text("subjectAltName=DNS:example.com\n")
        run("openssl", "ecparam", "-name", "prime256v1", "-genkey", "-noout", "-out", str(rogue_key))
        run("openssl", "req", "-new", "-key", str(rogue_key), "-subj", "/CN=example.com",
            "-out", str(rogue_request))
        run("openssl", "x509", "-req", "-in", str(rogue_request), "-CA", str(self.ca / "ca.crt"),
            "-CAkey", str(self.ca / "ca.key"), "-CAcreateserial", "-days", "30",
            "-extfile", str(extensions), "-out", str(rogue_cert))

        result = subprocess.run(
            ["openssl", "verify", "-CAfile", str(self.ca / "ca.crt"), str(rogue_cert)],
            capture_output=True, text=True)

        self.assertNotEqual(result.returncode, 0)

    def test_a_python_client_trusting_only_the_authority_accepts_the_dashboard(self) -> None:
        self.authority()
        self.leaf()

        for certificate, name in ((self.cert, "jittersim.local"), (self.cert, "netlab.local"),
                                  (self.wifi_cert, "netlab.local"), (self.wifi_cert, "pi.home.arpa"),
                                  (self.wifi_cert, "192.168.50.1")):
            with self.subTest(name=name):
                server = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
                server.load_cert_chain(str(certificate), str(self.key))
                client = ssl.create_default_context(cafile=str(self.ca / "ca.crt"))
                left, right = socket.socketpair()
                self.addCleanup(left.close)
                self.addCleanup(right.close)
                accepted = []

                def serve():
                    try:
                        with server.wrap_socket(left, server_side=True) as stream:
                            accepted.append(stream.version())
                    except (ssl.SSLError, OSError) as error:
                        accepted.append(error)

                thread = threading.Thread(target=serve)
                thread.start()
                with client.wrap_socket(right, server_hostname=name) as stream:
                    self.assertTrue(stream.version())
                thread.join(5)

    def test_apple_profile_installs_only_the_authority_with_a_neutral_name(self) -> None:
        self.authority()

        first = localca.apple_profile(self.ca / "ca.crt")
        second = localca.apple_profile(self.ca / "ca.crt")

        self.assertEqual(first, second)
        profile = plistlib.loads(first)
        self.assertEqual(profile["PayloadType"], "Configuration")
        payload = profile["PayloadContent"]
        self.assertEqual(len(payload), 1)
        self.assertEqual(payload[0]["PayloadType"], "com.apple.security.root")
        der = subprocess.run(
            ["openssl", "x509", "-in", str(self.ca / "ca.crt"), "-outform", "der"],
            check=True, capture_output=True).stdout
        self.assertEqual(payload[0]["PayloadContent"], der)
        self.assertNotIn(b"jittersim", first.lower())
        self.assertEqual(localca.certificate_download(self.ca / "ca.crt").count(b"BEGIN CERTIFICATE"), 1)

    def test_command_line_prepares_the_files_without_the_secret_in_output(self) -> None:
        # Isolate both the operation directory and its retained manager.
        # The real command lease still supervises the OpenSSL children.
        entry = (
            "import runpy, sys; from pathlib import Path; from jittersim import updates; "
            "updates.UPDATE_ROOT = Path(sys.argv[1]); "
            "updates.MANAGER_PATH = updates.UPDATE_ROOT.parent / 'manager.py'; "
            "updates.manual_identity_operation = getattr(updates, 'manual_identity_operation', updates.update_lock); "
            "sys.argv = ['jittersim.localca'] + sys.argv[2:]; "
            "runpy.run_module('jittersim.localca', run_name='__main__')"
        )
        output = run(
            "python3", "-c", entry, str(self.root / "operations"), "ensure",
            "--config-dir", str(self.root), "--host-name", "pi-lab.local",
            "--neutral-name", "netlab.local", "--ethernet-ip", "192.168.1.20")

        self.assertTrue((self.root / "ca" / "ca.crt").exists())
        self.assertTrue(self.cert.exists())
        self.assertTrue(self.wifi_cert.exists())
        self.assertNotIn("PRIVATE", output)

    def test_the_authority_permits_dot_local_so_a_renamed_dashboard_needs_no_new_authority(self) -> None:
        self.authority()
        original = (self.ca / "ca.crt").read_bytes()
        text = certificate_text(self.ca / "ca.crt")
        constraints = text.split("Name Constraints")[-1]
        self.assertIn("DNS:local", constraints)
        self.assertNotIn("netlab", text)

        self.leaf(names=("pi-lab.local", "other-name.local"), neutral_names=("other-name.local",))

        for certificate in (self.cert, self.wifi_cert):
            run("openssl", "verify", "-CAfile", str(self.ca / "ca.crt"), str(certificate))
        self.assertIn("DNS:other-name.local", alternative_names(self.wifi_cert))
        self.assertEqual((self.ca / "ca.crt").read_bytes(), original)

    def test_a_neutral_name_the_authority_cannot_vouch_for_is_refused(self) -> None:
        self.authority()

        for bad in ("example.com", "jittersim.local", "two.labels.local", "netlab"):
            with self.subTest(bad=bad), self.assertRaises((localca.TrustError, ValueError)):
                self.leaf(names=("pi-lab.local",), neutral_names=(bad,))

    def test_the_wifi_certificate_never_names_the_pi_or_the_product(self) -> None:
        self.authority()
        self.leaf()

        text = certificate_text(self.wifi_cert).lower()
        for secret in ("pi-lab", "jittersim", "192.168.1.20"):
            self.assertNotIn(secret, text)

    def test_prepare_covers_the_saved_name_on_both_certificates_and_reissues_on_change(self) -> None:
        created, result = localca.prepare(
            self.root, host_name="pi-lab.local", neutral_name="netlab.local",
            ethernet_ip="192.168.1.20")
        self.assertTrue(created and result.reissued)
        for certificate in (self.cert, self.wifi_cert):
            self.assertIn("DNS:netlab.local", alternative_names(certificate))
        self.assertFalse({"DNS:pi-lab.local", "DNS:jittersim.local"} & alternative_names(self.cert))
        self.assertFalse(localca.prepare(
            self.root, host_name="pi-lab.local", neutral_name="netlab.local",
            ethernet_ip="192.168.1.20")[1].reissued)

        created, result = localca.prepare(
            self.root, host_name="pi-lab.local", neutral_name="bench.local",
            ethernet_ip="192.168.1.20")

        self.assertFalse(created)
        self.assertTrue(result.reissued)
        for certificate in (self.cert, self.wifi_cert):
            self.assertIn("DNS:bench.local", alternative_names(certificate))
            self.assertNotIn("DNS:netlab.local", alternative_names(certificate))
            run("openssl", "verify", "-CAfile", str(self.ca / "ca.crt"), str(certificate))

    def test_prepare_refuses_a_name_that_names_the_product(self) -> None:
        with self.assertRaises(ValueError):
            localca.prepare(
                self.root, host_name="pi-lab.local", neutral_name="jittersim.local",
                ethernet_ip="192.168.1.20")


@unittest.skipUnless(HAVE_OPENSSL, "openssl is required")
class LocalAuthorityCommandTests(unittest.TestCase):
    def test_the_command_finds_the_name_and_host_itself_for_the_dashboard_update(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "trust.env").write_text("JITTERSIM_DASHBOARD_NAME=dash.local\n")
            from jittersim import updates
            self.assertEqual(localca.main(["ensure", "--config-dir", str(root)],
                                          operation=updates.update_lock, update_root=root / "operations"), 0)

            names = alternative_names(root / "dashboard-wifi.crt")
            self.assertEqual(
                names, {"DNS:dash.local", "DNS:pi.home.arpa", "IP Address:192.168.50.1"})
            self.assertEqual(
                {name for name in alternative_names(root / "dashboard.crt") if name.startswith("DNS:")},
                {"DNS:dash.local", "DNS:pi.home.arpa"})

    def test_the_command_sets_modes_and_skips_ownership_without_root(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            from jittersim import updates
            permissions = localca.set_permissions

            def without_root(*args):
                # Model this permission branch explicitly even when the suite
                # runs as root. Other real ownership checks keep their identity.
                with patch.object(localca.os, "geteuid", return_value=1000):
                    permissions(*args)

            with patch.object(localca, "set_permissions", side_effect=without_root), \
                    patch.object(localca.shutil, "chown") as chown:
                self.assertEqual(localca.main(
                    ["ensure", "--config-dir", str(root), "--host-name", "pi-lab.local",
                     "--ethernet-ip", "192.168.1.20", "--group", "no-such-group"],
                    operation=updates.update_lock, update_root=root / "operations"), 0)
                chown.assert_not_called()

            mode = lambda path: path.stat().st_mode & 0o777
            self.assertEqual(mode(root / "ca"), 0o750)
            self.assertEqual(mode(root / "ca" / "ca.key"), 0o600)
            self.assertEqual(mode(root / "ca" / "ca.crt"), 0o644)
            self.assertEqual(mode(root / "dashboard.key"), 0o640)
            self.assertEqual(mode(root / "dashboard.crt"), 0o644)
            self.assertEqual(mode(root / "dashboard-wifi.crt"), 0o644)


class LocalNameTests(unittest.TestCase):
    def test_default_and_validation(self) -> None:
        from jittersim import localname

        self.assertEqual(localname.DEFAULT_LOCAL_NAME, "netlab.local")
        self.assertEqual(localname.validate_local_name("Dash.Local."), "dash.local")
        for bad in ("jittersim.local", "pi.example.com", "local", "-x.local",
                    "a b.local", "", "x" * 70 + ".local", "pi.home.arpa"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                localname.validate_local_name(bad)


if __name__ == "__main__":
    unittest.main()
