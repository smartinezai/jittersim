# Runbook: the dashboard does not open

Status: current. Checked against the source on 2026-10-09 (main at `add7c87`).
The steps were read from the code, the units and the installer. They were not
run on a Pi for this page.

Use this page when the browser cannot open the dashboard or when it opens but
stays locked or degraded. The dashboard address is `https://netlab.local:8080`.
The Pi's Ethernet address and `https://192.168.50.1:8080` also work when the
certificate covers them.

## Know where you are first

SSH to the Pi works from Ethernet and from the router's network. It never works
from the test Wi-Fi, because the Pi blocks it on purpose
(`deploy/raspberry-pi/jittersim-control-firewall.nft`). The dashboard port 8080
is open to the test Wi-Fi and to private addresses on the Ethernet side only.

On the Mac, find the route to the Pi's Ethernet address. Use the address from
your router's device list or from step 2 below:

```sh
route -n get <pi-ethernet-address>
```

If the gateway is `192.168.50.1` the Mac is on the test Wi-Fi. SSH to the Pi will
time out from there. A timed-out SSH login with the Mac on the test Wi-Fi was
seen on 2026-10-04 (see
[verification-final-local-release-2026-10-04.md](../verification-final-local-release-2026-10-04.md)).
If the gateway is the router (usually `192.168.0.1`) the Mac is on the
management route.

## 1. Look at the symptom

| What the browser shows | Most likely cause | Next step |
| --- | --- | --- |
| Timeout or `ERR_ADDRESS_UNREACHABLE` | No route to the Pi, the Pi is off, a VPN, or Chrome has no Local Network permission | Steps 2 and 3 |
| Connection refused | The dashboard service is not running | Step 5 |
| Certificate warning | The Mac does not trust the Pi's authority yet, the name is not covered, or the certificate expired | Step 4 |
| Page loads but private data stays locked | The admin token was not accepted or not entered | Check the token (below) |
| `Allow this device to use the dashboard` on the test Wi-Fi | The computer is not registered | Register it (below) |
| Page loads and says the helper is missing or degraded | The network helper is not answering | Step 5 |

## 2. Probe from the Mac

Find the Pi's Ethernet address while the Mac is on the router network:

```sh
dns-sd -G v4 netlab.local
```

Press Ctrl-C after the answer. On the router network the name answers with the
Pi's Ethernet address. On the test Wi-Fi it answers `192.168.50.1`.

Ask the dashboard for its public health answer. This needs no token and sends no
secret. Use the Pi's authority certificate you already verified:

```sh
curl --cacert ~/jittersim-ca.crt https://netlab.local:8080/api/health
```

The answer is JSON. Read these fields:

- `"status": "ok"` and `"helper_reachable": true` mean the dashboard and the
  helper both work. The problem is the browser, the sign-in or the network path.
- `"status": "degraded"` means the dashboard answers but the helper does not.
  Go to step 5.
- No answer, a timeout or a refusal means the request never reached the
  dashboard. Go to step 3.

If you have no verified copy of the certificate, a plain `ping <pi-address>`
only tells you the Pi is on the network. Do not use `curl -k` for anything that
carries the admin token.

## 3. Check the network path

- Make sure the Mac is on the router's Wi-Fi, on Ethernet or on the test Wi-Fi.
  Port 8080 refuses addresses that are not private (10.x, 172.16 to 172.31 or
  192.168.x). A public address or a VPN exit does not work.
- Pause any VPN. A VPN that routes the Pi's address through its tunnel breaks
  every browser.
