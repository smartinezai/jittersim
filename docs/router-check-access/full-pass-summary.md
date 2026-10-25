## Local CI run local-ci-20261025-000452

- Commit: `5b943e91aad102c8d66278e741c2b062a9c40ba6` (branch `codex/router-check-access`): Keep router read status within compact bar and current Controls grid
- Tree: `756626354e056f7bdbbf5befffb0a3896b04d9fc`
- Working tree: clean
- Base: origin/main b061d2d84439bf906f1da8ca9ae306cfa0b27f52 (3 commits ahead, 4 behind)
- Command: scripts/local-ci.sh browser --only router_copy
- Scope: subset: --only router_copy (not a full browser run)
- Host: Darwin 27.0.0 arm64
- Tools: Python 3.12 (/Users/samantha.martinez/Documents/Codex/2026-10-24/task-12/router-check-worktree/.venv-local-ci-3.12/bin/python); ruff 0.17.2; pytest 8.4.2; playwright 1.63.0; node v22.23.1
- Python: this run covers Python 3.12 only; the workflow matrix is 3.9, 3.11 and 3.13 (see docs/local-ci.md)
- Started: 2026-10-25 00:04:52 +0200
- Finished: 2026-10-25 00:05:20 +0200
- Results: /Users/samantha.martinez/Documents/Codex/2026-10-24/task-12/router-check-worktree/test-results/local-ci-20261025-000452
- Hosted CI: Hosted GitHub Actions did not run (the account cannot start jobs). These are local results only.

| Step | Result | Exit | Seconds | Cases | Log |
|---|---|---|---|---|---|
| setup-venv-py3.12 | PASS | 0 | 1 | 1 passed | setup-venv-py3.12.log |
| setup-chromium | PASS | 0 | 1 | 1 passed | setup-chromium.log |
| browser:router_copy | PASS | 0 | 26 | 1 passed | browser_router_copy.log |

Totals: 3 steps, 3 passed, 0 not passed (none).
