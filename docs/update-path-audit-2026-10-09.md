# Update and release path audit (2026-10-09)

Goal from the owner: update the Pi from the dashboard or the terminal without a
bunch of hoops, keep the README correct and keep GitHub releases current.
This audit maps the chain on `origin/main` at `70a1301`, lists every manual step
a person takes today, marks which steps protect the household Pi and which are
accidental, and sets the plan for the changes that follow.

## 1. The chain today

```
Mac (owner)                                GitHub                      Pi
-----------                                ------                      --
release-system.py reserve/prepare  --->   tag vX.Y.Z + release   ---> jittersim-update-fetch.service
  build-release.sh (git archive)          2 assets:                    (root, token in /etc/jittersim)
  sign-release.sh (ssh-keygen -Y)          jittersim-release.tar.gz      stage_release(): signature,
release-system.py approve/publish          jittersim-release.tar.gz.sig   sequence, archive checks
                                                                       jittersim-update.service
deploy-from-mac.sh (dev builds, SSH)  -----------------------------> apply_update(): recovery copy,
deploy-over-https.sh / jittersim remote update upload (HTTPS) ----->   install, health check,
                                                                       automatic rollback
```

| Part | File | What it does |
|---|---|---|
| Updates page and sidebar badge | `src/jittersim/api/workflows.js` (`installReleaseControls`) | Check for updates (runs the online check when a token is saved), Install update, Restore previous installation, Retry, daily check and install automatically settings |
| Routes | `src/jittersim/api/workflows.py` | `GET /api/updates`, `GET/POST /api/updates/check-online`, `POST /api/updates/install`, `/rollback`, `/retry`, `/online-settings`; `POST /api/updates/upload` in `server.py` |
| Root side | `src/jittersim/networking/helper.py` | Passes those requests to `updates.py` as root |
| Signing, staging, install, rollback | `src/jittersim/updates.py` | `stage_release` (ssh-keygen verify against `/etc/jittersim/release-signers`, release order, archive checks), `check_online` (GitHub latest release, token only to `api.github.com`), `apply_update` (recovery copy, prepared wheels, health check, automatic restore) |
| Pi terminal | `src/jittersim/cli.py` | `jittersim update check`, `check --online`, `install --yes --wait`, `rollback`, `retry`, `stage`, `trust-key`, `set-token` |
| Mac terminal | `src/jittersim/remote_cli.py`, `remote_client.py` | `jittersim remote update status/upload/install` over HTTPS |
| Mac scripts | `deploy/raspberry-pi/` | `build-release.sh`, `sign-release.sh`, `make-release-key.sh`, `deploy-from-mac.sh`, `deploy-over-https.sh`, `release-system.py`, `publish-release.sh` |
| Docs | `README.md`, `deploy/raspberry-pi/README.md`, `docs/releases/README.md`, `docs/release-checklist-template.md` | Setup, signed releases, stable release steps |

## 2. Dry runs on the Mac (no Pi, throwaway key)

All in a scratch folder with a fresh `ssh-keygen -t ed25519` key. Nothing touched
the Pi, the router, GitHub state or the owner's key.

| Step | Command | Result |
|---|---|---|
| Version label | `build-release.sh --version HEAD` | `0.2.0.dev+70a1301` |
| Build and sign | `build-release.sh HEAD OUT KEY` | archive 668 KB plus `.sig`; manifest `{"format": 1, "commit": "70a1301…", "version": "0.2.0.dev+70a1301", "sequence": 1791564961}` |
| Manifest check | `check-release-manifest.sh` | passes, prints the sequence (build time in seconds) |
| Trust, stage, status, queue (temp folders) | `updates.trust_key`, `stage_release`, `update_status`, `request_update` | staged, installable, signed by the test key, install queued with `systemctl start --no-block jittersim-update.service` |
| Dev build over an installed `0.2.0` | `update_status` | refused: "The staged release is an older number than the installed one. Use Restore previous installation to go back." |
| Reserve a stable version | `release-system.py reserve 0.2.0` | refused: "Inspect supported installations and initialize their release-order floor first." |
| Stable CI gate on main | `release-system.verify_ci(main)` (read-only GitHub) | refused: "A required main workflow failed." Every hosted job on main ended `failure` with zero steps run (billing block) |
| Mac deploy | `deploy-from-mac.sh --dry-run --ssh` | one command: build, sign, copy, `sudo jittersim update stage`, `sudo jittersim update install --yes --wait` |
| GitHub | `gh release list`, `git tag -l` | no release and no tag exist, so the Pi's online check has nothing to find |

