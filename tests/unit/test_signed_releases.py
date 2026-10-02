"""The root update manager installs only archives signed by a key the owner trusted.

Every signature here is a real `ssh-keygen -Y sign` signature made with a throwaway key inside
the test's own folder.
"""

import gzip
import io
import json
import os
import stat
import tarfile
import threading
from pathlib import Path

import pytest

from jittersim import updates
from tests.release_support import (
    COMMIT,
    ed25519_line,
    fingerprint,
    have_ssh_keygen,
    make_key,
    manifest,
    release_files,
    sha256,
    sign,
    signed_release,
    write_archive,
)

needs_keygen = pytest.mark.skipif(not have_ssh_keygen(), reason="ssh-keygen signs the releases")


class Pi:
    """A stand-in Pi: update, code and configuration folders plus one trusted key."""

    def __init__(self, base):
        self.base = base
        self.root = base / "updates"
        self.code = base / "code"
        self.config = base / "config"
        self.sudoers = base / "sudoers.d" / "jittersim-update"
        for folder in (self.code, self.config, self.sudoers.parent):
            folder.mkdir()
            folder.chmod(0o755)
        self.private, self.public = make_key(base / "keys")
        updates.trust_key(self.public, config=self.config, root=self.root)

    def stage(self, archive, **options):
        return updates.stage_release(
            archive, root=self.root, code=self.code, config=self.config, **options
        )

    def status(self):
        return updates.update_status(root=self.root, code=self.code, config=self.config)

    def record(self):
        return json.loads((self.root / "staged" / ".jittersim-release.json").read_text())

    def installed(self, sequence, version="installed", commit=COMMIT):
        (self.code / ".jittersim-release.json").write_text(
            json.dumps({"version": version, "sequence": sequence, "commit": commit})
        )

    def release(self, **options):
        return signed_release(self.base, self.private, **options)


@pytest.fixture
def pi(tmp_path):
    if not have_ssh_keygen():
        pytest.skip("ssh-keygen signs the releases")
    return Pi(tmp_path)


def assert_refused(pi, archive, words, **options):
    """The archive is refused with a plain message and nothing on the Pi changes."""

    status = pi.root / "status.json"
    before = status.read_bytes() if status.exists() else None
    with pytest.raises(updates.UpdateError, match=words):
        pi.stage(archive, **options)
    assert not (pi.root / "staged").exists()
    assert (status.read_bytes() if status.exists() else None) == before
    # The private work folder is gone too.
    assert {item.name for item in pi.root.iterdir()} <= {"lock", "status.json",
                                                         updates.FLOOR_NAME}


# Signatures


def test_a_release_signed_by_a_trusted_key_is_staged_with_its_signer_and_sequence(pi):
    archive = pi.release(sequence=1_000, version="abc1234")
    pi.stage(archive)
    record = pi.record()
    assert record == {
        "version": "abc1234",
        "sha256": sha256(archive),
        "commit": COMMIT,
        "sequence": 1_000,
        "signer": fingerprint(pi.public),
        "signer_label": "owner@mac",
    }
    assert (pi.root / "staged" / "deploy" / "RELEASE").read_text() == manifest(1_000, "abc1234")
    status = pi.status()
    assert status["available"] == "abc1234"
    assert status["installable"] is True
    assert status["signed_by"] == "owner@mac"
    assert status["trusted_keys"] == 1
    assert json.loads((pi.root / "status.json").read_text())["phase"] == "ready"


@needs_keygen
def test_a_release_signed_by_an_unknown_key_is_refused(pi):
    stranger, _ = make_key(pi.base / "stranger", comment="stranger")
    archive = signed_release(pi.base, stranger)
    assert_refused(pi, archive, "signature")


