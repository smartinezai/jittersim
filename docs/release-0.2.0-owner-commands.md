# JitterSim v0.2.0: the commands you run (23 October 2026)

**[OWNER]** only you can do it (key passphrase, sudo password, y/N answers). **[EITHER]** anyone can paste it. Every command below was checked against `scripts/release.sh`, `src/jittersim/cli.py` and `src/jittersim/remote_cli.py` on main. The reasons are in the [owner runbook](release-runbook-2026-10-16.md).

Facts on 23 October (main `03fe31d8`): no tag, no GitHub release, `docs/releases/registry.json` reserves nothing, `pyproject.toml` and `__init__.py` say 0.2.0. The release text (Unreleased entries plus [the notes file](release-notes-0.2.0-draft.md)) is 1865 bytes. The script stops above 15000.

## Before you start (all must be true)

1. **Main holds everything for 0.2.0.** Must be merged: PR 173 (release tooling, merged), the PR that added this page and the feature PRs the coordinator merges today (list: to be filled in when the last one has merged). Check that no PR you still want is open: `gh pr list --repo smartinezai/jittersim --state open`.
2. **Runbook sections 2 and 3 are done.** The Pi runs `0.2.0.dev+<short>` built from origin/main and `jittersim update check` on the Pi says `Online check token: set`.
3. **Panic check (third trap, not in the runbook).** The Pi installs a package only when its `panic_guard.py`, `networking/helper.py` and `networking/access.py` are in the list of the update manager it already runs. That list comes from the bootstrap build. If a merge after the bootstrap changed one of them, the Pi refuses the release and you must bootstrap again before step 3 below. [EITHER]:

   ```sh
   ssh -i ~/.ssh/jittersim_ed25519 -o IdentitiesOnly=yes jittersim@192.168.0.17 'cat /opt/jittersim/.jittersim-release.json'   # note "commit"
   git fetch origin main
   git diff --stat COMMIT origin/main -- src/jittersim/panic_guard.py src/jittersim/networking/helper.py src/jittersim/networking/access.py
   ```

   No output means fine. Output means run runbook section 2 again from the current origin/main.
4. **The Mac can talk to the dashboard (runbook section 4).** Today `~/.config/jittersim/cli.json` points at a CA file that is gone and there is no `jittersim` command on the Mac. Do runbook section 4, then `jittersim remote update status` must print the Pi's release. Set `JITTERSIM` to that command, for example `JITTERSIM=$HOME/.venvs/jittersim/bin/jittersim`.
5. **GitHub login.** `gh api user -q .login` prints `smartinezai`.

## The ordering traps

1. **Nothing else to the Pi** between the moment `release.sh` reserves the release and the moment the release is installed. A later development build has a newer release order and the Pi then refuses 0.2.0 as "not newer". Tell the other agents and Codex sessions.
2. **Bootstrap before the release PR.** After the record PR moves main to 0.2.1, a development build has a higher version number than 0.2.0 and the Pi refuses 0.2.0. So the bootstrap is done before step 3 and no development build is made after step 3 starts.

## Commands, in order

```sh
# 1. [EITHER] clean, current checkout (this one has the final notes file)
cd "/Users/samantha.martinez/Github repos/jittersim.worktrees/release-dryrun"
git fetch origin main && git checkout --detach origin/main && git log --oneline -1

# 2. [OWNER] your passphrase, kept 4 hours
ssh-add -t 14400 ~/.ssh/jittersim_release_ed25519

# 3. [OWNER] first run. 30 to 60 minutes until the first question. Keep the tab open.
JITTERSIM_RELEASE_LOCAL_CI=none caffeinate -i scripts/release.sh --notes-file docs/release-notes-0.2.0-draft.md 0.2.0
```

Do not add `--yes`: it merges and publishes without asking. Lint, the Python tests and the browser journeys always run and are signed. `JITTERSIM_RELEASE_LOCAL_CI=none` only skips the extra Docker run.

| The script asks | Answer |
|---|---|
| `Merge release PR #N into main now? [y/N]` | **y**, after the three checks passed. Main gets a merge commit. |
| `Have you installed and tested this exact package on the Pi? [y/N]` | First time **n**. It exits cleanly. Do step 4, then run the step 3 command again, unchanged. |
| `Tested Pi (model / OS / architecture / Python):` | See below. |
| `Evidence that '<gate>' passed (empty stops):` six times | See below. Write only what you did. |
| `Publish v0.2.0 on GitHub as the latest release? [y/N]` | **y**, after reading the dry-run lines above it (version, commit, hashes). |
| `Merge PR #N (release record and version 0.2.1) now? [y/N]` | **y**. Main is 0.2.1 from then on (trap 2). |

