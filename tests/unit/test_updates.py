"""Real archive and filesystem checks for the release recovery boundary."""

import hashlib
import io
import json
import tarfile
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from jittersim.updates import (
    UNITS,
    UpdateError,
    apply_update,
    atomic_json,
    healthy,
    request_update,
    stage_release,
    update_status,
)


class UpdateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.root = self.base / "updates"
        self.manager = self.base / "manager.py"
        self.manager.write_text("old recovery manager")
        self.code, self.config, self.data, self.units = [
            self.base / item for item in ("code", "config", "data", "units")
        ]
        for directory in (self.code, self.config, self.data, self.units):
            directory.mkdir()
        (self.code / "old-file").write_text("previous installation")
        (self.data / "jittersim.db").write_text("original settings")
        (self.config / "control.env").write_text("private credentials")
        (self.config / "control.env").chmod(0o600)
        for unit in UNITS:
            (self.units / unit).write_text("old service")

    def tearDown(self):
        self.temp.cleanup()

    def archive(self, extra=None):
        path = self.base / "release.tar.gz"
        files = {
            "pyproject.toml": "[project]\nname='jittersim'",
            "README.md": "Release",
            "src/jittersim/api/server.py": "new code",
            "src/jittersim/updates.py": "new recovery manager",
        }
        files.update({"deploy/raspberry-pi/" + unit: "new service" for unit in UNITS})
        with tarfile.open(path, "w:gz") as archive:
            for name, content in files.items():
                raw = content.encode()
                info = tarfile.TarInfo(name)
                info.size = len(raw)
                archive.addfile(info, io.BytesIO(raw))
            if extra:
                archive.addfile(extra)
        return path, hashlib.sha256(path.read_bytes()).hexdigest()

    def stage(self):
        path, digest = self.archive()
        stage_release(path, digest, "v1", root=self.root)

    def apply(self, run, health):
        return apply_update(
            root=self.root,
            code=self.code,
            config=self.config,
            data=self.data,
            units=self.units,
            manager=self.manager,
            run=run,
            health=health,
        )

    def test_rejects_wrong_checksum_traversal_and_links_before_extraction(self):
        path, _ = self.archive()
        with self.assertRaises(UpdateError):
            stage_release(path, "0" * 64, "v1", root=self.root)
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
                stage_release(path, digest, "v1", root=self.root)
        self.assertFalse((self.base / "escaped").exists())
        self.assertFalse((self.root / "staged").exists())

    def test_request_names_only_an_approved_release_and_cannot_queue_twice(self):
        self.stage()
        with self.assertRaises(UpdateError):
            request_update("../../bad", root=self.root, code=self.code, run=lambda *args: None)
        request_update("v1", root=self.root, code=self.code, run=lambda *args: None)
        with self.assertRaises(UpdateError):
            request_update("v1", root=self.root, code=self.code, run=lambda *args: None)

    def test_failed_install_restores_code_database_services_and_permissions(self):
        self.stage()
        request_update("v1", root=self.root, code=self.code, run=lambda *args: None)

        def fail_package(command, timeout):
            if "pip" in command:
                (self.data / "jittersim.db").write_text("partially migrated")
                raise UpdateError("package install failed")

        with self.assertRaises(UpdateError):
            self.apply(fail_package, lambda: True)
        self.assertEqual((self.data / "jittersim.db").read_text(), "original settings")
        self.assertEqual((self.code / "old-file").read_text(), "previous installation")
        self.assertFalse((self.code / "src").exists())
        self.assertEqual((self.units / UNITS[0]).read_text(), "old service")
        self.assertEqual((self.config / "control.env").stat().st_mode & 0o777, 0o600)
        self.assertEqual(update_status(root=self.root, code=self.code)["phase"], "rolled-back")

    def test_failure_before_backup_does_not_restore_an_older_backup(self):
        self.stage()
        backup = self.root / "backup"
        backup.mkdir()
        atomic_json(backup / "complete.json", {"created_at": 1})
        request_update("v1", root=self.root, code=self.code, run=lambda *args: None)
        with patch("jittersim.updates.shutil.disk_usage") as disk:
            disk.return_value.free = 0
            with self.assertRaises(UpdateError):
                self.apply(lambda *args: None, lambda: True)
        self.assertEqual((self.code / "old-file").read_text(), "previous installation")

    def test_successful_install_can_be_rolled_back(self):
        self.stage()
        request_update("v1", root=self.root, code=self.code, run=lambda *args: None)
        self.apply(lambda *args: None, lambda: True)
        self.assertEqual(update_status(root=self.root, code=self.code)["installed"], "v1")
        self.assertEqual(self.manager.read_text(), "new recovery manager")
        request_update("", rollback=True, root=self.root, code=self.code, run=lambda *args: None)
        self.apply(lambda *args: None, lambda: True)
        self.assertFalse((self.code / "src").exists())
        self.assertEqual(self.manager.read_text(), "old recovery manager")
        self.assertEqual(update_status(root=self.root, code=self.code)["phase"], "rolled-back")

    def test_failed_health_check_restores_the_previous_installation(self):
        self.stage()
        request_update("v1", root=self.root, code=self.code, run=lambda *args: None)
        with patch("jittersim.updates.time.sleep"):
            with self.assertRaisesRegex(UpdateError, "health check"):
                self.apply(lambda *args: None, lambda: not (self.code / "src").exists())
        self.assertEqual((self.data / "jittersim.db").read_text(), "original settings")
        self.assertEqual(update_status(root=self.root, code=self.code)["phase"], "rolled-back")

    def test_interrupted_installation_recovers_before_trying_another_install(self):
        self.stage()
        request_update("v1", root=self.root, code=self.code, run=lambda *args: None)

        def interrupted(command, timeout):
            if "pip" in command:
                raise KeyboardInterrupt()

        # A process interruption leaves recovery intent for the next invocation.
        with self.assertRaises(KeyboardInterrupt):
            self.apply(interrupted, lambda: True)
        self.apply(lambda *args: None, lambda: True)
        self.assertFalse((self.code / "src").exists())
        self.assertEqual(update_status(root=self.root, code=self.code)["phase"], "rolled-back")

    def test_failed_recovery_keeps_the_request_for_another_attempt(self):
        self.stage()
        request_update("v1", root=self.root, code=self.code, run=lambda *args: None)
        self.apply(lambda *args: None, lambda: True)
        request_update("", rollback=True, root=self.root, code=self.code, run=lambda *args: None)
        with patch("jittersim.updates.time.sleep"), self.assertRaises(UpdateError):
            self.apply(lambda *args: None, lambda: False)
        self.assertEqual(update_status(root=self.root, code=self.code)["phase"], "failed")
        self.assertTrue(json.loads((self.root / "request.json").read_text())["rollback"])
        self.apply(lambda *args: None, lambda: True)
        self.assertFalse((self.root / "request.json").exists())

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
