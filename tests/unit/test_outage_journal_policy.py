from unittest.mock import patch

import pytest

from jittersim import journal_policy as policy


@pytest.fixture
def layout(tmp_path):
    roots = tuple(tmp_path / name for name in ("usr-lib", "usr-local", "run", "etc"))
    for root in roots:
        (root / "journald.conf.d").mkdir(parents=True)
    config = tmp_path / "project"
    vendor = roots[1] / "journald.conf.d"
    calls = []
    return {"config": config, "roots": roots, "vendor": vendor, "disk": tmp_path,
            "activate": lambda deadline: calls.append(deadline)}, calls


def test_default_configuration_is_bounded_and_repeat_install_is_safe(layout):
    options, calls = layout
    assert policy.install(**options) == "configured"
    text = (options["config"] / policy.TARGET_NAME).read_text()
    assert "SystemMaxUse=64M" in text
    assert "MaxRetentionSec=7day" in text
    assert "RuntimeMaxUse=8M" in text
    reserve = int(text.split("SystemKeepFree=")[1].splitlines()[0])
    assert reserve >= 256 * 1024**2
    assert policy.install(**options) == "unchanged"
    assert len(calls) == 1


@pytest.mark.parametrize("setting", ["Storage=volatile", "Storage=none", "SystemMaxUse=8M", "SystemKeepFree=8G", "MaxRetentionSec=1day", "Storage=persistent"])
def test_explicit_admin_settings_are_never_overridden(layout, setting):
    options, _ = layout
    original = "[Journal]\n" + setting + "\n"
    path = options["roots"][-1] / "journald.conf"
    path.write_text(original)
    with pytest.raises(policy.PolicyError, match="administrator-policy"):
        policy.install(**options)
    assert path.read_text() == original
    assert not options["config"].exists()


def test_mask_and_unsafe_links_refuse_installation(layout):
    options, _ = layout
    masked = options["roots"][-1] / "journald.conf.d" / policy.NAME
    masked.symlink_to("/dev/null")
    with pytest.raises(policy.PolicyError, match="administrator-mask"):
        policy.install(**options)
    masked.unlink()
    masked.symlink_to(options["roots"][-1] / "missing")
    with pytest.raises(policy.PolicyError, match="unsafe-configuration"):
        policy.install(**options)


def test_ambiguous_and_excessive_configuration_is_refused(layout):
    options, _ = layout
    path = options["roots"][-1] / "journald.conf"
    path.write_text("[Journal]\nStorage=persist\\\nent\n")
    with pytest.raises(policy.PolicyError, match="ambiguous"):
        policy.install(**options)
    path.write_bytes(b"#" * (policy.MAX_CONFIG + 1))
    with pytest.raises(policy.PolicyError): policy.install(**options)


def test_changed_owned_configuration_is_preserved(layout):
    options, _ = layout
    policy.install(**options)
    target = options["config"] / policy.TARGET_NAME
    target.write_text("[Journal]\nStorage=volatile\n")
    with pytest.raises(policy.PolicyError, match="administrator-policy"):
        policy.install(**options)
    with pytest.raises(policy.PolicyError, match="administrator-policy"):
        policy.remove(config=options["config"], vendor=options["vendor"], activate=options["activate"], roots=options["roots"])
    assert target.read_text() == "[Journal]\nStorage=volatile\n"


def test_removal_affects_only_project_files_and_preserves_other_logs(layout):
    options, _ = layout
    policy.install(**options)
    unrelated = options["vendor"] / "90-owner.conf"
    unrelated.write_text("[Journal]\nForwardToConsole=no\n")
    journal = options["disk"] / "existing.journal"
    journal.write_bytes(b"existing evidence")
    policy.remove(config=options["config"], vendor=options["vendor"], activate=options["activate"], roots=options["roots"])
    assert unrelated.exists()
    assert journal.read_bytes() == b"existing evidence"
    assert (options["vendor"] / policy.NAME).is_symlink()
    assert "Storage=volatile" in (options["config"] / policy.TARGET_NAME).read_text()
    assert policy.install(**options) == "configured"


def test_low_space_does_not_install_partial_defaults(layout):
    options, _ = layout
    class Space:
        f_blocks = 1000
        f_frsize = 1024
        f_bavail = 1
    with patch.object(policy.os, "statvfs", return_value=Space()):
        with pytest.raises(policy.PolicyError, match="low-space"): policy.install(**options)
    assert not options["config"].exists()


def test_activation_failure_keeps_recognizable_owned_files_for_recovery(layout):
    options, _ = layout
    def broken(deadline): raise OSError("fake-secret")
    options["activate"] = broken
    with pytest.raises(OSError): policy.install(**options)
    assert policy._matches_state(options["config"])
    options["activate"] = lambda deadline: None
    assert policy.install(**options) == "configured"


def test_unprivileged_entry_does_not_inspect_or_change_configuration():
    with patch.object(policy.os, "geteuid", return_value=1000), patch.object(policy, "install") as install:
        assert policy.main(["install"]) == 1
        install.assert_not_called()


