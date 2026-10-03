"""Real archive and filesystem checks for the release recovery boundary."""

import hashlib
import io
import shutil
import json
import os
import re
import shlex
import sys
import tarfile
import tempfile
import threading
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from jittersim import localca, updates
from jittersim.updates import (
    UNITS,
    UpdateError,
    apply_update,
    atomic_json,
    healthy,
    request_update,
    run_command,
    stage_release,
    trust_key,
    update_status,
)
from tests.release_support import have_ssh_keygen, make_key, manifest, sign

PI = Path(__file__).resolve().parents[2] / "deploy" / "raspberry-pi"
WATCHER = "jittersim-service-watch.service"
NAME_UNIT = "jittersim-mdns.service"
# The installer switches off new package defaults and picks a message. Neither
# changes a release file or a JitterSim service. Stopping Avahi happens only when
# it cannot be limited (a test below covers the update doing the same).
NOT_MIRRORED_SYSTEMCTL = (
    "systemctl disable --now unbound.service",
    "systemctl disable --now hostapd.service",
    "systemctl stop avahi-daemon.service avahi-daemon.socket",
)


def installer_lines():
    """install.sh with one command per line and its for loop written out."""
    text = (PI / "install.sh").read_text().replace("\\\n", " ")
    lines = [" ".join(line.split()) for line in text.splitlines()]
    lines = [line for line in lines if line and not line.startswith("#")]
    expanded = []
    index = 0
    while index < len(lines):
        if lines[index] == 'if [ "$adoption_finished" = completed ]; then':
            # This second manual-install phase runs only for a completed
            # interrupted adoption. Ordinary root updates use their own
            # healthy-startup completion path rather than this extra restart.
            index = lines.index("fi", index) + 1
            continue
        loop = re.fullmatch(r"for (\w+) in ([^;]+); do", lines[index])
        if loop is None:
            expanded.append(lines[index])
            index += 1
            continue
        end = lines.index("done", index)
        for value in loop.group(2).split():
            expanded.extend(
                line.replace("$" + loop.group(1), value) for line in lines[index + 1 : end]
            )
        index = end + 1
    return [re.sub(r" \|\| return 1$", "", line) for line in expanded]


