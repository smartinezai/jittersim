"""The online release check: the token, the GitHub fetch and the staging it ends in.

GitHub is a fake TLS server on the loopback address (tests/github_support.py). Signatures are real
ssh-keygen signatures made with throwaway keys.
"""

import io
import json
import logging
import shutil
import ssl
import stat
import time

import pytest

from jittersim import updates
from tests.github_support import ARCHIVE_NAME, REPO, SIGNATURE_NAME, TOKEN, FakeGitHub
from tests.release_support import have_ssh_keygen, make_key, signed_release
from tests.unit.test_signed_releases import Pi

pytestmark = pytest.mark.skipif(not have_ssh_keygen(), reason="ssh-keygen signs the releases")


class Online:
    """A stand-in Pi with a trusted key, a token and a fake GitHub that serves a signed release."""

    def __init__(self, base):
        self.pi = Pi(base)
        self.github = FakeGitHub()
        self.set_release(sequence=500, version="v7")
        self.github.start()
        self.run_calls = []
        updates.set_token(TOKEN + "\n", config=self.pi.config)

    def set_release(self, **options):
        archive = self.pi.release(**options)
        self.github.files[11] = archive.read_bytes()
        self.github.files[12] = (archive.parent / (archive.name + ".sig")).read_bytes()

    def source(self, **options):
        values = dict(api_host="localhost", port=self.github.port, asset_hosts=("127.0.0.1",),
                      context=self.github.trusting_context(), timeout=5, total=30)
        values.update(options)
        return updates.GitHubSource(**values)

    def check(self, **options):
        source = options.pop("source", None) or self.source()
        return updates.check_online(
            root=self.pi.root, code=self.pi.code, config=self.pi.config, source=source, **options
        )

    def run(self, command, timeout=0):
        self.run_calls.append(command)


@pytest.fixture
def online(tmp_path):
    fixture = Online(tmp_path)
    yield fixture
    fixture.github.stop()


def refused(online, words, **options):
    result = online.check(**options)
    assert result["result"] == "failed", result
    assert words in result["message"], result["message"]
    assert "\n" not in result["message"]
    assert TOKEN not in json.dumps(result)
    assert not (online.pi.root / "staged").exists()
    return result


# The token


def test_set_token_writes_a_root_only_file_atomically_and_checks_the_shape(tmp_path):
    config = tmp_path
    config.chmod(0o755)
    updates.set_token("  " + TOKEN + "  \n", config=config)
    path = config / updates.TOKEN_NAME
    assert path.read_text() == TOKEN + "\n"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert [item.name for item in config.iterdir()] == [updates.TOKEN_NAME]
    assert updates.token_is_set(config) is True
    updates.set_token("ghp_" + "a" * 36, config=config)
    assert path.read_text() == "ghp_" + "a" * 36 + "\n"
    updates.clear_token(config=config)
    assert not path.exists() and updates.token_is_set(config) is False


@pytest.mark.parametrize("text", [
    "", "\n", "hello", "github_pat_", "github_pat_short", "ghp_short",
    "gho_" + "a" * 36, "token " + TOKEN, TOKEN + "\n" + TOKEN, TOKEN + "x y",
    TOKEN[:20] + "é" + TOKEN[21:], "github_pat_" + "a" * 600,
])
def test_a_token_of_the_wrong_shape_is_refused_and_changes_nothing(tmp_path, text):
    tmp_path.chmod(0o755)
    updates.set_token(TOKEN, config=tmp_path)
    with pytest.raises(updates.UpdateError) as error:
        updates.set_token(text, config=tmp_path)
    assert (tmp_path / updates.TOKEN_NAME).read_text() == TOKEN + "\n"
    assert len(text.strip()) < 13 or text.strip() not in str(error.value)
    assert TOKEN not in str(error.value)


def test_clearing_a_token_that_is_not_set_says_so(tmp_path):
    with pytest.raises(updates.UpdateError, match="no token"):
        updates.clear_token(config=tmp_path)


