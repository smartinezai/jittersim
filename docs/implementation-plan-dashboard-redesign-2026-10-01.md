# Dashboard redesign: sidebar navigation (2026-10-01)

## Problem

The dashboard is one page of about 25 stacked cards. Finding a feature means scrolling, and two panels follow the reader down the page: the floating `credential-dock` (fixed at mid-height) and the sticky "Network controls" bar.

## Design

Laptop first. One left sidebar, one visible section at a time, collapsible cards inside sections. Phone layout is a later PR.

### Shell

- Sidebar: product name and mode badge, section links with live chips, then a footer that is always visible: Apply, Reset, Panic, router Wi-Fi switch, and status rows for the admin token and router password that open the existing credentials panel.
- `credential-dock` and the sticky `action-bar` are removed. Their buttons and IDs move into the sidebar footer.
- Narrow screens (under 48rem): the sidebar becomes a collapsible top menu. Nothing more is promised there.

### Sections

| Section | Hash | Contents |
|---|---|---|
| Devices (default) | `#devices` | General profile strip, connected clients, unaffected devices, add offline device |
| Profiles | `#profiles` | Save profile, assign a device, group devices |
| Schedules and limits | `#schedules` | Schedule, timer, progression, device/group/service data and time limits |
| Router and Wi-Fi | `#router` | Upstream router connection, Wi-Fi import, credentials, Wi-Fi reset, manual fallback |
| Traffic | `#traffic` | Live traffic, traffic discovery, random website analytics |
| Access and rules | `#access` | Dashboard access, network access rules |
| Diagnostics | `#diagnostics` | Profiles/devices/plan/runtime/access output, diagnostics, backup and history |

Unknown or empty hash opens Devices. The back button moves between sections. The Devices chip shows the connected count.

### Behavior

- All element IDs stay, so existing JavaScript and tests keep working.
- Sections that are not shown are hidden with `hidden`, and their timers keep running (the 5-second device refresh does not stop).
- Cards inside sections use `<details>`. Open or closed state is remembered per card in `localStorage`, wrapped in try/catch. The first card of each section starts open.
- Sidebar links are real links with `aria-current="page"` on the active one. Keyboard focus and a visible focus ring work throughout.
- The page does not scroll as a whole on a laptop-height window: the sidebar is full height and the content column scrolls.

### Look

Keep the dark navy identity. One system font stack, a 1.125 type scale, accent colour for primary actions and the current section only, lighter shadows, tighter card padding. Danger styling stays on Panic and Reset.

## Out of scope

- Phone layout (follow-up).
- Any change to API routes, helper behaviour or stored data.
- Rewriting the dashboard JavaScript.

## Testing

- Unit tests (written first): the page has the sidebar and the seven sections; no element has `credential-dock` or `action-bar` fixed/sticky positioning; every previously present ID is still present exactly once; hash routing shows only the matching section.
- Existing floating-credential and Panic tests are updated for the new location.
- Browser-journey tests run unchanged apart from navigation steps.
- Screenshots at 1440x900 and 1280x720, plus one narrow width, to confirm nothing is clipped.

## Pi checks (user)

Panic, router Wi-Fi switch and router password Remember from the new sidebar; Devices page applies a profile to a connected device.
