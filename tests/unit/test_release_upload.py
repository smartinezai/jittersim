"""The dashboard's HTTPS release upload: the way to deploy when SSH to the Pi is blocked.

A real TLS dashboard runs in a thread. Its helper stand-in calls the real stage_upload so the
signature, the release order and the archive checks are the root update manager's own.
"""

import http.client
import json
import shutil
import socket
import ssl
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from jittersim import release_uploads, updates
from jittersim.api import server as api
from jittersim.config.storage import ConfigRepository
from jittersim.networking.helper import HelperClientError
from tests.release_support import have_ssh_keygen, make_key, signed_release

TOKEN = "upload-admin-token"
PATH = "/api/updates/upload"
HEADER = "X-Jittersim-Archive-Bytes"


class StagingHelper:
    """Does what the root helper does for the dashboard, in this test's own folders."""

    def __init__(self, owner):
        self.owner = owner
        self.calls = 0

    def ping(self):
        return {"ok": True}

    def stage_uploaded_release(self, *, upload_id=None):
        self.calls += 1
        owner = self.owner
        try:
            record = updates.stage_upload(
                upload_id, spool=owner.spool, lock=owner.upload_lock,
                root=owner.root, code=owner.code, config=owner.config)
        except updates.UpdateError as error:
            raise HelperClientError(str(error), reason="release-refused") from error
        return {"ok": True, "staged_version": record["version"], "available": record["version"],
                "sequence": record["sequence"]}


