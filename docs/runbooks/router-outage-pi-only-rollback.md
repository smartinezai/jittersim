# Runbook: router outage and safe rollback from Pi-only Wi-Fi

Status: current. Checked against the source and the incident notes on
2026-10-09 (main at `add7c87`). The steps were not run on a router or a Pi for
this page. The cause of the 4 October outage is still unconfirmed.

Use this page when the household lost Wi-Fi or internet and you suspect the
router, the Pi or the switch from the router's Wi-Fi to the Pi's Wi-Fi.

"Pi-only mode" here means a setup where the router's Wi-Fi is off and devices
use only the Pi's Wi-Fi. JitterSim has no such mode. It never turns the router's
Wi-Fi on or off. Since PR 87 the helper refuses router Wi-Fi changes before it
contacts the router, and the dashboard switch is read only. Only the router's own
button or settings page changes it. That is why this page starts with the router.

Background reading:

- [Outage dossier, 4 to 9 October](../outage-dossier-2026-10-04-2026-10-09.md)
  (three separate events, ranked hypotheses and a read-only capture checklist)
- [Router outage findings (4 October)](../router-incident-findings-2026-10-04.md)
- [Router and Pi Wi-Fi troubleshooting research (9 October)](../research-router-pi-wifi-troubleshooting-2026-10-09.md)
- [Pi-only mode data path audit (9 October)](../pi-only-datapath-audit-2026-10-09.md)
- [Household Wi-Fi trial](../../deploy/raspberry-pi/README.md#household-wi-fi-trial)

## Rules for every step

- Work from a computer on Ethernet to the router. Do not depend on a device that
  is itself affected. A person must be physically near the router.
- Keep the router's Wi-Fi on whenever you try the Pi. It is the fallback. The
  research notes say any trial needs a rollback that does not rely on affected
  clients and a person physically near the router.
- Do not enter the router admin password again and again. The router locks its
  login after a few wrong passwords.
- Do not copy the router's name to the Pi and then turn the router's Wi-Fi off.
  That was the sequence of the 4 October outage. The research notes list a
  safer idea (rename the router's Wi-Fi by hand and give the Pi the old name).
  It is untested.

## If the router's Wi-Fi is off and devices have no Wi-Fi

Do these in this order.

1. **Turn the router's Wi-Fi back on with its own button.** If you cannot reach
   the button, use the router's settings page from a computer on Ethernet.
   JitterSim cannot do this for you.
2. **Look at the router lights and write them down.** The Vodafone guide quoted in
   the incident notes says a solid red PHONE light means a phone line failed to
   register and a red INTERNET light means an internet error. Do not restart
   the router yet.
3. **Test one device on the router's Wi-Fi.** Check that it gets an address in the
   router's range (192.168.0.x) and can browse. The Pi's test network is
   192.168.50.x. A device with a 192.168.50.x address is still on the Pi.
   Switch the device's Wi-Fi off and on if it keeps an address from the wrong
   network. Forums report that a stale lease can cause "connected, no
   internet". That is a report and not confirmed. The Pi audit found two
   Pi-side causes for the same symptom under a shared Wi-Fi name and main now
   fixes them: the Pi's DHCP server refuses a request for an address it never
   gave (`dhcp-authoritative`) and Pi leases last 5 minutes instead of an hour
   (`ipv4.shared-dhcp-lease-time 300`). A Pi gets this after it installs a
   release that has the change and its test Wi-Fi restarts.
4. **Save evidence before you unplug or restart anything.** The router's event
   log is lost on restart. Take a photo of the lights with a clock. Write down
   or screenshot the firmware version, the uptime, the phone status, the DOCSIS
   status page and the full event log with the time the router shows. If you can
   reach the Pi, run `sudo jittersim status` and
   `sudo jittersim diagnostics export > jittersim-outage-evidence.json`. The
   dossier's capture checklist has more read-only commands. The incident notes
   record `AP-DISABLED` and `KickAssocDevices` entries in the router log. The
   dossier thinks it unlikely that JitterSim sent them.
5. **If router Wi-Fi clients still cannot connect, take the Pi out of the path.**
   Unplug the Pi's power and its Ethernet cable. Test again. During the 4 October
   outage clients on the router still failed with the Pi unplugged, so the
   Pi alone does not explain that failure.
6. **Restart the router as the last step.** In the 4 October incident only a
   router restart restored service. Wait until the lights settle. Test one
   device again.
7. **Only now plug the Pi back in.** See the next section.

## Bring the Pi back safely

1. Power the Pi and connect its Ethernet cable. From the Mac on the router
   network, find it with `dns-sd -G v4 netlab.local` and log in with
   `ssh jittersim@<pi-ethernet-address>`.
2. Take every effect off:

   ```sh
   sudo jittersim normal-access
   ```

   This turns off profiles and household restrictions. Saved lists stay. Use
   `sudo jittersim reset` instead to keep household restrictions on.
3. Check the Pi:

   ```sh
   sudo jittersim status
   sudo jittersim diagnostics
   sudo jittersim persistence show
   ```

   `status` and `persistence show` print the router (gateway) state and the
   persistence mode. The helper checks the router every five seconds. Three
   unanswered checks confirm an outage and two replies confirm recovery. With
   persistence off a confirmed outage removes active profiles and keeps them
   stopped. Choose **Start** after the router is reachable.
   With persistence on, active profiles continue and come back after the
   router returns.
4. If the Pi's Wi-Fi still carries the router's copied name, restore the default
   name. This keeps the password and the security type and restarts the test
   Wi-Fi:

   ```sh
   sudo jittersim wifi reset
   ```

   Two networks with one name make devices choose an access point on their own
   and can leave them with an address from the wrong network. The dossier rates
   this a contributor and not the main cause.
5. Remember that the Pi's Wi-Fi password is still the router's password after a
   copy. Devices that know the router password can join the Pi's network. Change
   the Pi's password in **Router and Wi-Fi** (the manual access point form) if
   you do not want that. Devices then need the new password.
6. If you did not save evidence in step 4, save it now before you reboot or
   reset anything else:

   ```sh
   sudo jittersim diagnostics export > jittersim-outage-evidence.json
   ```

   The export reads selected events from the current and the previous boot. It
   omits unknown message text, device observations and personal network
   details. Check it before you share it. See [Outage evidence](../../deploy/raspberry-pi/README.md#outage-evidence).

## What JitterSim does to the router

- **Check router Wi-Fi** logs in to the router with the password you enter and
  reads the status. Checks are manual. Opening the dashboard or remembering the
  password does not start them. The Pi does not save the router password.
- **Copy router Wi-Fi to Pi** reads the router's Wi-Fi name, password and security
  and applies them to the Pi's own access point. It does not change the router.
  It restarts the test Wi-Fi and disconnects its clients.
- JitterSim sends no Wi-Fi change to the router. The helper answers
  `router-control-disabled` to a request to turn the router's Wi-Fi on or off
  and has no setting that turns that back on.

## Before you try Pi-only mode again

The research notes recommend a ladder of small tests before any cutover: a
router-only soak with the Pi off, the Pi on Ethernet with its radio off, the
Pi on with a separate name and no clients, then one or two spare test devices.
Every rung keeps the router's Wi-Fi on and changes no router setting. Read the
whole ladder in the
[research notes](../research-router-pi-wifi-troubleshooting-2026-10-09.md#safe-experiment-ladder).

The dossier's leading suspect is not the Wi-Fi name. It is the shaped traffic of
a profile with packet corruption, duplication, reordering and loss that the Pi
forwards to the router. Two Pi-only runs without that profile did not break the
router. The dossier calls this a correlation from three events and not proof.
Run any Pi-only trial with all impairments off first, and add delay and jitter
only after that. Its
[capture checklist](../outage-dossier-2026-10-04-2026-10-09.md#capture-checklist-for-the-next-attempt-read-only)
lists what to record.

Also keep in mind what the README says: do not switch the whole house to the Pi
yet. The Pi's coverage, throughput and client capacity have not been measured in
your house, test Wi-Fi clients cannot reach printers or other devices behind the
router, and a Pi outage would disconnect every device that uses it.
