"""The exact reviewed bridge extends trust without changing stop protection."""

import hashlib
import json
import os
import shutil
from pathlib import Path

import pytest

from jittersim import updates


FINAL_BUNDLE = {
    "src/jittersim/panic_guard.py": "041cf171875d7b85d1c0664c2acff262542a3ac3c2b2e8fe51f80d2d59d6d639",
    "src/jittersim/networking/helper.py": "f1d0e704b8ad61b4ef677e25d4b1c3f40ea1e16dfceb67246568f8ca35366587",
    "src/jittersim/networking/access.py": "5c335f3b72b90e8eb7bff3cef135fe4f6986402fa529261f1af0ebe53eb4a219",
}
OLD_TABLE_SHA256 = "2fd8f404f57641edc6af98d2b0d361921afc0b2876a9636645c480be9c99929a"


def test_only_one_exact_complete_bundle_extends_the_unchanged_old_table():
    approved = updates.PANIC_APPROVED_SOURCES
    assert len(approved) == 9
    old_table = json.dumps(approved[:8], sort_keys=True, separators=(",", ":")).encode()
    assert hashlib.sha256(old_table).hexdigest() == OLD_TABLE_SHA256
    assert approved[-1] == FINAL_BUNDLE
    assert all(set(bundle) == set(updates.PANIC_SOURCE_FILES) for bundle in approved)


@pytest.fixture
def source(tmp_path):
    root = Path(__file__).resolve().parents[2]
    candidate = tmp_path / "source"
    for name in updates.PANIC_SOURCE_FILES:
        path = candidate / name
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(root / name, path)
    return candidate


@pytest.mark.parametrize("latched", [False, True])
def test_current_reviewed_consumers_preserve_established_guard_bytes(source, tmp_path, latched):
    module = updates._panic_candidate(source)
    guard_path = tmp_path / "guard/state.json"
    module.PanicGuard(guard_path, owner_uid=os.geteuid()).write(
        dict(version=2, **{name: latched for name in module.PANIC_GUARD_FIELDS}))
    before = guard_path.read_bytes()
    safety = updates.PanicProtection(source, guard_path=guard_path,
                                     legacy_path=tmp_path / "absent", owner_uid=os.geteuid())
    safety.inspect(source)
    safety.provision(source)
    assert guard_path.read_bytes() == before


@pytest.mark.parametrize("latched", [False, True])
def test_unreviewed_consumer_is_still_refused_without_changing_intent(source, tmp_path, latched):
    module = updates._panic_candidate(source)
    guard_path = tmp_path / "guard/state.json"
    module.PanicGuard(guard_path, owner_uid=os.geteuid()).write(
        dict(version=2, **{name: latched for name in module.PANIC_GUARD_FIELDS}))
    safety = updates.PanicProtection(source, guard_path=guard_path,
                                     legacy_path=tmp_path / "absent", owner_uid=os.geteuid())
    before = guard_path.read_bytes()
    consumer = source / "src/jittersim/networking/access.py"
    consumer.write_bytes(consumer.read_bytes() + b"\n# unreviewed change\n")
    with pytest.raises(updates.PanicProtectionError, match="Panic protection"):
        safety.inspect(source)
    assert guard_path.read_bytes() == before
