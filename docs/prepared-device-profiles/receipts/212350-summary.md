## Local CI run local-ci-20261024-212350

- Commit: `e95e9d239c5982050421ddbb993c4156fce54df7` (branch `codex/prepared-device-profiles`): Keep prepared profile controls at required touch size
- Tree: `24592e475c015993198785644e2eb08abf376a0c`
- Working tree: clean
- Base: origin/main 3f5499a99a08498e547cac2a26e2475c3fc9456d (105 commits ahead, 0 behind)
- Command: scripts/local-ci.sh browser --only prepared_devices
- Scope: subset: --only prepared_devices (not a full browser run)
- Host: Darwin 27.0.0 arm64
- Tools: Python 3.12 (/Users/samantha.martinez/Documents/Codex/2026-10-24/task-12/prepared-profiles-worktree/.venv-local-ci-3.12/bin/python); ruff 0.17.2; pytest 8.4.2; playwright 1.63.0; node v22.23.1
- Python: this run covers Python 3.12 only; the workflow matrix is 3.9, 3.11 and 3.13 (see docs/local-ci.md)
- Started: 2026-10-24 21:23:50 +0200
- Finished: 2026-10-24 21:23:54 +0200
- Results: /Users/samantha.martinez/Documents/Codex/2026-10-24/task-12/prepared-profiles-worktree/test-results/local-ci-20261024-212350
- Hosted CI: Hosted GitHub Actions did not run (the account cannot start jobs). These are local results only.

| Step | Result | Exit | Seconds | Cases | Log |
|---|---|---|---|---|---|
| setup-venv-py3.12 | PASS | 0 | 1 | 1 passed | setup-venv-py3.12.log |
| setup-chromium | PASS | 0 | 0 | 1 passed | setup-chromium.log |
| browser:prepared_devices | FAIL | 1 | 3 | 1 failed | browser_prepared_devices.log |

Totals: 3 steps, 2 passed, 1 not passed (1 fail).
