# JitterSim first signed release (v0.2.0) - owner runbook, 2026-10-16

Who does what: **[OWNER]** only you can do it (passphrase, sudo password, GitHub web UI, y/N
decisions). **[CLAUDE]** Claude can do it for you (read-only checks, git and gh reads, drafting
text). **[EITHER]** a command anyone can paste.

Everything below was checked against the code of `origin/main` (48c948d) and read-only on the
Pi and on GitHub on 16 October. What could not be proven without publishing is listed in "Not proven".

**Status on 18 October 2026 (main `8c3f8171`).** PR 139 (step 1) and PR 138 (the Panic carry-forward
fixes, which cure the intermittent check failure named in step 7) have merged. No tag and no GitHub
release exist yet, and `docs/releases/registry.json` reserves no version. The facts about the Pi
and GitHub in section 0 are as read on 16 October and have to be read again before the steps run.
Main's pinned `helper.py` and `access.py` changed since: they are now `a955a151...` and
`41a0f6f9...`, which the Pi's old list does not hold either, so step 2 is still needed. Use the
short notes file `docs/release-notes-0.2.0-draft.md` for step 5.

**Status on 23 October 2026.** The commands in order, the y/N answers and the evidence wording are in
[release-0.2.0-owner-commands.md](release-0.2.0-owner-commands.md). It adds a third ordering trap: the
Pi checks a release against the Panic list of the update manager it already runs, so a Panic-pinned
file that changes after the bootstrap needs a new bootstrap before step 5.

## 0. Facts that decide the order

