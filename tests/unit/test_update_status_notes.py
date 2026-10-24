"""update_status reports the signed release notes of an installable staged release."""

import os

import pytest

from jittersim import updates
from tests.release_support import have_ssh_keygen, make_key, signed_release

pytestmark = pytest.mark.skipif(not have_ssh_keygen(), reason="ssh-keygen signs the releases")
NOTES = "deploy/RELEASE-NOTES.md"


@pytest.fixture
def pi(tmp_path):
    root, code, config = tmp_path / "updates", tmp_path / "code", tmp_path / "config"
    for folder in (code, config):
        folder.mkdir()
    private, public = make_key(tmp_path / "keys")
    updates.trust_key(public, config=config, root=root)
    return tmp_path, private, root, code, config


def put_staged(pi, notes=None, **options):
    base, private, root, code, config = pi
    replace = {NOTES: notes} if notes is not None else {}
    archive = signed_release(base, private, replace=replace, **options)
    updates.stage_release(archive, root=root, code=code, config=config)


def stage(pi, notes=None, **options):
    _, _, root, code, config = pi
    put_staged(pi, notes, **options)
    return updates.update_status(root=root, code=code, config=config, retry_probe=False)


def test_notes_of_a_signed_staged_release_are_reported(pi):
    status = stage(pi, "## v0.3.0 - 2026-10-09\n\n* Faster checks\n", version="0.3.0")
    assert status["installable"] is True
    assert status["notes"] == "## v0.3.0 - 2026-10-09\n\n* Faster checks"


def test_no_notes_and_no_staged_release_report_none(pi, tmp_path):
    assert stage(pi, version="0.3.0")["notes"] is None
    _, _, _, code, config = pi
    empty = updates.update_status(root=tmp_path / "other", code=code, config=config, retry_probe=False)
    assert empty["notes"] is None


def test_notes_are_cut_at_the_limit(pi):
    notes = stage(pi, "## v0.3.0\n" + "x" * 40000, version="0.3.0")["notes"]
    assert notes.startswith("## v0.3.0") and len(notes.encode()) <= updates.MAX_NOTES


def test_notes_of_a_release_that_cannot_be_installed_are_withheld(pi):
    base, _, root, code, config = pi
    stage(pi, "## v0.3.0\n", version="0.3.0")
    updates.untrust_key("owner@mac", config=config, root=root, sudoers=base / "no-sudoers")
    status = updates.update_status(root=root, code=code, config=config, retry_probe=False)
    assert status["notes"] is None


def test_a_link_in_place_of_the_notes_is_not_followed(pi, tmp_path):
    _, _, root, _, _ = pi
    stage(pi, "## v0.3.0\n", version="0.3.0")
    secret = tmp_path / "secret"
    secret.write_text("private")
    path = root / "staged" / NOTES
    path.unlink()
    os.symlink(secret, path)
    assert updates.staged_notes(root) is None


def test_a_pipe_in_place_of_the_notes_is_not_waited_for(pi):
    # A plain open() of a pipe with no writer blocks the helper for ever.
    _, _, root, _, _ = pi
    stage(pi, "## v0.3.0\n", version="0.3.0")
    path = root / "staged" / NOTES
    path.unlink()
    os.mkfifo(path)
    assert updates.staged_notes(root) is None


def test_the_largest_possible_notes_still_fit_in_one_helper_message(pi):
    # Every byte of a control character becomes a six character JSON escape, so 16 KB of notes
    # can take about 96 KB of the helper's 256 KB answer limit.
    import json

    from jittersim.networking.helper import MAX_MESSAGE_BYTES

    status = stage(pi, "\x01" * 40000, version="0.3.0")
    assert len(status["notes"]) == updates.MAX_NOTES
    encoded = (json.dumps({"ok": True, **status}, sort_keys=True) + "\n").encode("utf-8")
    assert len(encoded) < MAX_MESSAGE_BYTES // 2


def test_a_character_cut_by_the_limit_is_dropped_not_shown_as_a_mark(pi):
    # 16383 plain bytes then a three byte character: the limit lands inside the character.
    status = stage(pi, "a" * (updates.MAX_NOTES - 1) + "€" + "tail", version="0.3.0")
    assert status["notes"] == "a" * (updates.MAX_NOTES - 1)


