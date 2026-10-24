# Manual Apply fresh identity repair

The release accepted a matching stale Wi-Fi client list as authority to save and start a new manual device profile. Manual Apply now refuses that evidence through the existing identity check and returns the existing HTTP 409 response asking the user to refresh the list. The check runs before saving or applying anything. Scheduler continuity remains unchanged.

## Source and scope

- Release baseline: `3f5499a99a08498e547cac2a26e2475c3fc9456d`.
- Local branch: `codex/manual-apply-fresh-identity`.
- Isolated checkout: `/Users/samantha.martinez/Documents/Codex/2026-10-24/task/manual-apply-fresh-identity`.
- Production change: only `ControlServer._verify_selected_client` in `src/jittersim/api/server.py`, adding the stale flag to the existing refusal condition with a comment explaining the distinction from scheduler continuity.
- New regression module: `tests/unit/test_device_apply_fresh_identity.py`.
- The same verifier serves connected-group saves, so those also refuse stale identity evidence. Their response contract is unchanged.
- No helper, engine, pinned dependency, UI, storage format, deployment or remote changes.

## Baseline proof before production edits

The initial new module ran against the release source with eight failures and five passes. The source verifier did not raise for matching stale identity. Actual manual Apply returned HTTP 200 with `live_applied=true` for stale data from both supported cache sources while reset, running saved profiles or running Everyone. It also accepted a new device after Reset.

A separate temporary-database proof recorded these actual results after a successful scan followed by a fake helper scan failure:

```json
{"snapshot_stale": true, "status": 200, "live_applied": true, "saved_profile": "slow", "configuration_version_before": 2, "configuration_version_after": 3, "helper_applies": 1, "runtime": "applied"}
```

Baseline logs are `/tmp/manual-apply-fresh-identity-baseline-loopback.log` and `/tmp/manual-apply-fresh-identity-baseline-proof.log`. An earlier sandbox attempt only failed to bind the existing test harness to its loopback port. The explicitly scoped elevated rerun produced the baseline proof above. No real helper or external network was used.

## Verification

The native `scripts/local_ci_exec.py` wrapper ran Python 3.12 with `PYTHONPATH=src` against the isolated checkout and existing installed test dependencies. No package installation or network access was needed. The existing API harness binds `127.0.0.1` on an ephemeral port and the new tests use temporary SQLite databases with a fake helper.

Final focused result: **137 passed** in 3.46 seconds across:

- `tests/unit/test_device_apply_fresh_identity.py` (14 cases)
- `tests/unit/test_device_apply_start.py`
- `tests/unit/test_device_apply_start_review.py`
- `tests/unit/test_device_apply_everyone.py`
- `tests/unit/test_helper_snapshot_cache.py`

The new tests cover both stale cache sources, unavailable identity without a cache, unchanged complete database contents and runtime state on refusal, unchanged helper plans and access state, refusal to create a new device, fresh scans with an omitted or false stale flag and preservation of scheduler plan construction during the same scan failure. Existing coverage exercises Reset/Panic ordering and Everyone behavior. Final focused log: `/tmp/manual-apply-fresh-identity-final-focused.log`.

Five existing connected-group cases also passed in 0.14 seconds, covering fresh client additions and protection refusals through the shared verifier. Log: `/tmp/manual-apply-fresh-identity-group-contracts.log`. Total focused verification: **142 passed**.

The repository's gating Ruff rules passed for the modified source and new test module. `git diff --check` passed. Full native, browser, Docker and Pi runs were intentionally outside this bounded task.

## Review and limits

The guard performs no I/O and adds no retries, new storage or growing work. The existing interval between a successful scan and helper Apply remains. A helper-level identity assertion would be a separate change. Submit the local commit and exact head for independent review before any push. The urgent learning checkpoint deferred here is explaining why stale evidence is suitable for keeping an existing schedule running but cannot authorize a new manual save.