def test_a_token_file_others_can_read_or_a_link_is_not_used(tmp_path):
    tmp_path.chmod(0o755)
    path = tmp_path / updates.TOKEN_NAME
    path.write_text(TOKEN + "\n")
    path.chmod(0o644)
    with pytest.raises(updates.UpdateError, match="root only"):
        updates.read_token(tmp_path)
    path.unlink()
    (tmp_path / "other").write_text(TOKEN + "\n")
    path.symlink_to(tmp_path / "other")
    with pytest.raises(updates.UpdateError, match="not a link"):
        updates.read_token(tmp_path)


def test_set_token_command_reads_standard_input_only(online, monkeypatch, capsys):
    pi = online.pi
    for name, value in (("UPDATE_ROOT", pi.root), ("CODE_ROOT", pi.code),
                        ("CONFIG_ROOT", pi.config)):
        monkeypatch.setattr(updates, name, value)
    monkeypatch.setattr(updates, "running_as_root", lambda: True)
    updates.clear_token(config=pi.config)
    # A token on the command line would end up in the shell history and the process list.
    with pytest.raises(SystemExit):
        updates.main(["set-token", TOKEN])
    capsys.readouterr()
    assert not (pi.config / updates.TOKEN_NAME).exists()
    monkeypatch.setattr(updates.sys, "stdin", io.TextIOWrapper(io.BytesIO(TOKEN.encode() + b"\n")))
    assert updates.main(["set-token"]) == 0
    output = capsys.readouterr()
    assert TOKEN not in output.out + output.err
    assert "token is saved" in output.out.lower()
    assert (pi.config / updates.TOKEN_NAME).read_text() == TOKEN + "\n"
    monkeypatch.setattr(updates.sys, "stdin", io.TextIOWrapper(io.BytesIO(b"nonsense\n")))
    assert updates.main(["set-token"]) == 1
    output = capsys.readouterr()
    assert "nonsense" not in output.err and "Nothing was changed" in output.err
    assert updates.main(["clear-token"]) == 0
    assert not (pi.config / updates.TOKEN_NAME).exists()
    assert updates.main(["clear-token"]) == 1


def test_set_and_clear_token_need_root(online, monkeypatch, capsys):
    monkeypatch.setattr(updates, "running_as_root", lambda: False)
    assert updates.main(["clear-token"]) == 2
    assert updates.main(["check-online"]) == 2
    assert "root" in capsys.readouterr().err


def test_the_status_says_only_whether_a_token_is_set(online):
    status = online.pi.status()
    assert status["token_set"] is True
    assert TOKEN not in json.dumps(status)
    updates.clear_token(config=online.pi.config)
    assert online.pi.status()["token_set"] is False


def test_the_token_stays_through_a_rollback():
    assert updates.TOKEN_NAME in updates.PRESERVED_CONFIG


# A successful check


def test_a_check_downloads_two_assets_and_stages_the_signed_release(online):
    result = online.check()
    assert result["result"] == "staged"
    assert result["tag"] == "v7" and result["available"] == "v7"
    assert "v7" in result["message"]
    record = online.pi.record()
    assert record["source"] == "github" and record["tag"] == "v7"
    assert record["sequence"] == 500
    status = online.pi.status()
    assert status["installable"] is True
    assert status["source"] == "github" and status["tag"] == "v7"
    # Nothing is left of the downloads.
    assert {item.name for item in online.pi.root.iterdir()} <= {
        "lock", "status.json", "staged", "online.lock", "online-seen.json", updates.FLOOR_NAME}


def test_the_token_goes_to_github_only_and_never_to_the_asset_host(online):
    online.check()
    requests = online.github.requests
    api = online.github.seen(f"localhost:{online.github.port}")
    assets = online.github.seen(f"127.0.0.1:{online.github.port}")
    assert [item["path"] for item in api] == [
        f"/repos/{REPO}/releases/latest",
        f"/repos/{REPO}/releases/assets/11",
        f"/repos/{REPO}/releases/assets/12",
    ]
    assert all(item["auth"] == "Bearer " + TOKEN for item in api)
    # The asset URL is the API one and asks for the bytes.
    assert [item["accept"] for item in api[1:]] == ["application/octet-stream"] * 2
    assert [item["path"] for item in assets] == ["/dl/11", "/dl/12"]
    assert all(item["auth"] is None for item in assets)
    assert len(requests) == 5


