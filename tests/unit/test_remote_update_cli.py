"""jittersim remote update status|upload|install against a real TLS dashboard."""

import shutil
from pathlib import Path
from unittest.mock import patch

import pytest

from jittersim import cli, remote_cli, updates
from tests.release_support import have_ssh_keygen, make_key, signed_release
from tests.unit.test_deploy_over_https import TOKEN, Pi, make_certificate

pytestmark = pytest.mark.skipif(
    not (have_ssh_keygen() and shutil.which("openssl")), reason="ssh-keygen and openssl")


@pytest.fixture
def lab(tmp_path, monkeypatch):
    private, public = make_key(tmp_path / "keys")
    certificate, key = make_certificate(tmp_path, "dashboard")
    pi = Pi(tmp_path, certificate, key, public)
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("JITTERSIM_URL", pi.url)
    monkeypatch.setenv("JITTERSIM_CA_CERT", str(certificate))
    monkeypatch.setenv("JITTERSIM_ADMIN_TOKEN", TOKEN)
    builds = tmp_path / "builds"
    builds.mkdir()
    yield {"pi": pi, "private": private, "builds": builds, "tmp": tmp_path}
    pi.stop()


def run(capsys, *arguments):
    code = cli.main(["remote", "update", *arguments])
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def test_status_upload_and_install_in_order(lab, capsys):
    archive = signed_release(lab["builds"], lab["private"], sequence=300, version="abc1234",
                             name="jittersim-release.tar.gz")
    code, out, _ = run(capsys, "status")
    assert code == 0 and "Staged release: none" in out
    code, out, err = run(capsys, "upload", "--archive", str(archive))
    assert code == 0, err
    assert "staged release abc1234" in out
    code, out, _ = run(capsys, "status")
    assert "Staged release: abc1234" in out
    with patch.object(remote_cli.time, "sleep"):
        code, out, err = run(capsys, "install", "--wait", "30")
    assert code == 0, err
    assert "The Pi now runs release abc1234" in out
    assert lab["pi"].installs == ["abc1234"]
    assert TOKEN not in out + err


def test_install_without_a_staged_release_says_so(lab, capsys):
    code, _, err = run(capsys, "install")
    assert code == 1 and "No release is staged" in err


def test_the_pi_refuses_a_release_signed_by_a_stranger(lab, capsys):
    other, _ = make_key(lab["tmp"] / "other")
    archive = signed_release(lab["builds"], other, sequence=300, version="evil",
                             name="jittersim-release.tar.gz")
    code, _, err = run(capsys, "upload", "--archive", str(archive))
    assert code == 1 and "signature is not valid" in err
    assert not (lab["pi"].root / "staged").exists()


def test_a_missing_or_oversized_file_is_refused_before_anything_is_sent(lab, capsys):
    code, _, err = run(capsys, "upload", "--archive", str(lab["tmp"] / "nothing.tar.gz"))
    assert code == 2 and "could not be read" in err
    big = lab["tmp"] / "big.tar.gz"
    big.write_bytes(b"x" * (updates.MAX_ARCHIVE + 1))
    (lab["tmp"] / "big.tar.gz.sig").write_bytes(b"s")
    code, _, err = run(capsys, "upload", "--archive", str(big))
    assert code == 2 and "larger than the Pi accepts" in err


def test_a_wrong_token_is_refused(lab, capsys, monkeypatch):
    monkeypatch.setenv("JITTERSIM_ADMIN_TOKEN", "not-it")
    code, _, err = run(capsys, "status")
    assert code == 1 and "refused the admin token" in err
