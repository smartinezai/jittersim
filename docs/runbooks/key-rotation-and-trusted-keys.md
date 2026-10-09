# Runbook: release signing keys and the trusted-key list

Status: current. Checked against the source on 2026-10-09 (main at `add7c87`,
`src/jittersim/updates.py`, `deploy/raspberry-pi/make-release-key.sh`). The
steps were read from the code. They were not run on a Pi for this page.

The Pi installs code as root only from an archive signed by a key listed in
`/etc/jittersim/release-signers`. The private key stays on your Mac. The Pi holds
public keys only. This page covers three jobs: replace a key you still have,
remove a key you lost or that leaked, and repair a damaged list. The general
rules are in [Signed releases](../../deploy/raspberry-pi/README.md#signed-releases).

## Facts about the list

- Only `sudo jittersim update trust-key` and `untrust-key` change the file.
  Both need root.
- The file must belong to root and nobody else may be able to write it, and so
  must `/etc/jittersim`. A line that `trust-key` did not write makes the Pi
  refuse every signature until you fix it.
- At most 16 keys can be trusted. Each key needs its own label (1 to 64 letters,
  digits, dots, dashes, underscores or `@`, starting with a letter or digit). The
  same key cannot be added twice.
- Only a plain `ssh-ed25519` public key is accepted. A private key, a key with
  options, a certificate or another key type is refused.
- `sudo jittersim update list-keys` shows each key as `label  fingerprint`. Run as
  root it also shows whether passwordless staging is on. With no key it says
  `No release signing key is trusted on this Pi.`
- A rollback keeps the key list as it is now. A recovery copy never brings back a
  key you removed.

## Replace a key you still have (planned rotation)

Keep a management route to the Pi and the Pi's sudo password at hand. See
[Dashboard unreachable](dashboard-unreachable.md) for ways in.

1. **Set the old key aside on the Mac.** `make-release-key.sh` never replaces an
   existing key, so rename the old pair first:

   ```sh
   mv ~/.ssh/jittersim_release_ed25519 ~/.ssh/jittersim_release_ed25519.old
   mv ~/.ssh/jittersim_release_ed25519.pub ~/.ssh/jittersim_release_ed25519.old.pub
   ```

2. **Create the new key.** The default label is `mac-` plus the date. Give a
   different label if the old key has the same one, because the Pi refuses a
   label that is already used:

   ```sh
   deploy/raspberry-pi/make-release-key.sh --label mac-NEWDATE
   ```

   Add `--passphrase` for a key with a passphrase and `ssh-add` it. Add
   `--no-passwordless` if you do not want passwordless staging. The script prints
   the new fingerprint and one command for the Pi.
3. **Trust the new key on the Pi.** Run the printed command there (or over SSH
   with `ssh -t`). It looks like:

   ```sh
   echo 'ssh-ed25519 AAAA... mac-NEWDATE' | sudo jittersim update trust-key --label mac-NEWDATE --enable-passwordless-staging -
   ```

   It asks for the sudo password once.
4. **Check both keys are listed.** Compare the new fingerprint with the one on the
   Mac (`ssh-keygen -l -f ~/.ssh/jittersim_release_ed25519.pub`):

   ```sh
   sudo jittersim update list-keys
   ```

5. **Prove the new key works before you remove the old one.** Stage your next
   release with the new key (`deploy/raspberry-pi/deploy-from-mac.sh --ref
   origin/main --stage-only`). If the Pi already runs that commit the script does
   nothing, so wait for a new commit. If staging says the signature is not
   valid, the Pi does not trust the new key yet. Do not go on.
6. **Remove the old key from the Pi** by label or fingerprint:

   ```sh
   sudo jittersim update untrust-key OLD_LABEL_OR_FINGERPRINT
   ```

   What this does:
   - archives signed with the old key are refused from now on
   - a staged release signed by it is removed and cannot be installed
   - releases already installed stay installed
   - passwordless staging turns off if no key is left
7. **Retire the old private key on the Mac.** Delete `jittersim_release_ed25519.old`
   once you no longer need it and remove it from ssh-agent if you added it.
8. If you publish stable releases, trust the new public key on every tested Pi
   before you publish a package signed with it. `scripts/release.sh` and
   `deploy-from-mac.sh` sign with `~/.ssh/jittersim_release_ed25519` by default,
   so the new key goes to that path (or pass `--key`). See
   [Releasing JitterSim](../releasing.md) and the
   [release guide](../releases/README.md). Packages signed with the old key stop
   installing once the old key is removed. Build and sign them again.

## A key was lost, stolen or leaked

Act in this order.

1. **Remove the key at once** from a management route:

   ```sh
   sudo jittersim update list-keys
   sudo jittersim update untrust-key LEAKED_LABEL_OR_FINGERPRINT
   ```

   If it was the only key, `untrust-key` also turns passwordless staging off and
   says so. A lost key (not leaked) is the same: you cannot sign any more, so
   trust a new one as in the steps above.
2. **Decide how bad it is.** The signing key alone cannot put an archive on the
   Pi. It becomes dangerous together with the Pi login or with GitHub write
   access:
   - A stolen SSH key for the `jittersim` login together with the signing key,
     while passwordless staging is on, can install signed code as root.
   - Online **Install automatically** with GitHub write access can install a
     release nobody pressed Install for.
3. **If the signing key and SSH access were both exposed**, treat the Pi as
   possibly compromised. Remove the key, then reinstall from a fresh image and a
   release you build and sign with a new key. See
   [Restore and reimage](restore-and-reimage.md).
4. **Turn off the extras while you check:**

   ```sh
   sudo jittersim update disable-passwordless-staging
   sudo jittersim update clear-token
   ```

   Also turn off **Install automatically** on the **Updates** page. Change the
   SSH key for the `jittersim` login and the Pi's sudo password if either was
   exposed.
5. **Check what is installed and staged.** `sudo jittersim update check` shows
   the installed release, the staged release and who signed it. The installed
   release is recorded in `/opt/jittersim/.jittersim-release.json`. Compare its
   commit with the commit you built.
6. Turn passwordless staging back on only after a new key is trusted
   (`sudo jittersim update enable-passwordless-staging`).

## The list is damaged

If staging or `list-keys` says that `/etc/jittersim/release-signers` has a line
JitterSim did not write, or that the file or `/etc/jittersim` must belong to
root and not be writable by others:

1. Look at the file as root. It holds public keys only.
2. Fix the ownership and permissions if they are wrong. Remove the extra line,
   or remove the file.
3. Add every key you still want with `trust-key`. Compare each fingerprint with
   the one on the Mac before you add it.
4. A file that someone else could write may have been changed. Find out why
   before you trust the Pi again. Do not simply restore an old copy of the file
   from a backup, because it can bring back a key you removed.

## After any change

```sh
sudo jittersim update list-keys
sudo jittersim update check
```

The first shows the keys the Pi trusts and whether passwordless staging is on.
The second shows what is staged. Write down the date, the label and the
fingerprint of every key you added or removed.
