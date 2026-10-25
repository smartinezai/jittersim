## Local CI run local-ci-20261025-145335

- Commit: `2a9dadbeb8f7a16b6e55bd7b8a51dc15ab620e98` (branch `codex/pr255-hosted-recovery`): Record plain test-support verification and collection correction
- Tree: `597e46c7a49c6d3b43baf2bddb472b2827210483`
- Working tree: DIRTY (allowed with --allow-dirty): 2 changed paths
- Base: origin/main 00f5cb0c206e6588612bd16d5cbd8a04a9843e08 (45 commits ahead, 2 behind)
- Command: scripts/local-ci.sh browser --allow-dirty --only traffic_chart --step-timeout 180 --results-dir test-results/final-two-fixtures
- Scope: subset: --only traffic_chart (not a full browser run)
- Host: Darwin 27.0.0 arm64
- Tools: Python 3.12 (/private/tmp/js-pr255-recovery/.venv-local-ci-3.12/bin/python); ruff 0.17.2; pytest 8.4.2; playwright 1.63.0; node v22.23.1
- Python: this run covers Python 3.12 only; the workflow matrix is 3.9, 3.11 and 3.13 (see docs/local-ci.md)
- Started: 2026-10-25 14:53:35 +0100
- Finished: 2026-10-25 14:54:46 +0100
- Results: /private/tmp/js-pr255-recovery/test-results/final-two-fixtures/local-ci-20261025-145335
- Hosted CI: Hosted GitHub Actions did not run (the account cannot start jobs). These are local results only.

| Step | Result | Exit | Seconds | Cases | Log |
|---|---|---|---|---|---|
| setup-venv-py3.12 | PASS | 0 | 1 | 1 passed | setup-venv-py3.12.log |
| setup-chromium | PASS | 0 | 1 | 1 passed | setup-chromium.log |
| browser:traffic_chart | PASS | 0 | 68 | 1 passed | browser_traffic_chart.log |

Totals: 3 steps, 3 passed, 0 not passed (none).