```sh
# 4. [OWNER] install the exact package on the Pi (the update service the dashboard uses, no sudo)
export JITTERSIM_ADMIN_TOKEN="$(security find-generic-password -s jittersim-admin-token -w)"   # or type it at the hidden prompt
$JITTERSIM remote update upload --archive ~/jittersim-releases/0.2.0/package/jittersim-release.tar.gz
$JITTERSIM remote update install --wait 900

# 5. [EITHER] read-only evidence from the Pi
ssh -i ~/.ssh/jittersim_ed25519 -o IdentitiesOnly=yes jittersim@192.168.0.17 \
  'cat /opt/jittersim/.jittersim-release.json; echo; jittersim update check; systemctl is-active jittersim-dashboard jittersim-helper jittersim-dns'
ssh -i ~/.ssh/jittersim_ed25519 -o IdentitiesOnly=yes jittersim@192.168.0.17 \
  'tr -d "\0" </proc/device-tree/model; echo; . /etc/os-release; echo "$PRETTY_NAME"; uname -m; python3 --version'
```

Expect `version` `0.2.0` and the reserved `sequence` in the first output. Then try the checks of the [release checklist](release-checklist-template.md) on the Pi: dashboard and shaping, saved settings kept, SSH over Ethernet or the router network, Panic. Then run step 3 again and answer **y**.

**Tested Pi**, the line from the 16 October read (use what step 5 printed): `Raspberry Pi 3 Model B Plus Rev 1.3 / Debian GNU/Linux 13 (trixie) / aarch64 / Python 3.13.5`

**Evidence wording** (change it to what you saw, keep tokens, passwords and MAC addresses out):

- `physical`: Installed 0.2.0 on the real Pi with remote update upload and install. The dashboard opened, a slow profile shaped one test phone and Panic stopped it. The Pi reports 0.2.0.
- `upgrade`: Upgraded in place from 0.2.0.dev+SHORT (bootstrap build of main) to 0.2.0 through the update service. The health check passed and no sudo was needed.
- `recovery`: choose the one that is true. (a) Restore previous installation was tested and the Pi ran 0.2.0.dev+SHORT again with dashboard, helper and DNS active. (b) Restore was not tested here and the recovery that was exercised is SAY WHAT. If you do (a), do it after every other check: a restore does not lower the Pi's release-order mark, so the Pi cannot install this package again and stays on the development build until 0.2.1.
- `saved_settings`: Profiles, device names and the protected-device list saved before the update were all there after it.
- `management_access`: SSH over Ethernet and the dashboard at https://netlab.local:8080 from the router network worked during and after the update without the test Wi-Fi.
- `safety_state`: The Panic state and the saved access rules were the same before and after the update and Panic still stops profiles.

## Verify

```sh
# [EITHER] GitHub: v0.2.0, both flags false, exactly the two files
gh release view v0.2.0 --repo smartinezai/jittersim --json tagName,isDraft,isPrerelease,assets -q '{tagName,isDraft,isPrerelease,assets:[.assets[].name]}'
gh api repos/smartinezai/jittersim/releases/latest -q .tag_name     # v0.2.0
$JITTERSIM remote update check                                      # the Pi asks GitHub with its own token
```

**[OWNER] Pi dashboard:** open `https://netlab.local:8080`, Updates, **Check for updates**. On a Pi that runs 0.2.0 it says `Already up to date: the newest release on GitHub (v0.2.0) is not newer than the installed one.` That is the success message: the Pi used its token, downloaded both files and checked the signature. The install button is first used for 0.2.1, because this Pi already ran the exact 0.2.0 package for the acceptance. On a Pi that never installed it, the same button says `Release 0.2.0 (v0.2.0) is staged and ready to install` and **Install update** installs it.

If it stops: `Required main checks are still running` (exit 78): wait a minute and run step 3 again. A failed check names a log: read it, then run step 3 again and only the checks repeat. A half-finished publish continues from the draft when you run step 3 again. Nothing here ever changes the Pi except step 4.
