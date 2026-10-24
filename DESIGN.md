---
name: JitterSim
description: Dark network controls with clear applied state and deliberate actions.
colors:
  bg-page: "#090d14"
  bg-sidebar: "#0c121b"
  bg-card: "#0f1621"
  bg-raised: "#121b29"
  bg-inset: "#0d1520"
  bg-field: "#0a1019"
  bg-control: "#172131"
  bg-control-hover: "#1f2b3f"
  bg-primary: "#205fa8"
  bg-primary-hover: "#2769b6"
  bg-selected: "#1a2a44"
  bg-chip: "#141d2b"
  accent: "#5aa9ff"
  accent-text: "#86bfff"
  tx: "#cbd5e3"
  tx-bright: "#e9eef6"
  tx-muted: "#93a3b8"
  tx-muted-2: "#a3b1c5"
  tx-control: "#e6edf6"
  tx-on-primary: "#ffffff"
  tx-chip: "#c9d8ee"
  bd-card: "#1d2838"
  bd-control: "#2a3649"
  bd-control-hover: "#4a5d7a"
  bd-field: "#2f3b50"
  bd-primary: "#3b82c4"
  bd-selected: "#4a78b0"
  bd-strong: "#3a4a63"
  ok: "#4cc790"
  ok-text: "#6fe0b0"
  warn-text: "#f2c46d"
  bad-bg-2: "#3d2029"
  bad-bg-hover: "#552c37"
  bad-border-2: "#6e3a44"
  bad-border-hover: "#a35a67"
  bad-text-3: "#ffc4ca"
typography:
  headline:
    fontFamily: 'ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", sans-serif'
    fontSize: "1.5rem"
    lineHeight: 1.3
    letterSpacing: "-0.02em"
  title:
    fontFamily: 'ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", sans-serif'
    fontSize: "1.3125rem"
    fontWeight: 650
    lineHeight: 1.25
    letterSpacing: "-0.015em"
  body:
    fontFamily: 'ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", sans-serif'
    lineHeight: 1.5
  label:
    fontSize: "0.88rem"
    fontWeight: 520
  mono:
    fontFamily: 'ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, monospace'
rounded:
  sm: "0.45rem"
  default: "0.6rem"
  lg: "0.9rem"
  pill: "999px"
spacing:
  1: "0.25rem"
  2: "0.5rem"
  3: "0.75rem"
  4: "1rem"
  5: "1.5rem"
  6: "2rem"
components:
  button-primary:
    backgroundColor: "{colors.bg-primary}"
    textColor: "{colors.tx-on-primary}"
    rounded: "{rounded.sm}"
    padding: "0.62rem 0.9rem"
  button-primary-hover:
    backgroundColor: "{colors.bg-primary-hover}"
  button-secondary:
    backgroundColor: "{colors.bg-control}"
    textColor: "{colors.tx-control}"
    rounded: "{rounded.sm}"
    padding: "0.62rem 0.9rem"
  button-danger:
    backgroundColor: "{colors.bad-bg-2}"
    textColor: "{colors.bad-text-3}"
    rounded: "{rounded.sm}"
    padding: "0.62rem 0.9rem"
  input:
    backgroundColor: "{colors.bg-field}"
    rounded: "{rounded.default}"
  navigation-current:
    backgroundColor: "{colors.bg-selected}"
    textColor: "{colors.tx-bright}"
    rounded: "{rounded.sm}"
  chip:
    backgroundColor: "{colors.bg-chip}"
    textColor: "{colors.tx-chip}"
    rounded: "{rounded.pill}"
  card:
    backgroundColor: "{colors.bg-card}"
    rounded: "{rounded.lg}"
    padding: "1.35rem 1.4rem"
---

# Design System: JitterSim

## Overview

Keep the established dark navy interface even when the device prefers a light theme.
Blue identifies primary actions and the current page; green identifies observed traffic
and successful state. Household controls and repeatable network experiments share the
same compact, readable system and must both work on a phone.

**Key Characteristics:**
- Dark surfaces, restrained tonal layering and compact command groups.
- System sans-serif text, with monospace for technical identifiers and output.
- Explicit state labels, familiar controls and visible keyboard focus.

