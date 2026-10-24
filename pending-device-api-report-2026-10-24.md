# Pending profile API review handoff

The private API can prepare, review, edit and remove pending choices without changing live policy. Explicit profile use extends the existing connected-device Apply route. It derives MAC/IP only from one current unique associated reachable helper row and checks the reviewed pending revision and configuration version again inside the existing published save transaction. Pending choices survive every outcome. Protection use remains refused.

## Source boundary

- Base: `cd3d36bf13a4a11c825258d5d7a79df86b23937d`.
- Branch: `codex/pending-device-api`.
- Checkout: `/Users/samantha.martinez/Documents/Codex/2026-10-24/task/pending-device-api`.
- New boundary module: `src/jittersim/api/pending_devices.py`.
- Narrow existing server changes: imports, private read/write dispatch, `WRITE_PATHS` registration and the existing device Apply snapshot/save callback hooks.
- New tests: `tests/unit/test_pending_device_api.py`.
- No storage, model, engine, helper, pinned file, UI or remote changes. The reviewed manual freshness commit and stable branches remain unchanged.

The locked activation review, corrected persistence plan `c547dc94` and acceptance matrix were read before implementation. A concise implementation plan was written before production source. The API has no new automatic activation, consumption, retry or global lock.

## Baseline and development proof

Before production edits, a negative real-route test submitted pending markers mixed with legacy caller MAC/IP/profile fields. The existing route returned HTTP 200 with `live_applied=true` and saved the device. That test failed against the stable baseline. Log: `/tmp/pending-device-api-baseline.log`.

Final development proof for the new module: **54 passed in 1.63 seconds**. The repository's native `scripts/local_ci_exec.py` wrapper ran Python 3.12 with `PYTHONPATH=src`, temporary SQLite databases and fake helpers. The existing API harness binds loopback only. No dependency installation or external service was used. Log: `/tmp/pending-device-api-dev-freeze.log`.

Covered cases include:

- Real HTTP missing/wrong token refusal and dashboard device admission for list, review, create, delete and use.
- Inert CRUD, normalisation, server saved time/revision, stale edit/delete conflicts, durable revision after delete/recreate, strict fields and the 256-choice cap.
- Review reads pending/profile/version within one repository transaction without helper I/O.
- Rejection of boolean versions, forged identity/profile overrides, stale/unavailable association, non-boolean truthy flags, absent/unreachable/wrong-spelling neighbour field, duplicate selected rows including invalid duplicates, multiple IPs, shared IP ownership and changed/private MAC.
- Protection and existing saved IP/generated-ID conflicts, prepared protection use refusal, changed configuration/profile and pending revision conflicts.
- Cross-connection pending/profile edits introduced after snapshot and before the final transaction.
- Exactly one helper snapshot and no helper calls inside the save transaction.
- Precommit validation failure rolls back the whole assignment/scope change.
- Barrier-controlled queued Reset/Panic and Reset arriving during snapshot acquisition refuse pending saves. Panic during helper Apply after commit does not trigger a restoration attempt.
- Healthy Apply and helper failures in saved-plan, Reset-scoped and Everyone contexts. Everyone uses a real scheduler and checks the failed restoration message. Reads and scans never retry a failed pending use.
- Pending retention on success and failure.

An initial development run had three test assertion mistakes comparing the repository's empty tuple with an empty list. They were corrected without a production change. The final run above is green. Gating Ruff (`E9,F63,F7,F82,F811`) and `git diff --check` pass.

## Review and formal validation status

Source is frozen for independent review. The formal medium affected-module validation is intentionally not run before that review. Proposed scope is this new module plus the existing device Apply start/review/Everyone, fresh-identity, helper cache and applicable Panic modules, followed by gating lint. The coordinator owns the final exact-head validation receipt and integration decision.

Usage readings were 14% remaining at start, baseline, midpoint and immediately before this handoff, above the 10% stop-expansion threshold and 8% floor. No reset or other service was used. Full native, browser, Docker, hardware and publication checks remain outside this allocation.

Identity is a point-in-time helper observation. This slice does not authenticate physical hardware or close the existing interval between a scan and helper application. Post-save failure behavior is deliberately the existing saved-but-unconfirmed, scoped cleanup or Everyone restoration result. No blanket rollback of committed configuration is claimed. The urgent deferred learning checkpoint is tracing how pre-wait control intent and final transaction checks address different races.
