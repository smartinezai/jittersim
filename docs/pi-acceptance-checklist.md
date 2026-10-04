# Pi acceptance checklist

This is the one list of things the owner tries on the Pi after installing a release. Each box says what to do and what you should see. Tick a box only when you saw the result. If the result differs write down what you saw and the time. Panic in the sidebar is always the way back.

Last updated: 2026-10-03. Nobody has ticked these boxes on a real Pi yet unless a line says so.

Release tried: ________________  Date: ________________

- **Part A** covers everyday controls.
- **Part B** covers installation, trusted access and recovery. Do part A first.
  Use a signed release from the verified official `main` after its required
  checks are complete. A local fixture or branch test does not tick these boxes.

## Before you start

- **Keep two ways into the Pi.** SSH does not work from the test Wi-Fi. Use Ethernet or the router network for SSH and for installs. The test Wi-Fi cannot reach the router's own admin page either.
- **The router locks its login after a few wrong passwords.** Enter the router admin password with care. If the dashboard rejects it once stop and look at the password on the router. Do not try again and again.
- **Router Wi-Fi changes can cut you off.** The sidebar switch and **Copy router Wi-Fi to Pi** can drop the connection of the computer you work on. Do those checks from a computer on Ethernet.
- **Never paste secrets into a chat or ticket.** That covers the admin token and the router password and the Wi-Fi password. On the Pi you can read the admin token with `sudo grep JITTERSIM_ADMIN_TOKEN /etc/jittersim/control.env`.
- **Use test phones and a throwaway profile.** Never block or slow the computer you control the Pi from.
- If the Pi login password was ever pasted into a chat change it now with `passwd` on the Pi.

## Part A: everyday controls

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
- [ ] **Copy router Wi-Fi to Pi from the sidebar**
  - Do: from Ethernet with the router password accepted press **Copy router Wi-Fi to Pi** in the sidebar. Check the preview then cancel once. Repeat and confirm only when a test Wi-Fi restart is acceptable.
  - Expect: there is one combined button. Cancelling changes nothing. Confirming applies the router's Wi-Fi name and password together. The password is not displayed or copied to the clipboard. A rejected router password blocks another attempt until you edit it.
  - **Warning:** this changes the test Wi-Fi and disconnects its clients while it restarts. Keep Ethernet access throughout the check.
- [ ] **Router Wi-Fi switch**
  - Do: from a computer on Ethernet turn the sidebar **Router Wi-Fi** switch off then on again. A separate session also owns this check so tick it only if you did it yourself.
  - Expect: the switch shows `Changing` then the confirmed state. Devices on the router Wi-Fi drop and come back.
  - **Warning:** turning it off cuts every device on the router Wi-Fi. Turn it back on with the same switch or from a computer on Ethernet. The test Wi-Fi cannot reach the router page.

## Part B: installation, trusted access and recovery

### Install

