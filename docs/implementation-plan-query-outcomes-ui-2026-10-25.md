# Resolver outcomes in Traffic discovery

## Scope and ownership

UI-only branch codex/query-outcomes-ui starts from origin/main b061d2d8.
The independently reviewed graph head 314a803e stays frozen in its own branch.
No repository AGENTS.md or local relevant SKILL.md is present. The existing
Operate brief and DESIGN.md remain authoritative; no visual-system change.

This writer owns only dashboard.js discovery render/load helpers, the discovery
card's explanatory HTML hunk, and unique outcome UI/unit/browser tests. Root owns
integration and the backend producer/storage/API in task/query-decision-outcomes.
Do not write production UI until root relays its stable reviewed contract.

## Proposed presentation, pending exact contract

- Keep observed query/retry counts as queries. Keep the existing saved global
  block-list badge and Block/Unblock controls separate from recorded outcomes.
- Display authenticated producer-reported resolver outcomes as Blocked, Allowed
  and Unknown with their explicit measured scope/window. Allowed means permitted
  by the observed resolver policy, never successful website access or download.
- Use a plain quiet coverage/status line in the existing discovery summary, and
  compact plain text per domain row using existing classes. No new announcer,
  CSS, width redesign, toggle, toast on polling or outcome-derived action.
- Represent historical/missing outcomes, absent responses and unavailable
  telemetry truthfully. Never backfill legacy query_count or globally_blocked
  into an outcome. Distinguish unknown counts from zero, partial coverage from
  complete observation, and saved policy from the generation that produced an
  observation. Root must define policy generation type and count/health semantics.
- Show coverage health and policy generation from authenticated server data,
  never infer them from current UI settings. State that encrypted/bypassing DNS
  is outside observed coverage; no all-network or website-success claim.
- Reuse current revision guards for loadObservations. New outcome DOM/text must
  disappear on private lock or access loss and never return from a late read.
  Anticipated narrowly adjacent hunk: extend the existing discovery lock hook's
  removal selector for outcome row nodes; summary already resets in that hook.
  Coordinate that exact hunk with root before editing; no broader auth changes.

## Existing coverage prepared now

Unique tests/unit/test_dashboard_query_outcomes.py first pins legacy rendering:
query_count remains a query count, list membership stays explicitly saved-list
membership, and unavailable reads clear current displayed rows without claiming
zero resolver decisions. This is baseline coverage, NOT implemented outcomes.
Reuse the existing traffic_groups DOM harness and discovery_new_names read/scan
ownership tests; do not duplicate their group/action/focus/private-state suite.

## Contract-dependent acceptance matrix

1. Authenticated blocked/allowed/unknown counts reflect only producer evidence;
   membership true with allowed evidence and membership false with blocked
   evidence remain distinct. Retries count as queries, not verified block events.
2. Legacy records show unknown/historical; missing response, unavailable producer
   and partial/stale coverage have explicit text, never implied zeros/success.
3. Current and historical policy generation remain explicit. No relabelling old
   events when list membership/policy changes. Validate integer/version fields
   according to the producer contract; invalid/unrecognised payload fails unknown.
4. Private lock/access loss, delayed success/error and newer reads suppress stale
   outcome state. Protected observations remain excluded by server policy.
5. Preserve scan, new-name markers, device/group collapse, action confirmations,
   focus and keyboard/touch controls. Render strings through textContent only.
6. One focused Chromium module tests desktop and 375px phone, with 320px large
   text if the added line needs it. Test actual contract response fixtures; no
   live Pi, encrypted-DNS or physical resolver claim. Use shared wrapper slot.
7. Focused new/affected DOM tests, auth/discovery ownership, assembly/IDs/budgets,
   lint, syntax and self-review. All seven strict metrics, definitions and
   tolerance remain unchanged; propose measured non-strict excess to root.

## Integration and boundaries

Root chooses the release aggregate and normal publication. No main/release,
package/version, networking, helper, storage/API, pinned/security file or signing
write here. No owner Pi/network action. Do not invent a new observation producer,
extension, candidate feature or endpoint. Keep the 2% allowance floor, no reset.
Rollback is reverting the isolated UI commit; data migrations are backend-owned.
