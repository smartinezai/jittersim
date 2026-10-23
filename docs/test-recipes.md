# Test recipes for mobile apps

Short recipes for testing how a phone app copes with a bad connection. Each one
uses only what JitterSim does today. Status: current. Checked on 2026-10-23
against main at `03fe31d8`: the preset values and the button and field names
were read from the dashboard source and the [user guide](user-guide.md). The
recipes were not run on a Pi for this page.

## Before every recipe

1. Join a test phone to the test Wi-Fi (default name `TestNet`). Open the
   dashboard from a computer on the router's network or Ethernet, not from the
   test Wi-Fi, and choose **Check token**. Never slow the computer that controls
   the Pi.
2. On **Devices**, choose **Check for devices**. The phone shows under
   **Connected clients**.
3. Make the profile of the recipe on **Profiles**. Pick a preset under **Start
   from a preset** or fill the fields, then choose **Save profile**.
4. Beside the phone choose the profile and press **Assign profile** (the button
   reads **Apply profile** once the phone is saved). If the dashboard asks you
   to choose **Start** first, do that. After **Panic** you always need **Start**.
5. **Saved devices** shows the phone as Normal, Degraded or Offline.
   **Diagnostics** then **Impact** shows the loss and the speed cap that really
   happened (see [Impact check](user-guide.md#impact-check)).

**How to stop, for every recipe.** **Reset network** in the sidebar stops the
profiles and keeps household blocking. **Panic** also turns household blocking
off and keeps your saved settings. On a phone-sized screen Panic sits in the
bar at the bottom. After Panic press **Start** before the next recipe and turn
household blocking back on under **Access and rules**. If the dashboard will not
open, run `sudo jittersim normal-access` on the Pi over SSH from Ethernet or the router
network (SSH never works from the test Wi-Fi). See
[Starting and stopping](user-guide.md#starting-and-stopping).

## Offline at launch

- **Use:** the preset **Offline** (100% loss in both directions). Assign it to
  the phone.
- **Do:** quit the app completely and launch it.
- **Watch:** the first screen. Does it show an offline message or its saved
  content soon, or spin for ever? How long until it gives up? Then press
  **Reset network** with the app still open and watch whether it recovers without
  a relaunch.
- **Pass:** a usable offline state within your time limit, no crash, and a
  normal screen after Reset without a restart.
- **Note:** the phone stays joined to the Wi-Fi. The Pi drops every packet, so the
  app sees a network that is up but silent, not the "no network" state of
  airplane mode. Use airplane mode on the phone when you need that case.
- **Stop:** **Reset network**, or **Panic**.

## Packet loss in the middle of an upload

- **Use:** two profiles. `No limits` has nothing set and is the phone's normal
  profile (assign it first so the phone is a saved device). `Upload loss` has
  **Loss (%)** 20 and **Loss bursts (packets)** 3 under **Upload** and nothing
  under **Download**.
- **Do:** on **Schedules and limits** open **Run a profile timer**. Pick the phone,
  `Upload loss` and a **Duration (minutes)** of 1 or more. Start a large upload
  in the app, then press **Start timer**. The loss starts at once and the phone
  goes back to `No limits` when the timer ends.
- **Watch:** the progress bar, the retries and the end of the loss. Does the upload
  continue where it stopped or start again? **Impact** shows `set 20%` with the
  actual loss for the upload.
- **Pass:** the upload finishes after the loss ends, with no duplicate or damaged
  file, or the app stops with a clear message and offers a retry.
- **Stop:** the timer ends by itself. To end it sooner press **Reset network**.

## Wi-Fi drop and rejoin: not available yet

JitterSim cannot make a phone leave the Wi-Fi and come back. Refusing a device's
Wi-Fi connection needs a supported access point and Pi hardware testing (see the
[user guide](user-guide.md#what-you-can-try)). Do one of these instead.

- **Short outages with the phone still joined.** Save a profile with **Outage
  chance (%)** 100, **Outage length (seconds)** 15 and **Outage check interval
  (seconds)** 60 under **Randomness, outages and website access**. Assign it.
  The first outage starts about one check interval after the profile starts. The Pi then
  drops all traffic of the phone for 15 seconds, and **Activity** records
  `outage started` and `outage ended`. The preset **Train tunnel** does this at
  random (a 30% chance each minute for 10 seconds) with slow, lossy traffic.
- **A real rejoin.** Switch Wi-Fi off and on in the phone's settings while a profile
  runs. If the phone comes back with a new private Wi-Fi address see
  [Address changes](user-guide.md#address-changes) so its profile follows it.
- **Stop:** **Reset network**, or **Panic**.

## Slow start

- **Use:** the preset **3G** (100 ms each way, 330 kbit/s up and 780 kbit/s down),
  or **EDGE (2G)** for a worse start. The phone's normal profile is `No limits`.
- **Do:** open **Run a profile timer**, pick the phone and the profile, set 1 minute
  and press **Start timer**. Open the app at once. After the minute the phone
  returns to `No limits`. For a different length use **Scenarios**: save two steps
  (for example 3G for 30 seconds, then `No limits`), pick the phone under
  **Applies to** and press **Start scenario**.
- **Watch:** the first load, spinners and time-outs, and whether the app finishes
  loading when the speed comes back without a relaunch.
- **Pass:** it shows progress instead of a false "no connection" error and loads
  fully once the connection is fast again.
- **Stop:** the timer ends by itself. **Reset network** ends it sooner.

## High latency

- **Use:** the preset **Satellite**: 300 ms each way with 1% bursty loss and a cap
  of 2 Mbit/s up and 10 Mbit/s down. A lab run measured about 600 ms round trip
  (see [Measured effects](measurements-2026-09.md)). For delay only, save a profile with
  **Delay (ms)** 300 under both **Upload** and **Download** and nothing else.
  **Very bad network** is 500 ms each way with 10% loss.
- **Do:** assign it and use the app as a person would: sign in, load a list, send
  something, open a detail page.
- **Watch:** time-outs that are too short, buttons that can be tapped twice while
  a request waits, and feedback while the app waits. **Impact** cannot measure
  delay. The **Test speed** window pings the phone and its round trip includes the
  profile's download delay (see [Device speed test](user-guide.md#device-speed-test)).
- **Pass:** every action completes once, with no duplicate submit.
- **Stop:** **Reset network**, or **Panic**.

## A shared limit

- **Use:** a profile with **Bandwidth (kbit/s, optional)** 2000 under **Download**
  and 1000 under **Upload** and nothing else. Give it to **Everyone**. All
  affected devices share this one cap, as on a busy home link.
- **Do:** on **Devices** open **Everyone**, choose the profile under **Saved
  profile** and press **Apply to all**. Put a second device on the test Wi-Fi.
  Run the app on the phone. Then start a large download on the second device and
  stop it again. Remember that your own computer is also affected if it uses the
  test Wi-Fi, unless it is under **Protected devices**.
- **Watch:** the app's speed and quality while the second device downloads and
  afterwards. **Impact** shows a row for Everyone and says `cap reached` once the cap is hit.
- **Pass:** the app slows or lowers its quality but keeps working, and speeds up
  again when the other download ends.
- **Stop:** **Stop for everyone**, **Reset network** or **Panic**.
- A shared limit on data (bytes, not speed) is **Shared group data limit** on
  **Schedules and limits**. See [Data quotas](user-guide.md#data-quotas).

JitterSim has no captive portal and no IPv6-only network. The test Wi-Fi hands out
IPv4 addresses only.
