## Local CI run local-ci-20261024-212602

- Commit: `ade2477c8c51aee135f444f96b9fb95211c7afcc` (branch `codex/prepared-device-profiles`): Reset synthetic Panic state explicitly between consumer viewports
- Tree: `8d3ca52913c49c3f4e331839b7a74e4d3f56984c`
- Working tree: clean
- Base: origin/main 3f5499a99a08498e547cac2a26e2475c3fc9456d (107 commits ahead, 0 behind)
- Command: scripts/local-ci.sh browser --only prepared_devices
- Scope: subset: --only prepared_devices (not a full browser run)
- Host: Darwin 27.0.0 arm64
- Tools: Python 3.12 (/Users/samantha.martinez/Documents/Codex/2026-10-24/task-12/prepared-profiles-worktree/.venv-local-ci-3.12/bin/python); ruff 0.17.2; pytest 8.4.2; playwright 1.63.0; node v22.23.1
- Python: this run covers Python 3.12 only; the workflow matrix is 3.9, 3.11 and 3.13 (see docs/local-ci.md)
- Started: 2026-10-24 21:26:02 +0200
- Finished: 2026-10-24 21:26:12 +0200
- Results: /Users/samantha.martinez/Documents/Codex/2026-10-24/task-12/prepared-profiles-worktree/test-results/local-ci-20261024-212602
- Hosted CI: Hosted GitHub Actions did not run (the account cannot start jobs). These are local results only.

| Step | Result | Exit | Seconds | Cases | Log |
|---|---|---|---|---|---|
| setup-venv-py3.12 | PASS | 0 | 1 | 1 passed | setup-venv-py3.12.log |
| setup-chromium | PASS | 0 | 1 | 1 passed | setup-chromium.log |
| browser:prepared_devices | PASS | 0 | 7 | 1 passed | browser_prepared_devices.log |

Totals: 3 steps, 3 passed, 0 not passed (none).
