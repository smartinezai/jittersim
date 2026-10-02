# Pi acceptance checklist

This is the one list of things the owner tries on the Pi after installing a release. Each box says what to do and what you should see. Tick a box only when you saw the result. If the result differs write down what you saw and the time. Panic in the sidebar is always the way back.

Last updated: 2026-10-02. Nobody has ticked these boxes on a real Pi yet unless a line says so.

Release tried: ________________  Date: ________________

- **Part A** is for every build of the Pi line. It also applies to the integration build.
- **Part B** is only for the integration build (the draft pull request "Integrate main into the Pi line"). Do part A first.

## Before you start

- **Keep two ways into the Pi.** SSH does not work from the test Wi-Fi. Use Ethernet or the router network for SSH and for installs. The test Wi-Fi cannot reach the router's own admin page either.
- **The router locks its login after a few wrong passwords.** Enter the router admin password with care. If the dashboard rejects it once stop and look at the password on the router. Do not try again and again.
- **Router Wi-Fi changes can cut you off.** The sidebar switch and **Copy router Wi-Fi to Pi** can drop the connection of the computer you work on. Do those checks from a computer on Ethernet.
- **Never paste secrets into a chat or ticket.** That covers the admin token and the router password and the Wi-Fi password. On the Pi you can read the admin token with `sudo grep JITTERSIM_ADMIN_TOKEN /etc/jittersim/control.env`.
- **Use test phones and a throwaway profile.** Never block or slow the computer you control the Pi from.
- If the Pi login password was ever pasted into a chat change it now with `passwd` on the Pi.

## Part A: the Pi line

### Start up

- [ ] **Sign in and token memory**
  - Do: open the dashboard then choose **Admin token** and **Check token**. Tick **Remember for 24 hours** and check again. Reload the page.
  - Expect: the page unlocks without typing the token again and the note under the box reads `Remembered until` followed by a time about 24 hours away.
- [ ] **Token memory renews** (only if your release has the token renewal change)
  - Do: a few minutes later enter the token again and choose **Check token**.
  - Expect: the time in the note moves forward to 24 hours from now. A plain page reload does not move it.
- [ ] **Start button wording**
  - Do: press **Start with saved settings**. Then press **Panic**.
  - Expect: while it runs the button reads **Re-apply saved settings** and the message lists what started. After Panic it reads **Start with saved settings** again.
- [ ] **Reboot the Pi** (from Ethernet or a local terminal)
  - Do: reboot the Pi and wait two minutes.
  - Expect: the test Wi-Fi comes back on its own and the dashboard opens. The last state is restored or JitterSim shows as not running if you had pressed Panic or Reset.

### Devices and traffic

- [ ] **Device names**
  - Do: join a phone to the test Wi-Fi. Open **Devices** and press **Check for devices**. Save a name for one device.
  - Expect: each device shows a name with its address in small text underneath. A device with no name says `Unknown device` and not `Client` plus an address. The saved name shows in the group picker and the Traffic page and the sidebar list too.
- [ ] **Protected devices are not monitored**
  - Do: open **Traffic** and scan. Then press **Make unaffected** beside a phone on **Devices** and scan again.
  - Expect: the phone's lookups disappear from the results. Its row is greyed out and says `Traffic is not monitored` with its profile and group controls disabled. **Remove protection** still works.
- [ ] **Traffic labels**
  - Do: scan on the **Traffic** page while a phone uses a few apps.
  - Expect: names show plain labels such as app or tracker or content delivery. A hint reading `Seen with` and an app name and `(guess)` can show on delivery names. **What is this?** opens a web search in a new tab.
- [ ] **Traffic groups**
  - Do: open a group such as ImmoScout24 or Netflix. Press **Block all** then **Unblock all**.
  - Expect: the group lists its names with counts. Both buttons change every name at once. The group stays open when the page refreshes. A group of system or delivery names has no **Block all**.
- [ ] **Block and unblock one name**
  - Do: with JitterSim running press **Block globally** on one name. Try to load it on a test phone. Then press **Unblock**.
  - Expect: the button turns into **Unblock** with a **Blocked** badge and the name stops loading (a cached lookup may last a little while). After **Unblock** it loads again.
  - **Warning:** if you also press **Add to profile** use a throwaway profile. It can switch the profile to **Allow only listed domains** and every other site stops for devices on it.