def test_removal_restores_an_existing_persistent_default_without_compatibility_file(layout):
    options, _ = layout
    (options["disk"] / "journal").mkdir()
    policy.install(**options)
    assert policy.remove(config=options["config"], vendor=options["vendor"],
                         activate=options["activate"], roots=options["roots"]) == "removed"
    assert not (options["vendor"] / policy.NAME).exists()
    assert (options["disk"] / "journal").exists()


def test_new_admin_policy_prevents_storage_restoration(layout):
    options, _ = layout
    policy.install(**options)
    admin = options["roots"][-1] / "journald.conf"
    admin.write_text("[Journal]\nStorage=persistent\n")
    with pytest.raises(policy.PolicyError, match="administrator-policy"):
        policy.remove(config=options["config"], vendor=options["vendor"],
                      activate=options["activate"], roots=options["roots"])
    assert admin.read_text() == "[Journal]\nStorage=persistent\n"


def test_symlink_parent_cannot_redirect_privileged_file_creation(layout, tmp_path):
    options, _ = layout
    real = tmp_path / "destination"
    real.mkdir()
    link = tmp_path / "redirect"
    link.symlink_to(real, target_is_directory=True)
    options["config"] = link / "new"
    with pytest.raises(policy.PolicyError, match="unsafe-directory"):
        policy.install(**options)
    assert not (real / "new").exists()


def test_update_uses_the_retained_utility_to_restore_an_older_release(tmp_path):
    from jittersim import updates
    code, config, manager = tmp_path / "old-code", tmp_path / "config", tmp_path / "manager.py"
    code.mkdir()
    utility = manager.with_name("jittersim-outage-journal.py")
    utility.write_text("retained trusted policy utility")
    utility.chmod(0o644)
    calls = []
    assert updates.configure_outage_journal(code, config, manager, lambda args, timeout: calls.append((args, timeout)))
    assert calls[0][0][3:] == ["remove", "--config-root", str(tmp_path / "config-outage-journal")]
    assert calls[0][1] == 10
    assert updates.PRESERVED_CONFIG == (updates.SIGNERS_NAME, updates.TOKEN_NAME)


def test_update_installs_the_optional_utility_atomically_and_runs_fixed_arguments(tmp_path):
    from jittersim import updates
    code, config, manager = tmp_path / "code", tmp_path / "config", tmp_path / "manager.py"
    source = code / "src/jittersim/journal_policy.py"
    source.parent.mkdir(parents=True)
    source.write_text("trusted new source")
    calls = []
    assert updates.configure_outage_journal(code, config, manager, lambda args, timeout: calls.append(args))
    assert manager.with_name("jittersim-outage-journal.py").read_text() == "trusted new source"
    assert calls[0][3] == "install"


def test_optional_update_failure_does_not_change_application_result(tmp_path):
    from jittersim import updates
    code, config, manager = tmp_path / "old-code", tmp_path / "config", tmp_path / "manager.py"
    code.mkdir()
    manager.with_name("jittersim-outage-journal.py").write_text("trusted utility")
    def failed(*args): raise updates.UpdateError("fake-secret")
    assert updates.configure_outage_journal(code, config, manager, failed) is False


def test_unknown_systemd_version_does_not_change_any_configuration():
    from types import SimpleNamespace
    with patch.object(policy.os, "geteuid", return_value=0), \
         patch.object(policy.subprocess, "run", return_value=SimpleNamespace(returncode=0, stdout=b"unknown fake-secret")), \
         patch.object(policy, "install") as install:
        assert policy.main(["install"]) == 1
        install.assert_not_called()


def test_old_systemd_version_is_refused_and_supported_version_is_recognized():
    from types import SimpleNamespace
    with patch.object(policy.subprocess, "run", return_value=SimpleNamespace(returncode=0, stdout=b"systemd 239\n")):
        with pytest.raises(policy.PolicyError, match="unsupported-runtime"):
            policy.check_runtime()
    with patch.object(policy.subprocess, "run", return_value=SimpleNamespace(returncode=0, stdout=b"systemd 252 (252.31)\n")):
        policy.check_runtime()


@pytest.mark.parametrize("root_index", [0, 1, 2, 3])
def test_explicit_storage_in_any_main_location_is_preserved(layout, root_index):
    options, _ = layout
    main = options["roots"][root_index] / "journald.conf"
    main.write_text("[Journal]\nStorage=volatile\n")
    with pytest.raises(policy.PolicyError, match="administrator-policy"):
        policy.install(**options)
    assert not options["config"].exists()
    assert main.read_text() == "[Journal]\nStorage=volatile\n"


def test_repeat_removal_does_not_restart_the_journal_again(layout):
    options, calls = layout
    policy.install(**options)
    assert policy.remove(config=options["config"], vendor=options["vendor"],
                         activate=options["activate"], roots=options["roots"]) == "previous-memory-storage-restored"
    count = len(calls)
    assert policy.remove(config=options["config"], vendor=options["vendor"],
                         activate=options["activate"], roots=options["roots"]) == "unchanged"
    assert len(calls) == count
