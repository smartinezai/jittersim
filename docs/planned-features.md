# Planned features

This page lists what JitterSim does not do yet. It is a working list for the owner and for anyone who helps. Add an item when you think of one. Move it to the finished list when it ships.

Last updated: 2026-10-02.

How to read an entry:

- **Status** is one of: planned (nobody has started) or in progress (a branch exists) or needs a decision (the owner has to choose first).
- **Why** says what problem it solves in plain words.
- **Notes** record what is already known so the next person does not start from zero.

Please check `git branch -r` and the open pull requests before starting an item. Two people working on the same item cause painful conflicts in the dashboard file. Use one branch for each item.

## Requested by the owner

### 1. Phone layout of the dashboard

- **Status:** planned.
- **Why:** the owner wants to use the dashboard from a phone while standing next to the test devices. The page fits a narrow window without sideways scrolling but it was designed for a laptop screen. The sidebar is long and the cards are dense.
- **Notes:**
  - The browser journeys already check 390 pixel width for overflow. They do not check that the page is pleasant to use.
  - Ideas: a bottom bar with the five most used pages, a sticky bar with Reset and Panic that never scrolls away, bigger touch targets, cards that open one at a time, the sidebar status block as a small strip at the top.
  - Test with a real phone on the Pi Wi-Fi and on the router network.
  - The Panic button must stay reachable at every height. A pinned footer that hid the router switch was tried and rejected because the router controls matter too.

### 2. Homepage bounce

- **Status:** needs a decision.
- **Why:** the owner asked whether a visit to a page deep inside a site could sometimes send the browser back to the site's home page. This tests how an app copes with a surprise redirect.
- **Notes:**
  - DNS cannot do this. DNS only sees the site name and never the page path.
  - Two ways could work: a browser extension in the test browser or a proxy that opens HTTPS traffic with a certificate that the test device trusts.
  - The simpler first step is the "site can't be reached" error with an adjustable chance. The integration branch brings the DNS failure chance from the main line.
  - Decide first which devices need it. A browser extension covers only that browser.

### 3. Real phones on the Pi

- **Status:** planned.
- **Why:** every network check so far used Linux test machines or the Pi's own tools. The general profile and the protected devices have never been tried together on real phones.
- **Notes:**
  - Acceptance test: two affected phones and one protected phone on the Pi Wi-Fi with the general profile active. Check speed limits are shared by the two affected phones only. Join and leave the Wi-Fi while it runs.
  - Check the Impact card shows loss and speed caps for each phone.
  - Check that the protected phone is never listed in Traffic discovery or the Impact card.

### 4. Chrome cannot open the dashboard

- **Status:** needs a decision.
- **Why:** Chrome showed "This site can't be reached" for the Pi's dashboard address while Safari worked.
- **Notes:**
  - The Pi answered this Mac. The most likely cause is the macOS Local Network permission for Chrome. A VPN on the Mac can also hide the local network.
  - Open System Settings then Privacy and Security then Local Network and allow Chrome.
  - If it still fails, check `nc -vz 192.168.0.17 8080` from the Terminal.

## In progress

### 5. Remote command line and one-command deploy from the Mac

- **Branch:** `feat/cli-remote-and-deploy`.
- **What:** `jittersim status`, `profiles`, `traffic`, `wifi show`, `impact` and `connect` run on a Mac against the Pi's dashboard over verified HTTPS. A deploy script builds a release, copies it to the Pi and stages it. The password prompt for `sudo` stays with the owner.

### 6. Integration of the main line

- **Pull request:** the draft "Integrate main into the Pi line".
- **What:** brings scenarios, the activity log, access controls, the DNS failure chance, staged updates and the large command line from the main line into the Pi line. It stays a draft until its own checks are green and the owner has tried it on the Pi.

### 7. Entering the admin token again resets the 24 hours

- **Branch:** `feat/token-remember-refresh`.
- **What:** the remembered token (and router password) gets a fresh 24 hours when the owner enters it again. Restoring it automatically on page load does not extend it.

### 8. Signed releases and deploys without the sudo password

