## Local CI run local-ci-20261025-103944

- Commit: `86828a3e6e1da67cc998b4d8553d18eb033e032b` (branch `codex/router-ci-fixture-repairs`): Preserve carriage returns in CSV exports on Python 3.9
- Tree: `ec265f14364eea02d5e5b6e9d2465eb215165912`
- Working tree: DIRTY (allowed with --allow-dirty): 3 changed paths
- Base: origin/main b061d2d84439bf906f1da8ca9ae306cfa0b27f52 (5 commits ahead, 4 behind)
- Command: scripts/local-ci.sh browser --only group_devices --allow-dirty --python 3.12 --results-dir test-results/router-ci-fixture-repairs/group-final --echo
- Scope: subset: --only group_devices (not a full browser run)
- Host: Darwin 27.0.0 arm64
- Tools: Python 3.12 (/Users/samantha.martinez/Documents/Codex/2026-10-24/task/router-ci-fixture-repairs/.venv-local-ci-3.12/bin/python); ruff 0.17.2; pytest 8.4.2; playwright 1.63.0; node v22.23.1
- Python: this run covers Python 3.12 only; the workflow matrix is 3.9, 3.11 and 3.13 (see docs/local-ci.md)
- Started: 2026-10-25 10:39:44 +0100
- Finished: 2026-10-25 10:40:00 +0100
- Results: /Users/samantha.martinez/Documents/Codex/2026-10-24/task/router-ci-fixture-repairs/test-results/router-ci-fixture-repairs/group-final/local-ci-20261025-103944
- Hosted CI: Hosted GitHub Actions did not run (the account cannot start jobs). These are local results only.

| Step | Result | Exit | Seconds | Cases | Log |
|---|---|---|---|---|---|
| setup-venv-py3.12 | PASS | 0 | 2 | 1 passed | setup-venv-py3.12.log |
| setup-chromium | PASS | 0 | 0 | 1 passed | setup-chromium.log |
| browser:group_devices | PASS | 0 | 14 | 1 passed | browser_group_devices.log |

Totals: 3 steps, 3 passed, 0 not passed (none).
