# Device address repair verification

The repair preserves access and protection ownership through address changes. It clears private device details when credentials lose trust, keeps a return to an earlier address reversible and reports saved settings separately from their live network result. Recovery retains bounded evidence across the helper file and SQLite without claiming that the two writes are atomic.

The isolated branch starts at integration `0c8abd4b499900799e0614d2901d2b9eb4078f33`. The original identity worktree is unchanged. Existing feature commits were replayed before repairs. Independent source review approved the exact hashes below on 3 October 2026.

## Local evidence

- 672 focused database, API, helper, access CLI, protection and identity tests passed on macOS with Python 3.12.13 and pytest 8.4.2. JUnit records zero failures, errors or skips. This is focused verification rather than the full combined release matrix.
- The three browser modules `address_move_privacy`, `address_move` and `dashboard_access` passed at 1920 by 1000 and 390 by 844 using Playwright 1.63.0 and Chrome for Testing 148.0.7778.96. The phone screenshot was inspected. The normal move journey checks horizontal overflow and all journeys reject page errors.
- The configured CI lint command passed: `ruff check --no-cache --select E9,F63,F7,F82,F811 src tests`. The full default style selection includes inherited findings and is not claimed green. `git diff --check` passed.
- Failing-first evidence reproduced credential clearing, cyclic Undo, independent access or protection replacement, post-commit conflict, compensating-write failure, interrupted Undo, legacy ownership refusal and a source edit during helper work. Later independent review reproduced the mixed-helper-version failure and the lost-reply case after destination registration changed before those fixes were applied.

The final regressions exercise actual HTTP and the helper socket. They cover stale capability with older helper refusal for both owned operations, strict ID and destination checks, explicit false access intent, same-pair pending retry, imported decisions, database snapshot mismatch, completed root receipt after a parent fsync failure and preservation of later protection edits. A pending or undoing record cannot be replaced or silently evicted. History overflow is detected. At 64 records a return move can be refused before pruning would free space, which is a deliberate safe refusal.

## Source hashes

- `src/jittersim/api/server.py`: `e491a93bce7f0cf3a23ba1376d95970ab5c4bd8650d842834d6da530144bfd38`
- `src/jittersim/config/exemptions.py`: `7fdb846ef1bd2922be415232fabca38348478a7bb025030bcfabf091fdf6d786`
- `src/jittersim/config/storage.py`: `b7b8bea82b58b8d893ee2e0a7fee86e1fccb237b36ba382a2cdc1a1900b883b1`
- `src/jittersim/networking/dashboard_access.py`: `fb49129144f255b9ded2a18741c79f212062dac67f0c98afb5919a9f91db002c`
- `src/jittersim/networking/helper.py`: `e2a3f92f5c06012b1ef485647829564bc3822bdbb86debb690e5b8d5fc108cd9`

## Evidence location and remaining release gates

Artifacts are retained in `test-results/identity-address-repair-2026-10-03` in the repair worktree. The same bundle is preserved in the primary repository at `test-results/identity-address-repair-2026-10-03`. Its manifest records artifact sizes and hashes. The browser stdout file is a summary of the captured successful output rather than a complete network log.

The original helper mutation names are never used by the repaired dashboard, compensation or recovery. Ownership-aware operations require the move ID. Moving access also requires the exact verified destination and an explicit boolean. A helper that does not know the new operation refuses before mutation.

The shared all-method stale-response fence from the reconnect repair remains a combined-source prerequisite. No shared request or token-check function was changed here. The address handlers advance the agreed access-trust generation, pause old reads and cancel reconnect readers when that function is available. They recheck with reads and never replay a write.

The release coordinator owns final combined verification, publication and deployment. Hosted CI is not claimed green. When GitHub capacity prevents it, run the configured pipeline checks locally under the authorized non-MAIA fallback. A SQLite backup is not a paired root access-file snapshot. Rollback to a pre-feature helper requires the matching root file snapshot or an upgraded recovery helper. Missing legacy ownership is refused rather than inferred. Verify the management recovery connection before deployment.
