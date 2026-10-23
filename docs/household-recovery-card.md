# Household recovery card

Status: current (2026-10-23, main `03fe31d8`). The steps were not run on a real
router or Pi. The two minutes are the acceptance checklist's expectation, not timed.

Print this page and keep it next to the router. Fill in the blanks by hand.
Never write a password or the admin token on it.

Person who runs JitterSim: ______________ Router Wi-Fi button is: ______________

The Pi's power plug is: ______________ The Pi's network address: ______________

## The parts

- The **router** gives the whole house internet and the normal Wi-Fi.
- The **Pi** is a small computer next to the router, on power and on a network
  cable. It makes a separate **test Wi-Fi** (usually named `TestNet`).
- The **dashboard** is the Pi's web page: `https://netlab.local:8080`. On the test
  Wi-Fi it is also `https://192.168.50.1:8080`.

## What normal looks like

- The internet works on the router's Wi-Fi.
- The dashboard opens. After **Check token** its sidebar says `Profiles off` or `Not running` when no test runs.
- Test phones on the test Wi-Fi have an address that starts with `192.168.50.`.
- When the Pi gets power again the test Wi-Fi starts by itself. Allow two minutes.

## Only the test phones have no internet or are slow

1. On a computer on the router's Wi-Fi or Ethernet, open the dashboard and choose
   **Check token**. Ask the person above for the token.
2. Press **Panic**. It turns off every test effect and all blocking and keeps the
   saved settings. Tell the person above so they can turn the blocking back on.
3. If the dashboard will not open, ask the person above to log in to the Pi from
   the router's network and run `sudo jittersim normal-access`.

## The whole house has no internet or no Wi-Fi

Do these in order and stop as soon as the internet works.

1. If the router's Wi-Fi light is off, press the router's own Wi-Fi button to turn
   it on. JitterSim cannot do it for you. If you cannot reach the button, use the
   router's settings page from a computer on Ethernet. Enter the router password
   with care: it locks after a few wrong tries. If the light is on, go to step 2.
2. Write down the router lights and the time. Take a photo. Do not restart anything yet.
3. Test one phone on the router's Wi-Fi. Switch its Wi-Fi off and on first. Its
   address must not start with `192.168.50.`, which is the test Wi-Fi.
4. Still nothing? Unplug the Pi's power and its network cable. Test again.
5. Still nothing? Restart the router last. Wait until its lights settle. Test again.
6. Once the internet works, plug the Pi back in and wait two minutes. Ask the person
   above to run `sudo jittersim normal-access` and `sudo jittersim status` on the Pi.
   If the test Wi-Fi shows another name than `TestNet` (the router's name or
   `JitterSim-Recovery`), run `sudo jittersim wifi reset`. It asks you to type yes
   and it disconnects the test phones.

Never copy the router's Wi-Fi name to the Pi and then turn the router's Wi-Fi off.
More: [router outage runbook](runbooks/router-outage-pi-only-rollback.md) and
[dashboard unreachable](runbooks/dashboard-unreachable.md).
