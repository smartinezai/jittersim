"""The decisions of the update manager, one function at a time (mutation testing, 2026-10-18).

The root update manager decides what to install: which version is newer, which sequence is the
high-water mark, which signature to believe and which archive is safe to unpack. The older tests
reach these decisions through whole installs. A mutation run showed the limits and refusals
that no install happened to cross. Each test below calls one function with the exact values on
both sides of its limit.
"""

import base64
import hashlib
import io
import json
import os
import struct
import subprocess
import tarfile

import pytest

from jittersim import updates
from jittersim.updates import UpdateError

KEY_BLOB = updates.ED25519_PREFIX + bytes(range(32))
KEY = base64.b64encode(KEY_BLOB).decode()
OTHER_BLOB = updates.ED25519_PREFIX + bytes(range(1, 33))
OTHER_KEY = base64.b64encode(OTHER_BLOB).decode()
COMMIT = "0123456789abcdef0123456789abcdef01234567"


def fingerprint(blob):
    return "SHA256:" + base64.b64encode(hashlib.sha256(blob).digest()).decode().rstrip("=")


def refuses(message, call, *args, **kwargs):
    with pytest.raises(UpdateError) as raised:
        call(*args, **kwargs)
    assert str(raised.value) == message


# Version order


def test_a_version_is_three_numbers_and_a_dev_build_comes_before_its_release():
    assert updates.version_key("1.2.3") == (1, 2, 3, 1)
    assert updates.version_key("0.0.0") == (0, 0, 0, 1)
    assert updates.version_key("10.20.30") == (10, 20, 30, 1)
    assert updates.version_key("1.2.3.dev+abcdef0") == (1, 2, 3, 0)
    assert updates.version_key("1.2.3.dev+" + "a" * 40) == (1, 2, 3, 0)
    assert updates.version_key("1.2.3.dev+abcdef0") < updates.version_key("1.2.3")
    assert updates.version_key("1.2.3") < updates.version_key("1.2.4.dev+abcdef0")
    assert updates.version_key("1.2.3") < updates.version_key("1.10.0")
    assert updates.version_key("9.9.9") < updates.version_key("10.0.0")


@pytest.mark.parametrize("version", [
    None, 5, b"1.2.3", ["1.2.3"], "", "1", "1.2", "1.2.3.4", "01.2.3", "1.02.3", "1.2.03",
    "-1.2.3", "1.2.3 ", " 1.2.3", "v1.2.3", "1.2.3.dev", "1.2.3.dev+", "1.2.3.dev+abcdef",
    "1.2.3.dev+" + "a" * 41, "1.2.3.dev+ABCDEF0", "1.2.3.dev+abcdefg", "1.2.3-dev+abcdef0",
    "1.2.3\n", "1.2.3.dev+abcdef0\n",
])
def test_a_label_that_is_not_a_number_has_no_order(version):
    assert updates.version_key(version) is None


def test_a_version_may_have_sixty_four_characters_and_no_more():
    longest = "1" + "0" * 59 + ".0.0"
    assert len(longest) == 64
    assert updates.version_key(longest) == (10 ** 59, 0, 0, 1)
    assert updates.version_key("1" + "0" * 60 + ".0.0") is None


# Sequences


def test_a_sequence_is_a_whole_number_from_one_to_below_two_to_the_fifty_third():
    top = updates.MAX_SEQUENCE
    assert top == 2 ** 53
    for good in (1, 2, 1_000_000, top - 1):
        assert updates._is_sequence(good) is True
        assert updates._is_id(good) is True
    for bad in (0, -1, top, top + 1, True, False, 1.0, 5.5, "5", None, [1]):
        assert updates._is_sequence(bad) is False
        assert updates._is_id(bad) is False


# The signed manifest


def write_manifest(tmp_path, **changes):
    data = {"format": 1, "commit": COMMIT, "version": "1.2.3", "sequence": 7}
    data.update(changes)
    path = tmp_path / "RELEASE"
    path.write_text(json.dumps(data))
    return path


def test_a_good_manifest_is_returned_as_it_is(tmp_path):
    path = write_manifest(tmp_path)
    assert updates.read_manifest(path) == {
        "format": 1, "commit": COMMIT, "version": "1.2.3", "sequence": 7}


def test_a_missing_manifest_is_reported(tmp_path):
    refuses("the release manifest is missing", updates.read_manifest, tmp_path / "none")


def test_a_manifest_may_have_4096_bytes_and_no_more(tmp_path):
    body = json.dumps({"format": 1, "commit": COMMIT, "version": "1.2.3", "sequence": 7})
    path = tmp_path / "RELEASE"
    path.write_text(body + " " * (4096 - len(body)))
    assert updates.read_manifest(path)["sequence"] == 7
    path.write_text(body + " " * (4097 - len(body)))
    refuses("the release manifest is too large", updates.read_manifest, path)


@pytest.mark.parametrize("raw", [
    b"not json", b"", b"\xff\xfe", "ünicode".encode(), b'{"format": 1', b"[1]", b"null", b'"x"',
])
def test_a_manifest_that_is_not_json_or_not_ascii_is_not_valid(tmp_path, raw):
    path = tmp_path / "RELEASE"
    path.write_bytes(raw)
    refuses("the release manifest is not valid", updates.read_manifest, path)


@pytest.mark.parametrize("changes", [
    {"format": 2}, {"format": 0}, {"format": True}, {"format": "1"}, {"format": 1.0},
    {"commit": "0" * 39}, {"commit": "0" * 41}, {"commit": "0" * 63}, {"commit": "0" * 65},
    {"commit": "A" * 40}, {"commit": "g" * 40}, {"commit": 5}, {"commit": None},
    {"version": 5}, {"version": None}, {"version": ""}, {"version": "-1"},
    {"version": "a b"}, {"version": "v" * 65},
    {"sequence": 0}, {"sequence": -1}, {"sequence": 2 ** 53}, {"sequence": True},
    {"sequence": 1.0}, {"sequence": "7"}, {"sequence": None},
])
def test_a_manifest_with_a_bad_field_is_not_valid(tmp_path, changes):
    refuses("the release manifest is not valid", updates.read_manifest,
            write_manifest(tmp_path, **changes))


