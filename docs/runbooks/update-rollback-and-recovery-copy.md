# Runbook: update rollback and the recovery copy

Status: current. Checked against the source on 2026-10-09 (main at `add7c87`,
`src/jittersim/updates.py` and `src/jittersim/cli.py`). The steps were read from
the code. They were not run on a Pi for this page.

Use this page when an update failed, hangs or leaves the Pi in a bad state, or
when you want to go back to the release that ran before the last update. For the
normal update steps see
[Approved release updates](../../deploy/raspberry-pi/README.md#approved-release-updates).

## What the recovery copy is

Before an update changes anything, the root update service saves a recovery copy
under `/var/lib/jittersim-updates/`. The folder is `backup-<attempt>`. The file
`backup-current.json` says which one is the published copy. A copy only counts
once its `complete.json` file exists. Only root can read these folders.

The copy holds:

| Saved | Where it came from |
| --- | --- |
| The installed program | `/opt/jittersim` |
| The configuration | `/etc/jittersim`, including `dashboard-access.json` |
| The database and data | `/var/lib/jittersim` (not the upload spool `incoming-release`) |
| The saved access state | `/var/lib/jittersim-access` |
| The systemd units, the dashboard name dispatcher and the `jittersim` command link | `/etc/systemd/system`, `/etc/NetworkManager/dispatcher.d/80-jittersim-mdns` and `/usr/local/bin/jittersim` |

A restore puts these back as they were at the time of the backup. Anything you
changed after that point is lost. That includes saved profiles, devices,
schedules, protection settings, saved device access and usage counters.

A restore deliberately keeps the current state of these items, so a rollback can
never bring back something you removed:

- the trusted release signing keys (`release-signers`) and the GitHub update
  token (`update-token`)
- the local certificate authority, the dashboard certificates and
  `trust.env`
- the highest release order the Pi ever installed

The Panic state and the saved ad-blocking run or stop choice have their own
protections outside the copy, so a restore does not undo a later Panic.

A restore does **not** change NetworkManager connections. The test Wi-Fi
profile `jittersim-ap` keeps its current name, password and security type. A
running test Wi-Fi is not restarted. If you copied the router's Wi-Fi name
before the update, see
[Router outage and Pi-only rollback](router-outage-pi-only-rollback.md).

Only one previous installation is kept. A new successful update replaces the
recovery copy of the one before it. After two updates you cannot go back to the
release before the first one with the rollback command.

## Before an update

1. Keep a management route that does not depend on the test Wi-Fi (Ethernet or
   the router's network, or a keyboard on the Pi). See
   [Dashboard unreachable](dashboard-unreachable.md).
2. Make your own backups from the same moment: the
   [private database backup](../../deploy/raspberry-pi/README.md#private-database-backup),
   `/etc/jittersim`, `/var/lib/jittersim-access` and the NetworkManager
   connection. [Restore and reimage](restore-and-reimage.md) lists them.
3. Check free space. The update needs room for two installation copies plus
   100 MiB of working space. It stops before it changes anything with `not
   enough free space for update and recovery` if there is not enough:

   ```sh
   df -h /var/lib/jittersim-updates
   ```

4. Run `sudo jittersim update check`. Read the lines `Installed:`, `Staged:`,
   `Signed by:` and `Ready to install:`.

## While an update runs

The dashboard and test Wi-Fi interrupt briefly. The update runs these phases in
order: `queued`, `preparing`, `stopping`, `backing-up`, `installing`,
`checking` and then `complete`. The Pi follows them with:

```sh
sudo jittersim update check
sudo systemctl status jittersim-update.service
sudo journalctl -u jittersim-update.service -n 80 --no-pager
```

The update service has a 15 minute limit. A queued update runs again after a
reboot. An update that was interrupted after its recovery copy was complete is
restored before another attempt starts.

If the new release fails its health check or any step fails after the copy was
saved, the service restores the recovery copy by itself. The status then reads
`Update failed. Previous installation restored and checked.`

## When the update failed

1. Read `sudo jittersim update check`. The `Update phase:` and `Update state:`
   lines say what happened. The phases that matter are:
   - `rolled-back`: the previous installation is back and checked.
   - `failed`: read the message. Recovery may be waiting or may have failed.
   - `idle`: nothing is queued.
2. If the line `Retry available:` appears, retry once:

   ```sh
   sudo jittersim update retry --yes --wait
   ```

   If recovery restores the saved backup, changes after that backup are
   replaced. The command asks for confirmation unless you pass `--yes`.
3. Read the log (`sudo journalctl -u jittersim-update.service -n 80 --no-pager`
   or `sudo jittersim logs update --lines 80`). Read it before you share it.
4. Do not remove lock files, edit files in `/var/lib/jittersim-updates` or
   change file permissions to get past a refusal. Those folders carry the
   recovery state.

## Go back on purpose (rollback)

Use this when the update completed but the new release misbehaves.

From the dashboard: open the **Updates** page and choose **Restore previous
installation**. The button is enabled only when a complete recovery copy
exists.

From the Pi:

```sh
sudo jittersim update rollback
```

The command asks `Restore the previous installation and its settings backup?
Changes after that backup will be replaced.` Add `--yes` to skip the question.
If no copy exists it says `No previous installation is available for recovery.`
There is no `--wait` for this command. Follow it with:

```sh
sudo jittersim update check
sudo journalctl -u jittersim-update.service -n 80 --no-pager
```

The restore stops the services, puts the saved folders back, restarts the
services and checks the dashboard and the helper. It ends with the phase
`rolled-back` and the message `Previous installation restored and health check
passed.`

Two cases stop a rollback before it starts:

- Household access rules are on and the previous release has no access
  controls. The message says `disable household restrictions before restoring a
  release without access controls`. Turn the rules off first (**Access and
  rules** or `sudo jittersim normal-access`), then try again.
- The helper is not answering. The rollback request goes through the helper.
  Restart it (`sudo systemctl restart jittersim-helper.service`) and try again.

## Check the result

1. `sudo jittersim update check` shows the release you expect under
   `Installed:`.
2. `sudo jittersim diagnostics` shows the services as `active` and the helper as
   answering.
3. Open the dashboard and check **Updates**. Check your saved profiles. They are
   as they were at the time of the backup.
4. Check the test Wi-Fi with a real device. Rollback does not restore the
   NetworkManager profile.
5. Choose **Start** if you want profiles to run again.

## If rollback is not possible

If the helper and the dashboard both stay down and no recovery copy can be used,
the next step is a reinstall from a verified signed release over a management
connection. See [Restore and reimage](restore-and-reimage.md). Keep the failed
logs and the output of `sudo jittersim diagnostics export > evidence.json` before
you reinstall.
