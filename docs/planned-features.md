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

### 8. Forced test network window

- **Status:** in progress. Built as a stack of draft pull requests: the engine and storage (#61) then the helper steps and the root-only login (#62) then the service and API (#63) then the dashboard card (#69). Still open: a trial on the real Pi with a short window. The owner decided where the router login lives and what happens when the router does not answer.
- **Why:** the owner wants a weekend where the only Wi-Fi at home is the Pi. A schedule such as Friday to Sunday or Saturday 15:00 to Sunday 05:00 switches the router's Wi-Fi off and makes the Pi's test Wi-Fi look like it. Every device has to join the Pi. When the window ends the Pi goes back to its own name and the router's Wi-Fi comes back on.
- **How it would work:**
  - A new schedule kind with a start and an end. A window may cross midnight and may span several days.
  - At the start the Pi copies the router's Wi-Fi name and password to its test Wi-Fi and then turns the router's Wi-Fi off. It uses the existing copy and switch actions.
  - At the end the Pi restores its own name and password and turns the router's Wi-Fi on.
  - The window state is stored in the database. After a reboot or a crash the Pi either carries on or restores. Restoring twice is safe.
  - Panic and Reset end the window and restore everything at once.
  - The dashboard and the command line show "The router's Wi-Fi is off until <time>" while a window runs.
- **Rules that must hold:**
  - Order matters. Copy the name first and check the Pi's Wi-Fi works. Only then turn the router's Wi-Fi off. If the switch-off fails roll back to the Pi's own name and report it.
  - The Pi stays reachable over Ethernet. Do not start a window without an Ethernet path.
  - Do not start a window when the Pi's clock is not synced.
  - The restore never depends on the router answering. The Pi's own name comes back first and the router's Wi-Fi is retried with a warning until it works.
- **Decided:**
  - The Pi stores the router login so the switch-off and switch-on run with nobody present. The file belongs to root and nobody else can read it. The dashboard never shows the password again after saving it.
  - If the router does not answer when the window ends the Pi restores its own name first and keeps retrying the router with an alert on the dashboard and in the activity log until it works.
- **Tests:**
  - Storage: a window that crosses midnight and one that spans days. State survives a reopen. A second restore changes nothing.
  - Runner with a fake router: start and end in order. A failed switch-off rolls back. A crash after the copy resumes. Panic ends the window.
  - Journey: the Schedules page form and the notice while a window runs on desktop and phone sizes.
  - Mutation tests for the order of the steps and the rollback.
- **Notes:** it touches the router switching code (`networking/router.py` and `api/router_operations.py`). Coordinate with whoever works there.

### 9. Semantic version numbers for releases

- **Status:** in progress (#60). The owner chose semantic versions and the installer printing the number. Still open: the owner tags `v0.2.0` on the commit that is ready.
- **Why:** a release is named after a commit today (for example `a0dc551`). That says nothing about order or size of change. The package version is `0.1.0` in `pyproject.toml` and in `src/jittersim/__init__.py` and never changes.
- **How it would work:**
  - Releases are numbered `MAJOR.MINOR.PATCH`. A new feature raises MINOR. A fix raises PATCH. A change that needs a reinstall with the installer or breaks saved settings raises MAJOR.
  - Versions before `1.0.0` may change more freely. The first number to ship from the Pi line is `0.2.0`.
  - A tag `vX.Y.Z` marks each release. The build script takes the tag and names the bundle `jittersim-X.Y.Z.tar.gz`.
  - A single source for the number: `pyproject.toml`. The package reads it and the dashboard and the command line show it. A test fails when the tag and the file disagree.
  - The Diagnostics card shows `0.2.0 (a0dc551)`. The commit stays for tracing but the number is the name.
  - A build from a branch that is not tagged is `X.Y.Z.dev+<commit>` so it never looks like a release.
  - `jittersim.updates stage --version` accepts only these forms and refuses to install an older number over a newer one unless the owner restores on purpose.
- **Decided:** the installer prints the number it installs. A stack build that is installed for testing is a `.dev` build until the owner tags it. Only a tag makes a release.
  - The installer's last line reads `Installed JitterSim 0.2.0 (a0dc551)`. A `.dev` build reads `Installed JitterSim 0.2.0.dev+a0dc551`.
- **Tests:** the version regex accepts `0.2.0` and `1.0.0` and `0.2.0.dev+a0dc551` and rejects `v0.2` and `a0dc551`. The build script names the file after the tag. The tag and `pyproject.toml` must match. A downgrade is refused without the restore flag. The dashboard shows the number. The installer prints it.

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
- Help icons as small round marks with a 44 pixel touch target on phones.
- A note on the Traffic page and in the installer when an upgrade clears the DNS names.
- Sidebar status with the general profile and the affected devices.
- Copy router Wi-Fi name and password.
- Impact card in Diagnostics.
- Clear wording for the Start button and whether JitterSim is running.
- Support for more than eight shaped devices in one plan.
