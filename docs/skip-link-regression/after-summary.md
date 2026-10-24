## Local CI run local-ci-20261024-235143

- Commit: `a0575b5ade3629af6f87c81c3a3c7af11c40a3c0` (branch `codex/skip-link-review`): Hide wrapped skip link by its own height until focus
- Tree: `b967ecdc8a9826d12a2c180e09658f5ec0dccf11`
- Working tree: clean
- Base: origin/main b2d6209b8d154fbc979cb440316d00ec36c5185a (2 commits ahead, 3 behind)
- Command: scripts/local-ci.sh browser --only dashboard_navigation
- Scope: subset: --only dashboard_navigation (not a full browser run)
- Host: Darwin 27.0.0 arm64
- Tools: Python 3.12 (/Users/samantha.martinez/Documents/Codex/2026-10-24/task-12/skip-link-worktree/.venv-local-ci-3.12/bin/python); ruff 0.17.2; pytest 8.4.2; playwright 1.63.0; node v22.23.1
- Python: this run covers Python 3.12 only; the workflow matrix is 3.9, 3.11 and 3.13 (see docs/local-ci.md)
- Started: 2026-10-24 23:51:43 +0200
- Finished: 2026-10-24 23:52:04 +0200
- Results: /Users/samantha.martinez/Documents/Codex/2026-10-24/task-12/skip-link-worktree/test-results/local-ci-20261024-235143
- Hosted CI: Hosted GitHub Actions did not run (the account cannot start jobs). These are local results only.

| Step | Result | Exit | Seconds | Cases | Log |
|---|---|---|---|---|---|
| setup-venv-py3.12 | PASS | 0 | 1 | 1 passed | setup-venv-py3.12.log |
| setup-chromium | PASS | 0 | 1 | 1 passed | setup-chromium.log |
| browser:dashboard_navigation | PASS | 0 | 19 | 1 passed | browser_dashboard_navigation.log |

Totals: 3 steps, 3 passed, 0 not passed (none).
