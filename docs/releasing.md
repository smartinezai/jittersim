# Releasing JitterSim

This is the short version. The full rules and the manual commands are in
[docs/releases/README.md](releases/README.md).

## What a release is

A release is a version number `X.Y.Z`, a git tag `vX.Y.Z` and a GitHub release
with exactly two files: `jittersim-release.tar.gz` and its signature
`jittersim-release.tar.gz.sig`. The owner's release key on the Mac signs the
archive. The Pi installs only an archive signed by a key it trusts and only when
it is newer than what it runs. The key never leaves the Mac.

Raise PATCH for fixes, MINOR for new features and MAJOR when the full installer
must run again or saved settings change.

Version labels are the same everywhere:

| Where | Release | Development build |
|---|---|---|
| git tag | `v0.3.0` | none |
| `pyproject.toml` and `src/jittersim/__init__.py` | `0.3.0` | the next patch number, for example `0.3.1` |
| Signed manifest, dashboard, `jittersim version`, `jittersim update check` | `0.3.0` | `0.3.1.dev+abc1234` |

After a release main moves to the next patch number, so a development build is
always newer than the release before it.

## Cut a release (one command)

On the Mac, in a JitterSim checkout:

```sh
scripts/release.sh 0.3.0
```

It does this and stops only where you must act:

1. Checks the version is newer than the last tag, reserves it, sets it in the
   two version files and adds GitHub's generated notes (the merged PRs since
   the last tag) to `CHANGELOG.md` in a release commit.
2. Runs the checks on that commit (lint, the full Python tests, the browser
   journeys and `scripts/local-ci.sh` when it exists) and signs a local
   verification record with your key. A failing check stops here, before
   anything reaches main. While GitHub Actions cannot run, this record is what
   lets the release continue. A hosted check that ran and failed still stops
   the release.
3. Opens the release PR and asks before merging it. If main changed in the
   meantime the checks run again on exactly what was merged.
4. Builds and signs the exact package. The archive carries its own CHANGELOG
   section, covered by the same signature, so the Pi can show the notes.
5. Stops so you can install that exact package on the Pi and test it. Run the
   same command again when you are done: it asks for the tested Pi and one line
   of evidence for each of the six checks, then signs the approval.
6. Shows a dry run of the publication, asks, then creates the GitHub release
   with the two files and checks the uploaded bytes.
7. Opens a small PR that records the published release and moves main to the
   next patch number.

A passphrase on the key is asked once by `ssh-add` in your terminal. Nothing
else ever asks for it. Packages and evidence stay in `~/jittersim-releases/X.Y.Z`.

Try it without touching GitHub or the Pi:

```sh
JITTERSIM_RELEASE_PYTHON="$PWD/.venv/bin/python" scripts/release.sh --dry-run 0.3.0
```

## Install a release

Dashboard: open **Updates**, choose **Check for updates** and then **Install
update**. Pi terminal: `sudo jittersim update`. Mac terminal:
`jittersim remote update now`. Each one checks the signature,
keeps a recovery copy, checks the Pi's health after installing and restores the
previous installation by itself when that check fails.

The Pi checks GitHub with a read-only token saved once with
`sudo jittersim update set-token`. See
[Signed releases](../deploy/raspberry-pi/README.md#signed-releases).

## Turn the local record off

When hosted GitHub Actions works again, set `local_verification.allowed` to
`false` in `deploy/raspberry-pi/release-policy.json`. The release then needs
green hosted runs again.
