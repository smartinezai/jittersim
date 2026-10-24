## Local CI run local-ci-20261025-000255

- Commit: `a87425fe173cfc91b450bd8b68d336ec23f722ac` (branch `codex/readme-operate-screenshots`): Refresh README navigation and current dashboard screenshots
- Tree: `c68e8c480a49feb47e80cdbe69b2e3ba0d62d94c`
- Working tree: clean
- Base: origin/main b061d2d84439bf906f1da8ca9ae306cfa0b27f52 (0 commits ahead, 9 behind)
- Command: scripts/local-ci.sh browser --only router_copy
- Scope: subset: --only router_copy (not a full browser run)
- Host: Darwin 27.0.0 arm64
- Tools: Python 3.12 (/Users/samantha.martinez/Documents/Codex/2026-10-24/task-12/readme-worktree/.venv-local-ci-3.12/bin/python); ruff 0.17.2; pytest 8.4.2; playwright 1.63.0; node v22.23.1
- Python: this run covers Python 3.12 only; the workflow matrix is 3.9, 3.11 and 3.13 (see docs/local-ci.md)
- Started: 2026-10-25 00:02:55 +0200
- Finished: 2026-10-25 00:03:04 +0200
- Results: /Users/samantha.martinez/Documents/Codex/2026-10-24/task-12/readme-worktree/test-results/local-ci-20261025-000255
- Hosted CI: Hosted GitHub Actions did not run (the account cannot start jobs). These are local results only.

| Step | Result | Exit | Seconds | Cases | Log |
|---|---|---|---|---|---|
| setup-venv-py3.12 | PASS | 0 | 2 | 1 passed | setup-venv-py3.12.log |
| setup-chromium | PASS | 0 | 1 | 1 passed | setup-chromium.log |
| browser:router_copy | FAIL | 1 | 5 | 1 failed | browser_router_copy.log |

Totals: 3 steps, 2 passed, 1 not passed (1 fail).
