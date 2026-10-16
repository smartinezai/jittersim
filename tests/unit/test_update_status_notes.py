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


def stage(pi, notes=None, **options):
    base, private, root, code, config = pi
    replace = {NOTES: notes} if notes is not None else {}
    archive = signed_release(base, private, replace=replace, **options)
    updates.stage_release(archive, root=root, code=code, config=config)
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
