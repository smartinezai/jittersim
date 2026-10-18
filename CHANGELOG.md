# Changelog

Every JitterSim release. The newest is first. `scripts/release.sh` adds each
section from GitHub's generated release notes, and the same section travels
inside the signed release archive so the Pi can show it before installing.
Changes merged after the newest release are listed under Unreleased until the
next release takes them over.

## Unreleased

- **Address changes** on the Devices page: a saved device that comes back with a
  new private Wi-Fi address is suggested ("This looks like Dad's phone ... Move
  its settings?") with Yes, No and Not this device. Off, Ask (default) and
  Automatic. Automatic moves only the profile and the name on strong evidence.
  Protection moves only after Yes, and a saved dashboard access grant never moves
  from the card. Every move is in a history with Undo for 7 days.
- The Pi Wi-Fi follows the router. After a **Copy router Wi-Fi to Pi** that
  worked, the Pi records the router's Wi-Fi name and security (no password).
  Every boot renames a saved Wi-Fi with another name, such as
  `JitterSim-Recovery`, back to the router name. The router is not contacted.
  If the router name does not start, the previous name comes back for that
  boot. A manual Wi-Fi change or a name reset stops the following.
  `sudo /opt/jittersim/.venv/bin/python -m jittersim.networking.wifi_follow`
  `recovery` holds Recovery across reboots, `router` returns now and `status`
  names the state.
- `sudo jittersim update` and `jittersim remote update now` report success only
  when their install request reached `complete` and the Pi runs the new
  version. A rollback is reported only when the Pi confirmed it.
- A failed check on GitHub is an error instead of "Up to date".
- `sudo jittersim update --yes` and `--offline` work. An update that is already
  running is followed instead of started again, and under `--yes` a release
  that just failed is not installed again.
- `jittersim remote update now` follows an install the Pi already started when
  Install automatically is on, and `--json` prints one JSON answer (it cannot
  ask, so it needs `--yes`).
- The Updates page names the staged release on its install button (**Install
  v1.1.0**), shows whether a recovery copy is kept, shows the release notes when
  the Pi reports them, and the install confirmation names the signer and the
  automatic restore.
- The dashboard asks before deleting a profile, device or group, clearing the
  activity log, importing settings, changing the Pi Wi-Fi, restoring its name,
  reversing an address move, copying the router Wi-Fi, removing dashboard
  access or stopping protection of the device in use in its own dialog instead
  of the browser's. The dialog names the action on its button, Escape
  cancels it and focus returns to the control that opened it.
- A screen reader hears each result once. The dashboard has one status region
  and one alert region for results and errors, and lists that refresh every few
  seconds (devices, saved profiles, groups, schedules, timers, limits,
  observations and the router connection) no longer announce themselves. A
  refresh keeps keyboard focus on the button you were on.
- A save, an Apply or a delete no longer makes the dashboard read out its
  loading notes and device count again. The ad blocking line speaks only when
  its words change, and the random website line speaks only when you ask for it.
  Cancelling a question gives focus back to the button that asked, also when the
  list holding it was drawn again while the question was open. Escape in a
  question no longer clears a highlighted line of the traffic chart.
- The Pi's `.local` name is announced again. Avahi's setting
  `disable-user-service-publishing` was `yes`, which made Avahi refuse the name
  service's records, so nothing was ever announced. An install or update sets it
  to `no` and restarts Avahi. The name service also survives a hangup signal in
  the first second of boot and restarts after any exit. The journal is kept on
  disk (up to 64 MB) because its drop-in now sorts after Raspberry Pi OS's own
  volatile setting.
- The dashboard has a new look: layered dark surfaces, a type scale, an icon
  beside each page link, a logo mark and short motion that stops under reduced
  motion. Nothing behaves differently.
