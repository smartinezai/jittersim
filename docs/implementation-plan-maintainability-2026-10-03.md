# JitterSim maintenance and feature sequence

## Human review summary

Reduce the amount of unrelated code that must be understood to change one
feature. Start by separating dashboard source and page assembly from the HTTP
server. Preserve the exact served page and its security policy. Then move
request handling into small feature areas with explicit dependencies while
keeping authentication and operation ownership in one place.

Do this through small reviewed changes. A framework replacement or a single
large rewrite would increase risk while release repairs are still active.
Do not mix extraction with new behavior. Each change must leave JitterSim
usable and be independently reversible.

The first proposed ticket covers dashboard source and assembly only. It does
not move request handling or network operations. Its important risks are
missing installed assets, changed script order, changed page bytes and broken
content security hashes. A clean installed-package check is required.

The proposed feature order after that first maintenance slice is fractional
packet loss, a connection-rule explanation and automated application tests.
Calibration and stronger experiment records follow. Controlled congestion
remains a later decision with greater network-design cost.

Status: Ticket 1 implementation approved by the owner on 3 October 2026
after independent review and human feedback. The same approval permits
publishing and merging this change after passing checks. If GitHub billing
prevents checks, run the configured checks locally. Pi deployment is separate.
Later tickets retain their individual planning and review steps.

## Evidence and scope

Inspected on 3 October 2026:

- Primary checkout `codex/master-continuation` at `3022798`.
- Integration source `integrate/main-into-stack` at `600c476`.
- Combined release candidate `codex/remaining-release-repairs` at
  `d8622725ac36122858a29d662997feb70e7847d2`.

The following measurements were checked against the committed combined
release candidate with `git show`. Local refs do not prove the latest remote
state or the installed Pi release. During assessment its working tree acquired
staged edits and unresolved merge conflicts while active release work
continued. Those edits are excluded from these measurements. Do not start a
maintenance branch from an unsettled working tree or resolve another owner's
conflicts as part of this plan. Recheck the final source before implementation.

| Area | Current evidence | Maintenance consequence |
| --- | --- | --- |
| `api/server.py` | 17,318 lines. Its embedded dashboard occupies lines 291–11,540 and contains 541,515 UTF-8 bytes before extra assets are inserted | Presentation changes and network-control changes share the same large source file |
| Request handling | `response_for` spans 654 lines. `_write_response_for` spans 820 lines | A small route change requires reading many unrelated branches |
| Existing route modules | `api/workflows.py` and `api/access.py` receive the whole server. Device workspace handling calls back into `server.response_for` | Moving code alone can preserve hidden dependencies and repeated reads |
| Dashboard delivery | Existing CSS and JavaScript are inserted in a fixed order. Final inline content is hashed for the security policy. Raw bytes, compressed bytes and ETag are cached | A source move can break installed delivery even when a local page looks correct |
| `config/storage.py` | 3,821 lines. Schema setup spans 385 lines. One repository owns locks and transactions | Splitting storage must preserve one connection owner and transaction scope |
| `updates.py` | 3,270 lines. `apply_update` spans 479 lines | Release and recovery state are a sensitive later extraction |
| Runtime and helper | Runtime has 1,957 lines. Helper has 3,024 lines. State publication and recovery are intertwined with operation ownership | Keep these out of the first maintenance ticket |
| Tests | Many dashboard checks import `DASHBOARD_HTML` from `api.server`. Some inspect nearby resource files | Account for imports and source-based checks without replacing behavior checks with string checks |

No pull request template was found in the inspected `.github` trees.
Provisional risk is medium for presentation extraction because package delivery
and security hashes are affected. Moving authentication, state ownership,
updates or recovery is high risk and needs a separate reviewed ticket.
Recheck the actual implementation base for its current review requirements.

## Learning objective

The central reasoning is choosing boundaries that make one area independent
without duplicating control over shared state. Be able to explain which layer
owns authentication, a transaction, an operation lock and recovery publication.
File moves and routine packaging changes are supporting work.

