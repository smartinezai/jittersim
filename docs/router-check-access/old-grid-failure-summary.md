## Local CI run local-ci-20261025-000058

- Commit: `7aa7389fa2bdd31527e53dae5bf69ee61626cc18` (branch `codex/router-check-access`): Expose gated router Wi-Fi read action in persistent status bar
- Tree: `090cf81bbdba93b9715db3039d5000c96783513d`
- Working tree: clean
- Base: origin/main b061d2d84439bf906f1da8ca9ae306cfa0b27f52 (2 commits ahead, 4 behind)
- Command: scripts/local-ci.sh browser --only router_copy
- Scope: subset: --only router_copy (not a full browser run)
- Host: Darwin 27.0.0 arm64
- Tools: Python 3.12 (/Users/samantha.martinez/Documents/Codex/2026-10-24/task-12/router-check-worktree/.venv-local-ci-3.12/bin/python); ruff 0.17.2; pytest 8.4.2; playwright 1.63.0; node v22.23.1
- Python: this run covers Python 3.12 only; the workflow matrix is 3.9, 3.11 and 3.13 (see docs/local-ci.md)
- Started: 2026-10-25 00:00:58 +0200
- Finished: 2026-10-25 00:01:05 +0200
- Results: /Users/samantha.martinez/Documents/Codex/2026-10-24/task-12/router-check-worktree/test-results/local-ci-20261025-000058
- Hosted CI: Hosted GitHub Actions did not run (the account cannot start jobs). These are local results only.

| Step | Result | Exit | Seconds | Cases | Log |
|---|---|---|---|---|---|
| setup-venv-py3.12 | PASS | 0 | 3 | 1 passed | setup-venv-py3.12.log |
| setup-chromium | PASS | 0 | 0 | 1 passed | setup-chromium.log |
| browser:router_copy | FAIL | 1 | 4 | 1 failed | browser_router_copy.log |

Totals: 3 steps, 2 passed, 1 not passed (1 fail).
