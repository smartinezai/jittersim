# Rollback/OpenSSL process-disappearance recovery

Base6e4157649ba30d9ad3203a13e32426e9ddaa6b6f; isolated branchcodex/rollback-openssl-recovery. No existing worktree/main/release artifact was modified. This report describes the frozen local runtime/test diff, not publication or physical Pi acceptance.

## Actual cause

Hosted Python3.9.25 job121197317648/run40202792197 failed `UpdateTests.test_a_rollback_removes_outage_journal_settings_only_after_the_health_check`. Full failure section is retained at `/tmp/js-rollback-openssl-hosted-full-trace.txt`. It reaches actual `openssl version` through `finish_certificate_recovery` → certificate slot verification → capture_command. It is NOT an OpenSSL timeout: remaining command allowance was about60seconds. During completion/cancellation confirmation, `command_group_running` opens a numeric `/proc/PID/stat` entry and its read returns ESRCH/ProcessLookupError because that process disappears. The scan catches ENOENT/FileNotFoundError only. The unhandled ESRCH becomes CommandNotStopped, accurately preserving uncertainty but falsely failing the rollback test after service recovery.

The actual owner local Python3.9 suite reportedly passed; this nondeterministic Linux process-enumeration race explains why an isolated/other-platform pass cannot disprove the hosted failure. It is not evidence of an actual Pi problem.

## Minimal correction and limits

One runtime line in updates.py: per-entry stat open/read now catches `(FileNotFoundError, ProcessLookupError)` and continues scanning. A disappeared entry is no longer a live group member, but later entries are still checked. PermissionError and every other OSError still propagate into conservative uncertain-completion handling. Malformed stat, the finite deadline,65536entry bound, zombie filtering, group matching, waitable leader, cancellation ownership and process reaping are unchanged. No timeout increased, no catch-all, no OpenSSL substitute, no rollback test weakened. Source cause/plan was approved by parent before this runtime edit.

Unique deterministic tests inject ESRCH at the exact failing read seam and establish: disappearing entry then later live member returnsTrue; all disappeared returnsFalse; permission failure still raises; malformed stat still raises UpdateError. The unchanged implementation reproduced two failing ESRCH cases and two passing fail-closed controls before correction (`/tmp/js-rollback-openssl-baseline.log`).

## Executed narrow verification

Python3.12.13 private runtime `/Users/samantha.martinez/Documents/Codex/2026-10-24/task-12/recurring-clock-fixture/.venv-local-ci-3.12/bin/python`, `PYTHONPATH=src`, bytecode/cache disabled. Ran new four-case regression, full test_update_operation_lock.py and test_update_panic_source.py, actual affected rollback journal-health test and its failed-health companion, and four existing preparation ownership/group probes.57cases:56passed,0failures/errors,1explicit skip because macOS does not retain a Linux waitable group leader.4.185seconds. `/tmp/js-rollback-openssl-focused.xml` and `.log` preserve results. The skipped case is `test_cancelled_leader_does_not_hide_a_live_owned_child`; no Linux runtime pass claimed.

`/usr/bin/python3` (3.9.6) executed the AST-extracted exact corrected function and same four procfs helper cases using standard library only:4PASS. `/tmp/js-rollback-openssl-py39-probe.py` and `.log`. This is a narrow deterministic version-compatibility probe, not the hosted3.9.25 suite or a Linux process execution.

Gating Ruff E9,F63,F7,F82,F811 and git diff --check passed. No global install, full-suite repeat, owner-key access, real credentials, external network test, Pi/router operation, signed package/state/version/pinned hash change, push or merge. Independent parent actual-diff review is required before local commit; global writer owns any later integration.

## Final review and freeze

Parent independently reviewed the actual runtime diff CLEAR, then requested test-only open/read variants and unknown EIO propagation. Final unique regression:7PASS,0failures/errors/skips. Includes both open/read ESRCH followed by a live group member, both all-disappeared cases, PermissionError and generic OSError(EIO) propagation, malformed stat fail-closed. Only these changed tests were rerun after the earlier57case run; unchanged broader tests were not repeated. Runtime remains the exact reviewed one-line change. Gating Ruff/diffcheck passed again. Local commit only; no push or merge. Retained receipts and narrow3.9probe are in this report directory; full hosted trace stays in the private /tmp receipt described above.

Integration note: the committed baseline log has trailing whitespace removed for the repository diff check; its manifest hash records that presentation. The original unmodified log remains in the isolated worker receipt. No failure text or result was removed.
