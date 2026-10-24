# Pending device storage evidence

Stage 1 stores prepared choices only. There is no activation path or user
workflow. The isolated branch starts at
3f5499a99a08498e547cac2a26e2475c3fc9456d. No original branch or remote was changed.

The new model and repository methods retain one normalized MAC choice with a
256-row limit, exclusive profile/protect action, allowed source, bounded name
and times. The repository sets saved time locally. It ignores caller revision
values when assigning a new durable SQLite revision. A revision check and row
limit run inside BEGIN IMMEDIATE. SQLite AUTOINCREMENT prevents an old revision
from targeting a replacement after delete/recreate, including across reopening.
The MAC remains the unique logical key. SQLite internal sequence metadata is the
only existing table content pending writes change. Live configuration version
and all existing application tables remain untouched.

A deferred NO ACTION profile reference permits settings replacement to delete
and recreate the same profile within its transaction. Omitting that profile
fails at commit and rolls back all changes. Existing profile reference reporting
names the prepared choice. Full database backups preserve choices. Settings
exports retain their current format and do not include pending choices.

## Focused checks

The direct Python 3.12 run passed 150 tests across:

- tests/unit/test_pending_devices.py: 12 tests
- tests/unit/test_storage.py
- tests/unit/test_storage_migrations.py
- tests/unit/test_changing_profiles.py

New checks cover malformed fields and forged verification/IP arguments,
normalized MAC ownership, local saved time, absent/stale revision conflicts,
delete/recreate after reopening, competing capacity 255 to 256 and competing
revision edits across connections. They compare existing application tables
including grants, groups and runtime state. Injected failures after insert,
edit, delete and existing settings writes restore rows, revision sequence and
configuration version. A deferred trigger failure additionally proves rollback
after mutation when the final commit fails. Repeated opening, profile reference
errors, settings import and SQLite backup are covered.

Full Ruff checks passed on the three new Python files. Repository gating Ruff
and git diff --check passed. The native wrapper command for the final commit is:

```sh
PIP_NO_INDEX=1 PIP_NO_BUILD_ISOLATION=0 scripts/local-ci.sh rerun \
  tests/unit/test_pending_devices.py tests/unit/test_storage.py \
  tests/unit/test_storage_migrations.py tests/unit/test_changing_profiles.py \
  --python 3.12 --step-timeout 60 --results-dir test-results/pending-final
```

The wrapper uses a private copy of cached Python 3.12 dependencies and installs
the local package. Shared runtimes are not changed. Wrapper output is retained
under test-results/pending-final and its exact commit is in that run's summary.

## Actual older repository proof

The complete earlier src tree was exported from the allocated base. The probe
runs a subprocess importing that tree, checks its source path and requires the
exact storage SHA256. Normal tests do not access Git or the network.

```sh
mkdir -p test-results/pending-device-compatibility/old
git archive 3f5499a99a08498e547cac2a26e2475c3fc9456d src | \
  tar -x -C test-results/pending-device-compatibility/old
PYTHONPATH=src .venv-local-ci-3.12/bin/python \
  docs/pending-device-compatibility-proof-2026-10-24.py \
  test-results/pending-device-compatibility/old/src
```

The exact earlier storage blob is
5fbcf70ea2babdde00ad8243a6acceb3307e854d. Its SHA256 is
`d6490102d49a475cdb9e318428de2d76bf1b4441944079fb2d3f0af70605f43c`.
The exported source and final results are retained locally in
`test-results/pending-device-compatibility/`.

Executed on the final schema:

- Old code creates a database without the new table. New code adds it.
- Old code opens and reads that database with foreign_keys=1. An unrelated
  profile write leaves pending rows intact and creates no active assignment.
- Old code cannot delete a referenced profile. All original rows and live
  configuration version remain intact and no transaction stays open.
- Old code importing settings without that profile fails atomically. All
  original rows, pending choice and configuration version remain intact.
- Old code importing settings retaining that profile succeeds across the
  temporary delete/reinsert gap. The pending choice remains unchanged.
- New code reopens twice and a SQLite backup restores the choice. Both versions
  keep foreign keys enabled and foreign_key_check reports no violations.

Older code reports `profile prepared is still in use` for deletion and
`could not replace the saved settings` for the refused import. Its import error
cannot identify the prepared choice. Remove the pending reference with new code
before deleting that profile with an older version. Do not disable foreign keys
or drop choices during rollback.

No API, server, dashboard, scheduler, engine, helper or pinned file changed.
No real router, Pi, network call, browser, Docker or heavy suite was used.
Physical acceptance and Stage 2 activation remain unimplemented and untested.
Publication waits for independent review of the final diff and these receipts.