def test_a_manifest_with_a_missing_or_extra_field_is_not_valid(tmp_path):
    for name in ("format", "commit", "version", "sequence"):
        data = {"format": 1, "commit": COMMIT, "version": "1.2.3", "sequence": 7}
        del data[name]
        path = tmp_path / f"without-{name}"
        path.write_text(json.dumps(data))
        refuses("the release manifest is not valid", updates.read_manifest, path)
    extra = write_manifest(tmp_path, other=1)
    refuses("the release manifest is not valid", updates.read_manifest, extra)


def test_a_manifest_accepts_the_limits_of_each_field(tmp_path):
    for changes in ({"commit": "f" * 64}, {"commit": "0" * 40}, {"version": "v" * 64},
                    {"version": "a"}, {"version": "A.b_c+d-e"}, {"sequence": 1},
                    {"sequence": 2 ** 53 - 1}):
        path = write_manifest(tmp_path, **changes)
        assert updates.read_manifest(path)[next(iter(changes))] == next(iter(changes.values()))


# The high-water mark


def floor_folders(tmp_path):
    root, code = tmp_path / "root", tmp_path / "code"
    root.mkdir()
    code.mkdir()
    return root, code


def test_the_floor_is_the_highest_valid_sequence_of_the_installed_release_and_the_record(tmp_path):
    root, code = floor_folders(tmp_path)
    assert updates.release_floor(root=root, code=code) == 0
    (code / ".jittersim-release.json").write_text(json.dumps({"sequence": 10}))
    assert updates.release_floor(root=root, code=code) == 10
    (root / updates.FLOOR_NAME).write_text(json.dumps({"sequence": 25}))
    assert updates.release_floor(root=root, code=code) == 25
    (root / updates.FLOOR_NAME).write_text(json.dumps({"sequence": 4}))
    assert updates.release_floor(root=root, code=code) == 10
    (code / ".jittersim-release.json").unlink()
    assert updates.release_floor(root=root, code=code) == 4


@pytest.mark.parametrize("bad", [0, -3, 2 ** 53, True, 1.5, "9", None, [9]])
def test_the_floor_ignores_a_sequence_that_is_not_valid(tmp_path, bad):
    root, code = floor_folders(tmp_path)
    (code / ".jittersim-release.json").write_text(json.dumps({"sequence": 6}))
    (root / updates.FLOOR_NAME).write_text(json.dumps({"sequence": bad}))
    assert updates.release_floor(root=root, code=code) == 6
    (code / ".jittersim-release.json").write_text(json.dumps({"sequence": bad}))
    (root / updates.FLOOR_NAME).write_text(json.dumps({"sequence": 3}))
    assert updates.release_floor(root=root, code=code) == 3


def test_a_record_that_is_not_an_object_counts_as_empty(tmp_path):
    root, code = floor_folders(tmp_path)
    (root / updates.FLOOR_NAME).write_text(json.dumps([5]))
    (code / ".jittersim-release.json").write_text(json.dumps("5"))
    assert updates.release_floor(root=root, code=code) == 0


def test_an_unreadable_record_is_reported_with_its_path(tmp_path):
    root, code = floor_folders(tmp_path)
    (root / updates.FLOOR_NAME).write_text("{broken")
    refuses(f"{root / updates.FLOOR_NAME} cannot be read", updates.release_floor, root=root,
            code=code)
    (root / updates.FLOOR_NAME).write_text(" " * 65537)
    refuses("update record is too large", updates.release_floor, root=root, code=code)


def stored_floor(root):
    return json.loads((root / updates.FLOOR_NAME).read_text())


def test_the_floor_only_goes_up(tmp_path):
    root, _ = floor_folders(tmp_path)
    updates._raise_floor(root, 5)
    assert stored_floor(root) == {"sequence": 5}
    updates._raise_floor(root, 5)
    updates._raise_floor(root, 4)
    assert stored_floor(root) == {"sequence": 5}
    updates._raise_floor(root, 6)
    assert stored_floor(root) == {"sequence": 6}


def test_an_equal_or_lower_sequence_does_not_rewrite_the_floor_file(tmp_path, monkeypatch):
    root, _ = floor_folders(tmp_path)
    updates._raise_floor(root, 5)
    writes = []
    real = updates.atomic_json
    monkeypatch.setattr(
        updates, "atomic_json", lambda path, value: writes.append(value) or real(path, value))
    updates._raise_floor(root, 5)
    updates._raise_floor(root, 3)
    assert writes == []
    updates._raise_floor(root, 6)
    assert writes == [{"sequence": 6}]


def test_a_floor_record_that_is_not_valid_is_replaced(tmp_path):
    root, _ = floor_folders(tmp_path)
    for bad in ({"sequence": 0}, {"sequence": "9"}, {"sequence": True}, {}, [3]):
        (root / updates.FLOOR_NAME).write_text(json.dumps(bad))
        updates._raise_floor(root, 2)
        assert stored_floor(root) == {"sequence": 2}


def test_the_floor_file_is_private_to_its_owner(tmp_path):
    root, _ = floor_folders(tmp_path)
    updates._raise_floor(root, 5)
    assert (root / updates.FLOOR_NAME).stat().st_mode & 0o777 == 0o600
    assert not (root / "release-floor.tmp").exists()


# A staged release against what is installed