The owner authorized implementation of the first maintenance slice.
For the first slice, the comprehension check is to explain how moving source
files can still change browser execution or break a wheel installation.

## Design rules

1. Separate changes to structure from changes to behavior. Preserve existing
   URLs, request fields, response shapes, entry points and recovery results.
2. Keep a single HTTP admission boundary for authentication, saved dashboard
   access, request size limits and time limits. Feature handlers do not create
   a second public entry point.
3. Keep operation ownership explicit. Moving a handler must preserve lock
   order, database transactions, control revisions, worker limits and Panic's
   reserved capacity. A shared operation must not acquire a new nested lock
   merely because its code moved. Retain the original control intent before
   queue waits and through nested calls. Do not recapture it after waiting.
4. Depend on the small set of capabilities an area needs. Do not replace the
   whole server argument with a new object that still exposes everything.
   Do not use recursive HTTP route calls as the internal service boundary.
5. Do not introduce one new catch-all module for shared helpers. Shared code
   must have a named responsibility and a clear owner.
6. Keep current limits on requests, records, caches, DNS work and workers.
   Presentation assembly stays bounded and cached. A source move must not
   introduce per-request file loading or extra database and helper reads.
7. Preserve Python 3.9 support and the existing standard-library server.
   Add no new runtime dependency or JavaScript build system for extraction.
8. Avoid arbitrary file-size targets. Success means clearer ownership and
   fewer unrelated dependencies rather than a smaller file containing wrappers.

## Ticket 1: dashboard source and assembly

### Scope

Separate the dashboard's HTML, CSS and JavaScript source from `api/server.py`.
Keep assembly in a presentation-only module within the installed package.
Continue serving one assembled page with the current inline scripts and
styles. Do not add static HTTP routes or change browser loading behavior.

Preserve the current order of the base source, workflow assets, access assets,
phone styles and reconnect code. Preserve whitespace inside hashed script and
style blocks. Keep content security policy generation, compression, ETag
handling and page caching in the existing server delivery path for Ticket 1.
Generate hashes from the final assembled page through that existing path.

Document the small set of presentation exports used by the server and tests.
Keep `server.DASHBOARD_HTML` and `server.DASHBOARD_CONTENT_SECURITY_POLICY` as
the authoritative inputs used by existing delivery. The new presentation area
supplies the initial assembled source rather than a separate private page or
policy that bypasses those inputs. Existing tests replace the server's HTML to
check ETag changes and replace its policy for phone and wide-font checks.

Preserve the actual comparison rules. At the pinned source the page cache
invalidates when the HTML text object's identity changes. Response delivery
recognizes a dashboard body by bytes equality. Do not assume the docstring's
identity wording describes response recognition. Moving delivery and cache
ownership is excluded from Ticket 1 and needs a later plan if worthwhile.

Do not split the large JavaScript into independently initialized features in
this ticket. That requires a separate analysis of shared browser state,
credential generations, polling ownership and event listeners.

### Work sequence

1. Re-read the final agreed release source and record its revision. Confirm
   that active dashboard and release owners have finished overlapping edits.
   Require a clean agreed source with no unresolved conflicts. Capture the
   baseline from that committed source rather than mixing it with local edits.
2. Create the ticket's concise implementation plan from this scope. Complete
   independent review then obtain human feedback before implementation.
3. Capture the existing assembled page, security policy, ETag and compressed
   representation from that exact source. Record relevant behavior checks.
4. Move the presentation source and assembly without changing behavior.
   Update package-data declarations for every required resource.
5. Run the comparison and installed-package checks below. Review the complete
   change including files that appear to be mechanical moves.
6. Create focused local commits. Publish only with explicit authorization.
   Deployment remains a separate release action.

### Completion conditions

- The final assembled page is byte-for-byte equal to the baseline from the
  same source. The security policy and ETag are equal. Deterministic gzip
  output is equal or any unavoidable difference is explained and reviewed.
- All required resources ship in a wheel and work outside the source checkout.
- Existing dashboard imports used by supported callers remain compatible.
- A real browser can sign in, refresh and navigate without script errors or
  content security violations. Phone behavior is unchanged.
