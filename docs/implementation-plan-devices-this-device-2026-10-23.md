# Plan: mark the device you are using in the Devices list

Request (23 October 2026): the Devices list should tell you which device you are browsing with.

## Smallest solution

The page already asks `GET /api/dashboard-access/status` after the token check. The answer
carries `mac_address`: the Wi-Fi address the Pi's helper verified for this request. The helper
takes the socket peer address of the request, reads the kernel route and neighbour entry, and
requires that exactly one current Wi-Fi station has that MAC. The Connected clients list has the
same MAC for every row. So the page keeps that one value and puts a **This device** label on the
row with an equal MAC. No server code, no new route, no new field.

## Decisions

- **Match by MAC, not by IPv4.** One address equals one row, however many addresses the device
  has. The dashboard speaks IPv4 only, so IPv6 never reaches the list. A phone with a private
  Wi-Fi address shows the private MAC it uses on this Wi-Fi, in both places. The page reads the
  value at sign-in, so after the phone rejoins with a new private address a reload makes it
  current. A stale value matches no row.
- **Nothing matches, nothing is shown, no error.** The helper answers `null` on loopback, on the
  management (Ethernet) network, for a caller routed through a gateway and when the neighbour or
  station entry is missing or ambiguous. The page then adds no label.
- **No header is trusted.** The address comes from the TCP peer, never from `X-Forwarded-For` or
  `Forwarded`. A test sends both forged and still gets the verified MAC (or `null`). A device that
  copies another client's MAC is outside this plan: the Pi's access rules already key on the MAC.
- **Privacy.** The page compares the requester's own MAC, which the status answer already gave
  that requester. It adds no field and no list of other clients' addresses, and stores nothing.
- **Confirm text.** Only one question targets a single device: Block on the Access page. It gets
  one sentence when the target is this device: "This is the device you are using: its connection
  will be affected." Apply profile on a connected row has no question, and Reset and Panic cover
  the whole network, so they stay as they are.
- **Where.** The Connected clients list only. The device you browse with always appears there.
  Saved devices may be saved by IP without a MAC, so a label there would be right only
  sometimes. It can follow later through the connected row's saved device.

## Words and look

The label is the text **This device** in a pill next to the name, in the page's existing green
pill colours, so it never relies on colour alone. It wraps under the name on a phone. The list is
not a live region, so a screen reader reads it as part of the row name, not as an announcement.

## Tests

- `tests/integration/test_dashboard_access_http.py`: the status names only the requester's own
  Wi-Fi MAC, ignores forged headers, and gives `null` for loopback and the management network.
- `tests/unit/test_dashboard_this_device.py`: the matching function in node.
- `tests/browser/dashboard_access.py` (desktop and phone): the label, an axe contrast check of it
  and the Block sentence. `tests/browser/device_names.py`: no label and no sentence off Wi-Fi.

The page grew about 1 KB, inside the 2 percent tolerance of `gui-stats.json`, so the baseline
file is unchanged.
