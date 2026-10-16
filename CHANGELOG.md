# Changelog

Every JitterSim release. The newest is first. `scripts/release.sh` adds each
section from GitHub's generated release notes, and the same section travels
inside the signed release archive so the Pi can show it before installing.
Changes merged after the newest release are listed under Unreleased until the
next release takes them over.

## Unreleased

- `sudo jittersim update` and `jittersim remote update now` report success only
  when their install request reached `complete` and the Pi runs the new
  version. A rollback is reported only when the Pi confirmed it.
- A failed check on GitHub is an error instead of "Up to date".
- `sudo jittersim update --yes` and `--offline` work. An update that is already
  running is followed instead of started again, and under `--yes` a release
  that just failed is not installed again.
- `jittersim remote update now` follows an install the Pi already started when
  Install automatically is on, and `--json` prints one JSON answer.
- The dashboard asks before deleting a profile, device or group, clearing the
  activity log, importing settings, changing the Pi Wi-Fi, restoring its name,
  reversing an address move, copying the router Wi-Fi, removing dashboard
  access or stopping protection of the device in use in its own dialog instead
  of the browser's. The dialog names the action on its button, Escape
  cancels it and focus returns to the control that opened it.