def signer(blob=KEY_BLOB, key=KEY, label="owner@mac"):
    return updates.Signer(label, key, fingerprint(blob))


def staged(**changes):
    record = {"version": "1.2.4", "sequence": 10, "signer": signer().fingerprint}
    record.update(changes)
    return record


def installed(code, version, sequence):
    (code / ".jittersim-release.json").write_text(
        json.dumps({"version": version, "sequence": sequence}))


def problem(tmp_path, record, signers=None, floor=None, version=None):
    # Each call gets a folder of its own, so one test can ask several questions.
    tmp_path = tmp_path / f"case-{len(list(tmp_path.glob('case-*')))}"
    tmp_path.mkdir()
    root, code = floor_folders(tmp_path)
    if floor is not None:
        (root / updates.FLOOR_NAME).write_text(json.dumps({"sequence": floor}))
    if version is not None:
        installed(code, version[0], version[1])
    return updates.staged_problem(
        record, [signer()] if signers is None else signers, root=root, code=code)


def test_a_signed_newer_release_may_be_installed(tmp_path):
    assert problem(tmp_path, staged()) is None
    assert problem(tmp_path, staged(sequence=11), floor=10) is None


def test_a_staged_release_without_a_signer_or_a_sequence_is_refused(tmp_path):
    unsigned = "This release was staged without a signature. Stage a signed release."
    for record in (
        staged(signer=""), staged(signer=None), {"version": "1.2.4", "sequence": 10},
        staged(sequence=0), staged(sequence="10"), staged(sequence=None), staged(sequence=True),
        staged(sequence=2 ** 53),
    ):
        assert problem(tmp_path, record) == unsigned


def test_a_release_signed_by_a_key_that_is_no_longer_trusted_is_refused(tmp_path):
    message = "The key that signed the staged release is no longer trusted."
    assert problem(tmp_path, staged(signer=fingerprint(OTHER_BLOB))) == message
    assert problem(tmp_path, staged(), signers=[]) == message
    assert problem(tmp_path, staged(), signers=[signer(OTHER_BLOB, OTHER_KEY), signer()]) is None


def test_a_release_that_is_not_newer_than_the_floor_is_refused(tmp_path):
    message = "The staged release is not newer than the installed release."
    for sequence, floor in ((10, 10), (9, 10), (1, 50)):
        assert problem(tmp_path, staged(sequence=sequence), floor=floor) == message
    assert problem(tmp_path, staged(sequence=11), floor=10) is None
    assert problem(tmp_path, staged(sequence=10), version=("1.2.4", 10)) == message
    assert problem(tmp_path, staged(sequence=11), version=("1.2.4", 10)) is None


def test_a_release_with_an_older_number_than_the_installed_one_is_refused(tmp_path):
    message = ("The staged release is an older number than the installed one. "
               "Use Restore previous installation to go back.")
    cases = (
        ("1.2.3", "1.2.4", True), ("1.2.4", "1.2.4", False), ("1.2.5", "1.2.4", False),
        ("1.2.4.dev+abcdef0", "1.2.4", True), ("1.2.4", "1.2.4.dev+abcdef0", False),
        ("0.9.9", "1.0.0", True), ("1.10.0", "1.9.0", False),
    )
    for new, old, older in cases:
        got = problem(tmp_path, staged(version=new, sequence=20), version=(old, 10))
        assert got == (message if older else None), (new, old)


def test_a_version_without_an_order_is_never_called_older(tmp_path):
    for new, old in (("custom", "1.2.4"), ("1.2.3", "custom"), (None, "1.2.4"), ("1.2.3", None)):
        record = staged(version=new, sequence=20)
        if new is None:
            del record["version"]
        assert problem(tmp_path, record, version=(old, 10)) is None


# Keys


def test_a_fingerprint_is_the_base64_of_the_digest_without_padding():
    assert updates.key_fingerprint(KEY) == fingerprint(KEY_BLOB)
    assert updates.key_fingerprint(KEY).startswith("SHA256:")
    assert not updates.key_fingerprint(KEY).endswith("=")
    assert len(updates.key_fingerprint(KEY)) == len("SHA256:") + 43
    assert updates.key_fingerprint(KEY) != updates.key_fingerprint(OTHER_KEY)


def test_key_text_is_one_ed25519_key_of_sixty_eight_characters():
    assert updates._check_key_text(KEY) is True
    assert updates._check_key_text(OTHER_KEY) is True
    assert not updates._check_key_text(KEY[:-1])
    assert not updates._check_key_text(KEY + "A")
    assert not updates._check_key_text(KEY[:-1] + "!")
    assert not updates._check_key_text("")
    rsa = struct.pack(">I", 7) + b"ssh-rsa" + struct.pack(">I", 32) + bytes(32)
    assert not updates._check_key_text(base64.b64encode(rsa + bytes(51 - len(rsa))).decode())
    assert not updates._check_key_text(base64.b64encode(bytes(51)).decode())
    assert not updates._check_key_text(KEY + "\n")


def line(key=KEY, comment="owner@mac", kind="ssh-ed25519"):
    return f"{kind} {key} {comment}".strip()


def test_a_public_key_line_gives_a_signer():
    assert updates.parse_public_key(line()) == updates.Signer(
        "owner@mac", KEY, fingerprint(KEY_BLOB))
    assert updates.parse_public_key(line(comment=""), label="mine").label == "mine"
    assert updates.parse_public_key(line() + "\n").label == "owner@mac"
    assert updates.parse_public_key("# note\n\n" + line() + "\n# end\n").key == KEY
    assert updates.parse_public_key(line() + "\r\n").key == KEY
    assert updates.parse_public_key("  " + line() + "  ").key == KEY
    assert updates.parse_public_key(line(comment="") + "\tcomment").label == "comment"
    assert updates.parse_public_key(line(comment="one two"), label="x").label == "x"