- No route, database schema, helper message, networking command or operation
  ownership changes are included.
- `server.py` no longer owns the embedded dashboard source and assembly.

## Later maintenance tickets

Each row is a separate proposed ticket. Create its own plan and review before
implementation. Promote it only when its dependencies and base are agreed.

| Order | Slice | Boundary and exit condition |
| --- | --- | --- |
| 2 | Profile and impairment request handling | Give profile parsing, validation and response construction a clear home. Keep authentication and shared write coordination outside it. This prepares fractional loss without spreading parsing rules further |
| 3 | Device rules and explanation | Separate device, group and service rule descriptions. Reuse the policy engine's decisions. Avoid maintaining a second precedence algorithm for the dashboard |
| 4 | Experiment and measurement handling | Keep scenarios, sessions and reports together. Separate release operations currently mixed into `api/workflows.py` from experiment handling |
| 5 | Browser feature boundaries | Split one feature at a time after mapping shared state. Keep one owner for credentials, refresh generations and polling. Prevent duplicate listeners or extra polling per tab |
| 6 | Storage responsibility groups | Separate code by stored subject while retaining one repository connection and transaction owner. Preserve configuration versions and atomic cross-area changes |
| 7 | Release and runtime coordination | Consider only after current release and recovery work is stable. Preserve durable state, signing trust, control revisions and recovery ownership. Do not combine this with feature development |

For storage extraction, inventory the methods protected by the current
automatic repository lock wrapper. Moving a method to another object can
silently remove that protection. Preserve transaction depth, savepoints,
commit behavior and rollback across the moved methods. One shared connection
alone does not establish correct synchronization.

For runtime extraction, preserve the existing order between the runtime state
lock and repository locking. Recovery publication callbacks run under the
runtime state lock. Keep them short. Do not move helper calls or slow work
into those callbacks.

Avoid extracting an entire route system before the first small feature area
shows what dependencies it needs. Authentication and recovery are security
boundaries rather than convenient places to move code first.

## Preventing renewed growth

- Add a short architecture guide stating what each area owns and the allowed
  direction of imports. Update it with the first completed extraction.
- Add a review checklist item requiring each new feature to identify its
  existing owner and state boundaries. Keep project text in the team's voice.
- After the first extraction, add a small automated check for agreed forbidden
  imports and missing packaged resources. Check architecture rules that guard
  real risk rather than enforcing a general line-count ceiling.
- Track server responsibilities, changed-file overlap and slow test areas as
  observations. Do not claim improvement solely from lines moved.
- Pair each feature with only the maintenance it needs. Avoid accumulating a
  long parallel refactor branch that repeatedly conflicts with feature work.

## Verification and delivery

Planning in this document runs no production code and makes no live changes.
The following checks are implementation requirements rather than claimed
results.

For Ticket 1, run existing dashboard delivery and content security checks plus
the relevant dashboard and browser suites. Add one focused regression check
for installed resource completeness and assembled-page preservation if the
existing checks do not protect them. Do not add tests for every moved wrapper.
Compare compression before and after using the same Python runtime. A
cross-version compression difference is not a reason to accept a difference
caused by the extraction. Preserve page-transfer checks that replace the
server's HTML and browser fixtures that extend the security policy.

Build a fresh wheel and run it from a temporary directory outside the source
tree without the repository on the import path. Inspect the wheel contents.
Verify that package-data includes the new HTML, CSS and JavaScript resources.
`pyproject.toml` currently packages CSS and JavaScript only.

Use the configured Python 3.9, 3.11 and 3.13 checks. Run the actual workflows in
`.github/workflows/tests.yml` and `.github/workflows/wifi-recovery.yml` for the
final source. The browser checks already install the package to detect missing
assets. Run the relevant clean container builds without cached build layers
because packaging configuration changes. Keep real kernel checks in their
configured Linux environment. Do not substitute skipped Mac checks for them.

