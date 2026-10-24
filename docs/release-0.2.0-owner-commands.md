# JitterSim v0.2.0: the commands you run (23 October 2026)

**[OWNER]** only you can do it (key passphrase, sudo password, y/N answers). **[EITHER]** anyone can paste it. Every command below was checked against `scripts/release.sh`, `src/jittersim/cli.py` and `src/jittersim/remote_cli.py` on main. The reasons are in the [owner runbook](release-runbook-2026-10-16.md).

Facts on 24 October (main `174a304e`, later commits change only one test file and documents): no tag, no GitHub release, `docs/releases/registry.json` reserves nothing, `pyproject.toml` and `__init__.py` say 0.2.0. The release text (Unreleased entries plus [the notes file](release-notes-0.2.0-draft.md)) is about 4 KB and changes when either changes. The script stops above 15000 bytes.

## Before you start (all must be true)

1. **Main holds everything for 0.2.0.** Merged: PR 173 (release tooling), 180 and 202 (this page and the notes), and the features and fixes of 23 October: 181 This device, 182 Reset Wi-Fi name button, 161, 186 and 207 Check connection, 189 dashboard speed test, 188 block list of 2000 names, 201 DNS lookup pool and shutdown, 185 presets, 179 backup pull, 199 shared readings, 200, 203, 204, 187 and 208 tests, QA fixes and review follow-ups, 177, 183, 195 to 198 cleanups, runbook and locks, 205 the [acceptance card](owner-acceptance-card-2026-10-24.md), plus the docs and tests only PRs 174, 176, 178, 184, 190 and 206. Not in it: PR 175 (helper.py) waits until after v0.2.0. `gh pr list --repo smartinezai/jittersim --state open` shows nothing you still want in 0.2.0.
2. **Runbook sections 2 and 3 are done.** The Pi runs `0.2.0.dev+<short>` built from origin/main and `jittersim update check` on the Pi says `Online check token: set`.
3. **The Mac can talk to the dashboard (runbook section 4).** Today `~/.config/jittersim/cli.json` points at a CA file that is gone and there is no `jittersim` command on the Mac. After section 4, `jittersim remote update status` must print the Pi's release. Set `JITTERSIM` to that command, for example `JITTERSIM=$HOME/.venvs/jittersim/bin/jittersim`.
4. **GitHub login.** `gh api user -q .login` prints `smartinezai`.
5. **Read the notes you sign.** They go into the package before you test it. If the Known limits sentence "Tested on one Raspberry Pi 3 Model B Plus" will not be true after your acceptance, edit `docs/release-notes-0.2.0-draft.md` and merge it first.

## The Panic trap and the merge freeze (the runbook only points here)

The Pi installs a package only when its `panic_guard.py`, `networking/helper.py` and `networking/access.py` are in the list of the update manager it already runs, and that list comes from the bootstrap build. If one of them changed after the bootstrap, the Pi refuses the release at step 4, after the release is reserved, and only 0.2.1 can fix that. So:

- **Merge nothing into main from the start of step 3 until the record PR is merged.** PR 175 (changes `helper.py`) and every other change to those three files waits until after v0.2.0. The runbook's two traps still hold: nothing else goes to the Pi after step 3 starts and no development build is made after the record PR.
- **Run this check now and again right before you answer y to the first question** [EITHER], in the checkout of step 1. No output means fine. Output means bootstrap again (runbook section 2) first.

```sh
COMMIT=$(ssh -i ~/.ssh/jittersim_ed25519 -o IdentitiesOnly=yes jittersim@192.168.0.17 'cat /opt/jittersim/.jittersim-release.json' | python3 -c 'import json,sys; print(json.load(sys.stdin)["commit"])')
git fetch origin main
git diff --stat "$COMMIT" origin/main -- src/jittersim/panic_guard.py src/jittersim/networking/helper.py src/jittersim/networking/access.py
```

## Commands, in order

```sh
# 1. [EITHER] clean, current checkout (this one has the final notes file)
cd "/Users/samantha.martinez/Github repos/jittersim.worktrees/release-dryrun"
git fetch origin main && git checkout --detach origin/main && git log --oneline -1

# 2. [OWNER] your passphrase, kept 4 hours
ssh-add -t 14400 ~/.ssh/jittersim_release_ed25519

# 3. [OWNER] first run. 30 to 60 minutes until the first question. Keep this tab open:
#    every "run step 3 again" below means this tab, this directory, this exact line.
JITTERSIM_RELEASE_LOCAL_CI=none caffeinate -i scripts/release.sh --notes-file docs/release-notes-0.2.0-draft.md 0.2.0
```

Do not add `--yes`: it merges and publishes without asking. Lint, the Python tests and the browser journeys always run and are signed. `JITTERSIM_RELEASE_LOCAL_CI=none` only skips the extra Docker run.

| The script asks | Answer |
|---|---|
| `Merge release PR #N into main now? [y/N]` | **y**, after the three checks passed and the Panic check above is clean. Main gets a merge commit. |
| `Have you installed and tested this exact package on the Pi? [y/N]` | First time **n**. It exits cleanly. Do step 4, then run step 3 again. |
| `Tested Pi (model / OS / architecture / Python):` | See below. |
| `Evidence that '<gate>' passed (empty stops):` six times | See below. Write only what you did. |
| `Publish v0.2.0 on GitHub as the latest release? [y/N]` | **y**, after the dry run above it printed `Release operation completed: publish`. Version and commit: `cat ~/jittersim-releases/0.2.0/package/release.json`. |
| `Merge PR #N (release record and version 0.2.1) now? [y/N]` | **y**. Main is 0.2.1 from then on. |

