"""Older approved Panic writers must revoke permission introduced by newer code."""

import hashlib
import io
import json
import lzma
import os
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path
from unittest.mock import Mock, patch

from jittersim import updates
from jittersim.networking.adblocking import AdRouting, AdStore
from jittersim.networking.applier import NetworkApplier
from jittersim.networking.executor import FakeExecutor
from jittersim.networking.helper import HelperService
from jittersim.networking.topology import Topology
from tests.adblocking_support import provision_guard
from tests.unit.test_adblocking import ALICE, applied, controller

OLD_PANIC = r"""
import json, os, sys
from pathlib import Path
from jittersim.networking.applier import NetworkApplier
from jittersim.networking.helper import HelperService
from jittersim.networking.access import AccessManager
from jittersim.networking.executor import FakeExecutor
from jittersim.networking.topology import Topology
from jittersim.panic_guard import PanicGuard
from tests.access_support import AccessExecutor
root = Path(sys.argv[1])
topology = Topology('eth0', 'ads0')
access = AccessManager(executor=AccessExecutor(root), topology=topology,
                       directory=root/'old-access', runtime_path=root/'old-access-policy')
service = HelperService(NetworkApplier(executor=FakeExecutor(), topology=topology),
                        access_manager=access,
                        panic_guard=PanicGuard(root/'panic-state.json', owner_uid=os.geteuid()))
result = service.handle_payload({'action': 'normal-access'})
assert result['ok'], result
print(json.dumps(service._panic_guard.read()))
"""


def test_original_approved_helper_panic_invalidates_new_ad_permission(tmp_path):
    root = Path(__file__).resolve().parents[2]
    fixture = root / "tests/fixtures/adblocking-approved-consumers.tar.xz"
    manifest = json.loads(fixture.with_suffix("").with_suffix(".json").read_text())
    assert manifest["source_sha256"] in updates.PANIC_APPROVED_SOURCES
    compressed = fixture.read_bytes()
    assert len(compressed) <= 128 * 1024
    assert hashlib.sha256(compressed).hexdigest() == manifest["archive_sha256"]
    decoder = lzma.LZMADecompressor()
    archive = decoder.decompress(compressed, max_length=512 * 1024)
    assert decoder.eof and not decoder.unused_data
    legacy = tmp_path / "legacy"
    shutil.copytree(root / "src/jittersim", legacy / "src/jittersim",
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    with tarfile.open(fileobj=io.BytesIO(archive)) as package:
        for name, digest in manifest["source_sha256"].items():
            data = package.extractfile(name).read(512 * 1024 + 1)
            assert hashlib.sha256(data).hexdigest() == digest
            (legacy / name).write_bytes(data)

    guard = provision_guard(tmp_path)
    store = AdStore(
        AdRouting("ads0", FakeExecutor()), path=tmp_path / "policy",
        lock_path=tmp_path / "ad-lock", active_path=tmp_path / "active",
        status_path=tmp_path / "status", intent_path=tmp_path / "ad-intent",
        panic_guard=guard,
    )
    store.configure([ALICE], [], resume=True)

    def old_panic():
        result = subprocess.run(
            [sys.executable, "-c", OLD_PANIC, str(tmp_path)], cwd=legacy,
            env={**os.environ, "PYTHONPATH": str(legacy / "src") + os.pathsep + str(root),
                 "PYTHONDONTWRITEBYTECODE": "1"},
            check=True, capture_output=True, text=True, timeout=10,
        )
        assert json.loads(result.stdout)["panic_stop_latched"]

    old_panic()
    service = HelperService(
        NetworkApplier(executor=FakeExecutor(), topology=Topology("eth0", "ads0")),
        ad_blocking_store=store, panic_guard=guard,
    )
    assert service._panic_stop_latched
    assert not store.read()["running"]
    applied(store, tmp_path)
    item = controller(store, tmp_path)
    child = Mock()
    child.poll.return_value = None
    try:
        with (
            patch("jittersim.networking.adblocking.subprocess.run"),
            patch("jittersim.networking.adblocking.subprocess.Popen", return_value=child) as start,
            patch("jittersim.networking.adblocking.healthy", return_value=True),
        ):
            item.tick()
            start.assert_not_called()
            assert not store.status()["ready"]
            revision = service.handle_payload({"action": "control-revision"})["control_revision"]
            enabled = service.handle_payload({
                "action": "ad-blocking-configure", "mac_addresses": [ALICE],
                "allowed_domains": [], "resume": True, "expected_control_revision": revision,
            })
            assert enabled["ok"] and store.read()["running"]
            item.tick()
            assert store.status()["ready"]
        previous_stop_bytes = guard.path.read_bytes()
        old_panic()
        assert guard.path.read_bytes() == previous_stop_bytes
        assert not store.read()["running"]
        item.tick()
        assert not store.status()["ready"]
        assert json.loads(store.active_path.read_bytes())["expires_at"] == 0
    finally:
        item.close()