For later request-handler extraction, exercise the real HTTP boundary. Cover
missing and invalid credentials, unsaved test-Wi-Fi access, protected devices,
stale writes, helper failure and bounded input. Also cover supported methods,
cross-site requests and the existing special treatment of dashboard
registration, address moves and release uploads. Preserve concurrent Apply,
Reset and Panic checks when their paths are touched. A handler-only test
cannot prove that HTTP admission remains enforced.

No migration or backfill is required for source extraction. Existing release
archives include committed `src` and `deploy` content. Verify that new files
are committed and included by `deploy/raspberry-pi/build-release.sh` before
release preparation. Preserve installer permissions and service identities.

Use the existing release process for eventual deployment. Do not replace
manual files on the Pi or alter live Wi-Fi as part of this maintenance ticket.
Physical acceptance must use the final installed release and an independent
management path. Current recovery and release gates remain separate.

## Data and security

Source extraction reads no new user data. It adds no endpoint, traffic
inspection, logging, retention or external service. Preserve token checks,
saved dashboard access, protected-device exclusions and secret redaction.
The project is a private single-appliance tool with no new tenant model.

Existing device labels, domain observations, quota records and configuration
history keep their current limits and deletion behavior. Do not copy runtime
state into presentation modules or log request bodies to aid the move.

## Rollback and failure recovery

Ticket 1 is a source-only change with no data migration. Its local rollback is
the focused source commit. For an installed release use the existing complete
release rollback procedure. Do not promise that release rollback preserves
all newer configuration changes. Existing release work documents that limit.

If resources are absent or assembly fails, fail clearly during startup or
package verification. Do not weaken the content security policy or silently
serve an incomplete dashboard. Keep management and recovery tooling available
through the existing deployment design.

## Independent review checklist

Review the source evidence and challenge the proposed boundaries. In
particular, check that Ticket 1 preserves installed delivery, page cache
identity, content security hashes and compatibility imports. Check that later
ticket descriptions retain HTTP admission, transaction ownership, Panic
priority and bounded work. Identify omitted deployment or rollback cases.

Record review findings and their disposition here before asking for human
feedback. No implementation starts merely because a plan review passes.

## Review record

Independent read-only plan review completed on 3 October 2026 against committed
source `d8622725ac36122858a29d662997feb70e7847d2`. The direction was accepted
with the following corrections. This was an internal plan assessment rather
than an external pull request review.

| Finding | Disposition |
| --- | --- |
| The live release checkout changed during assessment | Evidence is pinned to the committed snapshot. Selecting a clean final base remains a required implementation gate |
| Existing tests replace the server's page and policy globals | Ticket 1 keeps those globals authoritative and retains delivery and cache ownership in the server |
| Response recognition uses bytes equality while cache invalidation uses text identity | The plan now records the actual rules and compares compressed output in the same runtime |
| Storage methods receive automatic lock protection that a move could remove | Later storage work must inventory the protected methods and preserve transaction scope |
| Queue waits and recovery callbacks carry operation ownership | Later control work must retain the original intent and existing lock order without adding slow work inside publication |
| HTTP checks need more than bearer credentials | Later route verification now includes method handling, cross-site requests and special admission paths |

Human feedback: approved on 3 October 2026.
Implementation: Ticket 1 in progress in `codex/dashboard-source-extraction`.

## Ticket 1 implementation base

Published integration source `0a41c2faf2441c8dadcbd029c8a4458cffd89d9f`
is the clean implementation base. It includes the published foundation and
dashboard repairs. The separate release-repair working tree remains under its
existing owner. This extraction targets `integrate/main-into-stack`.

This published source has no page compression or ETag cache. Those behaviors
are present only in later release work. Ticket 1 adds neither and retains
the published HTTP delivery path. Any later integration must preserve the
newer delivery and cache behavior while retaining the extracted source.

The assembled baseline is 573,246 UTF-8 bytes with SHA256
`cfe0a509fd82cf4c96ac9afa174984b134211cb4b3e93d0abc38790ff49709ff`.
Its page bytes and content security policy compare exactly after extraction.
`server.py` shrinks from 15,241 lines to 4,897 lines.