**Before step 4, take a fresh database backup.** New profiles or other settings may have been added since the previous backup. Run this on the Mac [OWNER], using the connection from the prerequisites above. Enter the admin token only at the hidden prompt:

```sh
"$JITTERSIM" backup pull
```

Continue only when the command succeeds and prints the new file's path in `~/jittersim-backups/`. Keep that file; an older backup is not a substitute. The command checks that the download is complete and looks like SQLite, not that its contents are sound. The [backup drill](user-guide.md#back-up-to-your-mac-and-check-the-copy) gives the separate database check. Repeat the fresh backup before any later Pi update.

**Also write down** the profile names, the device names, the protected-device list, whether Panic is on and the access rules. You compare after the install.

```sh
# 4. [OWNER] install the exact package on the Pi (the update service the dashboard uses, no sudo).
#    The token stays on these two lines only. Leave the variable out to type it at the hidden prompt.
JITTERSIM_ADMIN_TOKEN="$(security find-generic-password -s jittersim-admin-token -w)" $JITTERSIM remote update upload --archive ~/jittersim-releases/0.2.0/package/jittersim-release.tar.gz
JITTERSIM_ADMIN_TOKEN="$(security find-generic-password -s jittersim-admin-token -w)" $JITTERSIM remote update install --wait 900

# 5. [EITHER] read-only evidence from the Pi
ssh -i ~/.ssh/jittersim_ed25519 -o IdentitiesOnly=yes jittersim@192.168.0.17 \
  'cat /opt/jittersim/.jittersim-release.json; echo; jittersim update check; systemctl is-active jittersim-dashboard jittersim-helper jittersim-dns'
ssh -i ~/.ssh/jittersim_ed25519 -o IdentitiesOnly=yes jittersim@192.168.0.17 \
  'tr -d "\0" </proc/device-tree/model; echo; . /etc/os-release; echo "$PRETTY_NAME"; uname -m; python3 --version'
```

Expect `version` `0.2.0` and the reserved `sequence` in the first output. Then do the checks of the [release checklist](release-checklist-template.md) on the Pi: dashboard and shaping, a protected device, saved settings kept, a reboot of the Pi (the test Wi-Fi and the dashboard come back), SSH over Ethernet or the router network, Panic. Compare with what you wrote down. Then run step 3 again and answer **y**. If the same check fails twice, stop and ask.

**Tested Pi**, the line from the 16 October read (use what step 5 printed): `Raspberry Pi 3 Model B Plus Rev 1.3 / Debian GNU/Linux 13 (trixie) / aarch64 / Python 3.13.5`

**Evidence wording** (change it to what you saw, keep tokens, passwords and MAC addresses out):

- `physical`: Installed 0.2.0 on the real Pi with remote update upload and install. The dashboard opened, a slow profile shaped one test phone, a protected device stayed unshaped, Panic stopped the profile and the Pi came back after a reboot with the test Wi-Fi and the dashboard working.
- `upgrade`: Upgraded in place from 0.2.0.dev+SHORT (bootstrap build of main) to 0.2.0 through the update service. The health check passed and no sudo was needed.
- `recovery`: choose the one that is true. (a) Restore previous installation was tested and the Pi ran 0.2.0.dev+SHORT again with dashboard, helper and DNS active. (b) Restore was not tested here and a failed-install recovery was exercised: SAY WHAT. The upgrade to 0.2.0 is `upgrade` evidence, not recovery. If neither is true, leave the answer empty: the run stops and approves nothing (a line like "not tested" would be signed as passed). If you do (a), do it after every other check: a restore does not lower the Pi's release-order mark, so the Pi cannot install this package again and stays on the development build until 0.2.1.
- `saved_settings`: The profiles, device names and protected-device list I wrote down before the update were all there after it.
- `management_access`: SSH over Ethernet and the dashboard at https://netlab.local:8080 from the router network worked during and after the update without the test Wi-Fi.
- `safety_state`: The Panic state and the access rules were the same as I wrote down before the update and Panic still stops profiles.

## Verify

```sh
gh release view v0.2.0 --repo smartinezai/jittersim --json tagName,isDraft,isPrerelease,assets -q '{tagName,isDraft,isPrerelease,assets:[.assets[].name]}'   # v0.2.0, false, false, the two files
gh api repos/smartinezai/jittersim/releases/latest -q .tag_name     # v0.2.0
$JITTERSIM remote update check                                      # the Pi asks GitHub with its own token
```

**[OWNER] Pi dashboard:** open `https://netlab.local:8080`, Updates, **Check for updates**. On the Pi that ran the acceptance it says `Already up to date: the newest release on GitHub (v0.2.0) is not newer than the installed one.` That is the success message. Only the first check after publication proves the download and signature: keep its answer, because later checks can repeat the words without downloading again. The **Install v0.2.0** button is first used on a Pi that never installed 0.2.0, and on this Pi for 0.2.1. More in runbook section 8.

If it stops with `Required main checks are still running` (exit 78), wait a minute and run step 3 again. A failed check names a log: read it and run step 3 again. A half-finished publish continues from the draft.