def test_one_changed_byte_in_the_archive_is_refused(pi):
    archive = pi.release()
    raw = bytearray(archive.read_bytes())
    raw[len(raw) // 2] ^= 0x01
    archive.write_bytes(bytes(raw))
    assert_refused(pi, archive, "signature")


def test_a_byte_added_to_the_archive_is_refused(pi):
    archive = pi.release()
    with archive.open("ab") as handle:
        handle.write(b"\0")
    assert_refused(pi, archive, "signature")


def test_a_truncated_archive_is_refused(pi):
    archive = pi.release()
    archive.write_bytes(archive.read_bytes()[:-100])
    assert_refused(pi, archive, "signature")


def test_a_changed_signature_is_refused(pi):
    archive = pi.release()
    signature = Path(str(archive) + ".sig")
    lines = signature.read_text().splitlines()
    body = list(lines[2])
    body[10] = "A" if body[10] != "A" else "B"
    lines[2] = "".join(body)
    signature.write_text("\n".join(lines) + "\n")
    assert_refused(pi, archive, "signature")


@pytest.mark.parametrize("damage", ["half", "empty", "garbage", "missing"])
def test_a_truncated_empty_or_missing_signature_is_refused(pi, damage):
    archive = pi.release()
    signature = Path(str(archive) + ".sig")
    if damage == "half":
        signature.write_bytes(signature.read_bytes()[: len(signature.read_bytes()) // 2])
    elif damage == "empty":
        signature.write_bytes(b"")
    elif damage == "garbage":
        signature.write_bytes(b"\x00not a signature\n")
    else:
        signature.unlink()
    assert_refused(pi, archive, "signature")


def test_a_signature_made_for_another_purpose_is_refused(pi):
    # A key used for other signatures (for example git commits) must not approve a release.
    archive = pi.release()
    sign(archive, pi.private, namespace="git")
    assert_refused(pi, archive, "signature")


def test_a_valid_signature_of_another_release_is_refused(pi):
    first = pi.release(name="first.tar.gz", sequence=1)
    second = pi.release(name="second.tar.gz", sequence=2)
    Path(str(second) + ".sig").write_bytes(Path(str(first) + ".sig").read_bytes())
    assert_refused(pi, second, "signature")


def test_nothing_is_staged_while_no_key_is_trusted(pi):
    archive = pi.release()
    (pi.config / updates.SIGNERS_NAME).unlink()
    assert_refused(pi, archive, "trust-key")
    updates.trust_key(pi.public, config=pi.config, root=pi.root)
    updates.untrust_key("owner@mac", config=pi.config, root=pi.root, sudoers=pi.sudoers)
    assert_refused(pi, archive, "trust-key")


def test_the_signature_path_can_be_given_and_defaults_to_the_archive_name(pi):
    archive = pi.release()
    elsewhere = pi.base / "elsewhere.sig"
    Path(str(archive) + ".sig").rename(elsewhere)
    assert_refused(pi, archive, "signature")
    pi.stage(archive, signature=elsewhere)
    assert pi.record()["version"] == "v1"


def test_an_optional_checksum_and_version_must_match_too(pi):
    archive = pi.release(version="abc1234")
    assert_refused(pi, archive, "SHA-256", sha256="0" * 64)
    assert_refused(pi, archive, "version", version="def5678")
    pi.stage(archive, sha256=sha256(archive), version="abc1234")


# Archive contents behind a valid signature


def entry(name, kind=tarfile.REGTYPE, link="/etc/passwd"):
    info = tarfile.TarInfo(name)
    info.type = kind
    info.linkname = link
    return info


UNSAFE = [
    ("../escaped", tarfile.REGTYPE),
    ("/etc/escaped", tarfile.REGTYPE),
    ("src/../../escaped", tarfile.REGTYPE),
    ("src/link", tarfile.SYMTYPE),
    ("src/hardlink", tarfile.LNKTYPE),
    ("src/device", tarfile.CHRTYPE),
    ("src/pipe", tarfile.FIFOTYPE),
    ("etc/passwd", tarfile.REGTYPE),
    ("README.md/inside-a-file", tarfile.REGTYPE),
    ("RELEASE/inside-a-file", tarfile.REGTYPE),
    (".jittersim-release.json", tarfile.REGTYPE),
    ("src/jittersim/api/server.py", tarfile.REGTYPE),
]


@pytest.mark.parametrize("name, kind", UNSAFE)
def test_unsafe_entries_are_refused_even_with_a_valid_signature(pi, name, kind):
    archive = pi.release(extra=[(entry(name, kind), b"evil")])
    assert_refused(pi, archive, "unsafe or unexpected")
    assert not (pi.base / "escaped").exists()


def test_a_release_without_its_manifest_is_refused(pi):
    assert_refused(pi, pi.release(drop=["deploy/RELEASE"]), "missing required files")


def test_a_file_with_entries_below_it_is_refused(pi):
    archive = pi.release(extra=[(entry("deploy/RELEASE/inside"), b"x")])
    assert_refused(pi, archive, "unsafe or unexpected")
    archive = pi.release(extra=[(entry("README.md/inside"), b"x")])
    assert_refused(pi, archive, "unsafe or unexpected")


def test_an_archive_of_an_older_manager_layout_still_needs_the_manifest_in_deploy(pi):
    # A manifest at the top of the archive is not where build-release.sh puts it.
    files = release_files()
    files["RELEASE"] = files.pop("deploy/RELEASE")
    archive = write_archive(pi.base / "top.tar.gz", files)
    sign(archive, pi.private)
    assert_refused(pi, archive, "unsafe or unexpected")


BAD_MANIFESTS = [
    "not json",
    json.dumps({"format": 1, "commit": COMMIT, "version": "v1"}),
    json.dumps({"format": 1, "commit": COMMIT, "version": "v1", "sequence": 5, "extra": 1}),
    json.dumps({"format": 2, "commit": COMMIT, "version": "v1", "sequence": 5}),
    json.dumps({"format": True, "commit": COMMIT, "version": "v1", "sequence": 5}),
    json.dumps({"format": 1, "commit": COMMIT, "version": "v1", "sequence": True}),
    json.dumps({"format": 1, "commit": COMMIT, "version": "v1", "sequence": "5"}),
    json.dumps({"format": 1, "commit": COMMIT, "version": "v1", "sequence": 5.5}),
    json.dumps({"format": 1, "commit": COMMIT, "version": "v1", "sequence": 0}),
    json.dumps({"format": 1, "commit": COMMIT, "version": "v1", "sequence": -3}),
    json.dumps({"format": 1, "commit": COMMIT, "version": "v1", "sequence": 2 ** 60}),
    json.dumps({"format": 1, "commit": "xyz", "version": "v1", "sequence": 5}),
    json.dumps({"format": 1, "commit": COMMIT.upper(), "version": "v1", "sequence": 5}),
    json.dumps({"format": 1, "commit": COMMIT, "version": "../v1", "sequence": 5}),
    json.dumps({"format": 1, "commit": COMMIT, "version": "", "sequence": 5}),
    json.dumps([1, 2, 3]),
    "{" + " " * 5000 + "}",
    '{"format": 1, "commit": "' + COMMIT + '", "version": "vé", "sequence": 5}',
]


@pytest.mark.parametrize("text", BAD_MANIFESTS)
def test_a_damaged_manifest_is_refused(pi, text):
    assert_refused(pi, pi.release(release=text), "manifest")


def test_an_oversized_archive_is_refused_before_it_is_read_further(pi, monkeypatch):
    archive = pi.release()
    monkeypatch.setattr(updates, "MAX_ARCHIVE", archive.stat().st_size - 1)
    assert_refused(pi, archive, "too large")


def test_an_oversized_signature_is_refused(pi):
    archive = pi.release()
    Path(str(archive) + ".sig").write_bytes(b"A" * (updates.MAX_SIGNATURE + 1))
    assert_refused(pi, archive, "too large")


def test_an_archive_that_expands_too_far_is_refused(pi, monkeypatch):
    archive = pi.release(replace={"src/jittersim/big.py": "x" * 200_000})
    monkeypatch.setattr(updates, "MAX_EXPANDED", 100_000)
    assert_refused(pi, archive, "too large|size")


def test_links_folders_and_pipes_are_not_read_as_archives(pi):
    archive = pi.release()
    link = pi.base / "link.tar.gz"
    link.symlink_to(archive)
    Path(str(link) + ".sig").write_bytes(Path(str(archive) + ".sig").read_bytes())
    assert_refused(pi, link, "regular file|could not be opened")
    folder = pi.base / "folder.tar.gz"
    folder.mkdir()
    assert_refused(pi, folder, "regular file|could not be opened")
    pipe = pi.base / "pipe.tar.gz"
    os.mkfifo(pipe)
    errors = []
    worker = threading.Thread(
        target=lambda: errors.append(pytest.raises(updates.UpdateError, pi.stage, pipe)),
        daemon=True,
    )
    worker.start()
    worker.join(timeout=10)
    assert not worker.is_alive(), "staging waited on a named pipe"
    assert not (pi.root / "staged").exists()


# The exact bytes that were verified are the bytes that are extracted


def malicious_archive(path):
    files = release_files()
    files["src/jittersim/api/server.py"] = "evil code"
    return write_archive(path, files)


def test_the_copy_is_verified_and_not_the_path_that_may_change(pi, monkeypatch):
    signed = pi.release(name="signed.tar.gz")
    target = malicious_archive(pi.base / "target.tar.gz")
    Path(str(target) + ".sig").write_bytes(Path(str(signed) + ".sig").read_bytes())
    original = updates.copy_bounded

    def copy_then_swap(source, destination, limit, what):
        original(source, destination, limit, what)
        if Path(source) == target:
            # The owner's signed archive appears at the path after the copy was taken.
            target.write_bytes(signed.read_bytes())

    monkeypatch.setattr(updates, "copy_bounded", copy_then_swap)
    assert_refused(pi, target, "signature")


def test_the_verified_copy_is_the_one_that_is_extracted(pi, monkeypatch):
    archive = pi.release()
    original_run = updates.subprocess.run

    def verify_then_swap(command, *args, **kwargs):
        result = original_run(command, *args, **kwargs)
        if command[:3] == [updates.SSH_KEYGEN, "-Y", "verify"]:
            malicious_archive(archive)
        return result

    monkeypatch.setattr(updates.subprocess, "run", verify_then_swap)
    pi.stage(archive)
    staged = pi.root / "staged" / "src" / "jittersim" / "api" / "server.py"
    assert staged.read_text() == "new code"


def test_nothing_is_decompressed_or_parsed_before_the_signature_is_checked(pi, monkeypatch):
    stranger, _ = make_key(pi.base / "stranger", comment="stranger")
    archive = signed_release(pi.base, stranger)
    touched = []
    original_open, original_gzip = updates.tarfile.open, updates.gzip.GzipFile
    monkeypatch.setattr(updates.tarfile, "open",
                        lambda *a, **k: touched.append("tar") or original_open(*a, **k))
    monkeypatch.setattr(updates.gzip, "GzipFile",
                        lambda *a, **k: touched.append("gzip") or original_gzip(*a, **k))
    assert_refused(pi, archive, "signature")
    assert touched == []


def test_the_signature_check_reads_the_root_owned_copy(pi, monkeypatch):
    archive = pi.release()
    seen = []
    original_run = updates.subprocess.run

    def watch(command, *args, **kwargs):
        if command[:3] == [updates.SSH_KEYGEN, "-Y", "verify"]:
            stdin = kwargs["stdin"]
            seen.append((Path(stdin.name), command))
        return original_run(command, *args, **kwargs)

    monkeypatch.setattr(updates.subprocess, "run", watch)
    pi.stage(archive)
    (copy, command), = seen
    assert copy.parent.parent == pi.root and copy != archive
    # A fixed identity, the release namespace and a copy of the validated key list.
    assert command[command.index("-I") + 1] == "jittersim-release"
    assert command[command.index("-n") + 1] == "jittersim-release"
    signers = Path(command[command.index("-f") + 1])
    assert signers.parent.parent == pi.root


# Release order


@pytest.mark.parametrize("sequence, accepted", [(499, False), (500, False), (501, True)])
def test_a_release_must_be_newer_than_the_installed_one(pi, sequence, accepted):
    pi.installed(500)
    archive = pi.release(sequence=sequence)
    if accepted:
        pi.stage(archive)
        assert pi.record()["sequence"] == sequence
    else:
        assert_refused(pi, archive, "not newer")


def test_a_release_must_be_newer_than_any_release_installed_before(pi):
    pi.installed(500)
    pi.root.mkdir(mode=0o700, exist_ok=True)
    updates.atomic_json(pi.root / updates.FLOOR_NAME, {"sequence": 800})
    assert_refused(pi, pi.release(sequence=700), "not newer")
    assert_refused(pi, pi.release(sequence=800), "not newer")
    pi.stage(pi.release(sequence=801))


def test_an_unversioned_installation_accepts_any_signed_release(pi):
    pi.stage(pi.release(sequence=1))
    assert pi.record()["sequence"] == 1


def test_a_staged_release_that_became_older_is_not_installable(pi):
    pi.stage(pi.release(sequence=600, version="v600"))
    pi.installed(700)
    status = pi.status()
    assert status["installable"] is False
    assert "not newer" in status["reason"]
    with pytest.raises(updates.UpdateError, match="not newer"):
        updates.request_update("v600", root=pi.root, code=pi.code, config=pi.config,
                               run=lambda *args: None)
    assert not (pi.root / "request.json").exists()


def test_a_release_staged_without_a_signature_cannot_be_installed(pi):
    staged = pi.root / "staged"
    staged.mkdir(parents=True)
    updates.atomic_json(staged / ".jittersim-release.json", {"version": "old", "sha256": "0" * 64})
    status = pi.status()
    assert status["available"] == "old"
    assert status["installable"] is False
    assert "signature" in status["reason"]
    with pytest.raises(updates.UpdateError, match="signature"):
        updates.request_update("old", root=pi.root, code=pi.code, config=pi.config,
                               run=lambda *args: None)


def test_a_staged_release_is_not_installable_once_its_key_is_no_longer_trusted(pi):
    pi.stage(pi.release())
    other_private, other_public = make_key(pi.base / "other", comment="other")
    updates.trust_key(other_public, config=pi.config, root=pi.root)
    # A hand edit that drops the first key without the untrust command.
    signers = pi.config / updates.SIGNERS_NAME
    signers.write_text("".join(line + "\n" for line in signers.read_text().splitlines()
                               if "owner@mac" not in line))
    status = pi.status()
    assert status["installable"] is False and "no longer trusted" in status["reason"]
    with pytest.raises(updates.UpdateError, match="no longer trusted"):
        updates.request_update("v1", root=pi.root, code=pi.code, config=pi.config,
                               run=lambda *args: None)


def test_untrusting_a_key_removes_the_release_it_staged(pi):
    pi.stage(pi.release())
    other_private, other_public = make_key(pi.base / "other", comment="other")
    updates.trust_key(other_public, config=pi.config, root=pi.root)
    updates.untrust_key("owner@mac", config=pi.config, root=pi.root, sudoers=pi.sudoers)
    assert not (pi.root / "staged").exists()
    status = pi.status()
    assert status["available"] is None
    assert "no longer trusted" in status["message"]


def test_untrusting_another_key_keeps_the_staged_release(pi):
    pi.stage(pi.release())
    _, other_public = make_key(pi.base / "other", comment="other")
    updates.trust_key(other_public, config=pi.config, root=pi.root)
    updates.untrust_key("other", config=pi.config, root=pi.root, sudoers=pi.sudoers)
    assert pi.record()["signer_label"] == "owner@mac"


# The trusted key list


def signers_text(pi):
    return (pi.config / updates.SIGNERS_NAME).read_text()


def key_lines(text):
    return [line for line in text.splitlines() if line and not line.startswith("#")]


def test_a_trusted_key_is_written_in_one_strict_line_root_owned_and_world_readable(pi):
    blob = pi.public.split()[1]
    assert key_lines(signers_text(pi)) == [
        f'jittersim-release namespaces="jittersim-release" ssh-ed25519 {blob} owner@mac'
    ]
    path = pi.config / updates.SIGNERS_NAME
    info = path.stat()
    assert stat.S_IMODE(info.st_mode) == 0o644
    assert info.st_uid == os.geteuid()
    assert [(key.label, key.fingerprint) for key in updates.trusted_keys(pi.config)] == [
        ("owner@mac", fingerprint(pi.public))
    ]


def test_a_label_can_be_given_and_replaces_the_comment(pi):
    line = ed25519_line("this comment has spaces")
    with pytest.raises(updates.UpdateError, match="--label"):
        updates.trust_key(line, config=pi.config, root=pi.root)
    signer = updates.trust_key(line, label="desk-mac", config=pi.config, root=pi.root)
    assert signer.label == "desk-mac"
    assert key_lines(signers_text(pi))[-1].endswith(" desk-mac")


RSA = ("ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAAAgQDbcKvT4MeFTumsGeC5mVz2kQHv1L3Qdp1r2Ra8d5yR8PxY"
       "kD7kkgB0eW8S9u0QH1dbkdmgLtqS8l9mqvRmxVt2nV0x8z5cR6uO4v2F6e8b8Jw0gG5g5RZmZ5Kc5YgpY4hY"
       "Fq8Zq2Xy1d8u9XyW7sQe0rYx4uR2yQ6l6YH9wQ== rsa@mac")
ECDSA = ("ecdsa-sha2-nistp256 AAAAE2VjZHNhLXNoYTItbmlzdHAyNTYAAAAIbmlzdHAyNTYAAABBBEmKSENjQEe"
         "zOmxkZMy7opKgwFB9nkt5YRrYMjNuG5N87uRgg6CLrbo5wAdT/y6v0mKV0U2w0WZ2YB/++Tpockg= ec@mac")

HOSTILE = [
    ("rsa", RSA),
    ("ecdsa", ECDSA),
    ("security key", ed25519_line(outer="sk-ssh-ed25519@openssh.com",
                                  kind=b"sk-ssh-ed25519@openssh.com")),
    ("certificate", ed25519_line(outer="ssh-ed25519-cert-v01@openssh.com",
                                 kind=b"ssh-ed25519-cert-v01@openssh.com")),
    ("command option", 'command="/bin/sh" ' + ed25519_line()),
    ("certificate authority", "cert-authority " + ed25519_line()),
    ("namespace option", 'namespaces="*" ' + ed25519_line()),
    ("principal first", "jittersim-release " + ed25519_line()),
    ("two keys", ed25519_line("one") + "\n" + ed25519_line("two")),
    ("comment with a newline", ed25519_line("one\n" + ed25519_line("evil"))),
    ("comment with a carriage return", ed25519_line("one\r" + ed25519_line("evil"))),
    ("comment with a line separator", ed25519_line("one " + ed25519_line("evil"))),
    ("nul byte", ed25519_line("own\x00er")),
    ("escape sequence", ed25519_line("own\x1b[31mer")),
    ("bad base64", "ssh-ed25519 AAAA!!!!" + "A" * 60 + " x"),
    ("short key", ed25519_line(key=os.urandom(31))),
    ("long key", ed25519_line(key=os.urandom(33))),
    ("inner type differs", ed25519_line(kind=b"ssh-rsa")),
    ("trailing bytes", ed25519_line(extra=b"\0")),
    ("padding inside", "ssh-ed25519 " + "AAAA====" + "A" * 60 + " x"),
    ("empty", ""),
    ("comments only", "# ssh-ed25519 nothing\n\n"),
    ("private key", "-----BEGIN OPENSSH PRIVATE KEY-----\nb3BlbnNzaC1rZXk=\n"
                    "-----END OPENSSH PRIVATE KEY-----\n"),
    ("too large", ed25519_line("x" * (updates.MAX_KEY_FILE + 1))),
]


@pytest.mark.parametrize("name, text", HOSTILE, ids=[name for name, _ in HOSTILE])
def test_hostile_key_files_are_refused_and_change_nothing(pi, name, text):
    before = signers_text(pi)
    with pytest.raises(updates.UpdateError):
        updates.trust_key(text, label="label", config=pi.config, root=pi.root)
    assert signers_text(pi) == before


@pytest.mark.parametrize("label", ["two words", 'quo"te', "new\nline", "-dash", "", "é",
                                   "x" * 65, "semi;colon", "$(id)", "tab\tlabel"])
def test_unsafe_labels_are_refused(pi, label):
    with pytest.raises(updates.UpdateError, match="label"):
        updates.trust_key(ed25519_line(), label=label, config=pi.config, root=pi.root)


def test_a_private_key_is_named_as_such_and_never_repeated(pi):
    secret = "-----BEGIN OPENSSH PRIVATE KEY-----\nc2VjcmV0LXZhbHVl\n-----END OPENSSH PRIVATE KEY-----\n"
    with pytest.raises(updates.UpdateError) as caught:
        updates.trust_key(secret, config=pi.config, root=pi.root)
    assert "private key" in str(caught.value)
    assert "c2VjcmV0" not in str(caught.value)


def test_a_key_or_label_is_trusted_once(pi):
    with pytest.raises(updates.UpdateError, match="already trusted"):
        updates.trust_key(pi.public, label="again", config=pi.config, root=pi.root)
    with pytest.raises(updates.UpdateError, match="already used"):
        updates.trust_key(ed25519_line("owner@mac"), config=pi.config, root=pi.root)


def test_the_number_of_keys_is_limited(pi):
    for number in range(updates.MAX_KEYS - 1):
        updates.trust_key(ed25519_line(f"key{number}"), config=pi.config, root=pi.root)
    with pytest.raises(updates.UpdateError, match="at most"):
        updates.trust_key(ed25519_line("one-too-many"), config=pi.config, root=pi.root)
    assert len(updates.trusted_keys(pi.config)) == updates.MAX_KEYS


def test_an_interrupted_write_keeps_the_old_list_and_leaves_no_temporary_file(pi, monkeypatch):
    before = signers_text(pi)

    def interrupted(source, destination):
        raise OSError("power cut")

    monkeypatch.setattr(updates.os, "replace", interrupted)
    with pytest.raises(OSError):
        updates.trust_key(ed25519_line("second"), config=pi.config, root=pi.root)
    assert signers_text(pi) == before
    assert sorted(item.name for item in pi.config.iterdir()) == [updates.SIGNERS_NAME]


def test_a_list_that_is_a_link_or_writable_by_others_is_not_trusted(pi):
    path = pi.config / updates.SIGNERS_NAME
    path.chmod(0o664)
    with pytest.raises(updates.UpdateError, match="writable"):
        updates.trusted_keys(pi.config)
    with pytest.raises(updates.UpdateError, match="writable"):
        pi.stage(pi.release())
    path.chmod(0o644)
    pi.config.chmod(0o775)
    with pytest.raises(updates.UpdateError, match="writable"):
        updates.trusted_keys(pi.config)
    pi.config.chmod(0o755)
    real = pi.base / "real-signers"
    real.write_text(path.read_text())
    path.unlink()
    path.symlink_to(real)
    with pytest.raises(updates.UpdateError, match="link|regular"):
        updates.trusted_keys(pi.config)


@pytest.mark.parametrize("line", [
    "cert-authority " + ed25519_line(),
    'jittersim-release namespaces="*" ' + ed25519_line("x"),
    "jittersim-release " + ed25519_line("x"),
    'jittersim-release namespaces="jittersim-release" ' + RSA,
    "* " + ed25519_line("x"),
])
def test_a_hand_edited_line_stops_every_signature_check(pi, line):
    path = pi.config / updates.SIGNERS_NAME
    path.write_text(path.read_text() + line + "\n")
    with pytest.raises(updates.UpdateError, match="did not write"):
        updates.trusted_keys(pi.config)
    assert_refused(pi, pi.release(), "did not write")


def test_untrust_by_label_or_fingerprint(pi):
    _, other = make_key(pi.base / "other", comment="other")
    updates.trust_key(other, config=pi.config, root=pi.root)
    updates.untrust_key(fingerprint(other), config=pi.config, root=pi.root, sudoers=pi.sudoers)
    assert [key.label for key in updates.trusted_keys(pi.config)] == ["owner@mac"]
    with pytest.raises(updates.UpdateError, match="No trusted key"):
        updates.untrust_key("nobody", config=pi.config, root=pi.root, sudoers=pi.sudoers)
    updates.untrust_key("owner@mac", config=pi.config, root=pi.root, sudoers=pi.sudoers)
    assert updates.trusted_keys(pi.config) == []
    path = pi.config / updates.SIGNERS_NAME
    assert stat.S_IMODE(path.stat().st_mode) == 0o644
    assert key_lines(path.read_text()) == []


def test_untrusting_the_last_key_turns_passwordless_staging_off(pi):
    updates.enable_passwordless_staging(config=pi.config, sudoers=pi.sudoers, visudo=None)
    assert pi.sudoers.exists()
    messages = updates.untrust_key("owner@mac", config=pi.config, root=pi.root,
                                   sudoers=pi.sudoers)[1]
    assert not pi.sudoers.exists()
    assert any("Passwordless staging is off" in message for message in messages)


# Passwordless staging


def rule_lines(text):
    return [line for line in text.splitlines() if line.strip() and not line.startswith("#")]


def test_the_sudoers_rule_names_two_exact_commands_and_nothing_else():
    lines = rule_lines(updates.SUDOERS_TEXT)
    assert lines == [
        "jittersim ALL=(root) NOPASSWD: "
        "/usr/local/bin/jittersim update stage /home/jittersim/incoming/jittersim-release.tar.gz, "
        "/usr/local/bin/jittersim update install --yes --wait"
    ]
    rule = lines[0]
    # No wildcard, no escape, no negation and no command without arguments (which would allow
    # any arguments).
    for character in "*?[]\\!\"'":
        assert character not in rule
    commands = [command.strip() for command in rule.split("NOPASSWD:", 1)[1].split(",")]
    assert all(len(command.split()) >= 3 for command in commands)
    assert updates.INCOMING_ARCHIVE == "/home/jittersim/incoming/jittersim-release.tar.gz"
    assert updates.SUDOERS_TEXT.endswith("\n")


def test_the_sudoers_rule_passes_visudo(tmp_path):
    visudo = Path("/usr/sbin/visudo")
    if not visudo.exists():
        pytest.skip("visudo is not installed here")
    path = tmp_path / "jittersim-update"
    path.write_text(updates.SUDOERS_TEXT)
    import subprocess

    result = subprocess.run([str(visudo), "-cf", str(path)], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    # visudo does fail on a broken rule, so this check means something.
    path.write_text("jittersim ALL=(root NOPASSWD /bin/true\n")
    assert subprocess.run([str(visudo), "-cf", str(path)], capture_output=True).returncode != 0


def test_passwordless_staging_needs_a_trusted_key(pi):
    updates.untrust_key("owner@mac", config=pi.config, root=pi.root, sudoers=pi.sudoers)
    with pytest.raises(updates.UpdateError, match="trust-key"):
        updates.enable_passwordless_staging(config=pi.config, sudoers=pi.sudoers, visudo=None)
    assert list(pi.sudoers.parent.iterdir()) == []


def test_passwordless_staging_writes_the_checked_rule_read_only(pi, tmp_path):
    visudo = Path("/usr/sbin/visudo")
    updates.enable_passwordless_staging(
        config=pi.config, sudoers=pi.sudoers, visudo=str(visudo) if visudo.exists() else None
    )
    assert pi.sudoers.read_text() == updates.SUDOERS_TEXT
    assert stat.S_IMODE(pi.sudoers.stat().st_mode) == 0o440
    assert [item.name for item in pi.sudoers.parent.iterdir()] == ["jittersim-update"]
    updates.disable_passwordless_staging(sudoers=pi.sudoers)
    assert not pi.sudoers.exists()
    updates.disable_passwordless_staging(sudoers=pi.sudoers)


def test_a_rule_that_fails_the_check_is_never_installed(pi, tmp_path):
    fake = tmp_path / "visudo"
    log = tmp_path / "visudo.log"
    fake.write_text(f'#!/bin/sh\necho "$@" >> "{log}"\nexit 1\n')
    fake.chmod(0o755)
    with pytest.raises(updates.UpdateError, match="visudo"):
        updates.enable_passwordless_staging(config=pi.config, sudoers=pi.sudoers,
                                            visudo=str(fake))
    assert list(pi.sudoers.parent.iterdir()) == []
    checked = log.read_text().split()
    assert checked[0] == "-cf"
    # sudo skips files whose name has a dot so a leftover candidate is never read as a rule.
    assert "." in Path(checked[1]).name and Path(checked[1]).parent == pi.sudoers.parent


# The installer


INSTALLER = Path(__file__).resolve().parents[2] / "deploy" / "raspberry-pi" / "install.sh"


def run_installer(tmp_path, *arguments):
    """install.sh with a fake id (root) and a fake getent that stops it at the user check, so
    nothing after that check can run on this machine."""
    import subprocess

    fake = tmp_path / "bin"
    fake.mkdir(exist_ok=True)
    log = tmp_path / "installer.log"
    for name, body in (("id", 'echo 0'), ("getent", 'exit 1')):
        (fake / name).write_text(f'#!/bin/sh\necho "{name} $*" >> "{log}"\n{body}\n')
        (fake / name).chmod(0o755)
    result = subprocess.run(
        ["/bin/sh", str(INSTALLER), *arguments], capture_output=True, text=True, timeout=30,
        env={"PATH": f"{fake}:/usr/bin:/bin", "HOME": str(tmp_path)},
    )
    return result, log.read_text().split("\n") if log.exists() else []


def test_the_installer_refuses_passwordless_staging_without_a_trusted_key(tmp_path):
    if Path("/etc/jittersim/release-signers").exists():
        pytest.skip("this machine has a real trusted key list")
    result, calls = run_installer(tmp_path, "--enable-passwordless-staging")
    assert result.returncode == 1
    assert "needs a trusted release signing key first" in result.stderr
    # It stopped before the service user check and so before anything else.
    assert not any(call.startswith("getent") for call in calls)


def test_the_installer_without_the_flag_needs_no_key(tmp_path):
    result, calls = run_installer(tmp_path)
    assert "service user must be created" in result.stderr
    assert any(call.startswith("getent") for call in calls)


def test_the_installer_turns_passwordless_staging_on_only_when_asked():
    text = INSTALLER.read_text()
    lines = [line.strip() for line in text.splitlines()]
    command = "python3 -I /usr/local/lib/jittersim-update.py enable-passwordless-staging"
    assert lines.count(command) == 1
    index = lines.index(command)
    assert lines[index - 1] == 'if [ "$enable_passwordless_staging" = yes ]; then'
    assert lines[index + 1] == "fi"
    # After the manager that writes the rule is installed and never written by hand.
    assert index > lines.index(
        'install -m 0644 -o root -g root "$SOURCE_DIR/src/jittersim/updates.py" '
        "/usr/local/lib/jittersim-update.py")
    commands = "\n".join(line for line in lines if not line.startswith("#"))
    assert "sudoers" not in commands and "visudo" not in commands
    assert "NOPASSWD" not in commands
    assert lines.index("enable_passwordless_staging=no") < lines.index(
        'if [ "${1:-}" = --enable-passwordless-staging ]; then')
    # The key check comes before the Pi is changed in any way.
    assert text.index("needs a trusted release signing key first") < text.index("preflight.py")


# The command line


def run_main(argv, *, root=True, stdin=None, monkeypatch=None):
    if monkeypatch is not None:
        monkeypatch.setattr(updates, "running_as_root", lambda: root)
        if stdin is not None:
            monkeypatch.setattr(updates.sys, "stdin", io.TextIOWrapper(io.BytesIO(stdin)))
    return updates.main(argv)


@pytest.fixture
def local_pi(pi, monkeypatch):
    monkeypatch.setattr(updates, "UPDATE_ROOT", pi.root)
    monkeypatch.setattr(updates, "CODE_ROOT", pi.code)
    monkeypatch.setattr(updates, "CONFIG_ROOT", pi.config)
    monkeypatch.setattr(updates, "SUDOERS_PATH", pi.sudoers)
    monkeypatch.setattr(updates, "VISUDO", None)
    return pi


def test_trust_key_reads_a_file_or_standard_input_as_root(local_pi, monkeypatch, capsys):
    pi = local_pi
    first = ed25519_line("first")
    key_file = pi.base / "first.pub"
    key_file.write_text(first + "\n")
    assert run_main(["trust-key", str(key_file)], monkeypatch=monkeypatch) == 0
    assert "Trusted release signing key first" in capsys.readouterr().out
    second = ed25519_line("second")
    assert run_main(["trust-key", "--label", "laptop", "-"], stdin=(second + "\n").encode(),
                    monkeypatch=monkeypatch) == 0
    assert [key.label for key in updates.trusted_keys(pi.config)] == [
        "owner@mac", "first", "laptop"]


def test_key_commands_that_change_something_need_root(local_pi, monkeypatch, capsys):
    pi = local_pi
    key_file = pi.base / "first.pub"
    key_file.write_text(ed25519_line("first"))
    for argv in (["trust-key", str(key_file)], ["untrust-key", "owner@mac"],
                 ["enable-passwordless-staging"], ["disable-passwordless-staging"],
                 ["stage", str(key_file)]):
        assert run_main(argv, root=False, monkeypatch=monkeypatch) != 0
    assert "root" in capsys.readouterr().err
    assert [key.label for key in updates.trusted_keys(pi.config)] == ["owner@mac"]
    # Listing needs no root.
    assert run_main(["list-keys"], root=False, monkeypatch=monkeypatch) == 0
    output = capsys.readouterr().out
    assert "owner@mac" in output and fingerprint(pi.public) in output


def test_trust_key_can_turn_passwordless_staging_on_in_the_same_step(local_pi, monkeypatch):
    pi = local_pi
    assert run_main(["trust-key", "--enable-passwordless-staging", "-"],
                    stdin=(ed25519_line("laptop") + "\n").encode(),
                    monkeypatch=monkeypatch) == 0
    assert pi.sudoers.read_text() == updates.SUDOERS_TEXT


def test_errors_are_one_plain_line_without_a_traceback(local_pi, monkeypatch, capsys):
    pi = local_pi
    archive = pi.release()
    archive.write_bytes(archive.read_bytes() + b"x")
    assert run_main(["stage", str(archive)], monkeypatch=monkeypatch) == 1
    error = capsys.readouterr().err
    assert "Traceback" not in error
    assert "signature" in error and "Nothing was changed" in error
    assert not (pi.root / "staged").exists()


def test_stage_on_the_command_line_needs_no_checksum_or_version(local_pi, monkeypatch, capsys):
    pi = local_pi
    archive = pi.release(version="abc1234")
    assert run_main(["stage", str(archive)], monkeypatch=monkeypatch) == 0
    output = capsys.readouterr().out
    assert "abc1234" in output and "owner@mac" in output
    assert pi.record()["version"] == "abc1234"


def test_the_status_keeps_working_with_a_damaged_key_list(pi):
    (pi.config / updates.SIGNERS_NAME).write_text("garbage line\n")
    status = pi.status()
    assert status["trusted_keys"] == 0
    assert status["installable"] is False


def test_gzip_is_only_opened_on_the_verified_copy(pi, monkeypatch):
    archive = pi.release()
    opened = []
    original = updates.gzip.GzipFile

    def watch(*args, **kwargs):
        opened.append(Path(kwargs["fileobj"].name))
        return original(*args, **kwargs)

    monkeypatch.setattr(updates.gzip, "GzipFile", watch)
    pi.stage(archive)
    assert opened and all(path.parent.parent == pi.root for path in opened)
    assert gzip  # the module is used by the manager
