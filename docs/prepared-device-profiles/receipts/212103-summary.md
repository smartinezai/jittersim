## Local CI run local-ci-20261024-212103

- Commit: `a0f6bd9738c6cca76229afdf3c70ed6804848894` (branch `codex/prepared-device-profiles`): Merge reviewed dashboard candidate acceptance backlog
- Tree: `38491ee8bfd28aaf083a5eb600c29e17debe637f`
- Working tree: clean
- Base: origin/main 3f5499a99a08498e547cac2a26e2475c3fc9456d (104 commits ahead, 0 behind)
- Command: scripts/local-ci.sh browser --only prepared_devices,device_apply_start,dashboard_access
- Scope: subset: --only prepared_devices,device_apply_start,dashboard_access (not a full browser run)
- Host: Darwin 27.0.0 arm64
- Tools: Python 3.12 (/Users/samantha.martinez/Documents/Codex/2026-10-24/task-12/prepared-profiles-worktree/.venv-local-ci-3.12/bin/python); ruff 0.17.2; pytest 8.4.2; playwright 1.63.0; node v22.23.1
- Python: this run covers Python 3.12 only; the workflow matrix is 3.9, 3.11 and 3.13 (see docs/local-ci.md)
- Started: 2026-10-24 21:21:03 +0200
- Finished: 2026-10-24 21:21:19 +0200
- Results: /Users/samantha.martinez/Documents/Codex/2026-10-24/task-12/prepared-profiles-worktree/test-results/local-ci-20261024-212103
- Hosted CI: Hosted GitHub Actions did not run (the account cannot start jobs). These are local results only.

| Step | Result | Exit | Seconds | Cases | Log |
|---|---|---|---|---|---|
| setup-venv-py3.12 | PASS | 0 | 1 | 1 passed | setup-venv-py3.12.log |
| setup-chromium | PASS | 0 | 2 | 1 passed | setup-chromium.log |
| browser:prepared_devices | FAIL | 1 | 3 | 1 failed | browser_prepared_devices.log |
| browser:device_apply_start | PASS | 0 | 2 | 1 passed | browser_device_apply_start.log |
| browser:dashboard_access | PASS | 0 | 7 | 1 passed | browser_dashboard_access.log |

Totals: 5 steps, 4 passed, 1 not passed (1 fail).