def test_a_public_key_file_may_have_16_kib_and_no_more():
    padding = "#" * 16000
    text = padding + "\n" + line() + "\n"
    text += "#" * (updates.MAX_KEY_FILE - len(text) - 1) + "\n"
    assert len(text) == updates.MAX_KEY_FILE
    assert updates.parse_public_key(text).key == KEY
    refuses("the key file is too large to be one public key", updates.parse_public_key,
            text + "#")


def test_a_private_key_is_refused_with_a_hint():
    refuses("this is a private key. Give the public key (the .pub file) and keep the private "
            "key on your Mac", updates.parse_public_key,
            "-----BEGIN OPENSSH PRIVATE KEY-----\nabc\n-----END OPENSSH PRIVATE KEY-----\n")


@pytest.mark.parametrize("text", ["ssh-ed25519 \x00 x", "ssh-ed25519 \x7f x",
                                  "ssh-ed25519 é x", "ssh-ed25519 \x1b x"])
def test_odd_characters_are_refused(text):
    refuses("the key file holds characters that a public key never has", updates.parse_public_key,
            text)


def test_every_printable_ascii_character_may_be_in_the_comment_of_a_key():
    comment = "".join(chr(code) for code in range(33, 127))

    assert updates.parse_public_key(f"ssh-ed25519 {KEY} {comment}", label="owner").key == KEY


def test_a_key_file_holds_exactly_one_key():
    for text in ("", "# only a note\n", "\n\n", line() + "\n" + line(OTHER_KEY)):
        refuses("the key file must hold exactly one public key", updates.parse_public_key, text)


def test_only_a_plain_ed25519_key_is_accepted():
    message = ("only a plain ssh-ed25519 public key can sign releases: no options, "
               "certificates or other key types")
    for text in (line(kind="ssh-rsa"), line(kind="ecdsa-sha2-nistp256"),
                 'command="x" ' + line(), "ssh-ed25519", "ssh-ed25519-cert-v01@openssh.com " + KEY,
                 "SSH-ED25519 " + KEY):
        refuses(message, updates.parse_public_key, text)


def test_a_damaged_key_is_refused():
    for key in (KEY[:-4], KEY + "AA", "A" * 68, "!" * 68, "x"):
        refuses("the ssh-ed25519 key is damaged", updates.parse_public_key, line(key))


def test_a_label_has_one_to_sixty_four_safe_characters():
    assert updates.parse_public_key(line(comment="a" * 64)).label == "a" * 64
    assert updates.parse_public_key(line(), label="A.b_c@d-e").label == "A.b_c@d-e"
    refuses("the key's comment cannot be used as its label. Give one with --label NAME",
            updates.parse_public_key, line(comment="a" * 65))
    refuses("the key's comment cannot be used as its label. Give one with --label NAME",
            updates.parse_public_key, line(comment="-bad"))
    refuses("the key's comment cannot be used as its label. Give one with --label NAME",
            updates.parse_public_key, line(comment=""))
    message = ("the label must be 1 to 64 letters, digits, dots, dashes, underscores or @ "
               "and start with a letter or digit")
    for label in ("", "a" * 65, "-x", "a b", "a/b", ".x"):
        refuses(message, updates.parse_public_key, line(), label=label)


def test_the_allowed_signers_file_names_the_principal_the_namespace_and_the_key():
    assert updates._signer_lines([]) == ""
    one = updates._signer_lines([signer()])
    assert one == f'jittersim-release namespaces="jittersim-release" ssh-ed25519 {KEY} owner@mac\n'
    two = updates._signer_lines([signer(), signer(OTHER_BLOB, OTHER_KEY, "second")])
    assert two == one + (
        f'jittersim-release namespaces="jittersim-release" ssh-ed25519 {OTHER_KEY} second\n')


# The trusted key list


@pytest.fixture
def config(tmp_path, monkeypatch):
    folder = tmp_path / "config"
    folder.mkdir()
    folder.chmod(0o755)
    monkeypatch.setattr(updates, "TRUSTED_OWNER_UID", os.geteuid())
    return folder


def write_list(folder, text, mode=0o644):
    path = folder / updates.SIGNERS_NAME
    path.write_bytes(text if isinstance(text, bytes) else text.encode())
    path.chmod(mode)
    return path


def signer_line(key=KEY, label="owner@mac"):
    return (f'jittersim-release namespaces="jittersim-release" ssh-ed25519 {key} {label}\n')


def test_there_is_no_trusted_key_without_a_list(config):
    assert updates.trusted_keys(config) == []


def test_the_trusted_keys_are_the_lines_the_manager_wrote(config):
    write_list(config, updates.SIGNERS_HEADER + signer_line() + "\n"
               + signer_line(OTHER_KEY, "second") + "\n")
    assert updates.trusted_keys(config) == [
        updates.Signer("owner@mac", KEY, fingerprint(KEY_BLOB)),
        updates.Signer("second", OTHER_KEY, fingerprint(OTHER_BLOB)),
    ]


def test_a_list_that_is_a_link_is_refused(config, tmp_path):
    target = tmp_path / "target"
    target.write_text(signer_line())
    (config / updates.SIGNERS_NAME).symlink_to(target)
    path = config / updates.SIGNERS_NAME
    refuses(f"the trusted key list {path} must be a regular file, not a link",
            updates.trusted_keys, config)


