# Implementation plan: cite and extend the profile presets (2026-10-23)

Status: implemented on branch `feat/preset-sources-and-backup-pull`. This is
proposal 3 of the [feature proposals](feature-proposals-2026-10-09.md). Presets
are data that fill the profile form, so the engine, the helper and every
Panic-pinned file are not touched.

## What changed

- Each preset in the dashboard script has a `source` sentence. The form lists them
  under **Where the presets come from** and the user guide repeats them in a table.
- Two presets are added: DSL and Slow 3G (Chrome DevTools).
- An 802.11ac-class Wi-Fi preset is not added (see below).

## One source of truth

The presets live in `PROFILE_PRESETS` in `src/jittersim/api/dashboard.js`. Two
copies of its text existed or would exist:

- `tests/kernel/measure_profiles.py` kept hand copies of four presets for the
  measurement report. The container that runs it has no Node.js, so it cannot read
  the dashboard script. It keeps its copies.
- The table in `docs/user-guide.md`.

`tests/unit/test_profile_presets.py` reads the presets with Node.js and fails when
either copy differs, and when a number that comes from a published source changes.
Without Node.js the file is skipped, like the other Node tests.

## Where each source comes from

Read on 23 October 2026. The pages were read through a page reader that summarises,
so the quoted lines are as it returned them.

| Preset | Source and what was checked |
| --- | --- |
| 3G, EDGE, Very bad network, Offline, DSL | The presets of Apple's Network Link Conditioner with the same names. The table of one engineering blog lists them (3G 780 and 330 kbit/s at 100 ms, DSL 2 Mbit/s and 256 kbit/s at 5 ms, Edge 240 and 200 kbit/s at 400 and 440 ms, Very Bad Network 1 Mbit/s at 500 ms with 10% loss, 100% Loss). The speeds, delays and loss JitterSim already used for 3G, EDGE and Very bad network are exactly these, which is the evidence for "based on". The NSHipster page and the Apple forum thread already named in the proposals name the presets but give no numbers, which is why the table was used. |
| Slow 3G (Chrome DevTools) | `front_end/core/sdk/NetworkManager.ts` in the DevTools repository says "~500Kbps down", "~500Kbps up" and "400ms RTT" for the preset now titled 3G. The code applies 0.8 times the speed (400 kbit/s) and five times the latency (2000 ms) so that the browser feels like the aimed numbers. The DebugBear page already named in the proposals lists 400 kbit/s and 2000 ms for it. JitterSim delays every packet, so the preset uses the aimed numbers: 200 ms each way and 500 kbit/s each way. |
| Good 4G, Congested 4G, Lossy Wi-Fi, Train tunnel, Satellite | No source. The commit that added them (`a0bcbb18`) calls them rough approximations and records none. They say so. The Satellite note adds a check anyone can redo: four legs of the 35,786 km geostationary orbit at the speed of light take about 478 ms, so 600 ms is plausible. |

## Decisions

- **Own estimates say so.** "No published source" is the honest note. Inventing a
  citation for an old number would be worse.
- **The source texts do not name the product.** The dashboard script is public before
  sign-in and `tests/unit/test_dashboard_neutral_sign_in.py` forbids the name in it,
  so an estimate is "a rough estimate made for this tool".
- **DSL uses the Network Link Conditioner numbers** because three presets already
  come from that tool. WebPageTest's DSL profile uses other numbers (1.5 Mbit/s with
  a 50 ms round trip, from its maintainer in the WebPageTest forum, read through a
  search summary only), which is the point of the proposal. The note names the tool.
- **The list is a `<details>` under the preset select** and not a note that changes
  with the choice. A note would go stale when the form is reset or another profile
  is edited. The list is built from the same data as the select (three lines of
  script). The advanced settings `<details>` got an id because the script found it
  as the first `<details>` of the form, which would now be this one.
- **GUI budget.** The page grows by a few kilobytes, inside the 2% tolerance, so
  `gui-stats.json` needs no change unless the budget test says otherwise.

## Not added: an 802.11ac-class Wi-Fi preset

The only numbers found are the same third-party table's "Wi-Fi 802.11ac" row
(250 Mbit/s one way, 100 Mbit/s the other, 1 ms). That is not enough:

1. The row has no first-hand source and the table's columns were read two ways (which
   direction is 250 and which is 100). For DSL and 3G the direction follows from the
   technology. Wi-Fi is symmetric, so nothing says which way is right.
2. A cap of 250 Mbit/s is above what the Pi 3 Model B+ is shown to carry. The user
   guide says its Wi-Fi throughput was never measured and its Ethernet tops out at
   300 Mbit/s. The preset would add 2 ms and cap nothing.

A preset for it can be added once someone reads the numbers in Apple's tool or from
the standard and measures what the Pi really carries.

## Open questions for the owner

- Check the Apple numbers once in Network Link Conditioner itself (Additional Tools
  for Xcode, Network Link Conditioner pane) if you have it. The directions (down 780
  and up 330 kbit/s for 3G, delays 400 down and 440 up for Edge) match the layout of
  the usual preset pane but were not confirmed against the tool.
- Do you want Slow 4G and Fast 4G from DevTools too? Their numbers are in the same
  file. They were not in the proposal.
