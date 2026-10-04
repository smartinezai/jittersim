# Reset network tooltip verification

This report describes the original tooltip source63cabcdf642f59b43fbb297e150d20903c1a17b8.
The coordinated candidate ports its CSS and JavaScript into separate dashboard
assets and retains the current phone controls. Its combined checks are separate
from the original checks below. The standalone two-replacement Pi patch is
excluded from delivery. The official coordinated release remains required.

The explanation now opens beside its help icon. It stays within the window and
supports hover, keyboard focus and Escape. The shared explanation sits outside
the scrolling sidebar. Start uses the same behavior. Profile operations and
saved settings are unchanged.

## Verified source

The focused branch starts from published Pi source `7d98aa1`. The installed Pi
contains newer features. Its dashboard module was independently read over
management SSH and has this SHA-256 hash:

`61bdc62f60e496e6648b9ebbf0aa14185070c00ba4001f137e1dc8793f6d3500`

Deployment uses two exact text replacements in that installed source. It does
not install the older branch over the newer Pi release. Both installed module
copies must match the expected source before installation. Keep their original
files in a private recovery directory and restore them if the dashboard fails
to restart. Restart only the dashboard service.

## Checks

- 226 focused unit checks pass for help copy, navigation, Start summaries and
  Panic behavior.
- The repository's configured Ruff checks pass. The changed navigation browser
  file also passes its full Ruff checks. Existing long lines in other test
  files are outside this change.
- Dashboard navigation acceptance passes at normal and enlarged font sizes.
  Tooltip checks cover hover, moving onto the text, keyboard focus, Escape,
  blur, resize, scrolling and a 900 by 360 window.
- The Start and Reset browser journey passes at desktop and phone widths. It
  includes saving and applying profile changes against a disposable helper.
- The same tooltip checks pass against a local copy of the exact installed Pi
  page at 1440 by 900, 1280 by 560, 390 by 844 and 900 by 360. The original page
  scripts were omitted for this layout check and the controls were opened in
  the local copy. This check does not claim live profile application.
- Desktop and phone screenshots were inspected. The Pi's existing phone
  controls panel needs the tooltip above its layer. That is included.
- The Impeccable detector reported no findings. The complete diff was reviewed
  for clipping, event cleanup, bounded work and text insertion safety.

## Delivery state

The user authorized urgent implementation, testing, push and deployment. The
management connection is reachable and the dashboard and helper are active.
A narrow patch was staged in a private temporary directory and its read-only
preflight passed. No installed file or service was changed. The release
coordinator then held publication and deployment during the critical Vodafone
outage investigation. Use the coordinated official release path rather than
install the staged patch independently. Live installation and served-page
verification remain pending. The SSH account also requires sudo authentication
for installation.

## Composed source checks

The current source ports the tooltip into dashboard.css and dashboard.js. It
removes the obsolete phone pseudo-element placement while preserving the
phone Controls sheet. Escape retains the sheet's existing dismissal behavior.
The browser sequence reopens that sheet before continuing its focus checks.

Focused navigation and Start-summary unit checks passed at the composed source.
Dashboard navigation passed with draft recovery and keyboard diagnostics. Start
and Reset acceptance passed at desktop and phone widths. The final frozen Linux
checks remain required. The earlier unsuccessful port attempts are retained in
the local verification logs.