def test_a_list_or_folder_that_others_can_write_is_refused(config):
    path = write_list(config, signer_line())
    for mode in (0o666, 0o664, 0o646, 0o622, 0o602, 0o620):
        path.chmod(mode)
        refuses(f"the trusted key list {path} must belong to root and must not be writable by "
                "others", updates.trusted_keys, config)
    path.chmod(0o644)
    config.chmod(0o775)
    refuses(f"{config} must belong to root and must not be writable by others",
            updates.trusted_keys, config)
    config.chmod(0o757)
    refuses(f"{config} must belong to root and must not be writable by others",
            updates.trusted_keys, config)
    config.chmod(0o755)
    assert len(updates.trusted_keys(config)) == 1
    path.chmod(0o640)
    assert len(updates.trusted_keys(config)) == 1


def test_a_list_or_folder_of_another_owner_is_refused(config, monkeypatch):
    path = write_list(config, signer_line())
    monkeypatch.setattr(updates, "TRUSTED_OWNER_UID", os.geteuid() + 1)
    refuses(f"the trusted key list {path} must belong to root and must not be writable by "
            "others", updates.trusted_keys, config)
    monkeypatch.setattr(updates, "TRUSTED_OWNER_UID", os.geteuid())
    real = os.stat

    def stat_of_another_owner(target, *args, **kwargs):
        result = real(target, *args, **kwargs)
        if str(target) == str(config):
            values = list(result)
            values[4] = os.geteuid() + 1
            return os.stat_result(values)
        return result

    monkeypatch.setattr(os, "stat", stat_of_another_owner)
    refuses(f"{config} must belong to root and must not be writable by others",
            updates.trusted_keys, config)


def test_a_list_with_a_line_the_manager_did_not_write_is_refused(config):
    path = config / updates.SIGNERS_NAME
    damaged = (f"{path} has a line JitterSim did not write. Remove that line or the file and "
               "trust the keys again with trust-key")
    for text in (
        "garbage\n", signer_line() + "extra line\n", signer_line()[:-1] + " more\n",
        signer_line(KEY[:-1] + "!"), signer_line("A" * 68),
        signer_line().replace("jittersim-release namespaces", "other namespaces"),
        signer_line(label="-bad"), " " + signer_line(), signer_line().rstrip("\n") + "\r\n",
        b"\xff\xfe\n", signer_line("é" * 68),
    ):
        write_list(config, text)
        refuses(damaged, updates.trusted_keys, config)


def test_a_list_of_65536_bytes_is_read_and_one_more_is_not(config):
    path = config / updates.SIGNERS_NAME
    comment = "#" * (65536 - len(signer_line()) - 1) + "\n"
    write_list(config, signer_line() + comment)
    assert path.stat().st_size == 65536
    assert len(updates.trusted_keys(config)) == 1
    write_list(config, signer_line() + comment + "#")
    refuses(f"{path} has a line JitterSim did not write. Remove that line or the file and "
            "trust the keys again with trust-key", updates.trusted_keys, config)


def test_a_list_that_is_swapped_for_a_link_after_the_check_is_not_read(config, tmp_path,
                                                                      monkeypatch):
    path = write_list(config, signer_line())
    other = tmp_path / "other-list"
    other.write_text(signer_line(OTHER_KEY, "other"))
    real = os.lstat
    swapped = []

    def swap_after_the_check(target, *args, **kwargs):
        result = real(target, *args, **kwargs)
        if str(target) == str(path) and not swapped:
            path.unlink()
            path.symlink_to(other)
            swapped.append(True)
        return result

    monkeypatch.setattr(os, "lstat", swap_after_the_check)

    # The list was a plain file when it was checked. The open must not follow the new link.
    with pytest.raises((OSError, UpdateError)):
        updates.trusted_keys(config)
    assert swapped


# The saved trust settings


def test_a_missing_trust_file_is_fine(tmp_path):
    assert updates._check_trust_file(tmp_path / "none") is None


def test_a_trust_file_must_be_a_private_regular_file_of_the_administrator(tmp_path):
    message = ("saved current trust settings must be regular administrator-owned files and "
               "must not be writable by others")
    path = tmp_path / updates.SIGNERS_NAME
    path.write_text("x")
    for mode in (0o644, 0o640, 0o600):
        path.chmod(mode)
        assert updates._check_trust_file(path) is None
    for mode in (0o666, 0o664, 0o646, 0o622, 0o602, 0o620):
        path.chmod(mode)
        refuses(message, updates._check_trust_file, path)
    link = tmp_path / "link"
    link.symlink_to(path)
    refuses(message, updates._check_trust_file, link)
    folder = tmp_path / "folder"
    folder.mkdir()
    refuses(message, updates._check_trust_file, folder)


def test_a_trust_file_of_another_owner_is_refused(tmp_path, monkeypatch):
    path = tmp_path / updates.SIGNERS_NAME
    path.write_text("x")
    path.chmod(0o644)
    monkeypatch.setattr(os, "geteuid", lambda: os.stat(path).st_uid + 1)
    refuses("saved current trust settings must be regular administrator-owned files and must "
            "not be writable by others", updates._check_trust_file, path)


def test_the_update_token_must_be_private(tmp_path):
    path = tmp_path / updates.TOKEN_NAME
    path.write_text("token")
    path.chmod(0o600)
    assert updates._check_trust_file(path) is None
    path.chmod(0o400)
    assert updates._check_trust_file(path) is None
    for mode in (0o640, 0o604, 0o644, 0o650, 0o605, 0o610, 0o601):
        path.chmod(mode)
        refuses("the saved current update token must be private to the administrator",
                updates._check_trust_file, path)


# Reading a signature


def signature_text(public_blob=KEY_BLOB, namespace=b"jittersim-release", version=1):
    def field(value):
        return struct.pack(">I", len(value)) + value

    blob = (b"SSHSIG" + struct.pack(">I", version) + field(public_blob) + field(namespace)
            + field(b"") + field(b"sha512") + field(b"signature bytes"))
    encoded = base64.b64encode(blob).decode()
    body = "\n".join(encoded[number:number + 70] for number in range(0, len(encoded), 70))
    return f"-----BEGIN SSH SIGNATURE-----\n{body}\n-----END SSH SIGNATURE-----\n"