## 3. Hoops today

E = essential for safety (keep). A = accidental (remove or automate).

### (a) Update from the dashboard

| # | Step | Kind |
|---|---|---|
| 1 | One time: trust the owner's public key on the Pi (`sudo jittersim update trust-key`) | E |
| 2 | One time: create a read-only fine-grained token and `sudo jittersim update set-token` | E (private repo, decided 2026-10-03) |
| 3 | Sign in with the admin token | E |
| 4 | Check for updates | E (one click) |
| 5 | Install update and confirm | E (one confirmed click) |
| 6 | No GitHub release exists, so steps 4 and 5 never offer anything | A (release path blocked, see d) |
| 7 | No release notes before installing; button says "Install update" not the version; recovery copy shown only as an enabled Restore button | A |

### (b) Update from the Pi terminal

| # | Step | Kind |
|---|---|---|
| 1 | `sudo jittersim update check --online` to fetch and stage | A (separate step) |
| 2 | `sudo jittersim update check` to read what was staged | A |
| 3 | `sudo jittersim update install --yes --wait` (`--wait` needs root, flags to remember) | A |
| 4 | Signature, release order, recovery copy, health check and automatic rollback | E (already automatic in `apply_update`) |
| 5 | `update check` still says "It does not check the private repository online" | A (stale text) |

### (c) Update from the Mac

| # | Step | Kind |
|---|---|---|
| 1 | Dev build: `deploy-from-mac.sh` (one command, sudo password unless passwordless staging) | E (sudo or the signed passwordless rule) |
| 2 | HTTPS path: `jittersim remote update upload` then `install --wait 600` | A (two steps) |
| 3 | No way to ask the Pi to check GitHub from the Mac terminal | A |
| 4 | `remote update install --wait` does not notice a failed install or rollback and waits until its time runs out | A |
| 5 | After a stable `X.Y.Z` is installed every dev build of main is refused with a misleading message because main still says `X.Y.Z` | A (no post-release version bump) |
| 6 | Dashboard health shows the package number (`0.2.0`) while Updates shows the release label (`0.2.0.dev+70a1301`) | A |

### (d) Cut a new release

| # | Step | Kind |
|---|---|---|
| 1 | Inspect the highest release order on every Pi by hand (no command prints it) and `initialize --floor` | A (dev builds use build time, so a time-based order makes this unnecessary) |
| 2 | `reserve X.Y.Z` | A (scriptable) |
| 3 | Edit `pyproject.toml` and `src/jittersim/__init__.py` by hand | A |
| 4 | Write release notes by hand; no `CHANGELOG.md` | A |
| 5 | Commit, PR, merge the release commit | E (reviewed source in main) but scriptable |
| 6 | Wait for green hosted CI on main | blocked: billing. The gate cannot pass and local results cannot be recorded | A (owner decision: signed local record) |
| 7 | `prepare --ref FULL_COMMIT …` | A (scriptable) |
| 8 | Install the exact package on the Pi and test it | E (physical acceptance policy of 2026-10-03) |
| 9 | Hand-write `acceptance.json` (six gates, targets, notes) | A (prompted) |
| 10 | `approve` with the owner key | E (owner signature, key stays on the Mac) |
| 11 | `publish --dry-run`, then publish | E (explicit confirmation) but scriptable |
| 12 | Commit the registry's published state | A |
| 13 | `ssh-keygen` runs without a terminal inside `release-system.py`, so a passphrase key only works through ssh-agent and nothing says so | A |

