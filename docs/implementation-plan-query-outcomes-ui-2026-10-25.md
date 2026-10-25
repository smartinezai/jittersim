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
- Display separate authenticated observed-decision aggregates: local-zone policy
  block actions, NOERROR DNS answers and other DNS errors, with their measured
  scope/window. Unbound logs have no transaction ID: do not force these onto
  individual query attempts or call answers Allowed/successful website access.
  Categories may overlap; never sum/subtract them to invent an unknown count.
- Use a plain quiet coverage/status line in the existing discovery summary, and
  compact plain text per domain row using existing classes. No new announcer,
  CSS, width redesign, toggle, toast on polling or outcome-derived action.
- Represent historical/missing outcomes, absent responses and unavailable
  telemetry truthfully. Never backfill legacy query_count or globally_blocked
  into an outcome. Historical/unavailable evidence stays unknown/null, never zero.
  Coverage is always partial and counts lower bounds until sender-loss proof.
  Distinguish saved policy from the generation that produced an
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

1. Authenticated block-action, NOERROR-answer and error aggregates reflect only
   observed producer evidence; membership true with DNS answers and membership
   false with block actions remain distinct. Retries stay query counts, not
   verified block events. Never impose per-attempt pairing or sum overlapping
   aggregates; unmatched/historical outcomes stay unknown.
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

## Confirmed contract, 25 October

Root confirmed production UI may proceed. Per-domain dns_outcomes is null or
{blocked_decisions, answered_noerror, other_dns_errors, last_observed_at}. The
three counts are nonnegative safe integers; last_observed_at is Unix seconds.
Only the first two counts are displayed in v1. They are independent lower-bound
observed events, never an exclusive partition of query_count.

Top-level dns_outcome_collection supplies status unavailable|partial, opaque
nullable epoch, nullable safe-integer policy_revision, nullable Unix-second
window_start/window_end/last_event_at, lower_bound:true,
coverage_complete:false, collector_available:boolean, nullable safe-integer
dropped_events and enum coverage_reason. Unknown coverage reasons receive generic
plain text. A receiver-drop zero never establishes complete/sender-loss coverage.

No client accumulation or historical relabelling: every render replaces rows and
coverage text, including epoch changes. Null/invalid/missing evidence stays
unknown, not zero. The existing discovery lock hook will remove new outcome row
nodes and reset its existing summary; read-revision guards remain unchanged.