def test_the_key_and_the_namespace_are_read_from_a_signature():
    assert updates._signature_fields(signature_text().encode()) == (KEY, "jittersim-release")
    assert updates._signature_fields(signature_text(OTHER_BLOB, b"other").encode()) == (
        OTHER_KEY, "other")
    assert updates._signature_fields(("\n" + signature_text() + "\n\n").encode()) == (
        KEY, "jittersim-release")
    assert updates._signature_fields(signature_text().replace("\n", "\r\n").encode()) == (
        KEY, "jittersim-release")


@pytest.mark.parametrize("raw", [
    b"", b"\n", b"hello", "é".encode(), b"-----BEGIN SSH SIGNATURE-----\n",
    b"-----BEGIN SSH SIGNATURE-----\n-----END SSH SIGNATURE-----\n",
    b"-----BEGIN SSH SIGNATURE-----\n!!!!\n-----END SSH SIGNATURE-----\n",
    b"-----BEGIN SSH SIGNATURE-----\nAAAA\n-----END SSH SIGNATURE-----\n",
    b"-----BEGIN PGP SIGNATURE-----\nAAAA\n-----END PGP SIGNATURE-----\n",
])
def test_something_that_is_not_a_signature_has_no_fields(raw):
    assert updates._signature_fields(raw) == (None, None)


def test_a_signature_with_the_wrong_header_footer_or_magic_has_no_fields():
    good = signature_text()
    assert updates._signature_fields(good.replace("BEGIN", "BEGIN X").encode()) == (None, None)
    assert updates._signature_fields(good.replace("END SSH", "END X SSH").encode()) == (
        None, None)
    blob = base64.b64decode("".join(good.splitlines()[1:-1]))
    for broken in (b"SSHSIX" + blob[6:], b"sshsig" + blob[6:], blob[1:]):
        encoded = base64.b64encode(broken).decode()
        text = f"-----BEGIN SSH SIGNATURE-----\n{encoded}\n-----END SSH SIGNATURE-----\n"
        assert updates._signature_fields(text.encode()) == (None, None)


def test_a_namespace_that_is_not_ascii_has_no_fields():
    assert updates._signature_fields(
        signature_text(namespace="é".encode()).encode()) == (None, None)


def test_a_signature_with_a_stray_character_in_its_body_is_not_read():
    # ssh-keygen and this reader must agree on what a signature is: a stray character that a
    # forgiving base64 reader would skip makes it unreadable.
    lines = signature_text().splitlines()
    lines[1] = lines[1][:10] + "!" + lines[1][10:]

    assert updates._signature_fields("\n".join(lines).encode()) == (None, None)


# Deciding who signed


class Done:
    def __init__(self, code):
        self.returncode = code


@pytest.fixture
def check(tmp_path, monkeypatch):
    """Run verify_signature with a stand-in for ssh-keygen and a signature written by hand."""

    calls = []

    def run(returncode=0, signature=None, signers=None):
        archive = tmp_path / "release.tar"
        archive.write_bytes(b"archive bytes")
        signed = tmp_path / "release.sig"
        signed.write_text(signature_text() if signature is None else signature)
        work = tmp_path / "work"
        work.mkdir(exist_ok=True)

        def fake(command, **kwargs):
            calls.append((command, kwargs))
            return Done(returncode)

        monkeypatch.setattr(updates.subprocess, "run", fake)
        return updates.verify_signature(
            archive, signed, [signer()] if signers is None else signers, work)

    run.calls = calls
    run.work = tmp_path / "work"
    return run


def test_a_signature_that_ssh_keygen_accepts_names_the_trusted_key_that_made_it(check):
    result = check()
    assert result == signer()
    command, kwargs = check.calls[0]
    allowed = check.work / "allowed-signers"
    assert command == [updates.SSH_KEYGEN, "-Y", "verify", "-f", str(allowed), "-I",
                       "jittersim-release", "-n", "jittersim-release", "-s",
                       str(check.work.parent / "release.sig")]
    assert kwargs["timeout"] == 120
    assert kwargs["check"] is False
    assert kwargs["env"] == {"PATH": "/usr/bin:/bin", "LC_ALL": "C"}
    assert kwargs["stdout"] == subprocess.PIPE and kwargs["stderr"] == subprocess.PIPE
    assert allowed.read_text() == updates._signer_lines([signer()])
    assert allowed.stat().st_mode & 0o777 == 0o600


def test_the_archive_is_fed_to_ssh_keygen_and_the_right_key_is_returned(check):
    two = [signer(OTHER_BLOB, OTHER_KEY, "other"), signer()]
    assert check(signers=two) == signer()
    assert check(signature=signature_text(OTHER_BLOB), signers=two) == two[0]


def test_a_failed_check_or_an_unknown_key_or_another_namespace_is_a_bad_signature(check):
    refuses(updates.BAD_SIGNATURE, check, returncode=1)
    refuses(updates.BAD_SIGNATURE, check, returncode=255)
    refuses(updates.BAD_SIGNATURE, check, signature=signature_text(OTHER_BLOB))
    refuses(updates.BAD_SIGNATURE, check, signature=signature_text(namespace=b"other"))
    refuses(updates.BAD_SIGNATURE, check, signature="not a signature")
    refuses(updates.BAD_SIGNATURE, check, signature=signature_text(namespace=b"jittersim-releases"))


def test_no_trusted_key_means_no_check(check):
    refuses(updates.NO_KEY, check, signers=[])
    assert check.calls == []


