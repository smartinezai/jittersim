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

## Third review round (17 October 2026, head 2cac16d3)

Reviewers: Sol (GPT-6.1, high, read-only) and an independent Claude Opus review of the full
PR at 2cac16d3. One fix round. Each claim was first checked against the code, with a test
that failed before the fix where one could be written. `networking/helper.py`,
`panic_guard.py` and `networking/access.py` are unchanged in this round.

| # | Sev | Finding | Check | Disposition |
| --- | --- | --- | --- | --- |
| Sol 1 | S3 | The installer stops the helper and dashboard, then removes the upload intake. On an ordinary cleanup error nothing starts them again, and `install.sh` runs under `set -eu`, so the Pi is left without its helper and Panic. | Verified. The new test failed: only `systemctl stop` ran. | Fixed in cabb21dd. On an ordinary cleanup error the installer starts the unchanged installation again, then fails. A command that may still run, or whose ownership is unknown, is raised without a restart. Tests in `tests/unit/test_update_operation_lock.py`. |
| Sol 2 | S3 | Panic pauses ad blocking before it removes any rule. The pause saves its stop intent with a plain `fsync`, and its `wait` bounds only the settings lock, so a storage stall holds the network lock with every restriction in force. | Verified. The new test (real `AdStore` in the Panic fault rig, stalled file sync) failed: the rules were still in place two seconds into the stall. | Fixed in 2842447f, in `networking/adblocking.py` only. The pause bounds the lock wait and its saves together. Saves that outlive that finish in their own thread, which keeps the settings lock, and the pause raises, so Panic still releases both network layers and answers unconfirmed. A second pause while they still stall fails at once. A retry after storage answers confirms. |
| Sol 5 | S2 | A forced Panic raised its halt count before it counted itself as an owner. A newer Panic between the two writes could confirm the new count, and `can_apply()` admitted a Start while the forced Panic was unfinished. | Verified. The interleaving test (the forced Panic stopped between its two writes, a full newer recovery run there) failed: the Start was admitted. | Fixed in 9729ac7c. The owner is published before the halt count. |
| Opus O3 | S2 | `remove_upload_intake`: the final record removal raised a bare `OSError`, and `proven()` called `item.get()` before checking the item is an object. Both skipped the rollback restore, which only catches `UpdateError`. | Verified. Both new tests failed (`OSError`, `AttributeError`). | Fixed in cabb21dd. Both are now ordinary cleanup problems. A filter item that is not an object proves nothing, so that qdisc stays. |
| Opus O4 | S1 | No test for the unsynced `gateway.json` fallback after a failed synced save. | Refuted. Two mutations of `helper.py` in a scratch copy were each caught by existing cases: without `_gateway_state_error = True`, `test_failed_save_is_reported_and_stopped_effects_stay_stopped[gateway-flush-whole]` fails; without the unsynced write, `test_failed_save_leaves_only_whole_records_and_no_stale_temporary[gateway-flush-whole]` and `[gateway-file-sync-whole]` fail. | No change. |
| Opus O1 | S1 | The stop-record saver keeps a late error without a log line. | Not changed. The brief allowed it only if `helper.py` changed for Sol 2, and that fix needed no helper change. | Carried forward. |
| Opus O2 | S3 class | An early save error that is not a storage error ends Panic before any rule is removed. | Pre-existing and not made worse. Not changed: it would change the pinned helper only for this. | Carried forward. |
| Sol 3 | S3 | Rollback to a package without intake support can leave an intake that the restored Panic cannot remove. | Pre-existing (finding 2 of the first round). | Owner decision, unchanged. |
| Sol 4 | S3 | Rollback restores the database, so protection-derived dashboard access for a device whose protection was later removed comes back. | Pre-existing (finding 3 of the first round). | Owner decision, unchanged. |

### Carried forward from this round

- Owner decision 1 (Sol 3). Recommended default: keep the current behaviour (clean up before
  the restore, retry after it, warn with reboot advice) and do not block the rollback. A
  reboot removes the intake because tc state and the ifb device never survive it.
- Owner decision 2 (Sol 4). Recommended default: keep restoring the database as it is and
  tell the owner in the rollback notes to check protected devices after a rollback. The
  alternative is to keep the current protected device list or ask before restored
  protection grants dashboard access.
- Same class as Sol 2, not reported by the reviewers: the household release enters
  `AdStore.access_transition()`, which saves its suspension with a plain `fsync` once it
  holds the settings lock. A stall that begins there still holds the household release. A
  stall left by the Panic pause does not, because that thread holds the settings lock and
  the transition then gives up after its short wait. Fixing it needs care with the
  suspension semantics, so it is left for a later change.
- O1 and O2 above, the next time `networking/helper.py` changes for another reason.