@unittest.skipUnless(have_ssh_keygen() and shutil.which("openssl"), "ssh-keygen and openssl")
class ReleaseUploadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory(prefix="jittersim-upload-")
        base = Path(cls.directory.name)
        cls.certificate, cls.key = base / "dashboard.crt", base / "dashboard.key"
        subprocess.run(
            ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-sha256", "-nodes", "-days", "1",
             "-keyout", str(cls.key), "-out", str(cls.certificate), "-subj", "/CN=127.0.0.1",
             "-addext", "subjectAltName=IP:127.0.0.1"],
            check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        cls.private, cls.public = make_key(base / "keys")
        cls.other_private, _ = make_key(base / "keys", name="stranger", comment="stranger")

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        base = Path(self.temp.name).resolve()
        self.root, self.code, self.config = base / "updates", base / "code", base / "config"
        self.spool = base / "data" / "incoming-release"
        self.upload_lock = base / "release-upload.lock"
        release_uploads.prepare_lock(self.upload_lock)
        for folder in (self.code, self.config):
            folder.mkdir()
        updates.trust_key(self.public, config=self.config, root=self.root)
        self.helper = StagingHelper(self)
        ready = threading.Event()

        def serve():
            # SQLite objects stay in the thread that made them.
            self.repository = ConfigRepository(":memory:")
            self.server = api.ControlServer(
                ("127.0.0.1", 0), self.repository, admin_token=TOKEN,
                tls_certfile=self.certificate, tls_keyfile=self.key)
            self.server.release_spool = self.spool
            self.server.release_upload_lock = self.upload_lock
            self.server.helper_client = self.helper
            ready.set()
            self.server.serve_forever()
            self.server.server_close()
            self.repository.close()

        self.thread = threading.Thread(target=serve, daemon=True)
        self.thread.start()
        self.assertTrue(ready.wait(10))
        self.context = ssl.create_default_context(cafile=str(self.certificate))
        self.builds = base / "builds"
        self.builds.mkdir()

    def tearDown(self):
        self.server.shutdown()
        self.thread.join(timeout=5)
        self.temp.cleanup()

    # -- helpers -----------------------------------------------------------------------------

    def release(self, *, sequence=200, version="v2", private=None, name="release.tar.gz"):
        archive = signed_release(
            self.builds, private or self.private, name=name, sequence=sequence, version=version)
        return archive.read_bytes(), Path(str(archive) + ".sig").read_bytes()

    def connection(self):
        return http.client.HTTPSConnection(
            "127.0.0.1", self.server.server_address[1], context=self.context, timeout=15)

    def upload(self, archive, signature, *, token=TOKEN, headers=None):
        connection = self.connection()
        values = {"Content-Type": "application/octet-stream", HEADER: str(len(archive)),
                  "Content-Length": str(len(archive) + len(signature))}
        if token is not None:
            values["Authorization"] = "Bearer " + token
        values.update(headers or {})
        values = {name: value for name, value in values.items() if value is not None}
        try:
            connection.putrequest("POST", PATH, skip_accept_encoding=True)
            for name, value in values.items():
                connection.putheader(name, value)
            connection.endheaders()
            try:
                connection.send(archive + signature)
            except (BrokenPipeError, ConnectionResetError):
                # A header rejection can close the body stream. Check its reply.
                pass
            response = connection.getresponse()
            return response.status, json.loads(response.read() or b"{}")
        finally:
            connection.close()

    def assert_nothing_left(self):
        self.assertEqual(sorted(path.name for path in self.spool.glob("*")), [])
        incoming = self.root / "incoming"
        self.assertEqual(sorted(path.name for path in incoming.glob("*")) if incoming.exists()
                         else [], [".copy.lock"] if incoming.exists() else [])

    # -- who may upload ------------------------------------------------------------------------

    def test_the_upload_needs_the_admin_token(self):
        archive, signature = self.release()
        for token in (None, "wrong-token"):
            status, body = self.upload(archive, signature, token=token)
            self.assertEqual(status, 401)
        self.assertEqual(self.helper.calls, 0)
        self.assertFalse((self.root / "staged").exists())

    def test_a_device_on_the_test_wifi_must_be_allowed_first(self):
        archive, signature = self.release()
        with patch.object(self.server, "dashboard_access", return_value={
                "ok": True, "network": "test", "allowed": False, "can_register": True,
                "mac_address": None}):
            status, body = self.upload(archive, signature)
        self.assertEqual(status, 403)
        self.assertTrue(body["registration_required"])
        self.assertEqual(self.helper.calls, 0)
        self.assertFalse(self.spool.exists() and any(self.spool.iterdir()))

    def test_an_allowed_device_on_the_test_wifi_can_upload(self):
        archive, signature = self.release()
        with patch.object(self.server, "dashboard_access", return_value={
                "ok": True, "network": "test", "allowed": True, "can_register": False,
                "mac_address": "aa:bb:cc:dd:ee:ff"}):
            status, body = self.upload(archive, signature)
        self.assertEqual(status, 200)
        self.assertEqual(body["available"], "v2")

    def test_without_a_helper_the_dashboard_says_so(self):
        self.server.helper_client = None
        archive, signature = self.release()
        with patch.object(self.server, "dashboard_access", return_value={
                "ok": True, "network": "management", "allowed": True, "can_register": False,
                "mac_address": None}):
            status, body = self.upload(archive, signature)
        self.assertEqual(status, 503)

    # -- a good release ------------------------------------------------------------------------

    def test_a_signed_newer_release_is_staged_and_the_folders_are_empty_again(self):
        archive, signature = self.release(sequence=300, version="abc1234")
        status, body = self.upload(archive, signature)
        self.assertEqual(status, 200, body)
        self.assertTrue(body["uploaded"])
        self.assertEqual(body["staged_version"], "abc1234")
        self.assertEqual(body["sequence"], 300)
        self.assertNotIn(TOKEN, json.dumps(body))
        record = json.loads((self.root / "staged" / ".jittersim-release.json").read_text())
        self.assertEqual(record["version"], "abc1234")
        self.assert_nothing_left()
        self.assertEqual(oct((self.root / "incoming").stat().st_mode & 0o777), "0o700")

    def test_a_second_upload_cannot_replace_inputs_while_root_is_copying(self):
        first_archive, first_signature = self.release(sequence=300, version="first")
        second_archive, second_signature = self.release(sequence=301, version="second",
                                                       name="second.tar.gz")
        entered, release = threading.Event(), threading.Event()
        original = updates.copy_bounded
        answers = {}

        def held_copy(source, destination, *args):
            if args[-1] == "uploaded release archive" and not entered.is_set():
                entered.set()
                self.assertTrue(release.wait(10))
            return original(source, destination, *args)

        def first():
            answers["first"] = self.upload(first_archive, first_signature)

        with patch("jittersim.release_uploads.copy_bounded", held_copy):
            worker = threading.Thread(target=first)
            worker.start()
            try:
                self.assertTrue(entered.wait(3))
                started = time.monotonic()
                status, _ = self.upload(second_archive, second_signature)
                self.assertEqual(status, 409)
                self.assertLess(time.monotonic() - started, 2)
            finally:
                release.set()
                worker.join(5)
        self.assertFalse(worker.is_alive())
        self.assertEqual(answers["first"][0], 200, answers)
        self.assertEqual(answers["first"][1]["staged_version"], "first")
        self.assertEqual(self.helper.calls, 1)
        self.assert_nothing_left()

    def test_uncertain_helper_answer_keeps_inputs_until_root_finishes(self):
        archive, signature = self.release(sequence=300, version="first")
        entered, release = threading.Event(), threading.Event()
        original_copy = updates.copy_bounded
        original_stage = self.helper.stage_uploaded_release
        answers = {}

        def held_copy(source, destination, *args):
            if args[-1] == "uploaded release archive":
                entered.set()
                self.assertTrue(release.wait(10))
            return original_copy(source, destination, *args)

        def background(*args, **kwargs):
            try:
                answers["result"] = original_stage(*args, **kwargs)
            except Exception as error:
                answers["error"] = type(error).__name__

        worker = None

        def uncertain(*args, **kwargs):
            nonlocal worker
            worker = threading.Thread(target=background, args=args, kwargs=kwargs)
            worker.start()
            self.assertTrue(entered.wait(3))
            raise HelperClientError("test lost the helper answer")

        with patch("jittersim.release_uploads.copy_bounded", held_copy), \
                patch.object(self.helper, "stage_uploaded_release", uncertain):
            try:
                status, _ = self.upload(archive, signature)
                self.assertEqual(status, 503)
                self.assertTrue(list(self.spool.rglob(updates.UPLOAD_ARCHIVE)))
            finally:
                release.set()
                if worker:
                    worker.join(5)
        self.assertNotIn("error", answers)
        self.assertEqual(answers["result"]["staged_version"], "first")
        self.assert_nothing_left()

    # -- refused releases ----------------------------------------------------------------------

    def test_a_release_signed_by_a_stranger_is_refused(self):
        archive, signature = self.release(private=self.other_private)
        status, body = self.upload(archive, signature)
        self.assertEqual(status, 422)
        self.assertIn("signature is not valid", body["error"])
        self.assertFalse((self.root / "staged").exists())
        self.assert_nothing_left()

    def test_a_tampered_archive_is_refused(self):
        archive, signature = self.release()
        tampered = archive[:-1] + bytes([archive[-1] ^ 1])
        status, body = self.upload(tampered, signature)
        self.assertEqual(status, 422)
        self.assertFalse((self.root / "staged").exists())
        self.assert_nothing_left()

    def test_an_unsigned_upload_with_a_junk_signature_is_refused(self):
        archive, _ = self.release()
        status, body = self.upload(archive, b"not a signature")
        self.assertEqual(status, 422)
        self.assertFalse((self.root / "staged").exists())

    def test_an_older_release_is_refused(self):
        updates.atomic_json(self.root / updates.FLOOR_NAME, {"sequence": 500})
        archive, signature = self.release(sequence=400)
        status, body = self.upload(archive, signature)
        self.assertEqual(status, 422)
        self.assertIn("not newer", body["error"])
        self.assertFalse((self.root / "staged").exists())
        self.assert_nothing_left()

    # -- limits --------------------------------------------------------------------------------

    def raw(self, request_lines, body=b"", wait=True):
        raw = socket.create_connection(self.server.server_address, timeout=10)
        client = self.context.wrap_socket(raw, server_hostname="127.0.0.1")
        client.sendall(("\r\n".join(request_lines) + "\r\n\r\n").encode() + body)
        data = b""
        if wait:
            try:
                while True:
                    part = client.recv(4096)
                    if not part:
                        break
                    data += part
            except (ssl.SSLError, OSError):
                pass
        client.close()
        return data

    def head(self, *extra):
        return ["POST " + PATH + " HTTP/1.1", "Host: 127.0.0.1", "Connection: close",
                "Authorization: Bearer " + TOKEN, *extra]

    def test_content_length_is_required(self):
        data = self.raw(self.head("Transfer-Encoding: chunked", HEADER + ": 5"),
                        b"5\r\nhello\r\n0\r\n\r\n")
        self.assertIn(b" 411 ", data.split(b"\r\n")[0])
        data = self.raw(self.head(HEADER + ": 5"))
        self.assertIn(b" 411 ", data.split(b"\r\n")[0])
        self.assertEqual(self.helper.calls, 0)

    def test_the_archive_size_header_is_required_and_checked(self):
        for extra in ([], [HEADER + ": abc"], [HEADER + ": 0"], [HEADER + ": 100"],
                      [HEADER + ": -5"]):
            data = self.raw(self.head("Content-Length: 100", *extra), b"x" * 100)
            self.assertIn(b" 400 ", data.split(b"\r\n")[0], extra)
        self.assertEqual(self.helper.calls, 0)

    def test_an_archive_over_the_limit_is_refused_before_it_is_read(self):
        size = updates.MAX_ARCHIVE + 1
        data = self.raw(self.head(f"Content-Length: {size + 100}", f"{HEADER}: {size}"))
        self.assertIn(b" 413 ", data.split(b"\r\n")[0])
        self.assertIn(b"25 MiB", data)
        self.assertFalse(self.spool.exists() and any(self.spool.iterdir()))

    def test_a_signature_over_the_limit_is_refused_before_it_is_read(self):
        size = updates.MAX_SIGNATURE + 1
        data = self.raw(self.head(f"Content-Length: {100 + size}", f"{HEADER}: 100"))
        self.assertIn(b" 413 ", data.split(b"\r\n")[0])
        self.assertIn(b"16 KiB", data)

    def test_an_upload_that_stops_early_stages_nothing_and_the_dashboard_goes_on(self):
        archive, signature = self.release()
        self.raw(self.head(f"Content-Length: {len(archive) + len(signature)}",
                           f"{HEADER}: {len(archive)}"), archive[:1000], wait=False)
        # The dashboard notices the closed connection, drops the partial file and goes on.
        for _ in range(100):
            if not (self.spool.exists() and any(self.spool.iterdir())):
                break
            time.sleep(0.05)
        self.assertEqual(self.helper.calls, 0)
        self.assert_nothing_left()
        status, _ = self.upload(*self.release(sequence=210, version="v3"))
        self.assertEqual(status, 200)

    def test_a_slow_upload_is_stopped_at_the_deadline(self):
        self.server.release_upload_seconds = 1.0
        archive, signature = self.release()
        raw = socket.create_connection(self.server.server_address, timeout=10)
        client = self.context.wrap_socket(raw, server_hostname="127.0.0.1")
        lines = self.head(f"Content-Length: {len(archive) + len(signature)}",
                          f"{HEADER}: {len(archive)}")
        client.sendall(("\r\n".join(lines) + "\r\n\r\n").encode() + archive[:100])
        started = time.monotonic()
        data = b""
        try:
            while True:
                part = client.recv(4096)
                if not part:
                    break
                data += part
        except (ssl.SSLError, OSError):
            pass
        client.close()
        self.assertLess(time.monotonic() - started, 8)
        self.assertIn(b" 408 ", data.split(b"\r\n")[0])
        self.assertEqual(self.helper.calls, 0)
        self.assert_nothing_left()
        # The dashboard answers the next request.
        status, _ = self.upload(*self.release(sequence=220, version="v4"))
        self.assertEqual(status, 200)