def test_a_missing_or_broken_ssh_keygen_is_reported(check, monkeypatch, tmp_path):
    def missing(command, **kwargs):
        raise FileNotFoundError(command[0])

    def broken(command, **kwargs):
        raise PermissionError(command[0])

    def timed_out(command, **kwargs):
        raise subprocess.TimeoutExpired(command, 120)

    check()
    monkeypatch.setattr(updates.subprocess, "run", missing)
    refuses(f"{updates.SSH_KEYGEN} is missing. Install openssh-client",
            updates.verify_signature, tmp_path / "release.tar", tmp_path / "release.sig",
            [signer()], check.work)
    for failure in (broken, timed_out):
        monkeypatch.setattr(updates.subprocess, "run", failure)
        refuses("the signature check could not run", updates.verify_signature,
                tmp_path / "release.tar", tmp_path / "release.sig", [signer()], check.work)


# The archive


def required_names():
    names = {
        "pyproject.toml", "README.md", updates.MANIFEST, "src/jittersim/api/server.py",
        "src/jittersim/updates.py", updates.PREFLIGHT, updates.EGRESS_CHECK, updates.BOOTSTRAP,
        updates.FIREWALL_TEMPLATE,
    }
    names.update(source for source, _, _ in updates.release_files(
        code=updates.CODE_ROOT, units=updates.UNITS_ROOT, dispatcher=updates.DISPATCHER_PATH))
    return sorted(names)


def member(name, kind=tarfile.REGTYPE, size=1, link=""):
    info = tarfile.TarInfo(name)
    info.type = kind
    info.size = size
    info.linkname = link
    return info


def archive(extra=(), without=(), sizes=None):
    names = [name for name in required_names() if name not in without]
    return [member(name, size=(sizes or {}).get(name, 1)) for name in names] + list(extra)


UNSAFE = "release archive contains an unsafe or unexpected entry"


def test_a_complete_archive_is_accepted_and_all_members_are_returned():
    members = archive()
    assert updates.validate_archive(members) == members
    assert len(required_names()) > 15
    more = archive([member("src/jittersim/extra.py"), member("deploy/extra", tarfile.DIRTYPE)])
    assert updates.validate_archive(more) == more


def test_the_file_name_limit_is_240_characters():
    name = "src/" + "a" * 236
    assert len(name) == 240
    assert updates.validate_archive(archive([member(name)]))
    refuses(UNSAFE, updates.validate_archive, archive([member(name + "a")]))


@pytest.mark.parametrize("extra", [
    member("/etc/passwd"), member("src/../x"), member("../src/x"), member("src/a/../../x"),
    member("src/link", tarfile.SYMTYPE, link="/etc"), member("src/hard", tarfile.LNKTYPE,
                                                             link="src/x"),
    member("src/dev", tarfile.CHRTYPE), member("src/block", tarfile.BLKTYPE),
    member("src/fifo", tarfile.FIFOTYPE), member("other.txt"), member("etc/x"),
    member("srcx/y"), member("README.md", tarfile.DIRTYPE), member("pyproject.toml/x"),
    member("README.md/x"), member("deploy/RELEASE"), member("src/jittersim/updates.py"),
    member(""), member("."),
])
def test_an_unsafe_or_unexpected_entry_is_refused(extra):
    refuses(UNSAFE, updates.validate_archive, archive([extra]))


def test_a_folder_may_not_be_a_file_and_a_file_may_not_hold_entries():
    refuses(UNSAFE, updates.validate_archive,
            archive([member("src/jittersim/api/server.py/more")]))
    refuses(UNSAFE, updates.validate_archive,
            archive([member("src/new", tarfile.REGTYPE), member("src/new/inside")]))
    refuses(UNSAFE, updates.validate_archive,
            archive([member("src/new/inside"), member("src/new", tarfile.REGTYPE)]))
    assert updates.validate_archive(
        archive([member("src/folder", tarfile.DIRTYPE), member("src/folder/file")]))


def test_a_top_level_readme_or_pyproject_must_be_a_file_with_nothing_beside_it():
    assert updates.validate_archive(archive())
    refuses(UNSAFE, updates.validate_archive, archive([member("README.md.bak")]))
    refuses(UNSAFE, updates.validate_archive, archive([member("sub/README.md")]))


def test_the_size_limit_counts_all_members_together():
    limit = updates.MAX_EXPANDED
    assert limit == 100 * 1024 * 1024
    name = required_names()[0]
    assert updates.validate_archive(archive(sizes={name: limit - 1 - (len(required_names()) - 1)}))
    exactly = limit - (len(required_names()) - 1)
    assert updates.validate_archive(archive(sizes={name: exactly}))
    refuses("release archive exceeds its size or file limit", updates.validate_archive,
            archive(sizes={name: exactly + 1}))
    half = {required_names()[0]: 60 * 1024 * 1024, required_names()[1]: 60 * 1024 * 1024}
    refuses("release archive exceeds its size or file limit", updates.validate_archive,
            archive(sizes=half))


def test_the_member_limit_is_5000():
    filler = len(required_names())
    members = archive([member(f"src/f{number}") for number in range(5000 - filler)])
    assert len(members) == 5000
    assert updates.validate_archive(members) == members
    refuses("release archive exceeds its size or file limit", updates.validate_archive,
            archive([member(f"src/f{number}") for number in range(5001 - filler)]))


def test_every_required_file_is_required():
    for name in required_names():
        refuses("release archive is missing required files", updates.validate_archive,
                archive(without=(name,)))
    # A folder of that name is not the file.
    refuses("release archive is missing required files", updates.validate_archive,
            archive([member("src/jittersim/api/server.py", tarfile.DIRTYPE)],
                    without=("src/jittersim/api/server.py",)))