def test_the_asset_url_is_built_from_the_id_and_not_taken_from_the_answer(online):
    online.github.latest_body = json.dumps({
        **online.github.release_json(),
        "assets": [
            {"id": 11, "name": ARCHIVE_NAME, "size": len(online.github.files[11]),
             "url": "https://evil.example/steal"},
            {"id": 12, "name": SIGNATURE_NAME, "size": len(online.github.files[12]),
             "url": "https://evil.example/steal"},
        ]}).encode()
    assert online.check()["result"] == "staged"
    assert all("evil" not in item["host"] for item in online.github.requests)


def test_a_second_check_of_the_same_release_downloads_nothing(online):
    online.check()
    count = len(online.github.requests)
    result = online.check()
    assert result["result"] == "staged"
    assert "already staged" in result["message"]
    assert len(online.github.requests) == count + 1


def test_an_installed_release_is_up_to_date(online):
    online.pi.installed(sequence=500, version="v7")
    result = online.check()
    assert result["result"] == "up-to-date"
    assert result["message"].startswith("Already up to date")
    assert not (online.pi.root / "staged").exists()
    # The release is remembered so the next check downloads nothing.
    count = len(online.github.requests)
    again = online.check()
    assert again["result"] == "up-to-date"
    assert len(online.github.requests) == count + 1


def test_an_older_release_is_up_to_date_never_staged(online):
    online.pi.installed(sequence=900, version="v9")
    result = online.check()
    assert result["result"] == "up-to-date"
    assert "Already up to date" in result["message"]


def test_the_installed_tag_is_up_to_date_without_a_download(online):
    online.check()
    # Pretend that this staged release was installed.
    staged = json.loads((online.pi.root / "staged" / ".jittersim-release.json").read_text())
    (online.pi.code / ".jittersim-release.json").write_text(json.dumps(staged))
    count = len(online.github.requests)
    assert online.check()["result"] == "up-to-date"
    assert len(online.github.requests) == count + 1


def test_a_newer_release_replaces_the_staged_one(online):
    online.check()
    online.github.tag = "v8"
    online.set_release(sequence=600, version="v8")
    assert online.check()["result"] == "staged"
    assert online.pi.record()["tag"] == "v8" and online.pi.record()["sequence"] == 600


# Refusals


def test_without_a_token_nothing_is_fetched(online):
    updates.clear_token(config=online.pi.config)
    refused(online, "No GitHub token")
    assert online.github.requests == []


def test_a_release_with_another_asset_name_is_refused(online):
    online.github.names[11] = "other.tar.gz"
    refused(online, "jittersim-release.tar.gz")
    assert online.github.asset_requests() == []


def test_a_missing_signature_asset_is_refused(online):
    del online.github.files[12]
    refused(online, "jittersim-release.tar.gz.sig")


def test_an_extra_asset_is_refused(online):
    online.github.extra_assets = [{"id": 13, "name": "notes.txt", "size": 3}]
    refused(online, "exactly two files")


def test_a_repeated_asset_name_is_refused(online):
    online.github.files[13] = b"x"
    online.github.names[13] = ARCHIVE_NAME
    refused(online, "exactly two files")


def test_an_archive_declared_too_large_is_refused_without_a_download(online):
    online.github.sizes[11] = updates.MAX_ARCHIVE + 1
    refused(online, "too large")
    assert online.github.asset_requests() == []


def test_a_signature_declared_too_large_is_refused(online):
    online.github.sizes[12] = updates.MAX_SIGNATURE + 1
    refused(online, "too large")


def test_an_archive_that_grows_past_its_limit_is_cut_off(online, monkeypatch):
    monkeypatch.setattr(updates, "MAX_ARCHIVE", 200)
    online.github.sizes[11] = 10
    refused(online, "too large")
    assert not any(item.name.startswith("online-") for item in online.pi.root.iterdir())


def test_a_signature_that_grows_past_its_limit_is_cut_off(online):
    online.github.files[12] = b"x" * (updates.MAX_SIGNATURE + 100)
    online.github.sizes[12] = 10
    refused(online, "too large")


def test_an_answer_that_announces_too_much_is_refused(online):
    online.github.length_lie[11] = updates.MAX_ARCHIVE + 1
    refused(online, "too large")


