# Device workflows and repeatable tests

The approved product review covers everyday controls and repeatable app tests equally. Preserve unfinished edits, show live device state consistently and add a device workspace that works on phones. Add named test sessions with immutable settings snapshots and a combined report. Make connection failures clear, explain upcoming rules, allow settings restoration and complete router and release controls.

The main risks are losing unsaved work, presenting predicted conditions as measured facts, resetting usage during restoration and leaving the Pi unreachable during an update. Keep network isolation unchanged. Keep all saved histories and uploaded files bounded. Use existing rule decisions and network actions rather than creating competing behavior in the browser.

The product review and its proposed order were reviewed and approved on 26 September with “ok do all of that”. Implementation is authorized for the complete scope. New production deployment still needs working Pi access. There are no product questions blocking local work. The private release delivery mechanism will use verified local release bundles so the Pi needs no stored GitHub credential.

## Work order

1. Preserve dirty forms and menu choices across refreshes. Explain Save and Apply. Add nearby validation, profile duplication and consistent clipboard fallback.
2. Add phone device cards and a device workspace with active source, next change, usage and existing timer/scenario actions. Extend confirmed state into Overview.
3. Add a bounded rule timeline and schedule overlap feedback using the existing scheduling engine. Label unpredictable changes explicitly.
4. Add persistent connection warnings and plain health information with last refresh time. Preserve stale results rather than replacing them with empty lists.
5. Store up to 20 settings versions. Preview changes and restore with a current-version check. Retain usage and activity while stopping active temporary runs explicitly.
6. Store bounded named test sessions with settings snapshots, measurements, events and notes. Export reports and settings for repeat runs.
7. Add authenticated router state reads and one state-aware Wi-Fi switch. Keep credentials short-lived and verify changes.
8. Add installed version and release checking. Stage verified release bundles for a narrow privileged update service with backup, progress, restart health checks and rollback. Keep installation separate from ordinary dashboard writes.
9. Add meaningful regression and browser journey tests. Verify Python compatibility, Linux behavior and clean build inputs. Update user and deployment documentation.

## Storage and operational limits

- Settings history: 20 snapshots with a maximum size per snapshot.
- Test sessions: bounded count, name/notes length, measurements, samples and captured events.
- Timeline: fixed horizon and transition limit. Reuse results until relevant settings change.
- Update bundles: bounded compressed and extracted size. Reject unsafe paths, links and unverified contents. Only root-owned approved release material may be executed by the update service.
- New routes use existing sign-in and write-origin protection. Secrets stay out of exports, logs and history.

## Verification

- Browser journeys: dirty access rules survive Apply and unrelated saves, phone controls work without horizontal scrolling, active overrides agree between views and failed reads retain marked stale data.
- Rules: precedence, overlapping and overnight schedules, clock changes and unpredictable triggers.
- Storage: history retention, concurrent restore rejection, rollback after invalid import and preservation of usage and activity.
- Sessions: edited profiles do not rewrite completed results, malformed or excessive measurements are rejected and exported reports remain bounded.
- Router and updates: authentication failures, unknown state, verified result, invalid bundle, interrupted staging, unsuccessful restart and successful rollback.
- Run appropriate local tests before each focused commit. Review the combined change before the final authorized push. Pi deployment and radio/network acceptance remain unverified until hardware access is available.