def test_the_names_of_an_archive_made_by_tarfile_are_read_the_same_way():
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as built:
        for name in required_names():
            info = tarfile.TarInfo(name)
            info.size = 3
            built.addfile(info, io.BytesIO(b"abc"))
    buffer.seek(0)
    with tarfile.open(fileobj=buffer, mode="r") as reread:
        assert len(updates.validate_archive(reread)) == len(required_names())


# Reading GitHub's answer


def release_body(**changes):
    data = {
        "tag_name": "v1.2.3", "id": 55, "draft": False, "prerelease": False,
        "assets": [
            {"name": "jittersim-release.tar.gz", "id": 101, "size": 1000},
            {"name": "jittersim-release.tar.gz.sig", "id": 102, "size": 200},
        ],
    }
    data.update(changes)
    return json.dumps(data).encode()


BROKEN = "GitHub's answer is not a release JitterSim understands"


def test_a_final_release_with_two_files_is_understood():
    assert updates._parse_release(release_body()) == {
        "tag": "v1.2.3", "id": 55, "archive": 101, "signature": 102,
        "fingerprint": "55:101:102:v1.2.3"}


def test_the_assets_may_come_in_any_order():
    assets = list(reversed(json.loads(release_body())["assets"]))
    assert updates._parse_release(release_body(assets=assets))["archive"] == 101


@pytest.mark.parametrize("body", [b"not json", b"\xff", b"[1]", b"null", b"5", b'"x"'])
def test_an_answer_that_is_not_a_release_object_is_not_understood(body):
    with pytest.raises(updates.OnlineError) as raised:
        updates._parse_release(body)
    assert str(raised.value) == BROKEN


@pytest.mark.parametrize("changes", [
    {"tag_name": 5}, {"tag_name": None}, {"tag_name": ""}, {"tag_name": "-x"},
    {"tag_name": "a" * 65}, {"tag_name": "a b"}, {"id": 0}, {"id": True}, {"id": "55"},
    {"id": 2 ** 53}, {"id": None}, {"assets": None}, {"assets": "x"}, {"assets": [5]},
    {"assets": [{"name": "jittersim-release.tar.gz", "id": 0, "size": 1},
                {"name": "jittersim-release.tar.gz.sig", "id": 2, "size": 1}]},
    {"assets": [{"name": "jittersim-release.tar.gz", "id": 1, "size": -1},
                {"name": "jittersim-release.tar.gz.sig", "id": 2, "size": 1}]},
    {"assets": [{"name": "jittersim-release.tar.gz", "id": 1, "size": "1"},
                {"name": "jittersim-release.tar.gz.sig", "id": 2, "size": 1}]},
    {"assets": [{"name": "jittersim-release.tar.gz", "id": 1, "size": True},
                {"name": "jittersim-release.tar.gz.sig", "id": 2, "size": 1}]},
    {"assets": [{"name": "jittersim-release.tar.gz", "id": 1, "size": 1},
                {"name": "jittersim-release.tar.gz.sig", "id": True, "size": 1}]},
])
def test_a_release_with_a_bad_field_is_not_understood(changes):
    with pytest.raises(updates.OnlineError) as raised:
        updates._parse_release(release_body(**changes))
    assert str(raised.value) == BROKEN


def test_a_draft_or_a_prerelease_is_not_final():
    for changes in ({"draft": True}, {"prerelease": True}, {"draft": None}, {"prerelease": 0},
                    {"draft": "false"}):
        with pytest.raises(updates.OnlineError) as raised:
            updates._parse_release(release_body(**changes))
        assert str(raised.value) == "the newest GitHub release is not a final release"
    data = json.loads(release_body())
    del data["draft"]
    with pytest.raises(updates.OnlineError, match="not a final release"):
        updates._parse_release(json.dumps(data).encode())


def test_the_release_must_hold_exactly_the_two_named_files():
    message = ("the newest GitHub release must hold exactly two files named "
               "jittersim-release.tar.gz and jittersim-release.tar.gz.sig")
    good = json.loads(release_body())["assets"]
    for assets in ([], good[:1], good + [{"name": "extra", "id": 3, "size": 1}],
                   [good[0], dict(good[1], name="other.sig")],
                   [good[0], good[0]]):
        with pytest.raises(updates.OnlineError) as raised:
            updates._parse_release(release_body(assets=assets))
        assert str(raised.value) == message


def test_the_two_files_have_size_limits():
    good = json.loads(release_body())["assets"]

    def with_sizes(archive_size, signature_size):
        return release_body(assets=[dict(good[0], size=archive_size),
                                    dict(good[1], size=signature_size)])

    assert updates._parse_release(with_sizes(updates.MAX_ARCHIVE, updates.MAX_SIGNATURE))
    assert updates._parse_release(with_sizes(0, 0))
    with pytest.raises(updates.OnlineError) as raised:
        updates._parse_release(with_sizes(updates.MAX_ARCHIVE + 1, 1))
    assert str(raised.value) == "the release archive is too large"
    with pytest.raises(updates.OnlineError) as raised:
        updates._parse_release(with_sizes(1, updates.MAX_SIGNATURE + 1))
    assert str(raised.value) == "the release signature is too large"


def test_the_fingerprint_of_a_release_changes_with_each_part_of_it():
    base = updates._parse_release(release_body())["fingerprint"]
    good = json.loads(release_body())["assets"]
    other_archive = release_body(assets=[dict(good[0], id=111), good[1]])
    other_signature = release_body(assets=[good[0], dict(good[1], id=112)])
    prints = {base, updates._parse_release(release_body(id=56))["fingerprint"],
              updates._parse_release(release_body(tag_name="v1.2.4"))["fingerprint"],
              updates._parse_release(other_archive)["fingerprint"],
              updates._parse_release(other_signature)["fingerprint"]}
    assert len(prints) == 5
