# Quick button to reset the Pi's Wi-Fi name

Owner request of 23 October 2026: a quick access button to reset the name of
the Pi's Wi-Fi access point.

## What exists already

**Reset Wi-Fi name** is on the Router and Wi-Fi page (`#reset-wifi-name`). It
needs three clicks to reach. It already does everything the quick button needs:

- It asks in the in-page dialog (`confirmFromButton`, which uses
  `confirmWorkflow`) and shows the restart notice (`WIFI_RESTART_NOTICE`).
- It sends `POST /api/access-point/reset-name` with a request id. The server
  answers `202` at once and waits 0.75 s before the helper restarts the Wi-Fi
  (`WifiOperations`, `_start_wifi_change`). So the reply always leaves before
  the Wi-Fi goes down.
- `runWifiChange` in `reconnect.js` then reads the stored result with the
  backoff 1, 2, 3 s and then every 3 s. It gives up after 90 s without an
  answer (and after 240 s in all) and shows "The Pi has not answered yet" with
  a Check again button. It never sends the write again.
- All guards sit behind the one endpoint: the device access check, the cross
  site refusal and the helper's own checks. Reset-name has no Panic or
  protected-device rule of its own.

## Decisions

1. **One implementation.** The quick button is a second button for the same
   function (`resetWifiName` in `dashboard.js`). The page keeps one call of
   `/api/access-point/reset-name`. No server, helper or `networking/*.py`
   change. The hash-pinned files stay as they are.
2. **Where.** In the sidebar, in the Wi-Fi group under **Copy router Wi-Fi to
   Pi**. The dashboard has no top bar. The sidebar is on every page, so a new
   user sees it without opening settings. On a phone it is in the Controls
   sheet like Start, Reset network and the copy buttons. Panic and the router
   switch stay alone in the bottom bar. The Devices page was the other
   candidate. It shows only on one page and needs a new card.
3. **Look.** A normal button (not primary, not red) with the same label as the
   settings button, **Reset Wi-Fi name**. Its CSS is the existing sidebar
   button rule, so no new CSS. It sits below the Copy result line so that text
   stays under Copy. It costs about 46 px of height. Panic, the router switch
   and Copy router Wi-Fi to Pi stay fully in view at 1280 by 800. The Version
   line and the two credential buttons fall below the fold there, where the
   sidebar scrolls.
4. **Dialog text.** The shared restart notice now says "Every device on it,
   possibly including this one, will lose its connection for up to a minute."
   ("possibly" because the dashboard can be open over Ethernet or the router
   Wi-Fi.) All Wi-Fi restart dialogs (name reset, manual change, router copy)
   gain this. The reset dialog also names the default network and says the
   reset stops the Pi from following the router's Wi-Fi name until the router
   Wi-Fi is copied again.
5. **Restart state.** The sidebar button has no status line of its own, so a
   new banner scrolls into view once, and on a phone the Controls sheet closes
   after the person confirms. While the Wi-Fi restarts the banner says which
   network to join: "Join "TestNet" in the Wi-Fi menu. The password did not change." The
   name comes from `/api/brand` (`default_ssid`). A fourth parameter of
   `runWifiChange` carries it. Without it the old text shows.
6. **Poll.** The existing result follower is the poll. It asks the dashboard
   itself, so a reply proves the dashboard is back and also says how the reset
   went. A second health poller would repeat it.
7. **Both buttons wait together.** After the person confirms, both buttons are
   disabled until the reset ends. `wifiResetPending` stays the one guard.

## Tests

- Unit: the restart notice names every device ("possibly" this one). The reset banner names the
  network and keeps the password. The page has one reset call and both buttons
  use it. The sidebar holds the new button.
- Browser (`wifi_restart_reconnect`, at 1280 and at 390 wide): quick button
  from the Devices page scrolled down, Escape cancels without a write, the
  dialog text, the Reconnecting banner in view with the network name while
  polls fail, both buttons disabled, one write with a request id, finished
  banner, buttons enabled.
- Existing: `wifi_recovery`, `phone_layout`, the GUI baseline and the axe check.
- GUI budget: the page grows by about 0.7 KB. That is inside the 2 percent
  tolerance, so `gui-stats.json` stays as it is.

## Not done

No change to the Wi-Fi restart itself, to the helper or to guards. No test on
the real Pi or router.