| Fact | Evidence |
|---|---|
| The Pi is a Raspberry Pi 3 Model B Plus (905 MB RAM), Debian 13 trixie, aarch64, Python 3.13.5, 11 GB free | `ssh` read-only |
| The Pi runs `0.2.0.dev+4b343b6` (release order 1791369579, 6 Oct). Its update manager `/usr/local/lib/jittersim-update.py` is byte-identical to `updates.py` of 4b343b6 (sha256 prefix 86c0f4ef79aa6781) | `ssh` + git |
| That manager only installs a package whose `panic_guard.py`, `networking/helper.py` and `networking/access.py` match its built-in list. Main's `helper.py` (252ccaff... on 16 October, `a955a151...` now) and `access.py` (ddc801a8... then, `41a0f6f9...` now) are not in the Pi's list. So the dashboard, `jittersim remote update` and `sudo jittersim update install` all refuse main and any release built from it ("The selected package cannot preserve Panic protection"). The Pi's status already shows an earlier refusal: "Panic protection needs management access" | `updates.py` at 4b343b6 vs main, `jittersim update check` on the Pi |
| So the first install of current code needs the root installer (`install.sh`) once. That is the only step that needs sudo besides saving the token | `deploy-from-mac.sh --use-installer` |
| The Pi already trusts your release key: `/etc/jittersim/release-signers` holds `mac-20261004` and it is the same public key as `~/.ssh/jittersim_release_ed25519.pub` (SHA256:Bkbj5L1t9Qi4dfbuOMxFxgPq416FGZs/YzDTuWJgyi8) | `ssh` read-only |
| The Pi has no GitHub token yet (`Online check token: not set`), passwordless staging is off (no `/etc/sudoers.d/jittersim-update`) | `jittersim update check`, `ls` |
| The Pi reaches `api.github.com` over HTTPS and its clock is synchronized | `ssh` read-only |
| The repo is private, `gh` on the Mac is signed in as `smartinezai`, merge commits are allowed, there is no branch protection (the free plan does not offer it for private repositories), hosted CI is billing-blocked (main's runs end `failure` with 0 steps, which is exactly what `release-system.py` treats as "never ran" and replaces with your signed local record) | `gh api` read-only |
| `~/.config/jittersim/cli.json` points at `/private/tmp/pi.pem`, which no longer exists, so every `jittersim remote ...` command on the Mac fails until step 4 | `ls` |

### Two ordering traps (the reason for the order below)

1. A development build deployed **after** `release.sh` reserved the release has a newer release
   order than the release. The Pi refuses any package whose order is not above the highest one it
   ever installed ("not newer"). Deploy nothing else to the Pi between step 5 starting and the
   release being installed. Other agents and Codex sessions count: tell them.
2. A development build made **after** the release record PR moved main to 0.2.1 has a higher
   version number than 0.2.0 and the Pi would refuse 0.2.0 as "an older number". So the
   bootstrap build is made now, from the current main (0.2.0), before step 5.

## 1. Merge the tooling PR first  [OWNER] (done: PR 139 merged on 17 October)

PR #139 (https://github.com/smartinezai/jittersim/pull/139) fixed six things that a real release would hit
(see its description). `release.sh` runs `release-system.py` from the `origin/main` source it cuts the
release from, so the publish fix only takes effect with the PR in main, which it now is. Then:

```sh
cd "/Users/samantha.martinez/Github repos/jittersim.worktrees/release-dryrun"   # [EITHER]
git fetch origin main
git checkout --detach origin/main
git log --oneline -1
```

Use this checkout (clean, nobody else edits it) for steps 2 and 5.

## 2. One-time bootstrap of the Pi with the current main  [OWNER, one sudo password]

Be on the router network or Ethernet (not the Pi's test Wi-Fi). Keep a second terminal open with
`ssh -i ~/.ssh/jittersim_ed25519 jittersim@192.168.0.17` and stay near the Pi: the installer
restarts the dashboard, helper and DNS briefly (a running test Wi-Fi is not restarted).

```sh
cd "/Users/samantha.martinez/Github repos/jittersim.worktrees/release-dryrun"
git fetch origin main
deploy/raspberry-pi/deploy-from-mac.sh --ref origin/main --use-installer --signing-key /nonexistent --dry-run   # [EITHER] only prints
deploy/raspberry-pi/deploy-from-mac.sh --ref origin/main --use-installer --signing-key /nonexistent             # [OWNER] the one command
```

- `--signing-key /nonexistent` builds an unsigned development archive on purpose: the root
  installer route checks the SHA-256 that the script computed, not a signature, so no key
  passphrase is asked here.
- The only prompt is the Pi's `sudo` password, once (one `ssh -t` session runs the checked
  unpack and then `install.sh`).
- It builds `0.2.0.dev+<short>` from the current `origin/main`, copies it, unpacks it into a new
  root-private folder `/root/jittersim-install-source-<full commit>` and runs the installer. If
  it says that folder exists, the same commit was tried before: pick a newer `--ref`.
- Success looks like `The Pi now runs release 0.2.0.dev+<short>`. The installer keeps the
  verified certificate authority and the credentials and checks the dashboard and helper at the
  end. The verification below confirms that the trusted key is still there.

Verify  [CLAUDE, read-only, or paste it yourself]:

```sh
ssh -i ~/.ssh/jittersim_ed25519 -o IdentitiesOnly=yes jittersim@192.168.0.17 \
  'cat /opt/jittersim/.jittersim-release.json; echo; jittersim update check; systemctl is-active jittersim-dashboard jittersim-helper jittersim-dns jittersim-update-fetch.service'
```

Expect `Installed: 0.2.0.dev+<short>`, `Trusted signing keys: 1`, dashboard/helper/dns `active`
(the fetch unit says `inactive` between checks, that is normal). The `sequence` in the first
line is the new high-water mark of the Pi. Note it.

If the installer stops at `a retained update must finish before changing dashboard access`:
the Pi still holds a failed update request. It stops before changing anything. Do not delete
anything under `/var/lib/jittersim-updates` by hand: tell Claude and open the dashboard Updates
page for the retry/recovery choice. (Not expected: the update unit logged "skipped, unmet
condition request.json" at the last boot, so no request file existed then.)

## 3. The read-only GitHub token on the Pi  [OWNER, one sudo password]

The Pi's "Check for updates" asks GitHub with a token that only root can read
(`/etc/jittersim/update-token`, mode 0600, root). It survives updates and rollbacks.

1. [OWNER] GitHub web UI: Settings, Developer settings, Personal access tokens, Fine-grained
   tokens, Generate new token. Resource owner `smartinezai`. Repository access: Only select
   repositories, `smartinezai/jittersim`. Permissions: Repository, Contents: **Read-only**
   (Metadata read is added by itself). Nothing else. Expiry: 90 days (put the renewal date in
   your calendar: after it, Check for updates says "GitHub refused the token").
2. [OWNER] Copy the token. Then, on the Mac:

```sh
ssh -t -i ~/.ssh/jittersim_ed25519 -o IdentitiesOnly=yes jittersim@192.168.0.17 'sudo jittersim update set-token'
```

   Enter the sudo password, then paste the token (nothing is shown) and press Ctrl-D. It prints
   "The GitHub token is saved". The token never goes on a command line, into a file on the Mac or
   into chat.
3. [CLAUDE or EITHER] `ssh ... 'jittersim update check'` must now say `Online check token: set`.

You can do this while the checks of step 5 run. It does not change the release order.

## 4. Let the Mac talk to the dashboard  [OWNER or CLAUDE after your OK]

`jittersim remote update ...` needs the Pi's certificate authority and your admin token.

```sh
mkdir -p ~/.config/jittersim
scp -i ~/.ssh/jittersim_ed25519 -o IdentitiesOnly=yes jittersim@192.168.0.17:/etc/jittersim/ca/ca.crt ~/.config/jittersim/pi-ca.crt
shasum -a 256 ~/.config/jittersim/pi-ca.crt
# must print 369117764045b53eacb683ee936a791f3b64a9a2758a58b666259e0c94a5c906 (read on the Pi today)
# The Mac command. Use the `jittersim` you already have:
JITTERSIM=$(command -v jittersim)
# Only if you have none (this shell had none), run these three lines instead:
python3.12 -m venv ~/.venvs/jittersim
~/.venvs/jittersim/bin/pip install "/Users/samantha.martinez/Github repos/jittersim.worktrees/release-dryrun"
JITTERSIM=$HOME/.venvs/jittersim/bin/jittersim
$JITTERSIM connect --url https://192.168.0.17:8080 --ca-cert ~/.config/jittersim/pi-ca.crt
```

(A normal, not editable, install on purpose: step 1 moves that checkout to another commit
whenever you repeat it, and an editable install would change with it.)

The admin token is read from `JITTERSIM_ADMIN_TOKEN` or asked with a hidden prompt, for example
`export JITTERSIM_ADMIN_TOKEN="$(security find-generic-password -s jittersim-admin-token -w)"`
(macOS asks to allow the Keychain read). Test with `$JITTERSIM remote update status`: it should
print `Release on the Pi: 0.2.0.dev+<short>`.

## 5. Run the release  [OWNER, in a Terminal tab]

Recommended: you run it, so the y/N questions are yours. Claude cannot answer them: they are read
from the terminal. (`--yes` would merge and publish without asking and needs your explicit go.)

```sh
cd "/Users/samantha.martinez/Github repos/jittersim.worktrees/release-dryrun"
ssh-add -t 14400 ~/.ssh/jittersim_release_ed25519          # your passphrase, kept 4 hours
JITTERSIM_RELEASE_LOCAL_CI=none caffeinate -i scripts/release.sh 0.2.0
```

- Why `0.2.0`: `pyproject.toml` and `__init__.py` say 0.2.0 on main, there is no tag and no
  release, and the registry is empty. The script refuses a version that is not newer than the
  latest `v*` tag on GitHub (there is none).
- `JITTERSIM_RELEASE_LOCAL_CI=none` turns off only the extra `scripts/local-ci.sh all` check
  (Docker kernel and Pi Linux jobs, hours on a shared Docker VM). The three checks the policy
  requires (lint, python-tests, browser-journeys) always run and are signed. Every PR in main
  was already gated by local-ci. If you want the Docker checks inside the release, use
  `JITTERSIM_RELEASE_LOCAL_CI=release-checks` (and expect an hour or more) or run
  `scripts/local-ci.sh release-checks` from another clean checkout beforehand  [CLAUDE can do that].
  Docker steps need a quiet Docker VM: nothing else may use Docker meanwhile (see "The Docker VM"
  in [local-ci.md](local-ci.md)).
- Release notes: GitHub's generated list for a first release is the whole history (about 115
  pull requests, 13.7 KB today). Approval accepts at most 16000 characters and the Pi shows 16 KB,
  so the script stops at the very start (nothing kept) when the notes pass 15000 bytes, which
  roughly 10 more merged PRs tonight would cause. Recommended: give a short text from the start,
  edit the draft first:
  `scripts/release.sh --notes-file docs/release-notes-0.2.0-draft.md 0.2.0`
  (Put it on the same command line as the variable and `caffeinate` above.) The hand-written
  `## Unreleased` entries of CHANGELOG.md are added above that text, and `release.sh` stops when the
  whole section, with its heading, passes 15000 bytes (`build-release.sh` refuses above 16384). On
  23 October 2026 (main `3afb48d5`) the two together are 2479 bytes, because the long list moved to
  `docs/history/0.2.0-development-changes.md`.

What happens, in order (about 30 to 60 minutes until the first question, longer when other
agents load the Mac; do not close the tab):

1. Creates `~/jittersim-releases/0.2.0/` (mode 700), a worktree `source/` on branch
   `release/v0.2.0` from `origin/main`, reserves 0.2.0 with release order = now (this is the
   moment trap 1 is about), sets the version, adds the notes to CHANGELOG.md and commits
   "Release v0.2.0".
2. Creates the check environment `venv/` and runs lint, python-tests, browser-journeys. Signs
   `evidence/local-verification.json` with your key (asks for the passphrase again if the agent
   forgot it).
3. Pushes `release/v0.2.0`, opens the release PR and asks
   `Merge release PR #N into main now? [y/N]`  **[OWNER: y]**. The merge is a merge commit on
   main.
4. Builds and signs the exact package `~/jittersim-releases/0.2.0/package/jittersim-release.tar.gz`
   (+ `.sig`, `release.json`, `acceptance.json`, the local record). Creates the local tag `v0.2.0`
   (not pushed). Release order is the reserved one.
5. Stops with `Have you installed and tested this exact package on the Pi? [y/N]`. Answer **n**
   (it exits cleanly), do step 6, then run the same command again.

### 6. Install and test the exact package on the Pi  [OWNER; CLAUDE can run the read-only checks]

This uses the update service of the Pi you bootstrapped in step 2: the same path the dashboard
uses, no sudo.

```sh
$JITTERSIM remote update upload --archive ~/jittersim-releases/0.2.0/package/jittersim-release.tar.gz   # signature checked on the Pi
$JITTERSIM remote update install --wait 900
```

Then the acceptance checks of `docs/release-checklist-template.md` (dashboard and shaping work,
upgrade from the previous build, saved settings kept, management access independent of the test
Wi-Fi, safety state unchanged, Restore previous installation). Read-only evidence Claude can
collect over SSH: `cat /opt/jittersim/.jittersim-release.json` (version `0.2.0`, the reserved
sequence), `jittersim update check`, `systemctl is-active ...`, the update journal.

**Decide before you test "Restore previous installation".** The Pi keeps a high-water mark that a
rollback does not lower (README: "checks against older releases ... have separate recovery
protections"). After a restore the Pi runs the dev build again and can only get 0.2.0 back
with a package of a higher release order, i.e. the next release. The gate text says "and back"
but the same package cannot go back. Options: (a) test the restore last, write that fact in the
evidence and leave the Pi on the dev build until 0.2.1; (b) leave the restore for the 0.2.1
acceptance and give `recovery` evidence only for a failed-install recovery you really exercised
(the bootstrap upgrade is `upgrade` evidence, not recovery). With neither, leave the `recovery`
answer empty: the run stops and approves nothing, which is right, because the checklist makes
recovery a release blocker. Never write "not tested": `release.sh` records any answer as passed
and signs it. Write only what you did.

### 7. Finish  [OWNER]

Run `JITTERSIM_RELEASE_LOCAL_CI=none scripts/release.sh 0.2.0` again (same variable, same
directory). It resumes from the saved state and asks:

1. `Have you installed and tested ...` -> `y`.
2. `Tested Pi (model / OS / architecture / Python)` -> for example
   `Raspberry Pi 3 Model B Plus Rev 1.3 / Debian GNU/Linux 13 (trixie) / aarch64 / Python 3.13.5`
   (read today on the Pi; change it if you test another).
3. One line of evidence for each of `physical`, `upgrade`, `recovery`, `saved_settings`,
   `management_access`, `safety_state`. An empty answer stops the run and approves nothing.
4. Signs the approval with your key (asks the passphrase only if the agent lost it), shows a
   dry run of the publication and asks `Publish v0.2.0 on GitHub as the latest release? [y/N]`
   **[OWNER: y]**. It then pushes the tag `v0.2.0` to `origin`, creates a draft, uploads the two
   files, downloads and compares them twice and makes it the latest release.
5. Opens the record PR (`Record the v0.2.0 release and start 0.2.1 development`) and asks
   `Merge PR #N ... now? [y/N]` **[OWNER: y]**. Main becomes 0.2.1 from that moment (trap 2).

If a check fails (`release: check python-tests failed (exit 1). See .../logs/python-tests.log`):
read the log. On 16 October a known intermittent failure was
`tests/qa/test_browser_explore.py::test_every_page_is_clean_at_desktop_and_phone_width` with a
502 from `/api/connected-devices` (`network changed during client scan`). It had a found cause (two
status reads counted as network changes) that PR 138 fixed on 18 October, so it should not come back.
Ask the other agents to keep their pytest and local-ci runs off the Mac while the checks run. The state is
kept: run the same command again and only the checks repeat (no new reservation, same release
order). The script's own advice ("delete the state and the branch") is for a real bug in main.
Ask the other agents to keep their pytest and local-ci runs off the Mac while the checks run.

If it stops with `Required main checks are still running` (exit 78): wait a minute, run the
same command again. If a publish stops half way (a draft exists), run the same command again:
it continues from the draft and never replaces an uploaded file.

## 8. Verify that the Pi sees the release  [CLAUDE, read-only, and OWNER for the button]

1. GitHub side  [CLAUDE]:
   `gh release view v0.2.0 --repo smartinezai/jittersim --json tagName,isDraft,isPrerelease,assets -q '{tagName,isDraft,isPrerelease,assets:[.assets[].name]}'`
   must show `v0.2.0`, both flags false and exactly `jittersim-release.tar.gz` and
   `jittersim-release.tar.gz.sig`. `gh api repos/smartinezai/jittersim/releases/latest -q .tag_name` must print `v0.2.0`.
2. Pi side, one of  [OWNER for 2a, EITHER for 2b and 2c]:
   - 2a Dashboard: Updates, **Check for updates**.
   - 2b Mac: `$JITTERSIM remote update check` (admin token as in step 4).
   - 2c Pi with sudo (one password): `ssh -t -i ~/.ssh/jittersim_ed25519 jittersim@192.168.0.17 'sudo jittersim update check --online'`.
3. Expected result when the Pi runs the release from step 6:
   `Already up to date: the newest release on GitHub (v0.2.0) is not newer than the installed one.`
   That is the success message. On the first online check after publication the Pi used its
   token, listed the latest release, downloaded both files, verified the signature with the
   trusted key `mac-20261004` and compared the release order: keep that first answer as the
   proof. A later check can show the same words without downloading again (the Pi remembers that
   it already looked at this release). `jittersim update check` then shows `Online check token: set`.
   If the Pi is still on a dev build (you skipped step 6), the same button says
   `Release 0.2.0 (v0.2.0) is staged and ready to install` and **Install v0.2.0** installs it.
4. Read the failures like this: `no GitHub token is set` -> step 3; `GitHub refused the token`
   -> expired or wrong token; `GitHub has no release here or this token cannot see the
   repository` -> the token is not for `smartinezai/jittersim` or lacks Contents read;
   `must hold exactly two files` -> the release has other assets; `The Pi ... is not newer` ->
   see trap 1.
5. For the next release (0.2.1 or later) the same Check for updates stages it and
   `jittersim remote update now` (or **Install update**) installs it with no sudo.

## Not proven (could not be done without publishing, sudo or the Pi)

- A real `gh release create` / upload / edit on GitHub. The code was fixed for the documented
  fact that `releases/tags/TAG` does not return drafts; the test stand-ins now answer like
  GitHub. A retry path exists if it still misbehaves.
- `release-system.py prepare` and `approve` against the live GitHub API (the dry run builds
  the package by the manual path). Their GitHub reads were checked by hand: main's runs end as
  `failure` with 0 steps for every job, which the code classifies as "never ran".
- The passphrase prompts in your Terminal (tested with a throwaway key and a private agent).
- The locked-wheel preparation and the install on the Pi 3B+, and the physical acceptance.
- `scripts/local-ci.sh all` as part of a release (its command is now valid; it was not run to
  the end).
