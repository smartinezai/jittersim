## Local CI run local-ci-20261024-232814

- Commit: `468aa721defe7ea88f876325830e536c83e18e15` (branch `codex/fix-tab-scroll`): Keep dashboard header visible when switching pages
- Tree: `adb9f9b7922fe9a5a7caf6789e67f057b8055c0e`
- Working tree: clean
- Base: origin/main 2b101abd0da9e9a1249baaf4d481c674adf05a4c (2 commits ahead, 0 behind)
- Command: scripts/local-ci.sh browser --only dashboard_navigation
- Scope: subset: --only dashboard_navigation (not a full browser run)
- Host: Darwin 27.0.0 arm64
- Tools: Python 3.12 (/Users/samantha.martinez/Documents/Codex/2026-10-24/task-12/tab-scroll-worktree/.venv-local-ci-3.12/bin/python); ruff 0.17.2; pytest 8.4.2; playwright 1.63.0; node v22.23.1
- Python: this run covers Python 3.12 only; the workflow matrix is 3.9, 3.11 and 3.13 (see docs/local-ci.md)
- Started: 2026-10-24 23:28:14 +0200
- Finished: 2026-10-24 23:28:34 +0200
- Results: /Users/samantha.martinez/Documents/Codex/2026-10-24/task-12/tab-scroll-worktree/test-results/local-ci-20261024-232814
- Hosted CI: Hosted GitHub Actions did not run (the account cannot start jobs). These are local results only.

| Step | Result | Exit | Seconds | Cases | Log |
|---|---|---|---|---|---|
| setup-venv-py3.12 | PASS | 0 | 1 | 1 passed | setup-venv-py3.12.log |
| setup-chromium | PASS | 0 | 1 | 1 passed | setup-chromium.log |
| browser:dashboard_navigation | PASS | 0 | 18 | 1 passed | browser_dashboard_navigation.log |

Totals: 3 steps, 3 passed, 0 not passed (none).
