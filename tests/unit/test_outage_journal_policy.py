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


def test_debian_staff_group_local_directory_can_hold_the_owned_directories(layout, tmp_path):
    """Debian makes /usr/local/lib root:staff 2775. That must not block first installation."""
    options, _ = layout
    local = tmp_path / "local-lib"
    local.mkdir()
    local.chmod(0o2775)
    options["vendor"] = local / "systemd" / "journald.conf.d"
    with patch.object(policy, "_staff_gid", return_value=local.stat().st_gid):
        assert policy.install(**options) == "configured"
    assert options["vendor"].is_dir()
    assert not options["vendor"].stat().st_mode & 0o022


def test_group_writable_directories_of_other_groups_and_world_writable_ones_are_refused(tmp_path):
    other = tmp_path / "other-group"
    other.mkdir()
    other.chmod(0o775)
    with patch.object(policy, "_staff_gid", return_value=other.stat().st_gid + 1):
        with pytest.raises(policy.PolicyError, match="unsafe-directory"):
            policy._directory(other / "new")
    world = tmp_path / "world"
    world.mkdir()
    world.chmod(0o777)
    with patch.object(policy, "_staff_gid", return_value=world.stat().st_gid):
        with pytest.raises(policy.PolicyError, match="unsafe-directory"):
            policy._directory(world / "new")
    with patch.object(policy, "_staff_gid", return_value=None):
        with pytest.raises(policy.PolicyError, match="unsafe-directory"):
            policy._directory(other / "new")
    assert not (other / "new").exists() and not (world / "new").exists()


def test_refusal_prints_only_the_fixed_reason_code(capsys):
    with patch.object(policy.os, "geteuid", return_value=0), patch.object(policy, "check_runtime"), \
         patch.object(policy, "install", side_effect=policy.PolicyError("administrator-policy")):
        assert policy.main(["install"]) == 1
    output = capsys.readouterr().out
    assert "administrator-policy" in output
    assert "Administrator overrides and retained logs are kept." in output


def test_unexpected_failure_text_never_reaches_the_installer_output(capsys):
    for failure in (OSError("fake-secret /etc/private"), ValueError("fake-secret")):
        with patch.object(policy.os, "geteuid", return_value=0), patch.object(policy, "check_runtime"), \
             patch.object(policy, "install", side_effect=failure):
            assert policy.main(["install"]) == 1
        output = capsys.readouterr().out
        assert "fake-secret" not in output and "/etc/private" not in output
        assert "optional setup is unavailable" in output


# ---------------------------------------------------------------------------
# Raspberry Pi OS ships a volatile default. It is a distribution default, not an
# administrator's choice, and it sorts before the project's drop-in's name.
# ---------------------------------------------------------------------------

# A byte-for-byte copy of /usr/lib/systemd/journald.conf.d/40-rpi-volatile-storage.conf
# from the Pi (27 bytes).
PI_OS_VOLATILE_DEFAULT = "[Journal]\nStorage=volatile\n"
PI_OS_VOLATILE_NAME = "40-rpi-volatile-storage.conf"


def pi_os_default(options, text=PI_OS_VOLATILE_DEFAULT, root=0, name=PI_OS_VOLATILE_NAME):
    path = options["roots"][root] / "journald.conf.d" / name
    path.write_text(text)
    return path


def effective_value(options, key):
    """The last value systemd would use: drop-ins merge by file name, in order.

    A file in a later directory replaces an earlier one with the same name.
    """
    names = {}
    for root in options["roots"]:
        directory = root / "journald.conf.d"
        for path in directory.iterdir():
            names[path.name] = path
    value = None
    for name in sorted(names):
        for line in names[name].read_text().splitlines():
            if line.startswith(key + "="):
                value = line.split("=", 1)[1]
    return value


def test_the_project_drop_in_sorts_after_the_raspberry_pi_os_default():
    assert policy.NAME > PI_OS_VOLATILE_NAME


def test_the_raspberry_pi_os_volatile_default_does_not_stop_the_setup(layout):
    options, calls = layout
    vendor_file = pi_os_default(options)

    assert policy.install(**options) == "configured"

    assert vendor_file.read_text() == PI_OS_VOLATILE_DEFAULT
    assert effective_value(options, "Storage") == "persistent"
    assert effective_value(options, "SystemMaxUse") == "64M"
    assert len(calls) == 1
    assert policy.install(**options) == "unchanged"


def test_removal_also_works_on_raspberry_pi_os(layout):
    options, _ = layout
    pi_os_default(options)
    policy.install(**options)

    assert policy.remove(config=options["config"], vendor=options["vendor"],
                         activate=options["activate"], roots=options["roots"]) \
        == "previous-memory-storage-restored"
    assert effective_value(options, "Storage") == "volatile"


@pytest.mark.parametrize("text", [
    "[Journal]\nStorage=none\n",
    "[Journal]\nStorage=persistent\n",
    "[Journal]\nStorage=volatile\nSystemMaxUse=1M\n",
    "[Journal]\nStorage=volatile\nRuntimeMaxUse=4M\n",
])
def test_a_changed_distribution_file_is_still_a_decision_we_respect(layout, text):
    options, _ = layout
    path = pi_os_default(options, text)

    with pytest.raises(policy.PolicyError, match="administrator-policy"):
        policy.install(**options)

    assert path.read_text() == text
    assert not options["config"].exists()


@pytest.mark.parametrize("name", ["50-other.conf", "zz-local.conf", "20-jittersim-outage.conf"])
def test_only_the_known_distribution_file_is_accepted(layout, name):
    options, _ = layout
    pi_os_default(options, name=name)

    with pytest.raises(policy.PolicyError, match="administrator-policy"):
        policy.install(**options)
    assert not options["config"].exists()


