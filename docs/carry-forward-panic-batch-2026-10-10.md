# Carried forward from the combined Panic batch review (10 October 2026)

The combined Panic batch was reviewed by an Opus agent and by Sol (GPT-6.1) in several rounds.
These items were found and are NOT fixed in the batch. None is new in the batch except the
rollback cleanup, which was left out on purpose.

## Rollback cleanup of the upload intake (left out)
A rollback or a failed update restores the previous release. The previous release cannot remove
the ifb device and the ingress redirect that the upload-safe layout adds, so upload corruption
stays active on the test Wi-Fi until the next reboot (tc state does not survive a reboot). The
router never sees damaged frames in that state because the Pi's kernel checks IP headers first,
so this does not endanger the router. A cleanup was written (branches
`fix/upstream-safe-rollback-cleanup` 488553c and `fix/upstream-safe-rollback-cleanup2` fd3c5d4)
and reviewed. Three S2 problems remain in it, so it is not merged:
1. It removes the Wi-Fi ingress qdisc because it exists, but another tool may own rules there.
   It needs a durable proof that JitterSim created the intake, kept through a partial cleanup.
2. `CommandNotStopped` and `OperationOwnershipUnknown` (both subclasses of `UpdateError`) must
   propagate out of the cleanup and must not be turned into "continue to restore".
3. A cleanup warning written with `progress("rolling-back", ...)` is overwritten by the final
   `rolled-back` status, so the owner sees success. Keep the warning in the final status.

## Pre-existing, not changed by the batch
- A stalled fsync of the stop records (`panic_guard.py`, `gateway.json`) can hold the network lock
  before any rule is removed.
- Dashboard Panic reaches the scheduler lock through `begin_recovery()` and a repository publish
  that a database backup can hold for its whole duration. Fix: acquire it with a timeout and
  still call the helper, marking recovery unconfirmed.
- A rollback restores `/etc/jittersim/dashboard-access.json` from the recovery snapshot, so a
  revoked grant can come back. `PRESERVED_CONFIG` in `updates.py` should keep the current file.
- The Pi helper socket authorizes by group only (see docs/design-helper-action-authorization-2026-10-09.md).

## Smaller notes
- A boot with an incomplete household release latches a Panic stop, even if the owner never
  pressed Panic. Profiles stay stopped until Panic or Normal access succeeds.
- Panic is unavailable for up to 90 s at boot if a restore hangs, because the helper starts
  after `jittersim-access-boot.service` (`TimeoutStartSec=90`).
