"""Filesystem ownership survives caller timeout, restart and untrusted upload state."""

import json
import os
from pathlib import Path

import pytest

from jittersim import release_uploads as uploads
from jittersim.updates import UPLOAD_ARCHIVE, UPLOAD_SIGNATURE, UpdateError


@pytest.fixture
def handoff(tmp_path, monkeypatch):
    clock = {"now": 100.0, "boot": "a" * 32}
    monkeypatch.setattr(uploads, "_now", lambda: clock["now"])
    monkeypatch.setattr(uploads, "_boot_id", lambda: clock["boot"])
    lock = tmp_path / "release-upload.lock"
    uploads.prepare_lock(lock)
    return tmp_path / "spool", lock, tmp_path / "root", clock


def received(handoff):
    spool, lock, _, _ = handoff
    with uploads.receiving(spool, lock) as receipt:
        (receipt.folder / UPLOAD_ARCHIVE).write_bytes(b"test archive")
        (receipt.folder / UPLOAD_SIGNATURE).write_bytes(b"test signature")
        receipt.ready()
        return receipt.identifier


def root_inputs(handoff, identifier):
    spool, lock, root, _ = handoff
    return uploads.root_inputs(identifier, spool=spool, lock=lock, root=root)


def test_ready_inputs_reserve_admission_and_are_cleaned_after_root_finishes(handoff):
    identifier = received(handoff)
    spool, lock, _, _ = handoff
    with pytest.raises(UpdateError, match="another release"):
        with uploads.receiving(spool, lock):
            pass
    with root_inputs(handoff, identifier) as (archive, signature):
        assert archive.read_bytes() == b"test archive"
        assert signature.read_bytes() == b"test signature"
        assert archive.parent.stat().st_mode & 0o777 == 0o700
        assert uploads.discard(identifier, spool=spool, lock=lock) is False
    assert not list(spool.glob("*/"))
    assert not (spool / ".upload.json").exists()


def test_active_root_keeps_inputs_after_lease_expiry(handoff):
    identifier = received(handoff)
    spool, lock, _, clock = handoff
    with root_inputs(handoff, identifier) as (archive, _):
        clock["now"] = 1000.0
        assert uploads.discard(identifier, spool=spool, lock=lock) is False
        with pytest.raises(UpdateError, match="another release"):
            with uploads.receiving(spool, lock):
                pass
        assert archive.read_bytes() == b"test archive"
        assert (spool / identifier / UPLOAD_ARCHIVE).exists()


def test_late_root_call_cannot_start_after_an_uncertain_call_is_discarded(handoff):
    identifier = received(handoff)
    spool, lock, _, _ = handoff
    assert uploads.discard(identifier, spool=spool, lock=lock)
    replacement = received(handoff)
    with pytest.raises(UpdateError, match="waiting|changed"):
        with root_inputs(handoff, identifier):
            pass
    with root_inputs(handoff, replacement) as (archive, _):
        assert archive.read_bytes() == b"test archive"


def test_expired_ready_handoff_can_be_replaced_but_never_started(handoff):
    identifier = received(handoff)
    spool, lock, _, clock = handoff
    clock["now"] += 121
    with pytest.raises(UpdateError, match="expired"):
        with root_inputs(handoff, identifier):
            pass
    received(handoff)
    assert not (spool / identifier).exists()


def test_overlapping_monotonic_interval_on_another_boot_is_refused(handoff):
    identifier = received(handoff)
    spool, _, _, clock = handoff
    clock["now"] = 110.0
    clock["boot"] = "b" * 32
    with pytest.raises(UpdateError, match="expired|boot"):
        with root_inputs(handoff, identifier):
            pass
    received(handoff)
    assert not (spool / identifier).exists()


def test_restart_after_receiving_record_publication_cleans_partial_input(handoff):
    spool, lock, _, clock = handoff
    spool.mkdir(mode=0o700)
    identifier = "c" * 32
    (spool / ".upload.json").write_text(json.dumps({
        "id": identifier, "phase": "receiving", "boot": clock["boot"],
        "started": clock["now"], "expires": clock["now"] + 300,
    }))
    folder = spool / identifier
    folder.mkdir()
    (folder / UPLOAD_ARCHIVE).write_bytes(b"partial")
    received(handoff)
    assert not folder.exists()
    assert lock.exists()


def test_empty_receipt_and_failure_before_ready_leave_no_reserved_job(handoff):
    spool, lock, _, _ = handoff
    with pytest.raises(OSError):
        with uploads.receiving(spool, lock) as receipt:
            (receipt.folder / UPLOAD_ARCHIVE).write_bytes(b"partial")
            raise OSError("test receiver stopped")
    assert not list(spool.glob("*/"))
    received(handoff)


