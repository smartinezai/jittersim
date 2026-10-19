# Changelog

Every JitterSim release. The newest is first. `scripts/release.sh` adds each
section from GitHub's generated release notes, and the same section travels
inside the signed release archive so the Pi can show it before installing.
Changes merged after the newest release are listed under Unreleased until the
next release takes them over.

## Unreleased

- Choosing Check token after you edit the token no longer waits for an earlier
  check that the Pi never answered. The new check starts at once, and the late
  answer of the old one changes nothing.
- Revoke all homepage pairings is read out by a screen reader once instead of
  twice. The pairing code still appears in its own line, and the result of
  Revoke now shows in the message at the top instead of in that line.
- Clear on the Measurements page asks again after Copy table as Markdown failed
  because the browser refused the clipboard. A table counts as kept only when
  the copy worked.
- The look at the air before a boot rename no longer depends on the Pi's language. Its
  `nmcli` calls run with untranslated output (`LC_ALL=C.UTF-8`, so names with umlauts and
  other non-ASCII letters still match). A Pi with a German or other locale still waits
  for wlan0 at a cold boot and still retries a busy radio, and the look stays within its
  20 s budget. The start of the test Wi-Fi may now take up to 480 s in the worst case
  (it was 420 s) before systemd gives up.
- A helper that restarts after it died during an apply removes the queues and
  tables that apply left. Before, devices could stay shaped while the dashboard
  showed nothing running. If that removal fails, the helper still starts and
  asks for Panic before profiles can start again.
- The boot step of the Pi Wi-Fi looks once at the Wi-Fi names on the air before it
  renames the Pi Wi-Fi to the router name. When the router's own Wi-Fi already sends
  that name, the Pi keeps or takes `JitterSim-Recovery` for that boot, so one name
  never runs on two subnets. A neighbour, guest network or mesh repeater with
  exactly that name has the same effect, and devices that joined the Pi under the
  router name move to the router after such a boot. A failed, busy or empty look
  changes nothing. `wifi_follow status` shows the decision, the scan result and the
  reason. The test Wi-Fi also does not start when the Ethernet address is inside
  `192.168.50.0/24`.
- Applying a profile to one connected device while a profile for Everyone runs now
  starts it for that device. The device leaves Everyone and runs its own profile,
  and Everyone keeps running for every other device. Before, the dashboard refused
  with "Everyone uses ... right now". It is refused while Everyone is saved but not
  running, or while what runs is older than the saved settings. A failed start changes nothing. Start, Apply to all, Stop
  for everyone, Reset network and Panic end it for every device. The change adds a
  table to the settings database and needs the Pi helper restarted with the update.
- Add a device speed test that records traffic through the applied profile, saves
  results and compares them with numbers entered from a speed test run on the
  device. Deleting a result asks in the page's own dialog. The start check
  also compares the loss burst length of the queues with the applied profile.
- Add a **Check connection** button next to **Test speed**. The Pi sends a short
  burst of ping probes to the device and shows packet loss, round trip, jitter,
  duplicates and reordering. It compares them with the download side of the
  profile the Pi applied and says whether they match, are less than set or are
  more than set. A profile that changes only the upload side is not judged.
  Results are saved per device and can be exported.
- Speed test follow-ups: errors show in the open speed test window instead of
  under it, a length such as 45.5 is refused with a message, the export uses the
  dashboard's request handling, and one damaged saved result no longer hides the
  others. Labels reject hidden control and format characters (the joiners that emoji and Persian words need are allowed).
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
- A check that a person asks for (the dashboard's Check for updates, `jittersim
  remote update check` and `now`, `sudo jittersim update check --online`) only
  checks and stages a release. Only the daily check installs by itself, and only
  when Install automatically is on.
- The Pi's update status carries the signed release notes of a release that can
  be installed now, and a development build (`X.Y.Z.dev+COMMIT`) that stopped
  can be retried.
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
  seconds (the devices and their conditions, schedules, timers, scenarios,
  limits, website observations, activity, test sessions and the router
  connection) no longer announce themselves. Saved profiles and groups are read
  when a page opens and after a save, not every few seconds. A refresh keeps
  keyboard focus on the button you were on.
- **Revoke all homepage pairings** and **Clear** on the Measurements page ask
  first, in the same dialog as the other destructive actions, and say the result
  once. Clear asks only when the table holds runs that exist in the page alone:
  a table that was copied or attached to a test session clears without a
  question. The Edit, Open controls and Delete buttons of the device table and
  of the schedule list now say which row they belong to (**Delete Test phone**).
  The question about restoring the Wi-Fi name has a short heading and no longer
  repeats it in the first sentence. If the row that asked a question is gone
  when it closes (somebody removed it meanwhile), focus goes to its list.
- Checking the admin token no longer fails by itself. Two checks that overlapped
  (for example Allow this device followed at once by Remove) locked the dashboard
  with "Token check failed: undefined", and so did a save that refreshed the page
  while the check was still loading. A check now waits for the one that is
  running, and a read that a refresh made stale is read again.
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
- The installer checks the management firewall after it turns off a newly
  installed Unbound and hostapd, so a failed check no longer leaves them running.
  The firewall unit stops a stuck `nft` within under a minute.
