# Review results: carry-forward fixes (16 October 2026)

Branch `fix/carry-forward-2026-10-16`, based on `integrate/gated-batch-2026-10-09` (PR 136).
It works the items of `docs/carry-forward-panic-batch-2026-10-10.md`.

Reviewer: Sol (GPT-6.1, `codex exec`, reasoning effort high, read-only), one independent
review of the full diff `origin/integrate/gated-batch-2026-10-09...bfe21c0`.
Real S1 and S2 findings were fixed once, as the brief asked. The fixes were not reviewed again.

## Findings

| # | Sev | Item | Finding | Disposition |
| --- | --- | --- | --- | --- |
| 1 | S1 | c | After `begin_recovery()` another owner could take the scheduler lock before `publish_recovery()` and hold it through a publish that waits for a backup, so Panic blocked before the helper call. | Fixed in 87e1b6b. All three scheduler steps take a timeout and share `PANIC_SCHEDULER_WAIT_SECONDS`. A step that cannot take the lock fences the scheduler and Panic records an unconfirmed result. |
| 2 | S1 | a | A cleanup that fails installs an older helper whose Panic cannot remove the intake. | Partly fixed in 95f13a8: the cleanup is tried again after the restore passed its health check, and the final status keeps the warning with reboot advice. Not blocking the restore is deliberate (no restore leaves the Pi without helper and dashboard). Open owner decision: block the rollback instead, or keep a cleanup-capable Panic path until the intake is gone. |
| 3 | S1 | d | Keeping `dashboard-access.json` does not keep revoked protection-derived grants. The rollback restores the database with its older protected device list and `recover_protected_access()` grants that device again. | Left. A rollback restores all saved settings from the recovery copy by design. This is the database rollback, not the access file of item d. Recorded as a carry-forward item for an owner decision. |
| 4 | S1 | a | A retry after a partial cleanup used the interface of the current `runtime.env`, which a partial restore can change, and then deleted the record as if clean. | Fixed in 95f13a8. The record lists every interface still owed a check and the retry checks them until all pass. |
| 5 | S2 | b | A stalled first save of an earlier Panic could replace the confirmed records of a retry when it resumed. Stalled threads could pile up. | Fixed in 6934cb9. While the stalled save thread lives no new first saves start and Panic stays unconfirmed. |
| 6 | S2 | c | Concurrent forced halts could store a lower counter value after a higher one and reuse an old generation. | Fixed in 87e1b6b. Forced halts count under a short lock of their own. |
| 7 | S2 | a | A record that survived a reboot could authorize deleting another tool's ingress qdisc. | Fixed in 95f13a8. The record carries the boot id and proves nothing in a later boot. |

S3: none reported. The reviewer found no change of recovery snapshot digests.

## Left open

- Finding 2 (owner decision): block the rollback or keep a cleanup-capable Panic path.
  Until then the owner action after a failed cleanup stays: reboot the Pi.
- Finding 3 (owner decision): whether a rollback should keep the current protected
  device list or require approval before restored protection grants dashboard access.
- The stalled-save fix (item b) bounds only the saves before the rules are removed. A
  stall in the result saves after the reset still holds the network lock, but the
  rules are already gone then.

## Second review (17 October 2026, head a35e045 against main)

Reviewer: Sol (GPT-6.1, high, read-only), full diff `origin/main...a35e045`, focused on the
pinned `networking/helper.py`. It confirmed that the new read-only actions match the helper
handlers and that writes still invalidate a client scan. All three findings were fixed in one
commit after a35e045.

| # | Sev | Finding | Disposition |
| --- | --- | --- | --- |
| R2-1 | S1 | After a stalled stop save, the reset's own `gateway.json` write still waited for the same storage before the household rules were released. | Fixed: Panic skips that bookkeeping write while its stop saves stall. |
| R2-2 | S2 | An error the stalled saves raised during the reset was lost and Panic could answer a confirmed recovery. | Fixed: the saver's errors are kept with its thread and raised before any confirmation. |
| R2-3 | S2 | A forced recovery had no owner, so a newer Panic could confirm while the forced one was still running, and its later failure did not restore the fence. | Fixed: forced recoveries are counted per request until they end, and each ends as one more unconfirmed halt. |

S3: none.

## Check of the fix commit 4da63b3

Sol (GPT-6.1, high) checked 4da63b3 alone. R2-2 and the new wrapper and tuple: no findings.
Two findings, both fixed in the next commit:

- S1: R2-1 was only partly fixed. When the first saves finished and the reset's own write then
  stalled, the household release still waited. Panic now always skips the reset's bookkeeping
  write and saves its result after the release. When the synced save fails, an unsynced
  `gateway.json` still records the stop (the state error stays reported).
- S2: `end_forced_recovery()` removed the owner before it counted the halt, so a lock-free reader
  could see a short gap. The order is now: halt first, then owner.

## Burst-loss length through the client cleaner (17 October 2026)

PR 121 (device speed test) found that its start check could not see the burst length of the
loss, because `_clean_queue_impairment` in `networking/helper.py` kept only the other
impairment numbers. The fix is one entry in `_QUEUE_IMPAIRMENT_LIMITS`
(`"loss_burst_packets": 100`, the same 0 to 100 range as `Impairment`). The helper already
sends the field (`asdict(queue.impairment)`), so only the client side changes: the answer
parser, not the Panic path. Like the neighbouring fields it is required, a missing or
out-of-range value is rejected as invalid queue statistics. Tests:
`QueueStatsClientTests.test_keeps_the_burst_length_of_the_loss` and the new rejection and
largest-value cases in `tests/unit/test_helper.py`.