- If Safari opens the page and Chrome does not, give Chrome the **Local Network**
  permission under **System Settings** then **Privacy and Security**. See
  [Troubleshoot Chrome not opening the dashboard](../../deploy/raspberry-pi/README.md#troubleshoot-chrome-not-opening-the-dashboard).
- If the name does not resolve, use the Pi's Ethernet address or
  `192.168.50.1` on the test Wi-Fi.
- If the Mac only sees the test Wi-Fi, see
  [Reach the Pi when the test Wi-Fi is the only network](#reach-the-pi-when-the-test-wi-fi-is-the-only-network).

## 4. A certificate warning

- Compare the authority fingerprint with the one the installer printed before
  you trust anything. The browser's leaf certificate has a different
  fingerprint.
- Check that the certificate covers the address you typed. The test Wi-Fi
  certificate names only the one dashboard name (`netlab.local` unless you chose
  another), the earlier test Wi-Fi name `pi.home.arpa` and `192.168.50.1`.
- On the Pi, show when the dashboard certificate ends:

  ```sh
  sudo openssl x509 -in /etc/jittersim/dashboard.crt -noout -enddate
  ```

  The installer and dashboard updates renew the certificate from the existing
  authority when fewer than 30 days remain. If it has expired, run an update or
  the installer over a management connection. See
  [Install or update](../../deploy/raspberry-pi/README.md#install-or-update).
- Never accept an unverified replacement authority and never copy the private
  key.

## 5. Look at the services on the Pi

Log in over SSH from the management route (or use a keyboard and screen on the
Pi). Then:

```sh
sudo jittersim status
sudo jittersim diagnostics
systemctl status jittersim-dashboard.service jittersim-helper.service \
  jittersim-control-firewall.service jittersim-ap.service
sudo journalctl -u jittersim-dashboard.service -n 80 --no-pager
sudo ss -ltn 'sport = :8080'
sudo nft list table inet jittersim_control
```

How to read the output:

- `jittersim status` shows the last network action and whether the helper
  answers. `jittersim diagnostics` lists the services and the dashboard name
  announcement.
- `jittersim-dashboard.service` needs `jittersim-helper.service` and
  `jittersim-control-firewall.service`. If either one failed the dashboard does
  not start. Read the log of the unit that failed first
  (`sudo jittersim logs helper --lines 80`).
- `ss` should show a listener on port 8080. The nft table
  `inet jittersim_control` must exist. The test Wi-Fi unit refuses to start
  without it.
- If a unit is stopped or failed, restart the helper and the dashboard together:

  ```sh
  sudo systemctl restart jittersim-helper.service jittersim-dashboard.service
  ```

- A dashboard that keeps failing on its database needs the steps in
  [Restore and reimage](restore-and-reimage.md).
- A dashboard that broke right after an update needs
  [Update rollback and recovery copy](update-rollback-and-recovery-copy.md).

If the dashboard is the only thing in the way and devices on the test Wi-Fi
have no internet or stay slowed, take the effects off from the Pi:

```sh
sudo jittersim normal-access
```

This records the stopped state and asks the helper to remove JitterSim's
effects and the household restrictions. Saved lists stay. Use
`sudo jittersim reset` to keep household restrictions on. Both commands talk to
the helper, so restart the helper first if it is stopped. Neither command
restarts a dead dashboard.

## Reach the Pi when the test Wi-Fi is the only network

You cannot use SSH from the test Wi-Fi. Pick one of these ways in:

1. **Get back onto the management route.** Plug the Mac into the router by
   Ethernet (a USB Ethernet adapter works) or join the router's Wi-Fi if it is
   on. Then `dns-sd -G v4 netlab.local` shows the Pi's address and
   `ssh jittersim@<pi-ethernet-address>` logs in.
   Do not turn the router's Wi-Fi on or off from JitterSim. Use the router's own
   button. See [Router outage and Pi-only rollback](router-outage-pi-only-rollback.md).
2. **Use a keyboard and screen on the Pi.** This is the last resort and needs no
   network at all.
3. **Use the dashboard from the test Wi-Fi.** Open `https://netlab.local:8080`
   or `https://192.168.50.1:8080`, enter the admin token and choose **Check
   token** then **Allow this device**. **Panic** and **Reset network** work
   from there. A computer that is not registered gets the sign-in page only.
   The Mac can register itself without a browser:

   ```sh
   jittersim dashboard-access allow-self --url https://192.168.50.1:8080 \
     --ca-cert ~/jittersim-ca.crt --name 'My Mac'
   ```

   It asks for the admin token with a hidden prompt.
4. **Use Tailscale** if you set it up. It reaches the dashboard only and it is
   down when the Pi or the home internet is down. See
   [Remote control with Tailscale](../../deploy/raspberry-pi/README.md#remote-control-with-tailscale).

Read the admin token only on the Pi:
`sudo grep JITTERSIM_ADMIN_TOKEN /etc/jittersim/control.env`. If you must add a
computer from the Pi, `sudo jittersim dashboard-access add <mac> --name '...'`
works while the dashboard is stopped.

## When it works again

1. Run `sudo jittersim diagnostics` and check that every service reads `active`
   and that the dashboard name is announced.
2. Choose **Start** if you want your profiles back. A reset
   or Panic keeps your saved settings. After Panic also turn household blocking
   back on under **Access and rules**.
3. Write down what failed and the time. `sudo jittersim diagnostics export >
   jittersim-outage-evidence.json` saves a filtered evidence file. See
   [Outage evidence](../../deploy/raspberry-pi/README.md#outage-evidence).
