"""Offline exact Git-blob admission proof: OLD_REF BRIDGE_REF FINAL_REF.

Uses real updater bytes without changing either manager's approval table.
Temporary source trees and guard records are the only runtime writes.
"""

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import types
from pathlib import Path


def blob(ref, path):
    return subprocess.check_output(["git", "show", ref + ":" + path])


def manager(label, raw):
    module = types.ModuleType("proof_" + label)
    module.__file__ = "/offline-proof/" + label + "-updates.py"
    exec(compile(raw, module.__file__, "exec"), module.__dict__)
    return module


def main():
    old_ref, bridge_ref, final_ref = sys.argv[1:]
    refs = {name: subprocess.check_output(["git", "rev-parse", ref], text=True).strip()
            for name, ref in (("old", old_ref), ("bridge", bridge_ref), ("final", final_ref))}
    old_raw = blob(refs["old"], "src/jittersim/updates.py")
    bridge_raw = blob(refs["bridge"], "src/jittersim/updates.py")
    old = manager("old", old_raw)
    bridge = manager("bridge", bridge_raw)
    assert len(old.PANIC_APPROVED_SOURCES) == 8
    assert len(bridge.PANIC_APPROVED_SOURCES) == 9
    assert bridge.PANIC_APPROVED_SOURCES[:8] == old.PANIC_APPROVED_SOURCES
    with tempfile.TemporaryDirectory(prefix="exact-updater-bridge-") as directory:
        root = Path(directory)
        sources, bundles = {}, {}
        for name, ref in refs.items():
            source = root / name
            sources[name] = source
            bundles[name] = {}
            for relative in old.PANIC_SOURCE_FILES:
                raw = blob(ref, relative)
                path = source / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(raw)
                path.chmod(0o644)
                bundles[name][relative] = hashlib.sha256(raw).hexdigest()
        assert bundles["old"] == bundles["bridge"] == old.PANIC_APPROVED_SOURCES[-1]
        assert bundles["final"] == bridge.PANIC_APPROVED_SOURCES[-1]
        assert bundles["final"] not in old.PANIC_APPROVED_SOURCES
        for name in ("old", "bridge"):
            old._panic_candidate(sources[name])
        try:
            old._panic_candidate(sources["final"])
        except old.UpdateError:
            pass
        else:
            raise AssertionError("the old manager admitted the final bundle")
        for source in sources.values():
            bridge._panic_candidate(source)
        for latched in (False, True):
            path = root / ("latched.json" if latched else "clear.json")
            module = old._panic_candidate(sources["old"])
            module.PanicGuard(path, owner_uid=os.geteuid()).write(
                dict(version=2, **{field: latched for field in module.PANIC_GUARD_FIELDS}))
            before = path.read_bytes()
            safety = old.PanicProtection(sources["old"], guard_path=path,
                                         legacy_path=root / "absent", owner_uid=os.geteuid())
            try:
                safety.inspect(sources["final"])
            except old.PanicProtectionError:
                pass
            else:
                raise AssertionError("the old guard admitted the final bundle")
            assert path.read_bytes() == before
            ready = bridge.PanicProtection(sources["bridge"], guard_path=path,
                                           legacy_path=root / "absent", owner_uid=os.geteuid())
            ready.inspect(*sources.values())
            ready.provision(sources["bridge"], sources["final"])
            assert path.read_bytes() == before
    print(json.dumps({"refs": refs, "bundles": bundles,
                      "old_manager_sha256": hashlib.sha256(old_raw).hexdigest(),
                      "bridge_manager_sha256": hashlib.sha256(bridge_raw).hexdigest(),
                      "old_bundle_count": 8, "bridge_bundle_count": 9,
                      "old_admits_bridge": True, "old_refuses_final": True,
                      "bridge_admits_old_bridge_final": True,
                      "clear_and_latched_guard_bytes_preserved": True}, sort_keys=True))


if __name__ == "__main__":
    main()