- [ ] **Impact check**
  - Do: give a phone a profile with 10% loss and a 2 Mbit/s cap. Open **Diagnostics** and watch the **Impact** card while the phone loads a video.
  - Expect: the loss line and the cap line fill in and the verdict reads `Affected in the last minute`. Protected devices show on one muted line and never get a row.

### Profiles

- [ ] **Saved profiles list**
  - Do: open **Profiles**. Press **Apply to all** on one saved profile. Join a second phone while it runs. Then press **Stop general profile**.
  - Expect: every unprotected device gets the profile including the later arrival. A protected device stays normal. After **Stop** the saved device rules resume.
- [ ] **Profile help icons**
  - Do: open **Save profile** and hover or tap the **?** beside Profile ID and Jitter and Reorder and Duplicate and Corrupt.
  - Expect: each one explains the field in plain words.
- [ ] **Apply a profile to one device**
  - Do: with JitterSim running choose a profile beside a connected phone on **Devices** and press **Assign profile**. Ping or load a page from that phone. Write down what you see because this was never confirmed on the Pi.
  - Expect: the row shows the profile and the sidebar lists the device. The phone sees the delay or loss of that profile.
- [ ] **Sidebar status**
  - Do: look at the status block in the sidebar while a profile runs then after Panic.
  - Expect: it names the general profile and says whether it is active or paused. It counts the affected devices and opens a list with the profile of each. It names the enabled service lists. After Panic the devices line reads `Profiles off`.
- [ ] **Panic with a profile active**
  - Do: run a profile that slows a phone then press **Panic** in the sidebar.
  - Expect: the phone is back to normal speed within seconds and the sidebar reads `Profiles off`. Your saved settings are still there.
- [ ] **General profile on real phones** (tick it if the separate acceptance run already did this)
  - Do: put two phones and one protected phone on the test Wi-Fi. Apply a general profile with a speed cap. Run a speed test on all three. Join and leave the Wi-Fi while it runs.
  - Expect: the two phones share the cap. The protected phone runs at full speed and never shows in Traffic discovery or the Impact card.

### Service lists

- [ ] **Service list on an unprotected device**
  - Do: under **Schedules and limits** make a service list with a profile of 1000 ms delay. Press **Start with saved settings**. On an unprotected phone open a listed service then an unrelated site.
  - Expect: only the listed service is slow. A protected phone loads both at normal speed.
- [ ] **The Pi fills the list**
  - Do: on the Pi run `sudo nft list set inet jittersim svc_1` and `sudo systemctl status jittersim-service-watch.service`.
  - Expect: the set holds addresses within a minute of applying and the service shows `active (running)`. The Impact card shows a row for the list.

### Router

- [ ] **Router password check**
  - Do: open the **Router password** panel. Enter a wrong password once and choose **Check password**. Then enter the right one.
  - Expect: the wrong one is rejected and is not sent again until you edit it. The right one is accepted and the router switch shows On or Off with the time of the last check.
  - **Warning:** a wrong password only once. The router locks its login after a few tries.
- [ ] **Router password memory**
  - Do: tick **Remember for 24 hours** under the router password. Reload the page.
  - Expect: the password comes back and the router status keeps updating without typing. Press **Clear** afterwards if the computer is shared.
- [ ] **Copy router Wi-Fi name and password**
  - Do: with the router password accepted press **Copy Wi-Fi name** and **Copy Wi-Fi password** in the sidebar. Paste each into a harmless note. Then copy something else.
  - Expect: you get the router's own Wi-Fi name and password and neither shows on the screen. A wrong router password disables both buttons until you change it.
  - **Warning:** do not press **Copy router Wi-Fi to Pi** for this check. It renames the test Wi-Fi to the router's name and can move your computer onto the Pi.
- [ ] **Router Wi-Fi switch**
  - Do: from a computer on Ethernet turn the sidebar **Router Wi-Fi** switch off then on again. A separate session also owns this check so tick it only if you did it yourself.
  - Expect: the switch shows `Changing` then the confirmed state. Devices on the router Wi-Fi drop and come back.
  - **Warning:** turning it off cuts every device on the router Wi-Fi. Turn it back on with the same switch or from a computer on Ethernet. The test Wi-Fi cannot reach the router page.

## Part B: only the integration build

### Install