- **Branch:** `feat/signed-releases` (draft pull request "Install only signed releases and let the Pi fetch them").
- **Why:** the owner does not want to type the `sudo` password for every deploy and does not want a stranger or a leaked admin token to get root.
- **What:** the Pi's root update manager stages and installs only archives signed by a release signing key the owner trusts on the Pi (`ssh-keygen -Y` signatures, no new dependency). Every release carries a signed sequence and the Pi refuses older releases except through its own rollback. `make-release-key.sh` makes the key on the Mac and prints the one `sudo jittersim update trust-key` command for the Pi. An optional sudoers rule lets the `jittersim` login stage and install signed releases without a password. Key rotation and removal use `trust-key` and `untrust-key`.
- **Notes:**
  - The threat table and the one-time steps are in the deployment guide under "Signed releases".
  - On the Pi: check that `ssh-keygen -Y verify` of Pi OS accepts the signatures (OpenSSH 8.2 or newer) and that `sudo -n -l` reports the rule.
  - Next step, not built yet: the Pi finds and stages the newest signed release on GitHub itself with a read-only token. See `docs/implementation-plan-online-release-source-2026-10-03.md`.

## Ideas that look useful

These come from reviews of the recent work. Nobody has asked for them yet.

### Dashboard

- **Allow-only warning.** Add to profile switches a profile to "allow only listed domains" when it was not an allow list. Every other site then stops working for devices on that profile. Ask for confirmation first.
- **Delete a profile.** There is no delete call because a profile can be used by devices or schedules or service lists. Add it with a check that shows what would break.
- **Rename and regroup traffic names.** The groups come from a built-in table. Let the owner move a name to another group or give an unknown name a label.
- **Other tabs.** The Saved line under the Start button reads devices and groups and lists once. A change made in another tab shows after a reload. Poll them like the connected clients.
- **Double announcements.** The message line and the pop-up message both announce each message so a screen reader reads it twice.
- **Accessibility audit.** Run axe-core on every page. It could not be run so far.

### Diagnostics

- **History of the Impact card.** Keep a short history per device and draw it as a small graph. Today it shows only the last minute.
- **Delay measurement.** The Impact card cannot measure added delay because it reads packet and drop counters only. A short ping test could add it. The owner decided against it for now.
- **Shared answer.** `/api/impact` is read again by every open tab. A short server side cache would cut the work.
- **Running state.** The "running" flag reflects whether the scheduler is paused and not whether its thread is alive. Make it check the thread.

### Network

- **Duplicate packets.** The kernel refuses a duplicating delay queue next to any other delay queue on the same interface. A device can only duplicate while it is the only one with a delay queue. Look for another way such as separate queues per direction.
- **Fair use of the helper.** A write that waits for the helper can in theory lose to a tight loop of reads. A "write waiting" flag would close the gap.
- **Quieter logs.** Traffic counters write a full error trace on every failed read.

### Security

- **Router login throttle.** The dashboard does not limit wrong router password tries on the server side. The browser stops after one rejection. A script could keep trying.
- **Clipboard.** The Copy Wi-Fi password button leaves the password on the clipboard until something else replaces it. Offer a timer that clears it when the browser allows.

### Tests and releases

- **Journey races.** Two browser journeys failed on timing in CI before they were fixed (a message that arrived late and a busy helper). Review every journey for waits that depend on timing and wait for state instead.
- **Browser versions.** The local Playwright version differs from the one in CI. Pin both to one version.
- **Pi in the loop.** A regular check that installs the release on a spare Pi and runs the journeys would catch what the Linux container cannot.

## Not planned

- **A light theme.** The owner does not want one. The dashboard stays dark.

## Finished

For the record. See the pull requests for details.

- Device names instead of IP addresses.
- Protected devices are never monitored and look greyed out.
- Plain language labels for observed names with "seen with" hints.
- Traffic groups with Add all to profile and Block all.
- Block globally shows its state and can be undone.
- Saved profiles list with Edit and Apply to all.
- Help icons on every profile field.
- Sidebar status with the general profile and the affected devices.
- Copy router Wi-Fi name and password.
- Impact card in Diagnostics.
- Clear wording for the Start button and whether JitterSim is running.
- Support for more than eight shaped devices in one plan.
