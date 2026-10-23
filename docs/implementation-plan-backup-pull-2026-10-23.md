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
| Overwrite | Never. The answer is written to a hidden temporary file in the same folder (`mkstemp`, mode 0600), synced, and then published with `os.link`, which refuses a name that exists, including a symbolic link. The temporary name is removed at the end. Two pulls in the same second make the second one fail with a message. | A backup must not replace a good one, and a file with a final name is always complete, even after a crash. The price is a disk with hard links: FAT and exFAT fail with a message that names the folder. A kill can leave a hidden `.jittersim-backup-*.tmp` file. |
| What is saved | Only an answer that starts with the SQLite header, is at most 64 MiB and arrived completely (it has a Content-Length and all of it came, or it is chunked and ended properly). Nothing is created before the download has succeeded. | A captive portal page or a cut download must not become a "backup". The command proves a complete SQLite-looking file, not a sound database: the user guide drill runs `PRAGMA quick_check`. |
| Time limit | 120 seconds for the whole download (the client's default is 20). | The file can be several megabytes over Wi-Fi. |
| Failure | Exit 1 and one plain sentence: the Pi cannot be reached, the token is refused, the certificate does not match. | The existing client messages. With `--json` the failure is JSON too. |
| Schedule | Not built in. The user guide has a cron line. | The brief asked for the smallest version. A schedule needs the token stored somewhere, and the Keychain item already exists. |

The client change is small: `DashboardClient._send` is split so that a new
`download` method can return bytes instead of JSON. The size limit is a
parameter of the call, not a new setting. The check that all of a declared
Content-Length arrived runs for every call, so a JSON answer that is cut short
now says "The dashboard's answer stopped before it was complete." instead of
"The connection to the dashboard failed." A download without a Content-Length
that is not chunked is refused the same way. The installed `jittersim` command
(`jittersim.command`) lists `backup` so that the Mac commands never load the Pi
networking code.

## Restore

The repository has no restore command and no restore button for the database
file. The runbook [restore-and-reimage](runbooks/restore-and-reimage.md) lists
manual steps and says they were not run on a Pi. The dashboard's own **Import
settings...** reads a settings export, not this file. The user guide says this
plainly and gives a drill that needs no Pi: pull the backup and run SQLite's
`PRAGMA quick_check` on it.

## Tests

`tests/unit/test_remote_cli_backup.py` (21 cases) uses the fake HTTPS dashboard of the
other remote command tests and a small TLS server that cuts the answer: the saved file
and its modes, the default folder, `--json`, no overwrite (also a symbolic link at the
destination), a write, sync or cancel that leaves nothing, a disk without hard links, a
folder that cannot be used, a refused token, an unreachable Pi, an answer that is not
a database, over the limit or cut short or without a length, and the `--help` text. The
shared refusal tests (plain HTTP, wrong certificate, help text) include the command.

## Not done

- A schedule, a retention limit (old backups are never deleted) and a restore command.
- Running `PRAGMA quick_check` inside the command. It would catch a database that was already damaged on the Pi. It costs about six lines and an error path. The user guide drill does it by hand.
- A copy of the settings export (`/api/export`). It is the only file the
  dashboard can restore. Say so if you want `backup pull --settings`.
- A test of the cron line on a Mac.
