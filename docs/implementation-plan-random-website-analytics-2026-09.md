# Implementation plan: randomized website analytics

## Reviewer summary

Add a dashboard report counting the outcomes of the website entry chance at each scheduled device reroll. For each configured domain, count whether the random choice kept it in or removed it from that profile's selected allow list. Show daily totals by device, domain and outcome. These are generated profile choices, not proof of an applied network block or a website visit. Keep the report behind the admin token and retain 30 days of on-device history. The reviewer confirmed that 30 days is enough and chose to exclude analytics from downloaded database backups.

## Decisions, risks and boundaries

- Count one kept or removed outcome for each configured domain when a scheduled reroll runs for an `allow_only` profile. This measures the random choice, even when the selected list matches the previous result. Do not count profile startup, manual edits, profile switches or active progression updates.
- Record generated choices whether or not the helper later applies the plan. Label the data as randomized profile-list outcomes. A global website rule may still affect the final network filter. Do not describe the report as visits, DNS blocks or confirmed network effects.
- Use the stable configured device ID. Store only domains already configured in the profile and whether the random choice kept or removed each one. Do not copy observed DNS query names into these analytics records. Remove a device's analytics rows when that device is deleted.
- Store UTC daily totals in SQLite with one row per device, domain and day plus separate kept and removed counts. Keep 30 days and enforce a hard 50,000-row ceiling. Remove the oldest daily rows first when the ceiling is reached. Show the oldest available date so a shortened history is clear.
- Write all outcomes from one scheduler tick in one bounded database transaction. Keep batches bounded by the configured device and domain limits. Do not commit once per domain.
- Only the serialized scheduler tick may advance per-device random rolls or record analytics. The dashboard's `/api/plan` preview must not advance random state or record events. Refactor the shared plan-building path as needed so concurrent previews cannot consume a due reroll or count it twice.
- Persist captured outcomes before the scheduler returns early because the network-plan payload is unchanged. A reroll outcome still counts when it matches the prior selected list and causes no network apply.
- Remove expired rows at dashboard startup and during daily runtime cleanup. Also prune after event writes. The analytics read route is read-only and protected by the existing admin token.
- Risk: moderate. This adds device-linked domain history and a dashboard route. Keep records in the Pi's local database. Analytics write failures must not prevent network-plan processing. A failed write can cause the report to undercount, which the UI should describe as a history limitation if detectable.
- Schema setup uses idempotent SQLite table creation. No data backfill is needed. Preserve `/var/lib/jittersim/jittersim.db` during updates. Older code can ignore the added table on rollback.
- Exclude the analytics table from the downloaded database snapshot. Keep it in the live database. This makes the 30-day retention period apply to exported backups too.

## Work slices

1. Add a validated analytics record and an additive SQLite table with bounded daily updates, date and device queries, cleanup and the row ceiling. Cover a fresh database and an existing database created by the current version.
2. Keep `/api/plan` a side-effect-free preview. In the locked scheduler tick, capture one kept or removed result for each configured domain at the due reroll and persist it before the unchanged-payload early return. Exclude active progressions and changes caused by profile edits or profile switches. If storage fails, log a bounded error and continue processing the network plan.
3. Add an admin-token-protected read route with a maximum date range and response size. Add a dashboard panel with a device selector, a time window and per-domain kept and removed counts. Include clear empty, partial-history and error states. State that the data shows generated profile choices rather than visits or confirmed blocks.
4. Update user documentation. Verify the schema update and rollback in a clean Linux build. Check retention, access control, accurate per-device counts and behavior with two clients on the Pi before calling the feature deployed.

## Verification and recovery

- Use deterministic seeds to confirm each recorded kept or removed result matches the scheduled random choice. Confirm startup, profile switches, manual edits and progression updates do not add outcomes.
- Confirm `/api/plan` does not advance a random roll or change analytics counts. Confirm concurrent previews and a scheduler tick cannot count one roll twice. Confirm a due reroll is counted when its generated payload equals the currently applied payload.
- Confirm records stay attached to the same device when its IP address changes and are deleted when the device is removed.
- Confirm missing or incorrect admin tokens cannot read analytics. Confirm date range and response limits are enforced.
- Confirm expired rows are removed at startup and during daily cleanup. Confirm the table never exceeds its row ceiling and the dashboard reports when older history is unavailable.
- Confirm a storage failure does not prevent the runtime from processing its network plan.
- Confirm the report does not imply that a device visited a domain or received a network block.
- Confirm a clean install and an upgrade from the current database use the same schema setup. The installer preserves the database. Rollback is to reinstall the prior package, which ignores the added table and leaves its data available for a later upgrade.
- Confirm a downloaded backup still contains configuration and excludes the analytics table while the live database retains current analytics.
