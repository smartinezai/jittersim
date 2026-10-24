# Operate dashboard redesign — 24 October 2026

Owner lifted the full-redesign hold and requested isolated parallel work. Draft PR only;
no main/release merge, deployment, real Pi/router contact or pinned-network changes.

## Ownership and sequence

- Sole layout writer: task-12. Own dashboard.html, dashboard.css, phone.css,
  refresh.css and dashboard-navigation.js shell changes, plus affected layout tests.
- Parallel read-only UX/test preparation: ux_test_prep (completed). Final independent
  visual/code review follows committed implementation and batched captures.
- Coordinator selects the combined base. Provisional branch is
  codex/dashboard-operate-redesign at e5513b47; its production source equals 4c843cac.
  Shell-only work proceeded provisionally; final composition reuses immutable private
  UI aggregate63c1d84e and reviewed successor066d011b, preserving both parents.
- Preserve and reuse compact buttons, router text badges, version details, profile
  resets/unassign, notifications, collapsible discovery and New observations.
- Build once; batch desktop/mobile/font checks, fix material defects together,
  confirm once. Wrapper browser slots require coordinator scheduling, release first.

## Direction contract

THESIS: A network control surface whose navigation does not compete with actions.
OWN-WORLD: Retain dark navy tokens, system type, blue current/primary actions and
semantic traffic/safety colours. Use flat, compact command groups and real links.
STORY: Find a page, distinguish saved from running settings, act deliberately and
see the result. Secondary router/account tasks remain discoverable under Controls.
FIRST VIEWPORT: Full-width brand/status header, horizontal page links, compact
profile/Start/Reset row, visible page heading and content. Panic and read-only router
status remain in a persistent safety bar. On phones the existing Controls sheet
and explicit token-to-Allow consent flow remain intact.
FORM: Brief-pinned tab navigation and familiar Operate controls, code-led. No new
visual identity, framework, dependency, imagery, traffic claim or decorative motion.
FINISH: unreviewed and undocumented is unfinished; this build ends with the finish
review, the verdict, DESIGN.md, and every shipping raster carrying its provenance

## Verification

Run affected offline units and lint, then scheduled navigation/phone/access/status,
profile and discovery journeys plus GUI baseline/axe. Preserve confirmations,
announcers and IDs. Check 1440/1280 desktop, 390/320 phone and large-font/short-window
states, keyboard focus/return, overflow and read-only router semantics. Source-budget
records and security limits are unchanged unless applicable approval is received.
Record exact heads and distinguish inherited evidence from final-head checks.

## First review and correction batch

Read-only review of7ebe0090 found desktop utility resize focus, visible-heading
scroll anchoring, utility/toast overlap and stale landscape acceptance assumptions.
All are addressed in one correction batch. Compact headings retain their original
screen-reader presentation to preserve content room at320px with large fonts.
The credential panel returns focus to visible Controls when its original opener
becomes hidden after sign-in. The observation collapse fixture now permits only
the existing debounced, non-persisting range-preview POST; every other POST remains
forbidden. The first browser failures and setup failure are preserved in test-results.

The merged successor retains its four exact approved records; private aggregate
CSS records remain unchanged. Only JSbytes now670500 exceeds the previous limit
668854. No new baseline approval is inferred; coordinator decision is pending.

## Final source freeze and review

Production source is frozen at df1816dd5926015a6f01c597dfe69515e21b25d2.
Independent review marked all four initial findings resolved; fresh visual finish
review returned ship with no material fixes. The main coordinator explicitly
approved only the measured JS-byte record670500, then independent metadata review
verified that single leaf, source identity, all seven strict values and2% tolerance.
The previous pending wording above is historical evidence, not a current blocker.
Owner subsequently changed the final reserve to8%; no reset was used.
