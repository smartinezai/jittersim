# Runbook: back up, restore and reimage the Pi

Status: current. Checked against the source on 2026-10-09 (main at `add7c87`,
`deploy/raspberry-pi/install.sh`, `src/jittersim/config/storage.py`,
`src/jittersim/localca.py`). The repository has no restore command and no tested
reimage script. The steps below follow what the installer and the services do
with existing files. They were not run on a Pi for this page. Where a step is
only an expectation it says so.

Use this page when the settings database is damaged, when the SD card died, or
when you want to move JitterSim to a new card. Keep a second way into the Pi
while you work (Ethernet or the router's network, or a keyboard on the Pi). See
[Dashboard unreachable](dashboard-unreachable.md).

## What lives where

| What | Where | Back it up with |
| --- | --- | --- |
| Settings database (profiles, devices, groups, schedules, limits, DNS observations, usage) | `/var/lib/jittersim/jittersim.db` | The [private database backup](../../deploy/raspberry-pi/README.md#private-database-backup) command, or **Download backup** on **Diagnostics** |
| Settings as a file (no usage, no DNS history, no passwords) | not stored. You export it | **Export settings** on **Diagnostics** |
| Admin token | `/etc/jittersim/control.env` | A private copy of the folder (below) |
| Initial Wi-Fi name and password | `/etc/jittersim/ap.env` | A private copy of the folder |
| Current Wi-Fi name and password (what the dashboard changed later) | NetworkManager connection `jittersim-ap` | Read it as shown below |
| Interface names | `/etc/jittersim/runtime.env` | A private copy of the folder |
| Dashboard name | `/etc/jittersim/trust.env` | A private copy of the folder |
| Local certificate authority (public certificate and private key) | `/etc/jittersim/ca/` | A private copy of the folder |
| Saved device access for the test Wi-Fi | `/etc/jittersim/dashboard-access.json` | A private copy of the folder |
| Trusted release signing keys | `/etc/jittersim/release-signers` | Do not restore it. Trust keys again (see below) |
| GitHub update token | `/etc/jittersim/update-token` | Do not restore it. Save it again with `update set-token` |
| Saved household access state | `/var/lib/jittersim-access/` | A private copy of the folder |

The database download from the dashboard leaves out the random website
analytics. The dashboard-access file is not in the database. Backups hold device
addresses, names and observed domain names, so keep them private.

## Make a backup set

Do this before every update and on a schedule you can keep. Choose a new file
name each time.

1. **The database.** Run the command in
   [Private database backup](../../deploy/raspberry-pi/README.md#private-database-backup).
   It refuses to replace a file and checks the copy.
2. **The files.** Make a private archive of the configuration and the access
   state. The archive holds the admin token and the authority's private key:

   ```sh
   sudo sh -c 'umask 077 && tar --create --gzip \
     --file /var/backups/jittersim-files-$(date +%Y%m%d).tar.gz \
     /etc/jittersim /var/lib/jittersim-access'
   ```

3. **The Wi-Fi password.** The dashboard stores later Wi-Fi changes in
   NetworkManager. Write the current name and password to a private file. Never
   paste them into a chat or a ticket:

   ```sh
   sudo sh -c 'umask 077 && nmcli --escape no --show-secrets \
     -g 802-11-wireless.ssid,802-11-wireless-security.psk \
     connection show id jittersim-ap > /var/backups/jittersim-wifi-$(date +%Y%m%d).txt'
   ```

4. **Facts about the Pi.** Write down the host name, the Ethernet address, the
   WLAN country, `cat /etc/os-release`, `nmcli --version` and the output of
   `sudo jittersim update check` (the installed release).
5. **Copy everything off the Pi** to a private place, for example your Mac.
   Remember the archive and the Wi-Fi file are secrets.

## Restore the database on the same Pi

Use this when the dashboard fails on its database or when you want to go back to
an earlier backup. A backup from the same or an older release is the safe
choice. A backup from a newer release can fail on older code. The README says
older versions cannot read newer schedules.

1. Check the backup file first. It must print `('ok',)`:

   ```sh
   sudo python3 -I -c 'import sqlite3, sys; from pathlib import Path; c = sqlite3.connect(Path(sys.argv[1]).resolve(strict=True).as_uri() + "?mode=ro", uri=True); print(c.execute("PRAGMA quick_check").fetchone())' /absolute/path/to/backup.db
   ```

2. Stop the dashboard. Move the damaged database and any files that belong to it
   aside. Do not delete them yet:

   ```sh
   sudo systemctl stop jittersim-dashboard.service
   sudo install -d -m 0700 /var/backups/jittersim-damaged
   sudo sh -c 'mv /var/lib/jittersim/jittersim.db* /var/backups/jittersim-damaged/'
   ```

3. Put the backup in place with the right owner and start the dashboard:

   ```sh
   sudo install -m 0600 -o jittersim -g jittersim /path/to/backup.db /var/lib/jittersim/jittersim.db
   sudo systemctl start jittersim-dashboard.service
   ```

4. Check `sudo jittersim status` and `sudo jittersim diagnostics`, then open the
   dashboard. Usage counters and DNS observations are back at the time of the
   backup.

The dashboard repeats the last Start or Reset recorded in the restored database
when it starts. If that backup was taken while profiles ran, they can start
again. Press **Panic** if you do not want that.

Two other sources, if you have no backup:

- **Import settings...** on **Diagnostics** loads a settings export (versions 1,
  2 and 3) and replaces the saved settings with it. A file from an older release
  that lacks the profile for Everyone, the service lists or the ad-blocking sections
  leaves those saved sections as they are. It restores no usage, no DNS history
  and no passwords. Do a database backup first.
- The update recovery copy under `/var/lib/jittersim-updates/backup-*/data/`
  holds the database as it was before the last update. Copy it out as root. Never
  write into that folder. See
  [Update rollback and recovery copy](update-rollback-and-recovery-copy.md).

## Restore saved device access

The file `/etc/jittersim/dashboard-access.json` must belong to root and have the
group `jittersim` with mode 0640 (a stricter mode also works if the group stays
`jittersim`). Restore it with:

```sh
sudo install -m 0640 -o root -g jittersim /path/to/dashboard-access.json /etc/jittersim/dashboard-access.json
sudo jittersim dashboard-access list
```

The helper reads the file on every request, so no restart is needed. The file
holds at most 64 devices. If `list` reports unsafe ownership or permissions,
check the owner, the group and the mode above. Or add each device again. This
works while the dashboard is stopped:

```sh
sudo jittersim dashboard-access add 02:00:00:00:00:01 --name 'My Mac'
sudo jittersim dashboard-access list
```

A computer you did not register has to sign in and choose **Allow this device**
again.

## Reimage: the SD card died

### 1. Prepare the card

1. Write Raspberry Pi OS with Raspberry Pi Imager. In the OS customisation set
   the user name to `jittersim` (the installer needs it), a password and your SSH
   public key. The Mac deploy script logs in with `~/.ssh/jittersim_ed25519` by
   default, so use that key's `.pub` file here (or pass `--key` to the script).
   Use an OS release that ships NetworkManager 1.50 or newer. Set the WLAN
   country. See [Service user](../../deploy/raspberry-pi/README.md#service-user).
2. Connect the Pi to the router by Ethernet and boot it. Log in over SSH or at
   the keyboard.
3. Check `cat /etc/os-release` and `nmcli --version`. The installer stops if
   NetworkManager is older than 1.50.
4. The Mac's `known_hosts` entry for the Pi's address no longer matches the new
   SSH host key. Check the new key on the Pi's console first, then run
   `ssh-keygen -R <pi-address>` on the Mac.

### 2. Keep the identity (optional)

You can keep the admin token, the Wi-Fi name and password, the dashboard name and
the certificate authority. Then the browsers and devices that already trust the
authority need nothing. Extract only these items from the archive before you run
the installer. The installer keeps existing credential files and an existing valid
authority:

```sh
sudo tar --extract --gzip --same-owner --preserve-permissions \
  --file /path/to/jittersim-files.tar.gz --directory / \
  etc/jittersim/control.env etc/jittersim/ap.env etc/jittersim/runtime.env \
  etc/jittersim/trust.env etc/jittersim/ca
```

Do not restore `release-signers`, `update-token`, the dashboard certificates or
`dashboard-access.json` at this point. The installer issues the certificates
again from the authority. You restore the access file after the install.

This path is not covered by a repository test. If the installer stops with a
message about the authority or an existing credential file, do not remove files
to get past it. Read the message. As a fallback move `/etc/jittersim/ca` aside
and install without it. That creates a new authority, and every device must
trust the new one.

If you skip this step the installer generates a new admin token, a new Wi-Fi
password and a new authority. Every browser that remembered the old token is
locked. Update the Mac Keychain token (`deploy-from-mac.sh --save-token`) and run
`jittersim connect` again with the new authority certificate.

### 3. Install a signed release

Follow [Install or update](../../deploy/raspberry-pi/README.md#install-or-update).
For a Pi without the update service use the installer once, either by hand with
a verified archive or from the Mac:

```sh
deploy/raspberry-pi/deploy-from-mac.sh --ref origin/main --use-installer --host jittersim@<pi-address>
```

Use only a signed release from the verified official `main`. Keep your private
database backup and file archive until the recovery checks are complete. The
installer prints the authority fingerprint. Compare it with the one you recorded
before you trust anything.

### 4. Trust your signing key again

The new Pi trusts no key. Run the command that `make-release-key.sh` prints (it
needs the sudo password once), then check:

```sh
sudo jittersim update list-keys
```

See [Key rotation and trusted keys](key-rotation-and-trusted-keys.md). If the old
key leaked, use a new key and do not restore the old list.

### 5. Restore the data

1. Restore the database as shown above. Stop the dashboard first. The installer
   has already created the services.
2. Restore the saved device access (above).
3. Set the Wi-Fi name and password if you did not keep `ap.env` or if the
   dashboard had changed them. Use **Router and Wi-Fi** on the dashboard or the
   values you saved in the Wi-Fi file. A device keeps its saved Wi-Fi password
   only if the name, password and security type are the same.
4. Choose **Start**. Check **Access and rules**: household
   blocking can need **Apply access rules**. A missing ad-blocking run choice
   leaves the selected devices paused until you press Start or enable a switch
   again.

### 6. Check

- `sudo jittersim diagnostics` shows every service `active`, the helper
  answering and the dashboard name announced.
- Run the Part B items of the
  [Pi acceptance checklist](../pi-acceptance-checklist.md) that apply: authority
  fingerprint, `dns-sd -G v4 netlab.local` on both networks, sign-in and **Allow
  this device**.
- Join a real device to the test Wi-Fi and check the internet and one profile.
- If the authority is new, install it again on every Mac and iPhone from **Trust
  this Pi**.
- Make a new backup set now. The old ones belong to the old card.
