"""The standalone updater uses verified identity code and its own ownership."""

import shutil
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from jittersim import updates


@pytest.fixture
def identity_source(tmp_path):
    source = tmp_path / "source"
    target = source / "src/jittersim"
    target.mkdir(parents=True)
    checkout = Path(updates.__file__).parent
    for name in ("localname", "localca", "mdnsname"):
        shutil.copyfile(checkout / (name + ".py"), target / (name + ".py"))
    return source


def test_owned_modules_use_the_executing_manager_and_leave_no_imports(identity_source):
    before = set(sys.modules)
    with updates.identity_modules(identity_source) as modules:
        package = modules.localca.__package__
        assert sys.modules[package + ".updates"] is updates
        assert sys.modules[package].__path__ == []
        assert modules.localca.LeafResult(False).reissued is False
        assert modules.localname.DEFAULT_LOCAL_NAME == "netlab.local"
    assert not any(name.startswith(package) for name in sys.modules)
    assert set(sys.modules) >= before


@pytest.mark.parametrize("unsafe", ["missing", "link", "writable", "large"])
def test_unsafe_candidate_never_falls_back_to_installed_identity(identity_source, unsafe):
    candidate = identity_source / "src/jittersim/localca.py"
    if unsafe == "missing":
        candidate.unlink()
    elif unsafe == "link":
        saved = candidate.with_suffix(".saved")
        candidate.rename(saved)
        candidate.symlink_to(saved)
    elif unsafe == "writable":
        candidate.chmod(0o666)
    else:
        candidate.write_bytes(b" " * (1024 * 1024 + 1))
    before = {name for name in sys.modules if name.startswith("_jittersim_identity_")}
    with pytest.raises(updates.UpdateError):
        with updates.identity_modules(identity_source):
            pytest.fail("unsafe identity code ran")
    assert {name for name in sys.modules if name.startswith("_jittersim_identity_")} == before


def test_current_context_bounds_crypto_and_propagates_unknown_ownership(identity_source, tmp_path):
    budget = updates.UpdateBudget()
    budget.deadline = updates.time.monotonic() + 0.5
    failure = updates.OperationOwnershipUnknown("controlled unknown identity command")
    calls = []

    def capture(command, timeout, **options):
        calls.append((list(command), timeout, updates.COMMAND_CONTEXT.operation))
        raise failure

    with updates.update_lock(tmp_path / "updates"):
        operation = updates.COMMAND_CONTEXT.operation
        with patch.object(updates, "capture_command", capture):
            with updates.identity_modules(identity_source) as modules:
                with pytest.raises(updates.OperationOwnershipUnknown) as caught:
                    modules.localca.prepare(tmp_path / "config", host_name="pi.local",
                                            neutral_name="netlab.local", ethernet_ip=None,
                                            budget=budget)
    assert caught.value is failure
    assert calls and calls[0][0][0] == "openssl"
    assert 0 < calls[0][1] <= 0.5
    assert calls[0][2] == operation


def test_unregistered_standalone_module_cannot_borrow_a_different_manager(identity_source):
    with patch.dict(updates.__dict__, {"__name__": "missing_verified_manager"}):
        with pytest.raises(updates.UpdateError, match="executing"):
            with updates.identity_modules(identity_source):
                pytest.fail("a different installed manager was borrowed")
