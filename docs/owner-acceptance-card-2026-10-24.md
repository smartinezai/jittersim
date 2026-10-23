# v0.2.0 owner acceptance card

Commands and evidence wording: [owner commands page](release-0.2.0-owner-commands.md). Checked against main `c32ae49f`.

## Safety first

- **Panic** in the sidebar stops profiles and blocking. If test devices lose the internet, run `sudo jittersim normal-access` over SSH.
- If the test Wi-Fi drops and does not return, use the [recovery quick reference](../deploy/raspberry-pi/README.md#recovery-quick-reference).
- SSH works only from Ethernet or the router network. Work from there, except on the test phone.
- Use a test phone and a throwaway profile, never the computer you control the Pi from.

## Before and during the install

1. Merge nothing into main from the first release run until the record PR merges. PR 175 and changes to `panic_guard.py`, `helper.py` or `access.py` wait until after v0.2.0. Run the [Panic file check](release-0.2.0-owner-commands.md#the-panic-trap-and-the-merge-freeze-the-runbook-only-points-here) now and before your first **y**.
2. Write down profile names, device names, protected devices, Panic state and access rules.
3. Finish [Before you start](release-0.2.0-owner-commands.md#before-you-start-all-must-be-true) on the owner page first (Mac command line, token, bootstrapped Pi). Then run the release command from [Commands, in order](release-0.2.0-owner-commands.md#commands-in-order) (it needs `--notes-file docs/release-notes-0.2.0-draft.md`). Answer **n** at "Have you installed and tested this exact package on the Pi?", then run `remote update upload` and `install --wait 900` as shown there.

## Checks on the installed v0.2.0

1. **Boot look.** Needs an earlier **Copy router Wi-Fi to Pi** (from Ethernet), else `not-remembered` shows. With the router Wi-Fi on, power the Pi off and on. Over SSH run `sudo /opt/jittersim/.venv/bin/python -m jittersim.networking.wifi_follow status`. Expect `boot_scan: seen`, not `failed`. If the router name has an umlaut, `seen` also proves it was read right.
2. **Reset Wi-Fi name.** After check 1 (it forgets the router copy). Press **Reset Wi-Fi name** in the sidebar, then **Restore name** at "Restore the Wi-Fi name?". Expect `TestNet` and a brief drop for test phones. [Details](user-guide.md#pi-and-router-controls).
3. **This device.** Join a test phone to `TestNet`, open the dashboard, sign in and choose **Allow this device**. Its row in **Devices** shows **This device**.
4. **Test speed.** First press **Reset network**, then **Assign profile** on the phone's row (only that phone starts; do not press **Start**): without a profile of its own the Pi measures nothing. On its own row press **Test speed** (**This dashboard** is selected) and **Start measuring**. Expect a saved download result after about 12 seconds.
5. **Check connection.** Same phone, screen on: **Check connection**, **Start check**. Expect a saved verdict.
6. **Panic.** Press **Panic**. Expect `Profiles off` in the sidebar.

**Recovery is your decision.** (a) Test **Restore previous installation** last: the Pi then runs the development build and refuses v0.2.0 ("not newer") until 0.2.1. (b) Skip it and write only a failed-install recovery you really did. With neither, leave `recovery` empty. Never write "not tested".

## Finish

1. Run the release command again and answer **y**. It asks `Evidence that '<gate>' passed (empty stops):` six times, for `physical` (checks 1 to 6; mention a protected device only if you really tested one), `upgrade` (install worked), `recovery` (your decision), `saved_settings` (list intact), `management_access` (SSH, router-network dashboard) and `safety_state` (Panic, access rules same).
2. After publishing, open **Updates** and press **Check for updates**. On this Pi `Already up to date: the newest release on GitHub (v0.2.0) is not newer than the installed one.` together with **Installed** showing 0.2.0 means success (after a restore test the same words show while the Pi runs the development build) (**Install v0.2.0** is for a Pi that never ran it). More: [Verify](release-0.2.0-owner-commands.md#verify).