def test_lock_inode_survives_cleanup_and_reinitialization(handoff):
    spool, lock, _, _ = handoff
    inode = lock.stat().st_ino
    lock.chmod(0o440)
    identifier = received(handoff)
    assert uploads.discard(identifier, spool=spool, lock=lock)
    uploads.prepare_lock(lock)
    assert lock.stat().st_ino == inode


@pytest.mark.parametrize("change", [
    lambda record: record.update(id="../escape"),
    lambda record: record.update(phase="unknown"),
    lambda record: record.update(expires=float("inf")),
    lambda record: record.update(started=True),
    lambda record: record.update(expires=record["started"] + 10000),
    lambda record: record.update(started=10 ** 500),
])
def test_root_refuses_malformed_metadata_without_legacy_fallback(handoff, change):
    identifier = received(handoff)
    spool, _, _, _ = handoff
    record_path = spool / ".upload.json"
    record = json.loads(record_path.read_text())
    change(record)
    record_path.write_text(json.dumps(record))
    with pytest.raises(UpdateError):
        with root_inputs(handoff, identifier):
            pass


def test_root_refuses_an_oversized_or_linked_record(handoff, tmp_path):
    identifier = received(handoff)
    spool, _, _, _ = handoff
    record = spool / ".upload.json"
    record.write_bytes(b"x" * 1025)
    with pytest.raises(UpdateError):
        with root_inputs(handoff, identifier):
            pass
    record.unlink()
    outside = tmp_path / "outside"
    outside.write_text("private")
    record.symlink_to(outside)
    with pytest.raises(UpdateError):
        with root_inputs(handoff, identifier):
            pass
    assert outside.read_text() == "private"


def test_identified_handoff_cannot_be_read_through_legacy_shared_names(handoff):
    received(handoff)
    with pytest.raises(UpdateError, match="identifier"):
        with root_inputs(handoff, None):
            pass


def test_root_never_follows_a_request_directory_or_input_link(handoff, tmp_path):
    identifier = received(handoff)
    spool, _, _, _ = handoff
    source = spool / identifier / UPLOAD_ARCHIVE
    source.unlink()
    outside = tmp_path / "outside"
    outside.write_bytes(b"private")
    source.symlink_to(outside)
    with pytest.raises(UpdateError):
        with root_inputs(handoff, identifier):
            pass
    assert outside.read_bytes() == b"private"


@pytest.mark.parametrize("component", ["request", "spool", "lock"])
def test_root_refuses_linked_ownership_components(handoff, component):
    identifier = received(handoff)
    spool, lock, _, _ = handoff
    original = {"request": spool / identifier, "spool": spool, "lock": lock}[component]
    moved = original.with_name(original.name + "-moved")
    original.rename(moved)
    original.symlink_to(moved, target_is_directory=component != "lock")
    with pytest.raises(UpdateError):
        with root_inputs(handoff, identifier):
            pass
    source = moved / UPLOAD_ARCHIVE if component == "request" else (
        moved / identifier / UPLOAD_ARCHIVE if component == "spool"
        else spool / identifier / UPLOAD_ARCHIVE)
    assert source.read_bytes() == b"test archive"


def test_root_cleans_bounded_private_orphans_before_copying(handoff):
    identifier = received(handoff)
    _, _, root, _ = handoff
    incoming = root / "incoming"
    incoming.mkdir(mode=0o700, parents=True)
    orphan = incoming / ("f" * 32)
    orphan.mkdir(mode=0o700)
    (orphan / UPLOAD_ARCHIVE).write_bytes(b"interrupted copy")
    with root_inputs(handoff, identifier) as (archive, _):
        assert not orphan.exists()
        assert uploads.IDENTIFIER.fullmatch(archive.parent.name)
    assert [path.name for path in incoming.iterdir()] == [".copy.lock"]


def test_new_receipt_removes_legacy_partial_leaves_without_following_them(handoff, tmp_path):
    spool, _, _, _ = handoff
    spool.mkdir(mode=0o700)
    outside = tmp_path / "outside"
    outside.write_text("private")
    (spool / UPLOAD_ARCHIVE).symlink_to(outside)
    (spool / UPLOAD_SIGNATURE).write_bytes(b"partial")
    received(handoff)
    assert not (spool / UPLOAD_ARCHIVE).exists()
    assert not (spool / UPLOAD_SIGNATURE).exists()
    assert outside.read_text() == "private"


