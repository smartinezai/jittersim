## Local CI run local-ci-20261024-232630

- Commit: `f807f389a9a2b843def59e0683310522d2edbef7` (branch `codex/fix-tab-scroll`): Reproduce tab navigation scrolling below the header
- Tree: `49f596fedffe01111770bd3b4b7d51c1fa567731`
- Working tree: clean
- Base: origin/main 2b101abd0da9e9a1249baaf4d481c674adf05a4c (1 commits ahead, 0 behind)
- Command: scripts/local-ci.sh browser --only dashboard_navigation
- Scope: subset: --only dashboard_navigation (not a full browser run)
- Host: Darwin 27.0.0 arm64
- Tools: Python 3.12 (/Users/samantha.martinez/Documents/Codex/2026-10-24/task-12/tab-scroll-worktree/.venv-local-ci-3.12/bin/python); ruff 0.17.2; pytest 8.4.2; playwright 1.63.0; node v22.23.1
- Python: this run covers Python 3.12 only; the workflow matrix is 3.9, 3.11 and 3.13 (see docs/local-ci.md)
- Started: 2026-10-24 23:26:30 +0200
- Finished: 2026-10-24 23:26:39 +0200
- Results: /Users/samantha.martinez/Documents/Codex/2026-10-24/task-12/tab-scroll-worktree/test-results/local-ci-20261024-232630
- Hosted CI: Hosted GitHub Actions did not run (the account cannot start jobs). These are local results only.

| Step | Result | Exit | Seconds | Cases | Log |
|---|---|---|---|---|---|
| setup-venv-py3.12 | PASS | 0 | 3 | 1 passed | setup-venv-py3.12.log |
| setup-chromium | PASS | 0 | 1 | 1 passed | setup-chromium.log |
| browser:dashboard_navigation | FAIL | 1 | 5 | 1 failed | browser_dashboard_navigation.log |

Totals: 3 steps, 2 passed, 1 not passed (1 fail).