## Colors

### Primary
Use the blue action and accent tokens for primary commands, selected navigation,
links and keyboard focus. Selected navigation uses its own dark blue surface.

### Neutral
Use the page, sidebar, card, raised and inset tokens to distinguish layers. Main,
bright and muted text tokens express hierarchy; hairline borders separate controls
and containers. The frontmatter records the current source values, superseding the
September baseline table.

### Named Rules
**The Dark Surface Rule.** New screens, menus, dialogs, tooltips, loading states and
errors retain dark surfaces regardless of the device color preference.

Green, amber and red tokens carry observed/successful, pending/warning and error or
destructive meanings. Pair those colors with words; color alone does not explain state.

## Typography

Use the existing system sans-serif stack; additional web fonts are unnecessary.
Technical addresses, raw values and output use the monospace stack where it helps
scanning. There is no separate display type role.

Page headings use the headline role and card headings use the title role. Supporting
notes commonly use (0.82rem), while fields and buttons commonly use (0.88rem).
Do not treat every declared size token as an adopted type role. Preserve tabular
numerals in numeric fields, traffic metrics and analytics tables.

## Layout

Group related controls with spacing and headings before adding panels. The common
spacing rhythm is in the frontmatter; content grids use equal flexible columns with
(1rem) gaps and collapse on narrow screens. Main content is centered with an (84rem)
maximum width. Allow long names and technical values to wrap or scroll locally.

The Operate shell uses a full-width brand/status header and real fragment links.
Desktop links wrap; compact links scroll within their own row. Above (48rem) wide
and (36rem) tall, profile and Start/Reset controls sit below navigation. At or below
either threshold, the compact Controls sheet holds those commands. Compact page
headings remain available to assistive technology while the current link names the
visible page. Keep content and focused controls clear of the pinned safety bar.

## Elevation & Depth

Dark tonal layers, hairline borders and soft shadows establish depth. Cards retain
the source's subtle top sheen and card shadow. Credentials, toast messages, dialogs
and desktop utilities use the stronger pop shadow; compact Controls opens as a sheet.
The exact shadows, focus halo and short state transitions live in the sidecar.
Respect reduced-motion preferences.

## Shapes

Buttons use the small radius; cards and overlays use the large radius. Inset rows
use the default radius. Content fields retain the default radius because their
more specific content rule overrides the generic small field radius. Status chips
use pill shapes. Borders remain thin and tonal.

## Components

Buttons use blue for primary actions, neutral navy for secondary actions and the
existing danger variant for destructive actions. They retain a subtle top highlight,
short hover transition and visible focus outline/halo. Fine-pointer desktop content
buttons can use (2.25rem) minimum height; touch controls and specialized controls
retain their existing sizes, commonly (2.75rem). Preserve disabled and busy states.

Fields use a deep inset surface, tonal border and blue focus treatment. Keep network
field help available by touch and keyboard. Cards contain task groups; nested rows
use the inset surface rather than another full card.

Navigation uses real links with `aria-current="page"`, a selected surface and visible
focus. Operate keeps Panic and read-only router Wi-Fi text in a persistent safety bar.
Secondary router/account utilities open under Controls: a desktop disclosure above
the bar, or a scrollable compact sheet. Preserve focus return when an opener hides.

## Do's and Don'ts

### Do:
- **Do** make applied device state and the next useful action prominent.
- **Do** distinguish saved settings from settings already applied to the network.
- **Do** explain blocked access with words as well as color.
- **Do** keep familiar navigation and preserve unfinished edits during refreshes.
- **Do** keep advanced controls available without crowding the basic task.
- **Do** keep dialogs and help within a 320-pixel viewport; check zoom and long names.
- **Do** keep keyboard focus visible and honor reduced-motion preferences.
- **Do** verify text and control contrast against the actual dark surface.

### Don't:
- **Don't** substitute a light theme for the established dark surfaces.
- **Don't** imply that saved settings are already applied or that router status text is a switch.
