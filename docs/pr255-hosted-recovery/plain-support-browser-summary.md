## Local CI run local-ci-20261025-142831

- Commit: `12d7caa751ea0d734e0269e0add2d201079361ef` (branch `codex/pr255-hosted-recovery`): Keep request drain unit support independent of Playwright
- Tree: `e5ee9176f0a7605469f19361c5dd8656cc628ca7`
- Working tree: clean
- Base: origin/main 00f5cb0c206e6588612bd16d5cbd8a04a9843e08 (44 commits ahead, 2 behind)
- Command: scripts/local-ci.sh browser --only observation_confirmation --step-timeout 120 --results-dir test-results/plain-support-final
- Scope: subset: --only observation_confirmation (not a full browser run)
- Host: Darwin 27.0.0 arm64
- Tools: Python 3.12 (/private/tmp/js-pr255-recovery/.venv-local-ci-3.12/bin/python); ruff 0.17.2; pytest 8.4.2; playwright 1.63.0; node v22.23.1
- Python: this run covers Python 3.12 only; the workflow matrix is 3.9, 3.11 and 3.13 (see docs/local-ci.md)
- Started: 2026-10-25 14:28:31 +0100
- Finished: 2026-10-25 14:29:00 +0100
- Results: /private/tmp/js-pr255-recovery/test-results/plain-support-final/local-ci-20261025-142831
- Hosted CI: Hosted GitHub Actions did not run (the account cannot start jobs). These are local results only.

| Step | Result | Exit | Seconds | Cases | Log |
|---|---|---|---|---|---|
| setup-venv-py3.12 | PASS | 0 | 1 | 1 passed | setup-venv-py3.12.log |
| setup-chromium | PASS | 0 | 0 | 1 passed | setup-chromium.log |
| browser:observation_confirmation | PASS | 0 | 26 | 1 passed | browser_observation_confirmation.log |

Totals: 3 steps, 3 passed, 0 not passed (none).