- [ ] **Back up the database first**
  - Do: before installing run `sudo cp -p /var/lib/jittersim/jittersim.db ~/jittersim.db.before-integration` on the Pi. Also keep the backup the Pi guide asks for.
  - Expect: the copy exists. You need it because the old dashboard fails with `stored schedule is malformed` after a rollback if group or everyone-else schedules were created.
- [ ] **The first install makes a new certificate**
  - Do: install the release then open the dashboard in the browser. Compare the SHA-256 fingerprint the installer printed with the one the browser shows.
  - Expect: the browser warns once because the certificate is new. The fingerprints match. After you trust it the warning is gone.
- [ ] **The Pi name resolves**
  - Do: on the Mac run `ping -c 2 <pi-name>.local` and open `https://<pi-name>.local:8080`. Do the same with `jittersim.local`.
  - Expect: both names answer and the dashboard opens. If a name does not resolve from the management Wi-Fi use the Ethernet address.
- [ ] **Sign in again**
  - Do: choose **Check token**. From the test Wi-Fi also choose **Allow this device**.
  - Expect: the dashboard unlocks. Known limit of this build: the page still shows the name jittersim before you sign in.

### Access rules

- [ ] **DNS for test clients with a global block list**
  - Do: open **Access and rules** and type one harmless name under **Global blocked domains**. Press **Save access rules** then **Apply access rules**. On a test phone open ordinary sites and then that name.
  - Expect: ordinary sites work and the blocked name and the names under it fail. After you remove it from the list and apply again it loads again.
- [ ] **A blocked client can still sign in**
  - Do: under **MAC filtering** set **Mode** to **Block these MAC addresses**. Pick a test phone and press **Add to block list**. Press **Save access rules** then **Apply access rules**. On that phone open `https://192.168.50.1:8080`.
  - Expect: the phone has no internet but it reaches the dashboard sign-in. After **Panic** it has internet again and still reaches the sign-in.
  - **Warning:** never put the computer you control the Pi from on the list and never choose **Allow only these MAC addresses**. It can lock out every device.
- [ ] **Access controls and their summary**
  - Do: save a MAC rule and a website rule on **Access and rules** then apply them. Then run `sudo jittersim access status` on the Pi.
  - Expect: the summary line at the top of the card updates right after each save without a reload and the command shows the same rules.
- [ ] **Panic removes everything**
  - Do: run a profile and keep a website block and a MAC block rule active. Press **Panic** in the sidebar.
  - Expect: profiles are off. The blocked name loads and the blocked phone has internet. Your saved lists are still there. **Access and rules** says that Normal access is on and that you turn household blocking back on yourself.
- [ ] **DNS failure chance**
  - Do: on **Access and rules** set **DNS failure chance** to 20 then press **Save access rules** and **Apply access rules**. Open a few sites you have not visited in a private window. Then do the same with 100 and confirm the question. Then set 0 and apply.
  - Expect: at 20 about one in five new lookups fails at once with a site connection error. At 100 every new lookup fails but the dashboard still opens by its address. At 0 everything works. A cached name can delay the effect.
  - **Warning:** put it back to 0 when you finish. At 100 nothing loads on the test Wi-Fi.
- [ ] **Router outage option**
  - Do: on **Router and Wi-Fi** find **Router outage behavior**. Tick **Keep active profiles through router outages** and press **Save outage behavior**. Run a profile and restart the router from its own page. Repeat with the box unticked.
  - Expect: with it on the profile returns once the router connection is back. With it off profiles stay stopped until you press **Start with saved settings**.
  - **Warning:** restarting the router cuts the internet for the whole household and drops the Pi's Ethernet for a moment. Pick a quiet time.
- [ ] **The DNS service after a reboot**
  - Do: with household access rules on reboot the Pi. Then run `sudo systemctl is-active jittersim-dns` on the Pi.
  - Expect: it answers `active` and a test phone can look up names. With the access rules off it is expected to be inactive.

### More features

- [ ] **Staged update flow**
  - Do: stage a newer release on the Pi (for example with the deploy script below without `--install`). On **Router and Wi-Fi** press **Check for updates** then **Install staged release**.
  - Expect: the **Release staged** badge shows beside the page name and the check shows the staged and installed labels. After the install the dashboard comes back with the new release label.
  - **Warning:** keep Ethernet or SSH ready. The dashboard and the test network are cut for a short time. **Restore previous installation** brings back the settings from before the update and drops later changes so try it last or skip it.
