# Carried forward from the combined Panic batch review (10 October 2026)

The combined Panic batch was reviewed by an Opus agent and by Sol (GPT-6.1) in several rounds.
These items were found and are NOT fixed in the batch. None is new in the batch except the
rollback cleanup, which was left out on purpose.

## Status (18 October 2026)
PR 138 (merge `8c3f8171`, 18 October 2026) worked the rollback cleanup and the first three
pre-existing items below. `PANIC_APPROVED_SOURCES` has the entry for it. See
docs/review-results-carry-forward-2026-10-16.md for what is fixed and what is still open.

| Item | State on main |
| --- | --- |
| Rollback cleanup of the upload intake | Merged with an ownership proof (a pref 2 connmark filter or a durable record bound to the interface and the boot). The restore is never blocked. A failed cleanup is tried again after the restore and the final status warns. **Owner decision 1 is open**: block the rollback or keep a cleanup-capable Panic path. Until then the owner action stays: reboot the Pi |
| Stalled fsync of the stop records | Fixed. The first saves run in a thread with a bounded wait. On a stall Panic removes the rules, releases the household and answers that the recovery is not confirmed. The same stall class in `AdStore.access_transition()` (the household release) is still open |
| Dashboard Panic and the scheduler lock | Fixed. The three scheduler steps share a bounded wait and Panic still calls the helper, answering unconfirmed when a step cannot take the lock |
| Rollback restoring `dashboard-access.json` | Fixed for that file (`KEPT_CURRENT_CONFIG`). **Owner decision 2 is open**: a rollback restores the database, so a protected device removed after the update comes back with its protection grant. The default is to accept that and remove the device again |
| Helper socket authorizes by group only | Open (proposal only, see the design note) |
| The two boot notes | Open |
| Reviewer notes O1 and O2 | Open. A late stop-record error has no log line, and an early save error that is not a storage error ends Panic before any rule is removed. Both would change the pinned `helper.py` only for themselves, so they wait for the next change of it for another reason |

The sections below are the list as it stood on 10 October 2026.

## Rollback cleanup of the upload intake (left out)
Only a rollback from the first release that contains the upload-safe layout (PR 125) to an older
release is affected. The older release does not know the ifb device and the ingress redirect that
the layout adds, so its Panic and its Reset cannot remove them. While upload corruption was active
it stays active on the test Wi-Fi until the Pi reboots (tc state does not survive a reboot). The
older nftables rules also lack the rule that drops packets with a damaged TCP or UDP checksum,
so such packets can leave without NAT (see docs/upstream-safe-impairments-2026-10-09.md). Their
Ethernet and IPv4 headers stay well formed, because the Pi's kernel validates the IP header on
ingress. Whether a Vodafone Station reacts to those packets is not proven.
**Owner action after such a rollback: reboot the Pi.** Rollbacks between releases that both
contain the layout are not affected.

A cleanup was written (branches `fix/upstream-safe-rollback-cleanup` 488553c and
`fix/upstream-safe-rollback-cleanup2` fd3c5d4) and reviewed. Open S2 problems in it, so it is not merged:
1. It removes the Wi-Fi ingress qdisc because it exists, but another tool may own rules there.
   It needs a durable proof that JitterSim created the intake, kept through a partial cleanup.
2. `CommandNotStopped` and `OperationOwnershipUnknown` (both subclasses of `UpdateError`) must
   propagate out of the cleanup and must not be turned into "continue to restore".
3. A cleanup warning written with `progress("rolling-back", ...)` is overwritten by the final
   `rolled-back` status, so the owner sees success. Keep the warning in the final status.
4. Plans larger than the 4096-byte answer limit and a partial cleanup that passes on retry
   (round 4 findings, fixed in fd3c5d4 and then reopened by the findings above).

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
