## Local CI run local-ci-20261024-234923

- Commit: `58f0141971fff3935cfaee1438ea4397f8fe639e` (branch `codex/skip-link-review`): Preserve tab-scroll reproduction and focused verification
- Tree: `a3195fab15084a1745990afd107685d9254ff11f`
- Working tree: DIRTY (allowed with --allow-dirty): 1 changed paths
- Base: origin/main 2b101abd0da9e9a1249baaf4d481c674adf05a4c (3 commits ahead, 0 behind)
- Command: scripts/local-ci.sh browser --only dashboard_navigation --allow-dirty
- Scope: subset: --only dashboard_navigation (not a full browser run)
- Host: Darwin 27.0.0 arm64
- Tools: Python 3.12 (/Users/samantha.martinez/Documents/Codex/2026-10-24/task-12/skip-link-worktree/.venv-local-ci-3.12/bin/python); ruff 0.17.2; pytest 8.4.2; playwright 1.63.0; node v22.23.1
- Python: this run covers Python 3.12 only; the workflow matrix is 3.9, 3.11 and 3.13 (see docs/local-ci.md)
- Started: 2026-10-24 23:49:23 +0200
- Finished: 2026-10-24 23:49:31 +0200
- Results: /Users/samantha.martinez/Documents/Codex/2026-10-24/task-12/skip-link-worktree/test-results/local-ci-20261024-234923
- Hosted CI: Hosted GitHub Actions did not run (the account cannot start jobs). These are local results only.

| Step | Result | Exit | Seconds | Cases | Log |
|---|---|---|---|---|---|
| setup-venv-py3.12 | PASS | 0 | 2 | 1 passed | setup-venv-py3.12.log |
| setup-chromium | PASS | 0 | 1 | 1 passed | setup-chromium.log |
| browser:dashboard_navigation | FAIL | 1 | 4 | 1 failed | browser_dashboard_navigation.log |

Totals: 3 steps, 2 passed, 1 not passed (1 fail).
