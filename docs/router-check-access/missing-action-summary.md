## Local CI run local-ci-20261024-235732

- Commit: `b2d6209b8d154fbc979cb440316d00ec36c5185a` (branch `codex/router-check-access`): Merge pull request #250 from smartinezai/codex/readme-operate-screenshots
- Tree: `5130c5ff86e444a6a72536c04c018834641dbd93`
- Working tree: DIRTY (allowed with --allow-dirty): 1 changed paths
- Base: origin/main b2d6209b8d154fbc979cb440316d00ec36c5185a (0 commits ahead, 0 behind)
- Command: scripts/local-ci.sh browser --only router_copy --allow-dirty
- Scope: subset: --only router_copy (not a full browser run)
- Host: Darwin 27.0.0 arm64
- Tools: Python 3.12 (/Users/samantha.martinez/Documents/Codex/2026-10-24/task-12/router-check-worktree/.venv-local-ci-3.12/bin/python); ruff 0.17.2; pytest 8.4.2; playwright 1.63.0; node v22.23.1
- Python: this run covers Python 3.12 only; the workflow matrix is 3.9, 3.11 and 3.13 (see docs/local-ci.md)
- Started: 2026-10-24 23:57:32 +0200
- Finished: 2026-10-24 23:57:41 +0200
- Results: /Users/samantha.martinez/Documents/Codex/2026-10-24/task-12/router-check-worktree/test-results/local-ci-20261024-235732
- Hosted CI: Hosted GitHub Actions did not run (the account cannot start jobs). These are local results only.

| Step | Result | Exit | Seconds | Cases | Log |
|---|---|---|---|---|---|
| setup-venv-py3.12 | PASS | 0 | 1 | 1 passed | setup-venv-py3.12.log |
| setup-chromium | PASS | 0 | 1 | 1 passed | setup-chromium.log |
| browser:router_copy | FAIL | 1 | 7 | 1 failed | browser_router_copy.log |

Totals: 3 steps, 2 passed, 1 not passed (1 fail).