@unittest.skipUnless(have_ssh_keygen(), "ssh-keygen signs the releases")
class UpdateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.root = self.base / "updates"
        self.manager = self.base / "manager.py"
        self.manager.write_text("old recovery manager")
        self.access = self.base / "access"
        self.code, self.config, self.data, self.units = [
            self.base / item for item in ("code", "config", "data", "units")
        ]
        for directory in (self.code, self.config, self.data, self.units):
            directory.mkdir()
            directory.chmod(0o755)
        # A throwaway release signing key that this stand-in Pi trusts.
        self.private, self.public = make_key(self.base / "keys")
        trust_key(self.public, config=self.config, root=self.root)
        self.sequence = 100
        self.dispatcher = self.base / "dispatcher.d" / "80-jittersim-mdns"
        self.command = self.base / "bin" / "jittersim"
        self.command.parent.mkdir()
        (self.code / "old-file").write_text("previous installation")
        (self.data / "jittersim.db").write_text("original settings")
        (self.config / "control.env").write_text("private credentials")
        (self.config / "control.env").chmod(0o600)
        (self.config / "runtime.env").write_text(
            "JITTERSIM_UPLINK=eth7\nJITTERSIM_TEST_INTERFACE=wlan7\n"
        )
        for unit in UNITS:
            (self.units / unit).write_text("old service")
        # The test Wi-Fi is up unless a test says otherwise.
        self.wifi = "jittersim-ap"

    def tearDown(self):
        self.temp.cleanup()

    def archive(self, extra=None, sequence=None, version="v1"):
        """A release archive signed by the trusted key."""
        path = self.base / "release.tar.gz"
        files = {
            "pyproject.toml": "[project]\nname='jittersim'",
            "README.md": "Release",
            "deploy/RELEASE": manifest(sequence or self.sequence, version),
            "src/jittersim/api/server.py": "new code",
            "src/jittersim/updates.py": "# new recovery manager\nRECOVERY_FORMAT = 2\nOPERATION_FORMAT = 2\n",
        }
        # The release ships this checkout's Pi files so the installer steps are real.
        files.update(
            {
                "deploy/raspberry-pi/" + item.name: item.read_text()
                for item in PI.iterdir()
                if item.is_file()
            }
        )
        with tarfile.open(path, "w:gz") as archive:
            for name, content in files.items():
                raw = content.encode()
                info = tarfile.TarInfo(name)
                info.size = len(raw)
                archive.addfile(info, io.BytesIO(raw))
            if extra:
                archive.addfile(extra)
        sign(path, self.private)
        return path, hashlib.sha256(path.read_bytes()).hexdigest()

    def stage_archive(self, path, **options):
        return stage_release(path, root=self.root, code=self.code, config=self.config, **options)

    def stage(self, sequence=None, version="v1"):
        path, digest = self.archive(sequence=sequence, version=version)
        self.stage_archive(path, sha256=digest)

    def apply(self, run, health, query=None, *, isolate_panic=True):
        def package_commands(command, timeout):
            result = run(command, timeout)
            # Only the external package commands are stand-ins. Preparation,
            # exact hashes, filesystem publication and recovery run real code.
            if "pip" in command and "wheel" in command:
                folder = Path(command[command.index("--wheel-dir") + 1])
                (folder / "jittersim-0.2.0-py3-none-any.whl").write_bytes(b"fixture prepared wheel")
            return result

        def service_query(command, timeout):
            if command[:2] == ["systemctl", "show"]:
                return "ActiveState=inactive\nMainPID=0\nControlPID=0\nTasksCurrent=0"
            return query(command, timeout) if query else self.wifi

        def identity(source, config, budget, **options):
            # These archive tests use a minimal synthetic package. The verified
            # in-process identity calls have separate real-source tests.
            python = str(self.code / ".venv/bin/python")
            run([python, "-m", "jittersim.localname", "install",
                 "--config-dir", str(config), "--group", "jittersim"], budget.limit(60))
            run([python, "-m", "jittersim.localca", "ensure",
                 "--config-dir", str(config), "--group", "jittersim"], budget.limit(120))

        def name_service(source, config):
            run([str(self.code / ".venv/bin/python"), "-m", "jittersim.mdnsname", "configure",
                 "--uplink", "eth7", "--test-interface", "wlan7"], 60)

        from contextlib import nullcontext
        # The historical recovery fixtures predate the persistent guard. Real
        # default admission and provisioning are tested with reviewed sources.
        safety = types.SimpleNamespace(inspect=lambda *args: None, provision=lambda *args: None,
                                       retain=lambda *args: None)
        panic = patch.object(updates, "PanicProtection", lambda *args, **kwargs: safety, create=True) \
            if isolate_panic else nullcontext()
        with panic, patch.object(updates, "prepare_dashboard_identity", identity), \
                patch.object(updates, "configure_dashboard_name", name_service):
            return apply_update(
            root=self.root,
            code=self.code,
            config=self.config,
            data=self.data,
            access=self.access,
            units=self.units,
            manager=self.manager,
            dispatcher=self.dispatcher,
            command=self.command,
            run=package_commands,
            query=service_query,
            health=health,
        )

    def backup(self, relative=""):
        return updates.recovery_backup(self.root) / relative

    def recorder(self, fail=lambda command: False):
        commands = []

        def run(command, timeout):
            commands.append(list(command))
            if fail(command):
                raise UpdateError("command failed")

        return commands, run

    def install(self, fail=lambda command: False, query=None):
        """Install the staged release and return every command it ran."""
        self.stage()
        request_update("v1", root=self.root, code=self.code, config=self.config, run=lambda *args: None)
        commands, run = self.recorder(fail)
        self.apply(run, lambda: True, query)
        return commands

    def after_package(self, commands):
        index = next(i for i, command in enumerate(commands) if "pip" in command and "install" in command
                     and command[0] == str(self.code / ".venv/bin/python"))
        return commands[index + 1 :]

    def systemctl(self, commands):
        return [" ".join(command) for command in commands if command[0] == "systemctl"]

    def test_rejects_wrong_checksum_traversal_and_links_before_extraction(self):
        path, _ = self.archive()
        with self.assertRaises(UpdateError):
            self.stage_archive(path, sha256="0" * 64)
        for name, kind in (
            ("../escaped", tarfile.REGTYPE),
            ("src/link", tarfile.SYMTYPE),
            ("src/link", tarfile.LNKTYPE),
            ("src/./jittersim/api/server.py", tarfile.REGTYPE),
        ):
            entry = tarfile.TarInfo(name)
            entry.type = kind
            entry.linkname = "/etc/passwd"
            path, digest = self.archive(entry)
            with self.assertRaises(UpdateError):
                self.stage_archive(path, sha256=digest)
        self.assertFalse((self.base / "escaped").exists())
        self.assertFalse((self.root / "staged").exists())

    def test_request_names_only_an_approved_release_and_cannot_queue_twice(self):
        self.stage()
        with self.assertRaises(UpdateError):
            request_update("../../bad", root=self.root, code=self.code, config=self.config, run=lambda *args: None)
        request_update("v1", root=self.root, code=self.code, config=self.config, run=lambda *args: None)
        with self.assertRaises(UpdateError):
            request_update("v1", root=self.root, code=self.code, config=self.config, run=lambda *args: None)

    def test_failed_install_restores_code_database_services_and_permissions(self):
        self.stage()
        request_update("v1", root=self.root, code=self.code, config=self.config, run=lambda *args: None)

        def fail_package(command, timeout):
            if "pip" in command and "install" in command and command[0] == str(self.code / ".venv/bin/python"):
                (self.data / "jittersim.db").write_text("partially migrated")
                raise UpdateError("package install failed")

        with self.assertRaises(UpdateError):
            self.apply(fail_package, lambda: True)
        self.assertEqual((self.data / "jittersim.db").read_text(), "original settings")
        self.assertEqual((self.code / "old-file").read_text(), "previous installation")
        self.assertFalse((self.code / "src").exists())
        self.assertEqual((self.units / UNITS[0]).read_text(), "old service")
        self.assertEqual((self.config / "control.env").stat().st_mode & 0o777, 0o600)
        self.assertEqual(update_status(root=self.root, code=self.code, config=self.config)["phase"], "rolled-back")

    def test_rollback_keeps_a_certificate_authority_the_failed_update_created(self):
        self.stage()
        request_update("v1", root=self.root, code=self.code, config=self.config, run=lambda *args: None)
        created = {}

        def fail_package(command, timeout):
            if "pip" in command:
                localca.prepare(self.config, host_name="pi-lab.local", neutral_name="netlab.local",
                                ethernet_ip="192.168.1.20")
                created.update({name: hashlib.sha256((self.config / name).read_bytes()).hexdigest()
                                for name in updates.CERTIFICATE_FILES if (self.config / name).exists()})
                raise UpdateError("package install failed")

        with self.assertRaises(UpdateError):
            self.apply(fail_package, lambda: True)
        # Devices that already trust the authority must not be asked again.
        self.assertTrue(created)
        self.assertEqual({name: hashlib.sha256((self.config / name).read_bytes()).hexdigest()
                          for name in created}, created)
        self.assertEqual((self.config / "ca" / "ca.key").stat().st_mode & 0o777, 0o600)
        self.assertEqual((self.config / "control.env").read_text(), "private credentials")

    def test_rollback_refuses_an_incomplete_current_authority(self):
        (self.config / "ca").mkdir()
        (self.config / "ca" / "ca.crt").write_text("original authority")
        self.stage()
        request_update("v1", root=self.root, code=self.code, config=self.config, run=lambda *args: None)

        def fail_package(command, timeout):
            if "pip" in command:
                (self.config / "ca" / "ca.crt").write_text("replaced authority")
                raise UpdateError("package install failed")

        with self.assertRaises(UpdateError):
            self.apply(fail_package, lambda: True)
        self.assertEqual((self.config / "ca" / "ca.crt").read_text(), "replaced authority")
        self.assertEqual((self.data / "jittersim.db").read_text(), "original settings")

    def test_failure_before_backup_does_not_restore_an_older_backup(self):
        self.stage()
        backup = self.root / "backup"
        backup.mkdir()
        atomic_json(backup / "complete.json", {"created_at": 1})
        request_update("v1", root=self.root, code=self.code, config=self.config, run=lambda *args: None)
        with patch("jittersim.updates.shutil.disk_usage") as disk:
            disk.return_value.free = 0
            with self.assertRaises(UpdateError):
                self.apply(lambda *args: None, lambda: True)
        self.assertEqual((self.code / "old-file").read_text(), "previous installation")

    def test_access_policy_is_restored_after_a_failed_install(self):
        self.access.mkdir()
        policy = self.access / 'policy.json'
        policy.write_text('{"options":{"enabled":true}}')
        policy.chmod(0o640)
        self.stage()
        request_update('v1', root=self.root, code=self.code, config=self.config, run=lambda *args: None)
        def fail(command, timeout):
            if 'pip' in command and 'install' in command and command[0] == str(self.code / '.venv/bin/python'):
                policy.write_text('damaged')
                raise UpdateError('installation failed')
        with self.assertRaises(UpdateError):
            self.apply(fail, lambda: True)
        self.assertTrue(json.loads(policy.read_text())['options']['enabled'])
        self.assertEqual(policy.stat().st_mode & 0o777, 0o640)

    def test_downgrade_cannot_silently_remove_active_access_controls(self):
        self.stage()
        request_update('v1', root=self.root, code=self.code, config=self.config, run=lambda *args: None)
        self.apply(lambda *args: None, lambda: True)
        self.access.mkdir()
        (self.access / 'policy.json').write_text('{"options":{"enabled":true}}')
        request_update('', rollback=True, root=self.root, code=self.code, config=self.config, run=lambda *args: None)
        commands = []
        with self.assertRaisesRegex(UpdateError, 'disable household'):
            self.apply(lambda command, timeout: commands.append(command), lambda: True)
        self.assertEqual(commands, [])
        self.assertEqual(update_status(root=self.root, code=self.code, config=self.config)['phase'], 'failed')
        retained = updates.read_json(self.root / 'request.json')
        self.assertTrue(retained['rollback'])
        self.assertEqual(retained['request_kind'], 'rollback')
        self.assertEqual(updates.recovery_backup(self.root, pinned=retained['recovery']),
                         updates.recovery_backup(self.root))

    def test_successful_install_can_be_rolled_back(self):
        self.stage()
        request_update("v1", root=self.root, code=self.code, config=self.config, run=lambda *args: None)
        self.apply(lambda *args: None, lambda: True)
        self.assertEqual(update_status(root=self.root, code=self.code, config=self.config)["installed"], "v1")
        self.assertIn("RECOVERY_FORMAT = 2", self.manager.read_text())
        request_update("", rollback=True, root=self.root, code=self.code, config=self.config, run=lambda *args: None)
        self.apply(lambda *args: None, lambda: True)
        self.assertFalse((self.code / "src").exists())
        self.assertIn("RECOVERY_FORMAT = 2", self.manager.read_text())
        self.assertEqual(update_status(root=self.root, code=self.code, config=self.config)["phase"], "rolled-back")

    def test_failed_health_check_restores_the_previous_installation(self):
        self.stage()
        request_update("v1", root=self.root, code=self.code, config=self.config, run=lambda *args: None)
        with patch("jittersim.updates.time.sleep"):
            with self.assertRaisesRegex(UpdateError, "health check"):
                self.apply(lambda *args: None, lambda: not (self.code / "src").exists())
        self.assertEqual((self.data / "jittersim.db").read_text(), "original settings")
        self.assertEqual(update_status(root=self.root, code=self.code, config=self.config)["phase"], "rolled-back")

    def test_interrupted_installation_recovers_before_trying_another_install(self):
        self.stage()
        request_update("v1", root=self.root, code=self.code, config=self.config, run=lambda *args: None)

        def interrupted(command, timeout):
            if "pip" in command and "install" in command and command[0] == str(self.code / ".venv/bin/python"):
                raise KeyboardInterrupt()

        # A process interruption leaves recovery intent for the next invocation.
        with self.assertRaises(KeyboardInterrupt):
            self.apply(interrupted, lambda: True)
        self.apply(lambda *args: None, lambda: True)
        self.assertFalse((self.code / "src").exists())
        self.assertEqual(update_status(root=self.root, code=self.code, config=self.config)["phase"], "rolled-back")

    def test_failed_recovery_keeps_the_request_for_another_attempt(self):
        self.stage()
        request_update("v1", root=self.root, code=self.code, config=self.config, run=lambda *args: None)
        self.apply(lambda *args: None, lambda: True)
        request_update("", rollback=True, root=self.root, code=self.code, config=self.config, run=lambda *args: None)
        with patch("jittersim.updates.time.sleep"), self.assertRaises(UpdateError):
            self.apply(lambda *args: None, lambda: False)
        self.assertEqual(update_status(root=self.root, code=self.code, config=self.config)["phase"], "failed")
        self.assertTrue(json.loads((self.root / "request.json").read_text())["rollback"])
        self.apply(lambda *args: None, lambda: True)
        self.assertFalse((self.root / "request.json").exists())

    def installer_words(self, line):
        """Split an install.sh command and point its paths at this test's Pi."""
        replacements = (
            ("$SOURCE_DIR", str(self.root / "staged")),
            ("$INSTALL_DIR", str(self.code)),
            ("$CONFIG_DIR", str(self.config)),
            ("$DATA_DIR", str(self.data)),
            ("$JITTERSIM_UPLINK", "eth7"),
            ("$JITTERSIM_TEST_INTERFACE", "wlan7"),
            ("/etc/systemd/system", str(self.units)),
            ("/etc/NetworkManager/dispatcher.d/80-jittersim-mdns", str(self.dispatcher)),
            ("/etc/NetworkManager/dispatcher.d", str(self.dispatcher.parent)),
            ("/usr/local/lib/jittersim-update.py", str(self.manager)),
            ("/usr/local/lib/jittersim-profile-compat.py", str(self.manager.with_name("jittersim-profile-compat.py"))),
            ("/usr/local/bin/jittersim", str(self.command)),
        )
        words = []
        for word in shlex.split(line):
            for old, new in replacements:
                word = word.replace(old, new)
            words.append(sys.executable if word == "python3" else word)
        return words

    def test_the_update_runs_every_release_step_of_the_installer(self):
        # A new install.sh step that reads the release or installs a file fails
        # here until the dashboard update does the same.
        (self.code / "build" / "lib").mkdir(parents=True)
        (self.code / "build" / "lib" / "removed.py").write_text("module of an older release")
        (self.code / "old-file").chmod(0o666)
        commands = self.install()
        installed_units = set()
        for line in installer_lines():
            if not (
                "$SOURCE_DIR" in line
                or "$INSTALL_DIR" in line
                or line.startswith(("install ", "sed ", "ln ", "runuser "))
            ) or line.startswith(("SOURCE_DIR=", "INSTALL_DIR=", "if [ ", "echo ")):
                continue
            if any(name in line for name in ("install-lock.py", "create-ap-profile.py",
                                             " check-operation", " prepare-installer")):
                # Manual setup owns its lock and creates the first AP. Updates
                # already hold the shared lock and preserve the existing AP.
                continue
            words = self.installer_words(line)
            with self.subTest(line=line):
                if line.startswith("install -m "):
                    self.assertEqual(words[3:7], ["-o", "root", "-g", "root"])
                    source, destination = Path(words[7]), Path(words[8])
                    if destination.parent.name == "jittersim-dashboard.service.d":
                        self.assertTrue(destination.parent.is_dir())
                        self.assertEqual(destination.parent.stat().st_mode & 0o777, 0o755)

                    self.assertEqual(destination.read_bytes(), source.read_bytes())
                    self.assertEqual(destination.stat().st_mode & 0o777, int(words[2], 8))
                    if destination.parent == self.units:
                        installed_units.add(destination.name)
                elif line.startswith("install -d "):
                    if words[-1] == str(self.dispatcher.parent):
                        self.assertTrue(self.dispatcher.parent.is_dir())
                    else:
                        # An installed Pi has these folders and the recovery copy
                        # keeps their owners.
                        self.assertIn(
                            words[-1],
                            (
                                str(self.config),
                                str(self.data),
                                str(self.code),
                                str(self.units / "jittersim-dashboard.service.d"),
                                "/usr/local/lib",
                                "/var/lib/jittersim-access",
                            ),
                        )
                elif line.startswith("sed "):
                    self.assertEqual(words[-2], ">")
                    expected = Path(words[-3]).read_text()
                    for index, word in enumerate(words[:-3]):
                        if word == "-e":
                            _, placeholder, value, _ = words[index + 1].split("/")
                            expected = expected.replace(placeholder, value)
                    self.assertEqual(Path(words[-1]).read_text(), expected)
                elif "check-manager" in words:
                    # The queued path checks the same source before preparation.
                    updates.compatible_manager(self.root / "staged/src/jittersim/updates.py")
                elif words[-1:] == ["bootstrap-manager"]:
                    # Both paths use atomic manager publication. The separate
                    # ownership/interruption tests exercise the real boundary.
                    self.assertEqual(self.manager.read_bytes(),
                                     (self.code / "src/jittersim/updates.py").read_bytes())
                elif re.search(r"deploy/raspberry-pi/[\w.-]+\.py", line):
                    script = Path(words[2] if words[1] == "-I" else words[1]).name
                    if script == "prepare-locked-wheels.py":
                        # This fixture represents a legacy development archive without locks.
                        # Stable preparation is exercised separately with fixed packages.
                        self.assertFalse((self.root / "staged/deploy/raspberry-pi/release-locks").exists())
                        continue
                    if script == "retire-services.py":
                        # It refuses to run while the update service runs and no
                        # unit waits to be retired. A test below keeps that true.
                        continue
                    if script == "upgrade-notice.py":
                        # It prints a notice for the person who runs the installer. An update
                        # from the dashboard has no terminal and the Traffic page says it.
                        continue
                    if script == "replace-source.py":
                        # The update replaces the same folders itself.
                        self.assertFalse((self.code / "build").exists())
                        for name in ("src", "deploy", "pyproject.toml", "README.md"):
                            self.assertTrue((self.code / name).exists())
                        continue
                    self.assertIn(words, commands)
                elif line.startswith("runuser "):
                    self.assertIn(words, commands)
                elif line.startswith("ln "):
                    self.assertEqual(words[1], "-sf")
                    self.assertEqual(os.readlink(self.command), words[2])
                elif words[1:2] == ["-m"] and words[2].startswith("jittersim.local"):
                    if words[2] == "jittersim.localca" and words[3] in ("recover-adoption", "finish-adoption", "check-health"):
                        # The root update and rollback call the standard-library
                        # adoption helpers directly. Older packages need no new CLI.
                        continue
                    # The dashboard update runs the same name and certificate
                    # steps with the helpers' own defaults.
                    self.assertIn(
                        [str(self.code / ".venv/bin/python"), "-m", words[2], words[3]],
                        [command[:4] for command in commands],
                    )
                elif words[1:3] == ["-m", "jittersim.mdnsname"]:
                    # Avahi is limited with the saved interfaces before the
                    # firewall lets mDNS in on the test Wi-Fi.
                    self.assertIn(words, commands)
                    self.assertEqual(words[3:], [
                        "configure", "--uplink", "eth7", "--test-interface", "wlan7"])
                elif Path(words[0]).name == "pip" or ("pip" in words and "install" in words):
                    offline = [command for command in commands if command[:4] ==
                               [str(self.code / ".venv/bin/python"), "-m", "pip", "install"]]
                    self.assertEqual(len(offline), 1)
                    self.assertIn("--no-index", offline[0])
                    self.assertIn("--no-deps", offline[0])
                    self.assertTrue(offline[0][-1].endswith(".whl"))
                elif "pip" in words and words[-1] == "check":
                    self.assertIn([str(self.code / ".venv/bin/python"), "-m", "pip", "check"], commands)
                elif words[:2] == ["chmod", "-R"]:
                    self.assertEqual(words[2], "go-w")
                    for entry in [self.code, *self.code.rglob("*")]:
                        self.assertFalse(entry.stat().st_mode & 0o022, entry)
                elif words[1:3] == ["-m", "venv"] or words[:2] == ["chown", "-R"]:
                    # The update installs into the existing environment as root.
                    continue
                else:
                    self.fail("the dashboard update does not do this install.sh step: " + line)
        self.assertEqual(installed_units, set(UNITS))

    def test_the_update_runs_the_installer_service_steps_in_order(self):
        self.wifi = ""
        commands = self.install()
        installer = [
            call
            for line in installer_lines()
            for call in re.findall(r"systemctl [^;|&]*[^;|&\s]", line)
            if call not in NOT_MIRRORED_SYSTEMCTL
        ]
        self.assertIn("systemctl restart " + WATCHER, installer)
        self.assertEqual(self.systemctl(self.after_package(commands)), installer)

    def test_the_installer_adds_no_package_or_retired_unit_the_update_cannot_handle(self):
        packages = [
            word
            for line in installer_lines()
            if "apt-get install -y " in line
            for word in line.split("apt-get install -y ", 1)[1].split()
        ]
        # A dashboard update cannot install OS packages. A release that needs a
        # new one tells administrators to run install.sh first and updates this list.
        self.assertEqual(
            packages,
            "dnsmasq-base iputils-ping iw wpasupplicant nftables openssl python3-venv "
            "tcpdump unbound dnsutils hostapd conntrack avahi-daemon avahi-utils python3-dbus".split(),
        )
        # Retiring a unit needs its file and enabled state back on a rollback.
        self.assertIn("\nOBSOLETE_SERVICES = ()\n", (PI / "retire-services.py").read_text())

    def fresh(self):
        """Start again on a new Pi inside one test."""
        self.addCleanup(self.temp.cleanup)
        self.setUp()

    def test_failed_preflight_or_egress_check_stops_before_anything_changes(self):
        for script in ("preflight.py", "check-egress-hook.py"):
            with self.subTest(script=script):
                self.fresh()
                self.stage()
                request_update("v1", root=self.root, code=self.code, config=self.config, run=lambda *args: None)
                commands, run = self.recorder(lambda command: command[1].endswith(script))
                with self.assertRaises(UpdateError):
                    self.apply(run, lambda: True)
                # Only the checks ran. No service was stopped or restarted.
                self.assertTrue(all(command[0] == sys.executable for command in commands))
                self.assertFalse((self.code / "src").exists())
                self.assertEqual((self.units / UNITS[0]).read_text(), "old service")
                self.assertFalse(self.dispatcher.exists())
                self.assertFalse((self.root / "backup").exists())
                self.assertFalse((self.root / "request.json").exists())
                status = update_status(root=self.root, code=self.code, config=self.config)
                self.assertEqual(status["phase"], "failed")
                self.assertIn("before it changed anything", status["message"])

    def test_a_failed_release_script_is_named_in_the_error(self):
        with self.assertRaisesRegex(UpdateError, "preflight.py"):
            run_command([sys.executable, str(self.base / "missing/preflight.py")], 30)

    def test_the_checks_use_the_staged_scripts_and_the_saved_interfaces(self):
        staged = self.root / "staged/deploy/raspberry-pi"
        self.assertEqual(
            self.install()[:2],
            [
                [sys.executable, str(staged / "preflight.py"),
                 "--uplink", "eth7", "--test-interface", "wlan7"],
                [sys.executable, str(staged / "check-egress-hook.py"), "wlan7"],
            ],
        )
        # Without runtime.env the installer's defaults apply.
        self.fresh()
        (self.config / "runtime.env").unlink()
        self.assertEqual(
            self.install()[0],
            [sys.executable, str(self.root / "staged/deploy/raspberry-pi/preflight.py"),
             "--uplink", "eth0", "--test-interface", "wlan0"],
        )

    def test_an_unsafe_interface_name_stops_the_update_before_anything_runs(self):
        (self.config / "runtime.env").write_text(
            'JITTERSIM_UPLINK=eth7\nJITTERSIM_TEST_INTERFACE="wlan7; drop"\n'
        )
        self.stage()
        request_update("v1", root=self.root, code=self.code, config=self.config, run=lambda *args: None)
        commands, run = self.recorder()
        with self.assertRaisesRegex(UpdateError, "interface"):
            self.apply(run, lambda: True)
        self.assertEqual(commands, [])
        self.assertEqual(update_status(root=self.root, code=self.code, config=self.config)["phase"], "failed")

    def test_the_firewall_is_rendered_with_the_saved_interfaces(self):
        self.install()
        rules = (self.config / "jittersim-control-firewall.nft").read_text()
        self.assertIn('iifname "wlan7" tcp dport 22', rules)
        self.assertIn('iifname "wlan7" oifname "eth7"', rules)
        self.assertNotIn("@", rules)
        mode = (self.config / "jittersim-control-firewall.nft").stat().st_mode & 0o777
        self.assertEqual(mode, 0o644)

    def test_the_dashboard_name_unit_and_dispatcher_are_installed(self):
        self.install()
        self.assertEqual(
            (self.units / NAME_UNIT).read_text(), (PI / NAME_UNIT).read_text()
        )
        self.assertEqual(
            self.dispatcher.read_text(), (PI / "jittersim-mdns-dispatcher").read_text()
        )
        self.assertEqual(self.dispatcher.stat().st_mode & 0o777, 0o755)
        # The publisher ships inside the package, so no separate script is installed.
        self.assertFalse((self.code / "deploy/raspberry-pi/publish-dashboard-name.sh").exists())

    def test_avahi_is_limited_before_the_firewall_opens_mdns_on_the_test_wifi(self):
        commands = self.install()
        words = [" ".join(command) for command in self.after_package(commands)]
        limit = next(i for i, text in enumerate(words) if "jittersim.mdnsname configure" in text)
        restart = words.index("systemctl restart avahi-daemon.service")
        firewall = words.index("systemctl restart jittersim-control-firewall.service")
        publisher = words.index("systemctl restart " + NAME_UNIT)
        self.assertLess(limit, restart)
        self.assertLess(restart, firewall)
        self.assertLess(firewall, publisher)

    def test_when_avahi_cannot_be_limited_it_is_stopped_and_the_name_service_is_skipped(self):
        commands = self.install(
            fail=lambda command: "jittersim.mdnsname" in " ".join(command))
        calls = self.systemctl(self.after_package(commands))
        # The update still completes: the dashboard stays reachable by IP address.
        self.assertEqual(update_status(root=self.root, code=self.code)["phase"], "complete")
        self.assertIn("systemctl stop avahi-daemon.service avahi-daemon.socket", calls)
        self.assertNotIn("systemctl restart avahi-daemon.service", calls)
        self.assertNotIn("systemctl restart " + NAME_UNIT, calls)
        self.assertIn("systemctl restart jittersim-control-firewall.service", calls)

    def test_a_rollback_leaves_avahi_alone(self):
        self.install()
        request_update("", rollback=True, root=self.root, code=self.code, run=lambda *a: None)
        commands, run = self.recorder()
        self.apply(run, lambda: True)
        text = [" ".join(command) for command in commands]
        self.assertFalse([item for item in text if "mdnsname" in item])
        self.assertFalse([item for item in text if "avahi" in item])

    def test_the_watcher_restarts_and_a_running_test_wifi_is_left_alone(self):
        commands = self.install()
        calls = self.systemctl(commands)
        self.assertLess(
            calls.index("systemctl restart jittersim-helper.service"),
            calls.index("systemctl restart " + WATCHER),
        )
        # The running test Wi-Fi is only enabled for boot, never started again,
        # restarted or stopped.
        for call in calls:
            if "jittersim-ap.service" in call:
                self.assertTrue(call.startswith("systemctl enable "), call)

    def test_the_test_wifi_starts_only_when_it_is_down(self):
        asked = []

        def query(command, timeout):
            asked.append(command)
            return answer

        for answer, starts in (("jittersim-ap", False), ("", True), ("--", True)):
            with self.subTest(answer=answer):
                self.fresh()
                calls = self.systemctl(self.install(query=query))
                self.assertEqual("systemctl start jittersim-ap.service" in calls, starts)
                self.assertEqual(
                    asked[-1], ["nmcli", "-g", "GENERAL.CONNECTION", "device", "show", "wlan7"]
                )

        def broken(command, timeout):
            raise UpdateError("NetworkManager does not answer")

        self.fresh()
        calls = self.systemctl(self.install(query=broken))
        # Unknown state never risks a restart of the running Wi-Fi.
        self.assertNotIn("systemctl start jittersim-ap.service", calls)
        self.assertEqual(update_status(root=self.root, code=self.code, config=self.config)["phase"], "complete")

    def test_dashboard_name_setup_stays_best_effort(self):
        commands = self.install(fail=lambda command: command[-1:] == [NAME_UNIT])
        self.assertIn("systemctl enable " + NAME_UNIT, self.systemctl(commands))
        self.assertNotIn("systemctl restart " + NAME_UNIT, self.systemctl(commands))
        self.assertEqual(update_status(root=self.root, code=self.code, config=self.config)["phase"], "complete")

    def test_failed_import_check_or_service_restart_restores_every_replaced_file(self):
        failures = (
            lambda command: command[0] == "runuser",
            lambda command: command[1:] == ["restart", "jittersim-control-firewall.service"]
            and (self.code / "src").exists(),
        )
        for number, fail in enumerate(failures):
            with self.subTest(failure=number):
                self.fresh()
                self.dispatcher.parent.mkdir()
                self.dispatcher.write_text("old dispatcher")
                self.dispatcher.chmod(0o755)
                os.symlink("/old/jittersim", self.command)
                (self.config / "jittersim-control-firewall.nft").write_text("old rules")
                self.stage()
                request_update("v1", root=self.root, code=self.code, config=self.config, run=lambda *args: None)
                commands, run = self.recorder(fail)
                with self.assertRaises(UpdateError):
                    self.apply(run, lambda: True)
                self.assertEqual(self.dispatcher.read_text(), "old dispatcher")
                self.assertEqual(self.dispatcher.stat().st_mode & 0o777, 0o755)
                self.assertEqual(os.readlink(self.command), "/old/jittersim")
                self.assertEqual(
                    (self.config / "jittersim-control-firewall.nft").read_text(), "old rules"
                )
                for unit in UNITS:
                    self.assertEqual((self.units / unit).read_text(), "old service")
                self.assertIn("RECOVERY_FORMAT = 2", self.manager.read_text())
                self.assertFalse((self.code / "src").exists())
                # The recovered installation gets the same service steps.
                calls = self.systemctl(commands)
                stops = [i for i, call in enumerate(calls) if call.startswith("systemctl stop ")]
                self.assertIn("systemctl restart " + WATCHER, calls[stops[-1] :])
                self.assertEqual(
                    update_status(root=self.root, code=self.code, config=self.config)["phase"], "rolled-back"
                )

    def test_rollback_removes_what_the_update_added(self):
        (self.units / NAME_UNIT).unlink()
        self.install()
        self.assertTrue((self.units / NAME_UNIT).exists())
        self.assertTrue(self.dispatcher.exists())
        self.assertTrue(self.command.is_symlink())
        request_update("", rollback=True, root=self.root, code=self.code, config=self.config, run=lambda *a: None)
        commands, run = self.recorder()
        self.apply(run, lambda: True)
        self.assertFalse((self.units / NAME_UNIT).exists())
        self.assertFalse(self.dispatcher.exists())
        self.assertFalse(self.command.is_symlink())
        self.assertIn("systemctl disable --now " + NAME_UNIT, self.systemctl(commands))
        self.assertNotIn("systemctl restart " + NAME_UNIT, self.systemctl(commands))
        self.assertIn("systemctl restart " + WATCHER, self.systemctl(commands))
        self.assertEqual(update_status(root=self.root, code=self.code, config=self.config)["phase"], "rolled-back")

    def test_a_recovery_copy_without_a_file_list_keeps_newer_units(self):
        # Recovery copies from before this release covered only the older units.
        self.install()
        record = json.loads((self.backup("complete.json")).read_text())
        del record["managed"]
        for name in ("format", "trees", "files"):
            record.pop(name, None)
        saved = self.backup()
        atomic_json(saved / "complete.json", record)
        (saved / "units" / NAME_UNIT).unlink()
        # A manager from before selectors published a plain recovery directory.
        (self.root / "backup-current.json").unlink()
        saved.rename(self.root / "backup")
        request_update("", rollback=True, root=self.root, code=self.code, config=self.config, run=lambda *a: None)
        self.apply(lambda *args: None, lambda: True)
        self.assertEqual((self.units / NAME_UNIT).read_text(), (PI / NAME_UNIT).read_text())
        self.assertTrue(self.dispatcher.exists())
        self.assertEqual((self.units / UNITS[0]).read_text(), "old service")

    def test_low_space_leaves_the_watcher_running(self):
        self.stage()
        request_update("v1", root=self.root, code=self.code, config=self.config, run=lambda *args: None)
        commands, run = self.recorder()
        with patch("jittersim.updates.shutil.disk_usage") as disk:
            disk.return_value.free = 0
            with self.assertRaises(UpdateError):
                self.apply(run, lambda: True)
        self.assertFalse(any(command[:1] == ["systemctl"] for command in commands))

    def rollback(self, run=None):
        request_update("", rollback=True, root=self.root, code=self.code, config=self.config,
                       run=lambda *args: None)
        self.apply(run or (lambda *args: None), lambda: True)

    def signers(self):
        path = self.config / updates.SIGNERS_NAME
        return path.read_text() if path.exists() else None

    def test_an_install_raises_the_release_floor_and_a_rollback_keeps_it(self):
        self.install()
        self.assertEqual(
            json.loads((self.root / updates.FLOOR_NAME).read_text()), {"sequence": 100}
        )
        self.rollback()
        self.assertEqual(update_status(root=self.root, code=self.code, config=self.config)[
            "installed"], "Unversioned installation")
        self.assertEqual(
            json.loads((self.root / updates.FLOOR_NAME).read_text()), {"sequence": 100}
        )
        # The release the owner rolled back from cannot come back by staging it again.
        with self.assertRaisesRegex(UpdateError, "not newer"):
            self.stage(sequence=100)
        self.stage(sequence=101, version="v2")

    def test_a_failed_install_does_not_raise_the_floor(self):
        with self.assertRaises(UpdateError):
            self.install(fail=lambda command: command[0] == "runuser")
        self.assertFalse((self.root / updates.FLOOR_NAME).exists())
        # The same release can be tried again after a fix of the Pi.
        self.assertEqual(update_status(root=self.root, code=self.code, config=self.config)[
            "phase"], "rolled-back")
        request_update("v1", root=self.root, code=self.code, config=self.config,
                       run=lambda *args: None)

    def test_a_rollback_keeps_the_trusted_keys_and_token_of_now(self):
        self.install()
        self.assertFalse((self.backup("config") / updates.SIGNERS_NAME).exists())
        # The owner replaces a stolen key and sets a token after the backup was made.
        _, replacement = make_key(self.base / "replacement", comment="replacement")
        trust_key(replacement, config=self.config, root=self.root)
        updates.untrust_key("owner@mac", config=self.config, root=self.root,
                            sudoers=self.base / "sudoers")
        token = self.config / updates.TOKEN_NAME
        token.write_text("github_pat_test_value_only\n")
        token.chmod(0o600)
        now = self.signers()
        self.rollback()
        self.assertEqual(self.signers(), now)
        self.assertNotIn("owner@mac", self.signers())
        self.assertEqual(token.read_text(), "github_pat_test_value_only\n")
        self.assertEqual(token.stat().st_mode & 0o777, 0o600)
        self.assertEqual((self.config / "control.env").read_text(), "private credentials")
        self.assertFalse((self.root / "keep").exists())

    def test_a_rollback_never_brings_back_a_key_list_from_an_older_backup(self):
        self.install()
        # A recovery copy that an older manager made holds the old list.
        (self.backup("config") / updates.SIGNERS_NAME).write_text("old list\n")
        updates.untrust_key("owner@mac", config=self.config, root=self.root,
                            sudoers=self.base / "sudoers")
        now = self.signers()
        self.rollback()
        self.assertEqual(self.signers(), now)
        (self.config / updates.SIGNERS_NAME).unlink()
        self.install_again_and_roll_back_without_a_list()

    def install_again_and_roll_back_without_a_list(self):
        backup_config = self.backup("config")
        (backup_config / updates.SIGNERS_NAME).write_text("old list\n")
        self.rollback()
        # Nothing was trusted before the rollback so nothing is trusted after it.
        self.assertIsNone(self.signers())

    def test_an_interrupted_restore_leaves_no_key_trusted_until_it_finishes(self):
        self.install()
        _, replacement = make_key(self.base / "replacement", comment="replacement")
        trust_key(replacement, config=self.config, root=self.root)
        updates.untrust_key("owner@mac", config=self.config, root=self.root,
                            sudoers=self.base / "sudoers")
        now = self.signers()
        request_update("", rollback=True, root=self.root, code=self.code, config=self.config,
                       run=lambda *args: None)
        original = updates.snapshot_tree

        def broken_data(source, destination, *args, **kwargs):
            if Path(destination) == self.data:
                raise OSError("disk error")
            return original(source, destination, *args, **kwargs)

        with patch("jittersim.updates.snapshot_tree", broken_data), \
                patch("jittersim.updates.time.sleep"), self.assertRaises(OSError):
            self.apply(lambda *args: None, lambda: True)
        # The configuration came back from the recovery copy without any key.
        self.assertIsNone(self.signers())
        self.assertTrue(json.loads((self.root / "request.json").read_text())["rollback"])
        self.apply(lambda *args: None, lambda: True)
        self.assertEqual(self.signers(), now)

    def prepare_legacy_trust_recovery(self):
        old_signers = self.signers()
        self.install()
        legacy = self.backup("config")
        (legacy / updates.SIGNERS_NAME).write_text(old_signers)
        (legacy / updates.TOKEN_NAME).write_text("toy-old-token\n")
        (legacy / updates.TOKEN_NAME).chmod(0o600)
        _, replacement = make_key(self.base / "replacement", comment="replacement")
        trust_key(replacement, config=self.config, root=self.root)
        updates.untrust_key("owner@mac", config=self.config, root=self.root,
                            sudoers=self.base / "sudoers")
        token = self.config / updates.TOKEN_NAME
        token.write_text("toy-current-token\n")
        token.chmod(0o600)
        request_update("", rollback=True, root=self.root, code=self.code,
                       config=self.config, run=lambda *args: None)
        return self.signers()

    def interrupt_trust_recovery(self, boundary):
        original_tree = updates.snapshot_tree
        original_file = updates.shutil.copy2
        failures = []

        def copytree(source, destination, *args, **kwargs):
            if boundary == "data" and Path(destination) == self.data:
                failures.append(boundary)
                raise OSError("test interruption while restoring data")
            result = original_tree(source, destination, *args, **kwargs)
            if boundary == "config" and Path(destination) == self.config:
                failures.append(boundary)
                raise OSError("test interruption after restoring configuration")
            return result

        def copy2(source, destination, *args, **kwargs):
            if boundary == "trust" and Path(source) == self.root / "keep" / updates.TOKEN_NAME:
                failures.append(boundary)
                raise OSError("test interruption while restoring current trust")
            return original_file(source, destination, *args, **kwargs)

        with patch("jittersim.updates.snapshot_tree", copytree), \
                patch("jittersim.updates.shutil.copy2", copy2), \
                self.assertRaises(OSError):
            self.apply(lambda *args: None, lambda: True)
        # Both the requested restore and its automatic recovery hit the failure.
        self.assertEqual(failures, [boundary, boundary])
        self.assertTrue(json.loads((self.root / "request.json").read_text())["rollback"])

    def check_legacy_recovery_retry(self, boundary):
        now = self.prepare_legacy_trust_recovery()
        self.interrupt_trust_recovery(boundary)
        self.assertNotIn("owner@mac", self.signers() or "")
        kept = self.root / "keep"
        self.assertEqual((kept / updates.SIGNERS_NAME).read_text(), now)
        self.assertEqual((kept / updates.TOKEN_NAME).read_text(), "toy-current-token\n")
        token = self.config / updates.TOKEN_NAME
        self.assertFalse(token.exists())
        self.apply(lambda *args: None, lambda: True)
        self.assertEqual(self.signers(), now)
        self.assertEqual(token.read_text(), "toy-current-token\n")
        self.assertEqual(token.stat().st_mode & 0o777, 0o600)
        self.assertFalse(kept.exists())

    def test_legacy_recovery_keeps_current_trust_after_data_interruption(self):
        self.check_legacy_recovery_retry("data")

    def test_legacy_recovery_keeps_current_trust_after_config_interruption(self):
        self.check_legacy_recovery_retry("config")

    def test_legacy_recovery_keeps_current_trust_after_trust_interruption(self):
        self.check_legacy_recovery_retry("trust")

    def test_interrupted_legacy_recovery_does_not_invent_missing_current_trust(self):
        self.prepare_legacy_trust_recovery()
        for name in updates.PRESERVED_CONFIG:
            (self.config / name).unlink()
        self.interrupt_trust_recovery("data")
        self.assertIsNone(self.signers())
        self.apply(lambda *args: None, lambda: True)
        self.assertIsNone(self.signers())
        self.assertFalse((self.config / updates.TOKEN_NAME).exists())

    def test_key_revoked_during_interrupted_recovery_stays_revoked(self):
        self.prepare_legacy_trust_recovery()
        self.interrupt_trust_recovery("data")
        updates.untrust_key("replacement", config=self.config, root=self.root,
                           sudoers=self.base / "sudoers")
        self.apply(lambda *args: None, lambda: True)
        self.assertEqual(updates.trusted_keys(self.config), [])
        self.assertEqual((self.config / updates.TOKEN_NAME).read_text(), "toy-current-token\n")

    def test_interrupted_trust_snapshot_does_not_keep_a_partial_token(self):
        now = self.prepare_legacy_trust_recovery()
        original = updates.shutil.copy2

        def partial_token(source, destination, *args, **kwargs):
            if Path(source) == self.config / updates.TOKEN_NAME:
                Path(destination).write_text("partial-toy-token")
                raise OSError("test interruption while preserving current settings")
            return original(source, destination, *args, **kwargs)

        with patch("jittersim.updates.shutil.copy2", partial_token), self.assertRaises(OSError):
            self.apply(lambda *args: None, lambda: True)
        self.assertEqual(self.signers(), now)
        self.assertFalse((self.root / "keep" / updates.TOKEN_NAME).exists())
        self.apply(lambda *args: None, lambda: True)
        self.assertEqual(self.signers(), now)
        self.assertEqual((self.config / updates.TOKEN_NAME).read_text(), "toy-current-token\n")

    def test_recovery_key_change_refuses_a_link_in_place_of_the_live_list(self):
        self.prepare_legacy_trust_recovery()
        self.interrupt_trust_recovery("data")
        (self.config / updates.SIGNERS_NAME).symlink_to(self.base / "missing-list")
        with self.assertRaisesRegex(UpdateError, "regular file"):
            updates.untrust_key("replacement", config=self.config, root=self.root,
                               sudoers=self.base / "sudoers")

    def test_key_added_during_interrupted_recovery_keeps_the_other_current_key(self):
        self.prepare_legacy_trust_recovery()
        self.interrupt_trust_recovery("data")
        _, extra = make_key(self.base / "extra", comment="extra")
        trust_key(extra, config=self.config, root=self.root)
        self.apply(lambda *args: None, lambda: True)
        self.assertEqual([key.label for key in updates.trusted_keys(self.config)],
                         ["replacement", "extra"])

    def test_recovery_refuses_a_link_for_the_saved_current_trust_directory(self):
        now = self.prepare_legacy_trust_recovery()
        outside = self.base / "outside"
        outside.mkdir()
        (self.root / "keep").symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(UpdateError, "saved current trust directory"):
            self.apply(lambda *args: None, lambda: True)
        self.assertEqual(self.signers(), now)
        self.assertEqual(list(outside.iterdir()), [])

    def test_recovery_refuses_a_saved_current_trust_directory_others_can_change(self):
        now = self.prepare_legacy_trust_recovery()
        keep = self.root / "keep"
        keep.mkdir()
        keep.chmod(0o777)
        with self.assertRaisesRegex(UpdateError, "saved current trust directory"):
            self.apply(lambda *args: None, lambda: True)
        self.assertEqual(self.signers(), now)

    def test_revocation_survives_interruption_before_the_kept_list_is_removed(self):
        self.prepare_legacy_trust_recovery()
        self.interrupt_trust_recovery("data")
        with patch("jittersim.updates._forget_kept", side_effect=OSError("test interruption")):
            with self.assertRaises(OSError):
                updates.untrust_key("replacement", config=self.config, root=self.root,
                                   sudoers=self.base / "sudoers")
        self.assertEqual(updates.trusted_keys(self.config), [])
        self.apply(lambda *args: None, lambda: True)
        self.assertEqual(updates.trusted_keys(self.config), [])

    def check_unsafe_saved_trust(self, name, change, message):
        self.prepare_legacy_trust_recovery()
        self.interrupt_trust_recovery("data")
        change(self.root / "keep" / name)
        with patch("jittersim.updates.snapshot_tree", wraps=updates.snapshot_tree) as copying:
            with self.assertRaisesRegex(UpdateError, message):
                self.apply(lambda *args: None, lambda: True)
        copying.assert_not_called()
        self.assertIsNone(self.signers())

    def test_recovery_refuses_a_saved_signer_list_others_can_change(self):
        self.check_unsafe_saved_trust(updates.SIGNERS_NAME,
                                     lambda path: path.chmod(0o666), "writable by others")

    def test_recovery_refuses_a_damaged_saved_signer_list(self):
        self.check_unsafe_saved_trust(updates.SIGNERS_NAME,
                                     lambda path: path.write_text("damaged list\n"), "line")

    def test_recovery_refuses_a_saved_token_others_can_read(self):
        self.check_unsafe_saved_trust(updates.TOKEN_NAME,
                                     lambda path: path.chmod(0o644), "private")

    def test_recovery_refuses_ambiguous_trust_left_by_an_older_manager(self):
        now = self.prepare_legacy_trust_recovery()
        keep = self.root / "keep"
        keep.mkdir(mode=0o700)
        for name in updates.PRESERVED_CONFIG:
            updates.shutil.copy2(self.config / name, keep / name)
            updates.shutil.copy2(self.backup("config") / name, self.config / name)
        with patch("jittersim.updates.snapshot_tree", wraps=updates.snapshot_tree) as copying:
            with self.assertRaisesRegex(UpdateError, "older manager"):
                self.apply(lambda *args: None, lambda: True)
        copying.assert_not_called()
        self.assertEqual((keep / updates.SIGNERS_NAME).read_text(), now)
        self.assertEqual((keep / updates.TOKEN_NAME).read_text(), "toy-current-token\n")

    def test_interrupted_trust_marker_publication_can_be_retried_safely(self):
        now = self.prepare_legacy_trust_recovery()
        original = updates.os.replace

        def interrupted(source, destination):
            if Path(destination).name == ".trust-format.json":
                raise OSError("test interruption while publishing recovery format")
            return original(source, destination)

        with patch("jittersim.updates.os.replace", interrupted), self.assertRaises(OSError):
            self.apply(lambda *args: None, lambda: True)
        self.assertEqual(self.signers(), now)
        self.assertEqual(list((self.root / "keep").iterdir()), [])
        self.apply(lambda *args: None, lambda: True)
        self.assertEqual(self.signers(), now)

    def test_a_key_removed_after_staging_stops_the_install_before_anything_changes(self):
        self.stage()
        request_update("v1", root=self.root, code=self.code, config=self.config,
                       run=lambda *args: None)
        _, other = make_key(self.base / "other", comment="other")
        trust_key(other, config=self.config, root=self.root)
        path = self.config / updates.SIGNERS_NAME
        path.write_text("".join(line + "\n" for line in path.read_text().splitlines()
                                if "owner@mac" not in line))
        commands, run = self.recorder()
        with self.assertRaisesRegex(UpdateError, "no longer trusted"):
            self.apply(run, lambda: True)
        self.assertEqual(commands, [])
        self.assertFalse((self.code / "src").exists())
        status = update_status(root=self.root, code=self.code, config=self.config)
        self.assertEqual(status["phase"], "failed")
        self.assertIn("before it changed anything", status["message"])

    def test_an_older_release_staged_before_a_newer_install_is_not_installed(self):
        self.stage(sequence=100)
        request_update("v1", root=self.root, code=self.code, config=self.config,
                       run=lambda *args: None)
        (self.code / ".jittersim-release.json").write_text(
            json.dumps({"version": "newer", "sequence": 200})
        )
        commands, run = self.recorder()
        with self.assertRaisesRegex(UpdateError, "not newer"):
            self.apply(run, lambda: True)
        self.assertEqual(commands, [])
    def test_health_trusts_a_dashboard_certificate_issued_by_the_local_authority(self):
        from jittersim import localca
        from jittersim.api.server import ControlServer
        from jittersim.config.storage import ConfigRepository
        from tests.unit.test_api import FakeHelper

        if shutil.which("openssl") is None:
            self.skipTest("OpenSSL is required for the HTTPS health check")
        localca.prepare(self.config, host_name="pi.local", neutral_name="netlab.local",
                        ethernet_ip="192.168.1.20")
        (self.config / "control.env").write_text("JITTERSIM_ADMIN_TOKEN=release-health-test\n")
        with ConfigRepository(self.base / "health.db") as repository:
            with ControlServer(
                ("127.0.0.1", 0), repository, admin_token="release-health-test",
                helper_client=FakeHelper(),
                tls_certfile=self.config / "dashboard.crt",
                tls_keyfile=self.config / "dashboard.key",
            ) as server:
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                try:
                    with patch("jittersim.updates.socket.gethostname", return_value="pi"):
                        self.assertTrue(healthy(
                            config=self.config, port=server.server_port, run=lambda *args: None))
                finally:
                    server.shutdown()
                    thread.join(timeout=5)

    def test_health_uses_the_installer_certificate_name_and_checks_every_service(self):
        from jittersim.api.server import ControlServer
        from jittersim.config.storage import ConfigRepository
        from tests.unit.test_api import FakeHelper, create_test_certificate

        files = create_test_certificate(self.config, hostname="pi.local")
        if files is None:
            self.skipTest("OpenSSL is required for the HTTPS health check")
        (self.config / "control.env").write_text("JITTERSIM_ADMIN_TOKEN=release-health-test\n")
        with ConfigRepository(self.base / "health.db") as repository:
            with ControlServer(
                ("127.0.0.1", 0),
                repository,
                admin_token="release-health-test",
                helper_client=FakeHelper(),
                tls_certfile=files[0],
                tls_keyfile=files[1],
            ) as server:
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                try:
                    with patch("jittersim.updates.socket.gethostname", return_value="pi"):
                        self.assertTrue(
                            healthy(
                                config=self.config, port=server.server_port, run=lambda *args: None
                            )
                        )

                        def failed_proxy(command, timeout):
                            if command[-1] == "jittersim-browser-proxy.service":
                                raise UpdateError("proxy unavailable")

                        self.assertFalse(
                            healthy(config=self.config, port=server.server_port, run=failed_proxy)
                        )
                    with patch("jittersim.updates.socket.gethostname", return_value="different"):
                        self.assertFalse(
                            healthy(
                                config=self.config, port=server.server_port, run=lambda *args: None
                            )
                        )
                finally:
                    server.shutdown()
                    thread.join(timeout=5)