@pytest.mark.parametrize("replaced", ["before the notes are read", "after the notes are read"])
def test_notes_of_another_release_staged_during_the_read_are_withheld(pi, monkeypatch, replaced):
    # A check can stage release B while a status request still judges release A.
    base, private, root, code, config = pi
    stage(pi, "## A\n", version="0.3.0")
    real = updates.staged_notes

    def restage_around(path):
        if replaced.startswith("before"):
            put_staged(pi, "## B\n", version="0.4.0")
        notes = real(path)
        if replaced.startswith("after"):
            put_staged(pi, "## B\n", version="0.4.0")
        return notes

    monkeypatch.setattr(updates, "staged_notes", restage_around)
    status = updates.update_status(root=root, code=code, config=config, retry_probe=False)
    assert status["available"] == "0.3.0"
    assert status["notes"] is None
    # Asked again, the status describes release B and carries its own notes.
    monkeypatch.setattr(updates, "staged_notes", real)
    again = updates.update_status(root=root, code=code, config=config, retry_probe=False)
    assert again["available"] == "0.4.0" and again["notes"] == "## B"


def test_release_metadata_reports_only_recorded_identity_and_keeps_raw_version(pi):
    _, _, root, code, config = pi
    status = stage(pi, version="0.2.0.dev+03fe31d")
    record = updates.read_json(root / "staged" / ".jittersim-release.json")
    expected = {key: record.get(key) for key in ("commit", "signer", "signer_label")}
    assert status["available_metadata"] == expected
    assert status["available"] == "0.2.0.dev+03fe31d"
    assert status["installed_metadata"] is None
    updates.atomic_json(code / ".jittersim-release.json", {
        "version": "0.2.0.dev+03fe31d", "commit": "b" * 40, "sequence": 1,
        "sha256": "c" * 64, "do_not_expose": "private fixture value",
    })
    status = updates.update_status(root=root, code=code, config=config, retry_probe=False)
    assert status["installed_metadata"] == {
        "commit": "b" * 40, "signer": None, "signer_label": None,
    }
    assert status["installed"] == "0.2.0.dev+03fe31d"
    assert status["installable"] is True
    assert updates.read_json(root / "staged" / ".jittersim-release.json") == record


def test_release_metadata_does_not_fabricate_legacy_fields(pi):
    _, _, root, code, config = pi
    updates.atomic_json(code / ".jittersim-release.json", {"version": "old-label"})
    status = updates.update_status(root=root, code=code, config=config, retry_probe=False)
    assert status["installed_metadata"] == dict.fromkeys(("commit", "signer", "signer_label"))
    assert status["available_metadata"] is None


def test_metadata_survives_helper_transport_and_stays_private(pi, monkeypatch):
    import json
    from email.message import Message
    from unittest.mock import patch

    from jittersim.api.server import ControlRequestHandler, ControlServer
    from jittersim.config.storage import ConfigRepository
    from jittersim.networking import helper
    from tests.unit.test_api import FakeHelper

    status = stage(pi, version="0.2.0")
    monkeypatch.setattr(helper, "update_status", lambda: status)
    transported = helper.HelperService._handle_payload(None, {"action": "update-status"})
    assert transported == {"ok": True, **status}
    repository = ConfigRepository(":memory:")
    server = ControlServer(("127.0.0.1", 0), repository, admin_token="fixture-admin")
    fake = FakeHelper()
    fake.release_status = lambda: transported
    server.helper_client = fake

    def get(path, authorization=None):
        handler = object.__new__(ControlRequestHandler)
        handler.server, handler.path = server, path
        handler.client_address = ("127.0.0.1", 45000)
        handler.headers = Message()
        if authorization:
            handler.headers["Authorization"] = authorization
        response = {}
        handler._send_response = lambda **values: response.update(values)
        with patch.object(handler, "_check_device_access", return_value=True):
            handler.do_GET()
        return response

    try:
        assert get("/api/updates")["status"] == 401
        assert get("/api/updates", "Bearer wrong")["status"] == 401
        answer = get("/api/updates", "Bearer fixture-admin")
        assert answer["status"] == 200
        assert json.loads(answer["body"])["available_metadata"] == status["available_metadata"]
        public = get("/api/health")
        assert public["status"] == 200
        assert b"metadata" not in public["body"]
        assert status["available_metadata"]["commit"].encode() not in public["body"]
    finally:
        server.server_close()
        repository.close()
