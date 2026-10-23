# v0.2.0 owner acceptance card

Owner-only checks for the v0.2.0 release. Source and UI labels below were checked against `origin/main` at `3afb48d5d61fdbfeb1f51cd1f99622893b8482f9`.

## Before the release command

The release command signs records and the package, merges the release PR after asking, and can publish v0.2.0. Run it only from the clean, selected release checkout and only when you are ready to answer its owner prompts. The command is copied from [the release runbook](release-runbook-2026-10-16.md); it is shown here as text and has not been run:

```sh
ssh-add -t 14400 ~/.ssh/jittersim_release_ed25519
JITTERSIM_RELEASE_LOCAL_CI=none caffeinate -i scripts/release.sh 0.2.0
```

Expected: the script runs its required release checks, prepares and signs the exact package, then pauses for the owner to test that exact package and provide acceptance evidence before publication. It asks before merging and before publishing. After all required evidence is accepted and the owner confirms publication, v0.2.0 is published with its signed package and acceptance record.

**Release blocker:** recovery must be tested for this exact candidate. A bootstrap upgrade alone is not recovery evidence. Do not enter a passing `recovery` answer unless the recovery check was actually completed and its evidence retained. If it has not been completed, stop before approval or publication. The runbook currently suggests deferring Restore to 0.2.1 and substituting a bootstrap upgrade; that does not satisfy this gate.

**Order dependency:** `scripts/release.sh` requires installation and testing of the exact package before it will publish. The dashboard update check below can only fetch the release after publication, so it is a post-publication check; it does not replace the exact-package acceptance required before publication. Confirm the owner-approved exact-package test path and recovery plan before starting the release.

## Physical checks, in order

### 1. Check for and install v0.2.0 from the dashboard

On the dashboard, open **Updates**, choose **Check for updates**, review the displayed version and release notes, then choose **Install update** and confirm.

Expected: the Pi stages the trusted, newer signed release; the dashboard and test network briefly restart; the page reports success only after installation and its health check complete. Confirm the installed version reads `0.2.0`. If the release is not listed, the check cannot establish the online update path; stop and resolve the trusted signing key, read-only GitHub token, or release availability first. See [signed updates and the dashboard workflow](user-guide.md#update-the-pi).

### 2. Cold boot and inspect `boot_scan`

With the router Wi-Fi on, fully power the Pi off and on. Once it has booted, run this on the Pi:

```sh
sudo /opt/jittersim/.venv/bin/python -m jittersim.networking.wifi_follow status
```

Expected: the status includes `boot_scan: seen` (not `failed`). Also record `boot_decision`; if the router's own Wi-Fi name was seen on air, the Pi can keep its separate recovery name for this boot. The documented check is [the Pi Wi-Fi follow status](../deploy/raspberry-pi/README.md#the-pi-wi-fi-follows-the-router).

### 3. Check SSID handling with a non-English locale

On the Pi, run exactly:

```sh
LC_ALL=C nmcli -t -f SSID dev wifi list
```

Expected: the command lists visible Wi-Fi names and completes without losing or corrupting a name containing an umlaut. Confirm the umlaut-bearing network is in range before treating its absence as a failure; record the output without including passwords or other credentials.

### 4. Reset the Pi Wi-Fi name

On the dashboard, choose **Reset Wi-Fi name** (in the sidebar or on **Router and Wi-Fi**) and confirm if prompted. Reconnect to the Pi's test Wi-Fi if this device disconnects.

Expected: the test network name returns to `TestNet`; its existing password and security type are kept. Wi-Fi restarts and test clients disconnect during the change. See [Reset Wi-Fi name](user-guide.md).

### 5. Check the this-device label

Open the dashboard over the Pi's test Wi-Fi and find the connected client representing the device currently showing the dashboard.

Expected: that client is marked **This device** when the Pi can identify it. The label is not expected when viewing the dashboard through Ethernet or the router network. See [Devices](user-guide.md#devices-and-website-rules).

### 6. Test speed

In **Devices**, choose **Test speed** for a connected, unprotected device. In the test window choose **Start measuring**; run the displayed speed test on that device, then choose **Finish now** or let the selected test length end.

Expected: JitterSim saves a result for the selected device, including the Pi's traffic measurements and the speed values entered from the device's test. The result identifies the applied profile and can be compared with another run. See [Device speed test](user-guide.md#device-speed-test).

### 7. Check connection

In **Devices**, choose **Check connection** for a connected device, wake that device, then choose **Start check** in the dialog.

Expected: the check completes and saves a verdict comparing measured packet loss, round-trip delay, jitter, duplicate answers, and ordering with the applied profile. Record the actual verdict; it depends on the selected profile and link. See [Connection check](user-guide.md#connection-check).

### 8. Press Panic

Choose **Panic** in the dashboard bar and confirm if prompted.

Expected: profiles stop and household blocking turns off; saved settings remain. The status shows profiles are off. Confirm normal access is restored, then record the result. See [Starting and stopping](user-guide.md#starting-and-stopping).

## Record the evidence

For each check, record when it was performed, the v0.2.0 version shown, the observed result, and any failure. Keep release/signature evidence with the release record. Do not mark a gate passed when it was skipped, inferred from a different package, or replaced by an ordinary upgrade.