- [ ] **Back up the database first**
  - Do: before installing make the [private consistent database backup](../deploy/raspberry-pi/README.md#private-database-backup) and keep the configuration and service backups from the same recovery point.
  - Expect: the backup command completed and the private copy is available. Copying only the live database file does not establish a consistent backup. Keep a current backup before any later rollback as well.
- [ ] **Verify and trust the retained certificate authority**
  - Do: after installing the signed official release verify the authority fingerprint on the Pi or through an independently trusted management connection. Sign in and open **Trust this Pi** under **Access and rules**. Compare its authority fingerprint with the installer's before installing the public certificate or Apple profile. Follow the displayed steps for the actual device.
  - Expect: a fresh Pi creates its authority once. An existing valid authority keeps the same fingerprint. The card and installer show the same authority fingerprint. After enrollment the canonical dashboard address opens without a warning on that device. The browser's leaf-certificate fingerprint is a different value.
  - **Warning:** keep this box unticked until the real browser and operating-system trust steps succeed. Never copy the private CA key.
- [ ] **The public sign-in page stays neutral**
  - Do: open a fresh browser context without a saved token. Check the title, visible page and downloaded page source before signing in.
  - Expect: the public page uses neutral names and shows the canonical address. Private device details, certificate details and product branding appear only after the token and device access are accepted.
- [ ] **The Pi name resolves on the router network**
  - Do: on a Mac on the home router's Wi-Fi run `dns-sd -G v4 netlab.local`, then open `https://netlab.local:8080`. For an existing installation also test any saved compatibility name.
  - Expect: the canonical name shows the Pi's Ethernet address and the dashboard opens. A fresh installation adds no product or host-name alias. Required existing certificate aliases may remain on Ethernet. If discovery fails use a certificate-covered IP address.
- [ ] **The same name resolves on the test Wi-Fi with the test address**
  - Do: switch the same Mac to the configured test Wi-Fi and run `dns-sd -G v4 netlab.local`, then open `https://netlab.local:8080`.
  - Expect: the name answers with `192.168.50.1`, the dashboard opens and the remembered sign-in from the router network is still there. Before the first visit at this address you sign in once.
- [ ] **Only the one name is announced on the test Wi-Fi**
  - Do: on the Mac on the test Wi-Fi run `dns-sd -B _ssh._tcp local.` and `dns-sd -B _workstation._tcp local.` and `dns-sd -G v4 <pi-name>.local` for a few seconds each. On the Pi run `sudo tcpdump -ni wlan0 udp port 5353`.
  - Expect: no SSH or workstation service appears and `<pi-name>.local` does not resolve on the test Wi-Fi. The capture shows the question and answer for `netlab.local` only.
- [ ] **The name follows an Ethernet address change**
  - Do: on the Pi run `sudo nmcli connection down <ethernet-connection>; sudo nmcli connection up <ethernet-connection>` (or renew the DHCP lease so the address changes). Then on the Mac run `dns-sd -G v4 netlab.local`.
  - Expect: within a few seconds the name answers with the new Ethernet address on the router network and still with `192.168.50.1` on the test Wi-Fi. `sudo jittersim diagnostics` shows both.
- [ ] **A name conflict is reported**
  - Do: on another computer on the router network run `dns-sd -P Conflict _http._tcp local. 80 netlab.local 192.168.0.250` (or any device that claims `netlab.local`), then restart the name service with `sudo systemctl restart jittersim-mdns`.
  - Expect: `sudo jittersim diagnostics` and the dashboard's diagnostics say another device already uses the name and give the fix. The Pi does not announce `netlab-2.local`. The dashboard still opens by IP address.
- [ ] **Changing the name works**
  - Do: `sudo jittersim dashboard-name set bench.local`, then open `https://bench.local:8080` on both networks.
  - Expect: the new name opens on both networks without a warning on a device that trusts the retained authority. Its fingerprint stays unchanged and you sign in once at the new address. Existing required aliases may still resolve on Ethernet.
- [ ] **A failure of the name service never blocks the dashboard**
  - Do: `sudo systemctl stop avahi-daemon avahi-daemon.socket`, then open the dashboard by its IP address.
  - Expect: the dashboard opens and diagnostics say the name is not announced and how to fix it.
- [ ] **Sign in again**
  - Do: choose **Check token**. From the test Wi-Fi also choose **Allow this device**.
  - Expect: the dashboard unlocks and shows private controls and product branding only after accepted access.

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
  - Do: stage a newer signed release from verified official `main` using `--stage-only`. On **Router and Wi-Fi** press **Check for updates** then **Install staged release**.
  - Expect: the **Release staged** badge shows beside the page name and the check shows the staged and installed labels. After the install the dashboard comes back with the new release label.
  - **Warning:** keep independent management access ready. **Restore previous installation** uses the selected complete recovery point. Later ordinary profile, protection, device-access and usage changes can revert. Current signing trust, update token, certificate authority and Panic state have separate protections. Back up current data before trying restore.
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
  - Do: copy `/etc/jittersim/ca/ca.crt` from the Pi over a connection you trust and compare its fingerprint with the installer's. Run `jittersim connect --url https://<pi-address>:8080 --ca-cert ~/jittersim-ca.crt`. Then run `jittersim status` and `jittersim profiles list` and `jittersim traffic --scan` and `jittersim impact`.
  - Expect: each command asks for the admin token with a hidden prompt and prints its result. Status names the release on the Pi and the affected devices. The impact check waits about ten seconds.
  - **Warning:** never type the token or a password in the command itself. Skip `jittersim wifi show --password` because it prints the Wi-Fi password on the screen.
- [ ] **Deploy script**
  - Do: verify the official Git remote and fetch `main`. Confirm the exact reviewed commit and completed checks. Use `--ref origin/main --dry-run`, then `--ref origin/main --stage-only`. Install that signed release through the verified update route with independent management recovery available.
  - Expect: the printed source commit and checksum match the exact signed archive. After install its semantic version and source commit match the manifest. Any required sudo password is entered by the owner in the terminal.
  - **Warning:** a dry run does not prove completed checks or management recovery. SSH is blocked on the test Wi-Fi. Trusted HTTPS can upload a signed release there but initial manager upgrades and physical recovery need an independent route.
- [ ] **Phone layout on a real phone** (only if your build has the phone layout change)
  - Do: sign in from a real phone on the test Wi-Fi after **Allow this device** and again on the router network. Open a long page and scroll to the bottom.
  - Expect: Panic is always in view and the router switch is one tap away. A page starts on the first screen after you tap its name. Buttons are easy to hit and nothing scrolls sideways.
  - **Warning:** do not turn the router Wi-Fi off from a phone that uses it.

## Remaining physical checks

Local tests do not establish actual radio behavior, native browser trust or
discovery on either physical network. Keep those acceptance boxes unticked
until tested on the installed official release. Verify normal Chrome separately
from automated Chromium. Also record real reboot recovery and the behavior of
ordinary browser traffic through profiles and service rules.
