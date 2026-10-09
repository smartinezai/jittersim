"""Protect the host clock and old evidence from disposable-only proof helpers."""

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from tests.integration import linux_outage_journal as proof
from tests.integration import linux_outage_retention as retention


@pytest.mark.parametrize("container,virtual_machine", [(0, 0), (1, 1), (127, 0)])
def test_container_host_and_unknown_virtualization_are_refused_before_clock_changes(monkeypatch, container, virtual_machine):
    monkeypatch.setenv("JITTERSIM_DISPOSABLE_JOURNAL_PROOF", "yes")
    monkeypatch.setenv("JITTERSIM_DISPOSABLE_RETENTION_PROOF", "yes")
    results = [SimpleNamespace(returncode=container), SimpleNamespace(returncode=virtual_machine)]
    with patch.object(proof.os, "geteuid", return_value=0), patch.object(proof.sys, "platform", "linux"), \
         patch.object(proof.subprocess, "run", side_effect=results), \
         patch.object(retention.time, "clock_settime", create=True) as clock, \
         patch.object(proof.journal_policy, "_atomic") as write:
        with pytest.raises(RuntimeError, match="actual disposable VM"):
            retention.run("a" * 40)
        clock.assert_not_called()
        write.assert_not_called()


def test_missing_disposable_permission_refuses_before_running_guest_commands(monkeypatch):
    monkeypatch.delenv("JITTERSIM_DISPOSABLE_JOURNAL_PROOF", raising=False)
    with patch.object(proof.subprocess, "run") as run:
        with pytest.raises(RuntimeError, match="explicitly disposable"):
            proof._require_disposable()
        run.assert_not_called()


def test_clock_helper_itself_rechecks_the_disposable_gate(tmp_path):
    with patch.object(proof, "_require_disposable", side_effect=RuntimeError("refused")), \
         patch.object(retention, "_command") as command, \
         patch.object(retention.time, "clock_settime", create=True) as clock:
        with pytest.raises(RuntimeError, match="refused"):
            with retention.guest_clock(tmp_path, 100):
                pass
        command.assert_not_called()
        clock.assert_not_called()


def test_failed_proof_restores_guest_clock_and_ntp(monkeypatch, tmp_path):
    monkeypatch.setenv("JITTERSIM_DISPOSABLE_RETENTION_PROOF", "yes")
    with patch.object(proof, "_require_disposable"), patch.object(retention, "_command", return_value=b"yes"), \
         patch.object(proof, "_run", return_value=b"") as restore_ntp, \
         patch.object(retention.time, "time", return_value=1000), \
         patch.object(retention.time, "monotonic", return_value=10), \
         patch.object(retention.time, "CLOCK_REALTIME", 0, create=True), \
         patch.object(retention.time, "clock_settime", create=True) as clock:
        with pytest.raises(ValueError, match="controlled failure"):
            with retention.guest_clock(tmp_path, 100) as advance:
                advance(2)
                raise ValueError("controlled failure")
        assert [call.args[1] for call in clock.call_args_list] == [1000 + 2 * retention.DAY, 1000]
        restore_ntp.assert_called_once_with(["/usr/bin/timedatectl", "set-ntp", "true"], timeout=5)
        assert (tmp_path / "clock-before.json").is_file()


def test_expired_proof_does_not_start_another_command():
    with patch.object(retention.time, "monotonic", return_value=100), patch.object(proof, "_run") as run:
        with pytest.raises(RuntimeError, match="deadline"):
            retention._command(["/usr/bin/journalctl", "--sync"], 99)
        run.assert_not_called()


def test_journal_scan_stops_at_its_directory_entry_limit(tmp_path):
    for count in range(retention.MAX_FILES + 1):
        (tmp_path / (str(count) + ".txt")).touch()
    with pytest.raises(RuntimeError, match="file count"):
        retention._journals(tmp_path)


def test_repeated_evidence_path_is_never_overwritten(monkeypatch, tmp_path):
    import json
    monkeypatch.setenv("JITTERSIM_DISPOSABLE_RETENTION_PROOF", "yes")
    retained = tmp_path / "evidence"
    retained.mkdir(mode=0o700)
    (retained / "prior-proof.json").write_text("prior evidence")
    reboot = {"phase": "verified-after-reboot", "source": "a" * 40,
              "current_boot": "b" * 32, "modules": {}}
    configured = b"Storage=persistent\nSystemMaxUse=64M\nSystemMaxFileSize=8M\nSystemKeepFree=268435456\nMaxRetentionSec=7day\nMaxFileSec=1day\n"
    def read(path, limit):
        return json.dumps(reboot).encode() if path == proof.REBOOT_RESULT else configured
    with patch.object(proof, "_require_disposable"), patch.object(proof, "_identity", return_value={}), \
         patch.object(proof.journal_policy, "_read", side_effect=read), \
         patch.object(proof.journal_policy, "_matches_state", return_value=True), \
         patch.object(retention, "EVIDENCE", retained), \
         patch.object(retention.Path, "read_text", side_effect=lambda: "b" * 32), \
         patch.object(retention, "_command") as command, \
         patch.object(retention.time, "clock_settime", create=True) as clock:
        with pytest.raises(FileExistsError):
            retention.run("a" * 40)
        command.assert_not_called()
        clock.assert_not_called()
    assert (retained / "prior-proof.json").read_text() == "prior evidence"