def test_an_oversized_api_answer_is_refused(online):
    online.github.latest_body = b'{"x": "' + b"a" * 70000 + b'"}'
    refused(online, "too large")


def test_an_answer_that_is_not_a_release_is_refused(online):
    for body in (b"not json", b"[]", b'{"tag_name": 7}', b'{"tag_name": "../x", "assets": []}',
                 b'{"tag_name": "v1", "assets": "x"}'):
        online.github.latest_body = body
        refused(online, "not a release")


def test_a_draft_or_prerelease_is_refused(online):
    online.github.latest_body = json.dumps(
        {**online.github.release_json(), "prerelease": True}).encode()
    refused(online, "not a final release")


def test_a_release_signed_by_a_key_the_pi_does_not_trust_is_refused(online, tmp_path):
    other, _ = make_key(tmp_path / "other", name="other")
    archive = signed_release(tmp_path, other, name="x.tar.gz", sequence=700, version="v9")
    online.github.files[11] = archive.read_bytes()
    online.github.files[12] = (tmp_path / "x.tar.gz.sig").read_bytes()
    refused(online, "release signature is not valid")


def test_a_changed_archive_fails_its_signature(online):
    data = bytearray(online.github.files[11])
    data[len(data) // 2] ^= 0xFF
    online.github.files[11] = bytes(data)
    refused(online, "release signature is not valid")


def test_garbage_in_place_of_the_assets_is_refused(online):
    online.github.files[11] = b"hello"
    online.github.files[12] = b"hello"
    refused(online, "signature")


def test_a_check_with_no_trusted_key_says_so(online):
    online.pi.config.joinpath("release-signers").unlink()
    refused(online, "No release signing key is trusted")


@pytest.mark.parametrize("status,words", [
    (401, "refused the token"), (403, "refused the token"), (404, "cannot see"),
    (429, "limiting"), (500, "error (HTTP 500)"), (503, "error (HTTP 503)"),
])
def test_github_errors_become_one_plain_line(online, status, words):
    online.github.latest_status = status
    online.github.latest_body = b'{"message": "' + TOKEN.encode() + b'"}'
    refused(online, words)


def test_an_asset_error_never_shows_what_github_said(online):
    online.github.asset_status[11] = 404
    refused(online, "could not be downloaded")


def test_a_certificate_that_is_not_trusted_is_refused(online):
    source = online.source(context=ssl.create_default_context())
    refused(online, "certificate", source=source)
    assert online.github.requests == []


def test_a_slow_github_times_out_with_a_plain_message(online):
    online.github.delay = 3
    refused(online, "did not answer in time", source=online.source(timeout=1))


def test_the_total_time_is_limited(online):
    online.github.delay = 1
    refused(online, "took too long", source=online.source(timeout=5, total=1))


def test_a_download_that_trickles_is_stopped_at_the_total_time(online):
    # One byte every 0.1 s never trips the per-read timeout. Only the total time can stop it.
    online.github.trickle = 0.1
    started = time.monotonic()
    refused(online, "took too long", source=online.source(timeout=5, total=2))
    assert time.monotonic() - started < 8


def test_answer_headers_that_trickle_are_stopped_at_the_total_time(online):
    online.github.trickle_headers = 0.1
    started = time.monotonic()
    refused(online, "took too long", source=online.source(timeout=5, total=2))
    assert time.monotonic() - started < 8


def test_an_unreachable_github_is_a_plain_message(online):
    refused(online, "Could not reach GitHub", source=online.source(port=1))


# Redirects


def redirect_case(online, target, words):
    online.github.redirect_to[11] = target
    refused(online, words)
    assert TOKEN not in json.dumps(online.github.requests[2:])


@pytest.mark.parametrize("target", [
    "https://evil.example/x", "http://127.0.0.1:{port}/dl/11", "https://user@127.0.0.1:{port}/dl/11",
    "https://localhost:{port}/dl/11", "https://127.0.0.1.evil.example:{port}/dl/11",
    "//evil.example/x", "ftp://127.0.0.1/x", "/dl/11",
])
def test_a_redirect_to_another_host_or_scheme_is_refused(online, target):
    redirect_case(online, target.format(port=online.github.port), "redirect")
    # Nothing was sent to the other host and the token never left GitHub's own host.
    assert all(item["host"] != "evil.example" for item in online.github.requests)
    assert all(item["auth"] is None for item in online.github.seen(f"127.0.0.1:{online.github.port}"))


def test_a_redirect_to_another_port_is_refused(online):
    redirect_case(online, f"https://127.0.0.1:{online.github.port + 1}/dl/11", "redirect")


def test_two_redirects_are_followed_and_a_third_is_not(online):
    port = online.github.port
    online.github.redirect_to[11] = f"https://127.0.0.1:{port}/hop1"
    online.github.hops["/hop1"] = f"https://127.0.0.1:{port}/dl/11"
    assert online.check()["result"] == "staged"
    online.github.requests.clear()
    shutil.rmtree(online.pi.root / "staged")
    online.github.redirect_to[11] = f"https://127.0.0.1:{port}/hop1"
    online.github.hops["/hop1"] = f"https://127.0.0.1:{port}/hop2"
    online.github.hops["/hop2"] = f"https://127.0.0.1:{port}/dl/11"
    refused(online, "too many redirects")
    assert all(item["auth"] is None for item in online.github.seen(f"127.0.0.1:{port}"))


def test_a_redirect_without_a_target_is_refused(online):
    online.github.redirect_to[11] = ""
    refused(online, "redirect")


# The token never shows


def test_the_token_is_in_no_log_message_exception_record_or_file_but_its_own(online, caplog,
                                                                                capsys):
    caplog.set_level(logging.DEBUG)
    online.github.latest_status = 401
    online.github.latest_body = TOKEN.encode()
    online.check()
    online.github.latest_status = 200
    online.github.latest_body = None
    online.check()
    online.github.asset_status[12] = 500
    online.github.requests.clear()
    online.check()
    output = capsys.readouterr()
    assert TOKEN not in output.out + output.err + caplog.text
    for path in online.pi.root.rglob("*"):
        if path.is_file():
            assert TOKEN.encode() not in path.read_bytes(), path
    for path in online.pi.config.rglob("*"):
        if path.is_file() and path.name != updates.TOKEN_NAME:
            assert TOKEN.encode() not in path.read_bytes(), path


def test_an_unexpected_failure_leaves_a_plain_message_without_the_token(online, monkeypatch,
                                                                       caplog):
    caplog.set_level(logging.DEBUG)

    def broken(*args, **kwargs):
        raise RuntimeError("boom " + TOKEN)

    monkeypatch.setattr(updates, "_download", broken)
    result = online.check()
    assert result["result"] == "failed"
    assert TOKEN not in json.dumps(result) + caplog.text
    assert "unexpected" in result["message"]


def test_the_default_source_is_the_owners_repository_on_github(monkeypatch):
    source = updates.default_source()
    assert source.api_host == "api.github.com" and source.port == 443
    assert source.repo == "smartinezai/jittersim"
    assert set(source.asset_hosts) == {
        "objects.githubusercontent.com", "release-assets.githubusercontent.com"}
    assert source.timeout == 30 and source.total == 300
    assert source.context.check_hostname is True
    assert source.context.verify_mode.name == "CERT_REQUIRED"


def test_check_online_command_prints_one_line_and_exits_by_result(online, monkeypatch, capsys):
    pi = online.pi
    for name, value in (("UPDATE_ROOT", pi.root), ("CODE_ROOT", pi.code),
                        ("CONFIG_ROOT", pi.config)):
        monkeypatch.setattr(updates, name, value)
    monkeypatch.setattr(updates, "running_as_root", lambda: True)
    monkeypatch.setattr(updates, "default_source", online.source)
    assert updates.main(["check-online"]) == 0
    out = capsys.readouterr().out
    assert len(out.strip().splitlines()) == 1 and "v7" in out
    assert TOKEN not in out
    pi.installed(sequence=900, version="v9")
    shutil.rmtree(pi.root / "staged")
    assert updates.main(["check-online"]) == 0
    assert capsys.readouterr().out.startswith("Already up to date")
    online.github.latest_status = 401
    assert updates.main(["check-online"]) == 1
    error = capsys.readouterr().err
    assert "refused the token" in error and "Nothing was changed" in error