@pytest.mark.parametrize("root", [1, 2, 3])
def test_the_same_file_outside_the_distribution_directory_is_an_administrator_file(layout, root):
    options, _ = layout
    pi_os_default(options, root=root)

    with pytest.raises(policy.PolicyError, match="administrator-policy"):
        policy.install(**options)
    assert not options["config"].exists()


def test_an_administrator_file_next_to_the_distribution_default_still_wins(layout):
    options, _ = layout
    pi_os_default(options)
    admin = options["roots"][-1] / "journald.conf.d" / "50-keep-small.conf"
    admin.write_text("[Journal]\nSystemMaxUse=16M\n")

    with pytest.raises(policy.PolicyError, match="administrator-policy"):
        policy.install(**options)

    assert admin.read_text() == "[Journal]\nSystemMaxUse=16M\n"
    assert not options["config"].exists()


def test_an_administrator_copy_of_the_default_replaces_it_and_is_respected(layout):
    # The manual fix from the triage: the same name in /etc replaces the vendor file.
    options, _ = layout
    pi_os_default(options)
    pi_os_default(options, "[Journal]\nStorage=persistent\n", root=3)

    with pytest.raises(policy.PolicyError, match="administrator-policy"):
        policy.install(**options)
    assert not options["config"].exists()


def test_an_administrator_mask_of_the_default_leaves_nothing_to_override(layout):
    options, _ = layout
    pi_os_default(options)
    (options["roots"][-1] / "journald.conf.d" / PI_OS_VOLATILE_NAME).symlink_to("/dev/null")

    assert policy.install(**options) == "configured"


def test_an_unreadable_distribution_default_is_refused_not_guessed(layout):
    options, _ = layout
    path = pi_os_default(options)
    path.write_bytes(b"[Journal]\nStorage=vol\\\natile\n")

    with pytest.raises(policy.PolicyError, match="ambiguous"):
        policy.install(**options)


# -- the drop-in used to be called 20-jittersim-outage.conf --------------------

def owned_old_link(options):
    link = options["vendor"] / "20-jittersim-outage.conf"
    link.symlink_to(options["config"] / policy.TARGET_NAME)
    return link


def test_an_install_by_the_earlier_name_moves_to_the_new_name(layout):
    options, calls = layout
    policy.install(**options)
    (options["vendor"] / policy.NAME).unlink()
    old = owned_old_link(options)

    assert policy.install(**options) == "configured"

    assert not old.is_symlink()
    assert (options["vendor"] / policy.NAME).is_symlink()
    assert policy.install(**options) == "unchanged"


def test_removal_cleans_up_the_earlier_name_too(layout):
    options, _ = layout
    (options["disk"] / "journal").mkdir()
    policy.install(**options)
    (options["vendor"] / policy.NAME).unlink()
    old = owned_old_link(options)

    assert policy.remove(config=options["config"], vendor=options["vendor"],
                         activate=options["activate"], roots=options["roots"]) == "removed"

    assert not old.is_symlink()
    assert not (options["vendor"] / policy.NAME).is_symlink()


def test_a_foreign_file_with_the_earlier_name_is_not_ours_to_remove(layout):
    options, _ = layout
    foreign = options["vendor"] / "20-jittersim-outage.conf"
    foreign.write_text("[Journal]\nForwardToConsole=no\n")

    assert policy.install(**options) == "configured"

    assert foreign.read_text() == "[Journal]\nForwardToConsole=no\n"


def test_failed_removal_of_an_already_persistent_journal_is_finished_by_a_retry(layout):
    options, _ = layout
    (options["disk"] / "journal").mkdir()
    assert policy.install(**options) == "configured"
    target = options["config"] / policy.TARGET_NAME
    receipt = options["config"] / policy.STATE_NAME
    link = options["vendor"] / policy.NAME
    def broken(deadline): raise policy.PolicyError("activation-unavailable")
    with pytest.raises(policy.PolicyError, match="activation-unavailable"):
        policy.remove(config=options["config"], vendor=options["vendor"], activate=broken, roots=options["roots"])
    # journald still runs with the project limits, so the owned files stay as proof.
    assert target.exists() and receipt.exists()
    assert policy._matches_state(options["config"])
    restarts = []
    assert policy.remove(config=options["config"], vendor=options["vendor"],
                         activate=restarts.append, roots=options["roots"]) == "removed"
    assert len(restarts) == 1
    assert not link.exists() and not link.is_symlink()
    assert not target.exists() and not receipt.exists()
    assert (options["disk"] / "journal").exists()
    # A finished removal is a no-op and restarts nothing.
    assert policy.remove(config=options["config"], vendor=options["vendor"],
                         activate=restarts.append, roots=options["roots"]) == "unchanged"
    assert len(restarts) == 1
    assert policy.install(**options) == "configured"


def test_interrupted_volatile_install_is_not_mistaken_for_an_unfinished_removal(layout):
    options, _ = layout
    def broken(deadline): raise policy.PolicyError("activation-unavailable")
    options_broken = dict(options, activate=broken)
    with pytest.raises(policy.PolicyError):
        policy.install(**options_broken)
    restarts = []
    # The link is active here, so removal writes the memory-storage file as before.
    assert policy.remove(config=options["config"], vendor=options["vendor"], activate=restarts.append,
                         roots=options["roots"]) == "previous-memory-storage-restored"
    assert len(restarts) == 1