## 4. Decisions

| Topic | Decision | Why |
|---|---|---|
| Release notes | GitHub's own generator (`gh api repos/…/releases/generate-notes`) writes the CHANGELOG section and the release body | Mature and already used through `gh`. It lists merged PRs, which is how this repo works. release-drafter needs GitHub Actions (blocked by billing). git-cliff expects conventional commits, which this repo does not use, and adds a new install |
| Signing | Keep OpenSSH `ssh-keygen -Y sign/verify` | Already on every Pi and Mac, already trusted on the Pi, Ed25519. minisign adds a binary on both sides with no gain. Sigstore needs an online identity and a transparency log, which does not fit an offline owner key |
| Publication | Keep `gh release create/upload/edit` in `release-system.py` | Already implemented with draft, verify and promote steps |
| Version bump | Two `sed` edits in the release script | Two files only. bump-my-version would be a new dependency for two lines |
| Release order | Reserve `max(previous + 1, floor + 1, now)` | Dev builds already use build time, so a time-based stable order is always newer than any earlier dev build. The release script initializes an empty registry with the current time, so nobody inspects Pis by hand |
| After a release | Main moves to the next patch number (`X.Y.(Z+1)`) in the same PR | Dev builds become `X.Y.(Z+1).dev+commit`, newer than `X.Y.Z`, so dev deploys keep working |
| Hosted CI | A local verification record signed with the owner key is accepted only when hosted CI never ran (no run, or every job of the run has zero executed steps). A hosted job that ran and failed still refuses. Switch: `local_verification.allowed` in `release-policy.json` | Lets releases happen while Actions is blocked, keeps a real hosted failure binding, the record names the exact commit and every command, and the release body says "local verification" |
| Physical acceptance | Kept. The script prompts for the six gates and their evidence instead of a hand-edited JSON file | Owner policy of 2026-10-03 |
| Release notes on the Pi | `build-release.sh` puts this version's CHANGELOG section into the signed archive as `deploy/RELEASE-NOTES.md` | Notes are covered by the same signature as the code |
| Version shown | Health and `jittersim version` read the signed manifest `/opt/jittersim/deploy/RELEASE` first | One label everywhere: tag `vX.Y.Z`, manifest `X.Y.Z`, dev `X.Y.Z.dev+commit` |

## 5. Plan (small PRs)

1. This audit (docs only).
2. Release tooling, non-gated: `release-system.py` (time-based order, signed local verification record, `record-local`, `inspect`), `build-release.sh` (signed notes), `scripts/release.sh X.Y.Z` (one command, resumable, dry-run mode), `CHANGELOG.md`, `docs/releasing.md`, policy switch, README Update section, tests including a dry run of the script with a throwaway key.
3. Terminal, non-gated: `sudo jittersim update` (check online, show notes, confirm, install, wait, report rollback), `jittersim version`, `jittersim remote update check` and `now` from the Mac, failure-aware wait, health version label, tests.
4. Dashboard, non-gated: Install button names the version, signature and recovery copy facts, notes when the Pi reports them. Small diff because other agents are editing the dashboard.
5. Gated (branch `feat/update-path-gated`, not merged here): `update_status()` reports the signed notes of the staged release so the dashboard can show them before install. Needs independent review and a new `PANIC_APPROVED_SOURCES` entry from the coordinator.

## 6. Owner decisions

1. Accept a signed local verification record as the CI gate while hosted Actions is unavailable (implemented, switch in `deploy/raspberry-pi/release-policy.json`).
2. Keep physical acceptance for every stable release, or allow a lighter route for patch releases (not implemented; current policy kept).
3. Which checks the local record must contain (default: lint, full Python tests, browser journeys).
</content>
</invoke>