- [ ] **Scenarios**
  - Do: under **Schedules and limits** save a scenario with two or three short steps for one test phone. Start it.
  - Expect: the phone's profile changes at each step on time and the **Activity** page records each change. A finished scenario does not start again by itself.
- [ ] **Schedules for a group and for everyone else**
  - Do: save a schedule with a window a few minutes ahead for a group and one for **Everyone else**.
  - Expect: the profile applies only inside the window. Protected devices stay normal.
- [ ] **Activity log**
  - Do: open **Activity** after a few actions. Filter by an event type. Press **Copy as Markdown** and paste it into a note. Press **Clear all**.
  - Expect: your actions show newest first and moving a group shows one event for the group. The copy holds the same list. **Clear all** asks before it deletes.
- [ ] **Export and import of settings**
  - Do: on **Diagnostics** open **Backup and action history**. Press **Export settings**. Then press **Import settings...** and choose the same file.
  - Expect: the file holds profiles and devices and groups and schedules and access rules and limits. It holds no usage and no passwords. After the import the service lists and the general profile are still there.
  - **Warning:** an import replaces all settings. Do the database backup first. Keep the file private because it lists device addresses.
- [ ] **A device that comes back with a new address**
  - Do: switch a test phone's Wi-Fi off and on until it gets another address. Apply a profile to it.
  - Expect: the dashboard shows one saved device for it and not a second copy.
- [ ] **Client isolation**
  - Do: run `sudo nmcli connection up jittersim-ap` on the Pi from Ethernet so the test Wi-Fi restarts. Join two phones. Try to reach one from the other.
  - Expect: both phones reach the internet but not each other.
  - **Warning:** the test Wi-Fi drops for a few seconds. Do not run the command from a computer that uses it.
- [ ] **Reset over SSH while the dashboard runs**
  - Do: from the router network run `sudo jittersim reset` over SSH while a profile runs.
  - Expect: the command succeeds. The profile stops and the sidebar reads `Profiles off`.
- [ ] **Bursty loss**
  - Do: apply a bursty loss preset to a test phone and ping from it.
  - Expect: lost packets come in short runs and not spread evenly.

### From the Mac

- [ ] **Remote commands**
  - Do: copy `/etc/jittersim/dashboard.crt` from the Pi over a connection you trust and compare its fingerprint with the installer's. Run `jittersim connect --url https://<pi-address>:8080 --ca-cert ~/jittersim-dashboard.crt`. Then run `jittersim status` and `jittersim profiles list` and `jittersim traffic --scan` and `jittersim impact`.
  - Expect: each command asks for the admin token with a hidden prompt and prints its result. Status names the release on the Pi and the affected devices. The impact check waits about ten seconds.
  - **Warning:** never type the token or a password in the command itself. Skip `jittersim wifi show --password` because it prints the Wi-Fi password on the screen.
- [ ] **Deploy script**
  - Do: from a JitterSim checkout on the Mac run `deploy/raspberry-pi/deploy-from-mac.sh --ref RELEASE_REF --dry-run`. Then run it without `--dry-run` and once more with `--install`.
  - Expect: it prints the commit and the SHA-256 and stages the release. The Pi asks for its `sudo` password in your terminal and you type it yourself. After `--install` it prints the release the Pi runs and that matches `git rev-parse --short RELEASE_REF`.
  - **Warning:** SSH times out from the test Wi-Fi. Put the Mac on Ethernet or the router network first.
- [ ] **Phone layout on a real phone** (only if your build has the phone layout change)
  - Do: sign in from a real phone on the test Wi-Fi after **Allow this device** and again on the router network. Open a long page and scroll to the bottom.
  - Expect: Panic is always in view and the router switch is one tap away. A page starts on the first screen after you tap its name. Buttons are easy to hit and nothing scrolls sideways.
  - **Warning:** do not turn the router Wi-Fi off from a phone that uses it.

## Known limits of the integration build

These are on the list of planned features. Do not count them as failures of a check.

- The page shows the name jittersim before sign-in.
- The certificate does not cover `192.168.50.1`.
- A device in a random outage also loses the Pi's DNS.
- Browser tunnels to a listed service are not counted in a device's data.
