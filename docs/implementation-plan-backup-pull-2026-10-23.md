# Implementation plan: pull a backup to the Mac (2026-10-23)

Status: implemented on branch `feat/backup-pull`. This is proposal 14 of the
[feature proposals](feature-proposals-2026-10-09.md). The Pi, the helper and
every Panic-pinned file are not touched.

## What it does

`jittersim backup pull` runs on a Mac. It asks for the admin token the way the
other Mac commands do, downloads `/api/backup` (the route behind **Download
backup** on **Diagnostics**) and saves the file. It prints the path.

## Decisions and defaults

| Question | Decision | Why |
| --- | --- | --- |
| Which route | The existing `GET /api/backup`. No new server route. | It already makes a consistent SQLite snapshot and needs the admin token. |
| Connection and token | The shared `DashboardClient`: the saved `jittersim connect` address, the pinned certificate and the hidden token prompt. | The same checks as every Mac command. A wrong certificate never receives the token. |
| Folder | `~/jittersim-backups/`, or `--directory FOLDER`. Created with mode 0700 when missing. An existing folder keeps its mode. | The file is the secret, and it is 0600. |
| File name | `jittersim-backup-YYYYMMDDTHHMMSSZ.db` in UTC. | Sorts by time. Holds no address, name or token. |
| Overwrite | Never. The file is created with `O_EXCL`. Two pulls in the same second make the second one fail with a message. | A backup must not replace a good one. |
| What is saved | Only an answer that starts with the SQLite header and is at most 64 MiB. Nothing is created before the download has succeeded. | A captive portal page or a failed download must not become a "backup". |
| Time limit | 120 seconds for the whole download (the client's default is 20). | The file can be several megabytes over Wi-Fi. |
| Failure | Exit 1 and one plain sentence: the Pi cannot be reached, the token is refused, the certificate does not match. | The existing client messages. With `--json` the failure is JSON too. |
| Schedule | Not built in. The user guide has a cron line. | The brief asked for the smallest version. A schedule needs the token stored somewhere, and the Keychain item already exists. |

The client change is small: `DashboardClient._send` is split so that a new
`download` method can return bytes instead of JSON. The size limit is a
parameter of the call, not a new setting.

## Restore

The repository has no restore command and no restore button for the database
file. The runbook [restore-and-reimage](runbooks/restore-and-reimage.md) lists
manual steps and says they were not run on a Pi. The dashboard's own **Import
settings...** reads a settings export, not this file. The user guide says this
plainly and gives a drill that needs no Pi: pull the backup and run SQLite's
`PRAGMA quick_check` on it.

## Tests

`tests/unit/test_remote_cli_backup.py` uses the fake HTTPS dashboard of the
other remote command tests: the saved file and its modes, the default folder,
`--json`, no overwrite, a refused token, an unreachable Pi, an answer that is
not a database, an answer over the limit and the `--help` text. The shared
refusal tests (plain HTTP, wrong certificate, help text) now include the
command.

## Not done

- A schedule, a retention limit and a restore command.
- A copy of the settings export (`/api/export`). It is the only file the
  dashboard can restore. Say so if you want `backup pull --settings`.
- A test of the cron line on a Mac.
