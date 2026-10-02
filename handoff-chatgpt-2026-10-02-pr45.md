# Handoff to ChatGPT: draft pull request 45 of JitterSim (2026-10-02)

You cannot see my conversation or memory so this note names every path and command. Read it top to bottom. It is written to be pasted whole into a ChatGPT (Codex) chat.

Remove this file before anything is merged to main. It lives on its own branch `docs/handoff-pr45-2026-10-02` and not in pull request 45.

## Goal

Make draft pull request 45 mergeable and green. It is `integrate/main-into-stack` merged into the Pi line `feat/service-profiles-and-pi-hardening`. Repository: github.com/smartinezai/jittersim (private). JitterSim is a Raspberry Pi network impairment tool in Python with a standard library HTTP dashboard in `src/jittersim/api/server.py` (inline JavaScript) and `src/jittersim/api/workflows.js`.

## What is done

Everything below is pushed to `origin/integrate/main-into-stack`. The head is `4065e72c3daa6ade4fb33b3812de3994ad777217`. The push was a fast-forward from `a1da456`. GitHub reported the pull request as MERGEABLE with state UNSTABLE right after the push. I did not wait for CI.

1. Merge commit `191e306` brings the Pi line (tip `7d98aa1`, pull request 57) into the branch. One content conflict in `src/jittersim/api/server.py` in the `POST /api/policies` handler. The integration side used a version-checked combined save `save_access_policies`. Pull request 57 wrote only the sections that were sent. Both now hold:
   - `ConfigRepository.save_access_policies` in `src/jittersim/config/storage.py` takes `None` for the device section or the website section and then leaves that stored section alone. It still checks the expected version first and still writes every given section plus site rules and options in one transaction.
   - The handler checks every section that was sent and then passes `None` for a section the request did not carry. An explicit empty section resets only that section. A request with no sections changes nothing.
   - The fixture in `tests/unit/test_partial_access_policies.py` needed `scheduler = None` and `helper_client = None` on the bare server. Five tests cover the combination of version check and partial save and site rules and rollback.
2. Commit `ee7cbd9` fixes a flaky journey `tests/browser/saved_profiles.py`. The dashboard unlocks before the token check ends. The check then wrote "Token accepted. Diagnostics loaded." over any newer message such as "Profile saved." The page change is in `showMessage` and `checkAdminToken`: a counter `messageSerial` lets the check skip its success message when something else was shown since it started. The journey now holds the last request of the check, writes a newer message, releases the request and expects the newer message to stay. That assertion failed before the page change.
3. Commit `b525655` fixes the flaky `tests/browser/impact_check.py` (CI run 37048789117 on pull request 54 saw 218 kbit/s instead of the 250 kbit/s cap). The fake kernel in `tests/browser/fixture.py` added a fixed 150000 bytes per read. It now grows the bytes at 250 kbit/s of elapsed time through an injectable clock. New unit tests in `tests/unit/test_browser_fixture.py`.
4. Commit `4065e72` declares `messageSerial` in the two node test harnesses `tests/unit/test_dashboard_auth_state.py` and `tests/unit/test_floating_credentials.py`. Without it 23 tests failed.

Local results on the final tree (Python 3.12):
- Full pytest: 2874 tests with 0 failed and 37 skipped.
- `ruff check --select E9,F63,F7,F82,F811 src tests`: clean.
- All 13 browser modules that CI runs passed with Playwright 1.63.0 and Chrome for Testing on macOS: journeys dashboard_access dashboard_navigation protected_clients router_copy device_names token_remember wifi_recovery saved_profiles general_profile apply_clarity sidebar_status impact_check.

## What is in progress

Nothing is running. My worktree is `/Users/samantha.martinez/Github repos/jittersim.worktrees/claude-int-merge` on local branch `claude-tmp-int`. It has an untracked virtual environment `.venv-claude/` that you may delete.

## What must still be done in order

