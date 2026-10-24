# Active source status reconciliation

This update will make the active planning documents describe source revision `309be4d7` and its reviewed composition. It will distinguish source present in this checkout from a release that has been installed, published or accepted on physical devices. The main risk is carrying forward evidence from the earlier `afa7d222` candidate as if it covered newer source. Open owner decisions and the installation and publication holds for PRs 175 and 209 remain unchanged.

## Scope and decisions

- Update `docs/planned-features.md`, `docs/planned-fixes.md` and the current-work section of `ROADMAP.md`, including the owner runbook sequence for Mac connection and a fresh checked backup before each Pi update.
- Record the reviewed PR composition through PR 228, including PR 227's content-cache change and fixture isolation PR 228 (`666f7b04`). Keep the local source distinct from any candidate that has not run its gates.
- Refresh source-dependent descriptions for the three-path content-keyed cache, summary polling, bounded DNS renewal behavior, immutable Docker image IDs, operator wording and the help target evidence. Preserve limits on physical acceptance and all open owner decisions.
- Keep the separate accessibility title work pending full native checks after source review. Do not alter historical handoffs or unrelated user-facing guidance.

## Review and checks

Check every status against this checkout and its verified evidence. Run only existing documentation-link tests or narrow documentation checks with rc-a Python 3.12, `PYTHONPATH=src:.` and bytecode/cache disabled. Amend the reviewed local documentation commit after the focused test passes. Do not run the full gate, push or merge.