@unittest.skipUnless(have_ssh_keygen(), "ssh-keygen signs the releases")
class StageUploadTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        base = Path(self.temp.name).resolve()
        self.root, self.code, self.config, self.spool = (
            base / "updates", base / "code", base / "config", base / "spool")
        self.upload_lock = base / "release-upload.lock"
        release_uploads.prepare_lock(self.upload_lock)
        for folder in (self.code, self.config, self.spool):
            folder.mkdir()
        self.private, self.public = make_key(base / "keys")
        updates.trust_key(self.public, config=self.config, root=self.root)
        self.archive = signed_release(base, self.private, sequence=50, version="v5")

    def tearDown(self):
        self.temp.cleanup()

    def put(self):
        shutil.copy(self.archive, self.spool / updates.UPLOAD_ARCHIVE)
        shutil.copy(str(self.archive) + ".sig", self.spool / updates.UPLOAD_SIGNATURE)

    def stage(self):
        return updates.stage_upload(
            spool=self.spool, lock=self.upload_lock, root=self.root, code=self.code, config=self.config)

    def test_a_good_upload_is_staged_and_the_spool_is_emptied(self):
        self.put()
        record = self.stage()
        self.assertEqual(record["version"], "v5")
        self.assertEqual(list(self.spool.iterdir()), [])
        self.assertEqual([path.name for path in (self.root / "incoming").iterdir()], [".copy.lock"])

    def test_a_link_in_the_spool_is_never_followed(self):
        secret = self.spool.parent / "secret"
        secret.write_text("private")
        (self.spool / updates.UPLOAD_ARCHIVE).symlink_to(secret)
        shutil.copy(str(self.archive) + ".sig", self.spool / updates.UPLOAD_SIGNATURE)
        with self.assertRaises(updates.UpdateError):
            self.stage()
        self.assertEqual(secret.read_text(), "private")
        self.assertFalse((self.root / "staged").exists())

    def test_a_missing_upload_is_a_plain_error(self):
        with self.assertRaises(updates.UpdateError):
            self.stage()

    def test_a_spool_that_is_a_link_is_refused(self):
        target = self.spool.parent / "elsewhere"
        target.mkdir()
        link = self.spool.parent / "link"
        link.symlink_to(target)
        with self.assertRaises(updates.UpdateError):
            updates.stage_upload(spool=link, lock=self.upload_lock,
                                 root=self.root, code=self.code, config=self.config)

    def test_an_oversized_file_in_the_spool_is_refused(self):
        self.put()
        with (self.spool / updates.UPLOAD_SIGNATURE).open("ab") as handle:
            handle.write(b"x" * (updates.MAX_SIGNATURE + 1))
        with self.assertRaises(updates.UpdateError):
            self.stage()
        self.assertEqual(list(self.spool.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