1. Read the CI result for head `4065e72` on pull request 45. The Linux kernel checks (`tests/kernel/run-in-docker.sh`) never ran locally. CI uses Python 3.13. If anything is red read the job log and fix it in a new commit. Write the failing test first.
2. When CI is green tell Samantha. Only she takes a pull request out of draft or merges it.
3. After pull request 45 lands bring it forward into pull request 46 `feat/cli-remote-and-deploy` and then pull request 52 `feat/signed-releases`. Use merge commits. The coordination file `CLAUDE-TO-CHATGPT-COORDINATION.md` in the main checkout lists the open findings for those two (the response-header deadline in `remote_client.py` and the non-root `list-keys` in `updates.py`). Pull request 58 `codex/publish-homepage-reference` has its base on this branch and will need a forward merge too.
4. Later the online release check from `docs/implementation-plan-online-release-source-2026-10-03.md` once Samantha has settled the token question. Her decision on 2026-10-02 22:25 is in the coordination file.

## Rules to follow

- Never force-push. Never rebase a published branch. Bring a base branch in with a merge commit.
- Never merge a pull request. Never change a draft state. Never comment on a pull request.
- Never start or stop or delete Docker containers. The shared virtual machine is full. Never touch the real Pi or SSH or Wi-Fi or the router.
- Work in your own worktree and branch. Do not edit the main checkout `/Users/samantha.martinez/Github repos/jittersim` or any other worktree. Many belong to other running sessions.
- Run tests with `/Users/samantha.martinez/Github repos/jittersim/.venv/bin/python` and always set `PYTHONPATH` to `<your worktree>/src`. First confirm with `python -c "print(__import__('jittersim').__file__)"` that the import comes from your worktree.
- Browser journeys need Playwright 1.63.0 in a venv inside your worktree. Run each as `python -m tests.browser.<name>` with `PYTHONPATH` set to `<worktree>/src:<worktree>`. Point `JITTERSIM_BROWSER_EXECUTABLE` at a Chromium binary if you have one cached.
- Write the failing test first for every bug fix and confirm it fails.
- Commit messages use plain words and say what and why.
- Only send a pull request for review when the branch has a green build and CI on its head commit.

## Risks and open decisions

- The page change in `ee7cbd9` alters behavior. A finishing token check no longer replaces a newer message and its success toast is skipped in that case. The token status line still shows the result and a failed check always shows its error. Samantha may prefer a test-only fix. To get that revert `ee7cbd9` and `4065e72` and in `tests/browser/saved_profiles.py` wait for `#token-check-status` to contain "Token accepted. Diagnostics loaded" after sign-in.
- The fake kernel rate equals the cap. The page calls the cap reached from 90 percent of it so there is 10 percent room for jitter between the fixture read and the server timestamp. I did not test on a slow CI machine.
- `docs/planned-features.md` changed by 173 lines in the automatic merge. I did not review it line by line. The two README files merged cleanly and read fine.
- `.gitignore` covers `.venv/` and `venv/` only. Stage explicit paths and never use `git add -A` in a worktree that has `.venv-claude/`.
- Tests that build a bare `ControlServer` with `object.__new__` break whenever `write_response_for` starts reading another attribute. The pull request 57 fixture did exactly that and I patched it by setting `scheduler` and `helper_client`. Expect the same kind of break if that method grows.

## The first three actions

1. Run `git fetch --all --prune` in your own clone or worktree and confirm `git rev-parse origin/integrate/main-into-stack` prints `4065e72c3daa6ade4fb33b3812de3994ad777217`.
2. Run `gh pr view 45 --json mergeable,mergeStateStatus,isDraft` and `gh run list --branch integrate/main-into-stack --limit 5`.
3. If a run is red run `gh run view <run id> --log-failed` and fix the cause on a new commit. If all is green report that to Samantha and stop.

## How to check the state

```
git log --oneline -6 origin/integrate/main-into-stack
git diff --stat a1da456 origin/integrate/main-into-stack
PYTHONPATH="<worktree>/src" "/Users/samantha.martinez/Github repos/jittersim/.venv/bin/python" -m pytest -q
PYTHONPATH="<worktree>/src" "/Users/samantha.martinez/Github repos/jittersim/.venv/bin/python" -m ruff check --select E9,F63,F7,F82,F811 src tests
```

Expected log: `4065e72` then `b525655` then `ee7cbd9` then `191e306` then `7d98aa1` then `a1da456`.
