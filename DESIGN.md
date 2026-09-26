# JitterSim design

## Required direction

Keep the current dark theme. The page background and major surfaces must
remain dark even when the device or browser prefers a light theme. Apply
this to new screens, menus, dialogs, tooltips and loading or error states.

Refine the existing interface with clearer grouping, readable labels and
consistent controls. Preserve its navy surfaces, blue actions and green
traffic indicators. Everyday household controls and repeatable network
experiments are equally important. Both need to work on a phone.

## Existing visual baseline

These values were read from the current dashboard styles on 26 September
2026. They describe the baseline rather than a new implementation.

| Role | Current value |
| --- | --- |
| Page background | `#0b1220` |
| Background highlight | `#15345a` fading into the page background |
| Card surface | `rgba(18, 31, 49, 0.9)` |
| Field surface | `#0b1727` |
| Main text | `#e7eef8` |
| Supporting text | `#a8b8ce` |
| Blue accent and focus outline | `#70b7ff` |
| Main action | `#2479bd` |
| Green traffic accent | `#70d7a9` |
| Warning text | `#f5ca78` |
| Error text | `#ffb0b0` |

Use the existing sans-serif font stack and system fallbacks. Keep monospace
for addresses, raw values and technical output where it improves scanning.
Additional web fonts are unnecessary for this pass.

## Interaction requirements

- Make applied device state and the next useful action prominent.
- Distinguish saved settings from settings already applied to the network.
- Explain blocked access with words as well as color.
- Keep familiar tabs and preserve unfinished edits during refreshes.
- Group related controls with spacing and headings before adding more panels.
- Keep advanced controls available without crowding the basic task.
- Give network fields consistent help that works by touch and keyboard.
- Keep dialogs and help within a 320-pixel viewport. Check zoom and long names.
- Keep keyboard focus visible and honor reduced-motion preferences.
- Verify text and control contrast against the actual dark surface.

## Review method

Use [Impeccable's existing-design guidance](https://impeccable.style/docs/improve-design/)
to review clarity, readability and interaction. Its examples are references
for the review process. The dark JitterSim palette above remains the visual
direction. Review real device and profile workflows before and after changes.