def test_lock_creation_is_readable_by_its_protected_group_even_with_a_private_umask(tmp_path):
    previous = os.umask(0o077)
    try:
        lock = tmp_path / "release-upload.lock"
        uploads.prepare_lock(lock)
    finally:
        os.umask(previous)
    assert lock.stat().st_mode & 0o777 == 0o640
    assert lock.stat().st_gid == tmp_path.stat().st_gid


def test_failed_ready_publication_does_not_leave_an_admissible_job(handoff, monkeypatch):
    spool, lock, _, _ = handoff
    original = uploads._write_record

    def failed_ready(descriptor, record):
        if record["phase"] == "ready":
            raise OSError("test storage failure")
        return original(descriptor, record)

    monkeypatch.setattr(uploads, "_write_record", failed_ready)
    with pytest.raises(OSError, match="storage failure"):
        received(handoff)
    assert list(spool.iterdir()) == []
    monkeypatch.setattr(uploads, "_write_record", original)
    with uploads.receiving(spool, lock) as receipt:
        assert receipt.identifier


def test_entry_scan_refuses_unknown_growth_before_touching_inputs(handoff):
    spool, lock, _, _ = handoff
    spool.mkdir(mode=0o700)
    for index in range(uploads.MAX_ENTRIES + 1):
        (spool / str(index)).write_bytes(b"untouched")
    with pytest.raises(UpdateError, match="many retained upload entries"):
        with uploads.receiving(spool, lock):
            pass
    assert len(list(spool.iterdir())) == uploads.MAX_ENTRIES + 1


def test_receiver_closes_its_spool_descriptor_when_cleanup_fails(handoff, monkeypatch):
    spool, lock, _, _ = handoff

    def failed_cleanup(*args, **kwargs):
        raise OSError("test cleanup failure")

    monkeypatch.setattr(uploads, "_remove_folder", failed_cleanup)
    with pytest.raises(OSError, match="cleanup failure"):
        with uploads.receiving(spool, lock) as receipt:
            descriptor = receipt.spool
    with pytest.raises(OSError):
        os.fstat(descriptor)


def test_root_closes_its_spool_descriptor_when_input_cleanup_fails(handoff, monkeypatch):
    identifier = received(handoff)
    original_open, original_remove = uploads._open_spool, uploads._remove_folder
    descriptors = []

    def remember_spool(*args, **kwargs):
        descriptor = original_open(*args, **kwargs)
        descriptors.append(descriptor)
        return descriptor

    def failed_input_cleanup(parent, *args, **kwargs):
        if parent == descriptors[0]:
            raise OSError("test input cleanup failure")
        return original_remove(parent, *args, **kwargs)

    monkeypatch.setattr(uploads, "_open_spool", remember_spool)
    monkeypatch.setattr(uploads, "_remove_folder", failed_input_cleanup)
    with pytest.raises(OSError, match="input cleanup failure"):
        with root_inputs(handoff, identifier):
            pass
    with pytest.raises(OSError):
        os.fstat(descriptors[0])


def test_lock_preparation_closes_a_new_descriptor_when_ownership_fails(tmp_path, monkeypatch):
    descriptors = []

    def failed_ownership(descriptor, *args):
        descriptors.append(descriptor)
        raise OSError("test ownership failure")

    monkeypatch.setattr(uploads.os, "fchown", failed_ownership)
    with pytest.raises(OSError, match="ownership failure"):
        uploads.prepare_lock(tmp_path / "release-upload.lock")
    with pytest.raises(OSError):
        os.fstat(descriptors[0])


def test_retained_folder_cap_refuses_admission_without_unbounded_cleanup(handoff):
    spool, lock, _, _ = handoff
    spool.mkdir(mode=0o700)
    for index in range(9):
        (spool / f"{index:032x}").mkdir()
    with pytest.raises(UpdateError, match="many|limit"):
        with uploads.receiving(spool, lock):
            pass
    assert len(list(spool.iterdir())) == 9


def test_root_private_copy_stays_bound_to_the_open_request_directory(handoff, monkeypatch):
    identifier = received(handoff)
    spool, _, _, _ = handoff
    original = uploads.copy_bounded
    moved = spool / ("e" * 32)

    def exchanged(source, destination, *args):
        if not moved.exists():
            (spool / identifier).rename(moved)
            (spool / identifier).symlink_to(moved, target_is_directory=True)
        return original(source, destination, *args)

    monkeypatch.setattr(uploads, "copy_bounded", exchanged)
    with root_inputs(handoff, identifier) as (archive, _):
        assert archive.read_bytes() == b"test archive"
    assert not list(moved.iterdir())
