# Homepage bounce and connection-error acceptance

## Human review summary

The follow-up review stays within the approved local reference. Re-pairing
must leave the effect off and discard the old local session. Off and test-tab
closure must invalidate pending work even if Enable is clicked afterwards.
Real worker and browser restarts are checked separately from simulated server
identity and session loss. Live pairing and production delivery remain open.

Verify the existing DNS connection-error setting first. Then build a small
optional Chrome extension prototype that starts explicit test navigations on
enrolled sites. A selected navigation goes either to the requested test page
or to that exact origin's root according to a chance from 0% to 100%.
Default off. This first slice proves the browser and policy boundary without
claiming to classify every ordinary page load safely.

Use a separate short-lived read-only browser credential. Pair it through the
authenticated dashboard and bind it to the requesting device's verified
Wi-Fi identity. The extension never receives the dashboard admin token. Each
test navigation needs a fresh policy response. Unknown identity, protection,
an old policy or an unavailable Pi disables the browser effect.

Keep the current rule priority: protected devices bypass profile effects.
Matching service lists win before general mode. General mode wins before
device assignments. Existing global access restrictions still apply. Choose
policy from the successfully applied plan rather than merely saved settings.

**Risk: high for a future live identity and policy endpoint.** The local
reference prototype is isolated from the Pi and has no live device authority.
Its result cannot establish physical protection or release readiness.
Independent implementation and testing are authorized. Save and review this
plan before starting the prototype. Chrome installation, host permissions and
OS permission changes remain separate owner actions.

No TLS interception, browsing-history collection or random destinations.
Keep Claude's integration, CLI, deployment and token-memory work untouched.
The three-device trial waits until other work is finished and clients exist.

## Current evidence and acceptance order

Source inspected at `6aae9d8` in the homepage-verification checkout:

- `AccessOptions.dns_failure_percent` accepts whole numbers from 0 to 100.
- `render_access_firewall` rejects selected UDP DNS packets and new TCP DNS
  connections arriving from test Wi-Fi. It preserves the dashboard exception.
  This is a persistent access setting rather than a browser navigation chance.
- `tests/kernel/test_access.py` already covers 0%, 100%, UDP, TCP and management
  isolation. Source inspection is evidence of coverage rather than a fresh run.
- `browser_proxy.select_mark` implements the profile priority described above.
- `dashboard_access.identify_peer` derives identity from the actual peer
  address, kernel route, one neighbor mapping and a current Wi-Fi station.
  An extension-supplied hardware address is not an identity proof.
- Existing protected dashboard reads require the admin token. They cannot be
  reused as an extension policy feed.

First run the current DNS model, API, CLI and renderer checks. Exercise the
existing kernel test when an isolated Linux environment is available. Verify
that saved chance differs from applied chance and that explicit disable
removes the error rule. Record current protection behavior as observed. Do
not silently change persistent household rules to match browser-profile rules.

DNS failure is per UDP packet or TCP connection. Retries and cached answers
mean 50% DNS failure does not promise 50% failed page loads. Secure DNS, cached
connections and direct IP access can bypass this effect. Do not promise a
specific Chrome error message. Browser acceptance must use uncached test names
with a known resolver and verify the actual failure and subsequent recovery.

Docker is unavailable while unrelated MAIA containers use the shared VM.
Run no Docker build or test until that work is gone. Leave unavailable kernel
and physical checks explicitly unverified. Local browser and ordinary Python
checks can proceed without Docker.

## Concrete first implementation slice

Build a Manifest V3 extension with an extension-owned test page and a local
off control. The owner enrolls at most eight controlled HTTPS test origins
and explicit safe page paths. The test page offers named navigation actions.
It does not watch normal browsing, typed URLs, forms or other applications.

For each user action, ask for fresh policy using only the target origin.
If eligible, make one chance decision and navigate the current dedicated test
tab either to the enrolled page or to `scheme://host:port/`. Preserve the exact
scheme, host and port. The resulting root load triggers no further decision.
Only new explicit test actions can trigger another decision.

This slice deliberately initiates a known GET navigation. Forms, downloads,
login flows and callbacks never supply eligible actions. Enrolled fixture
paths must serve ordinary HTML with no authentication, attachment response
or server redirect. Reject queries, fragments, URL credentials and root-page
targets. If a fixture stops satisfying these conditions, remove its action.
Use owned public test origins for live use. A local fixture origin is allowed
only by the isolated reference-test build and never by the live build.

Reject private, loopback, link-local and multicast destinations. Reject IP
literal origins, single-label names, `.local`, management names and Pi/router
addresses in the live build. Verify enrolled names resolve only to public
addresses before enrollment and before live policy approval. Failed or mixed
resolution disables the effect. Do not grant general wildcard host access.
This does not promise to police a public site's later redirects. First-slice
fixtures must have no redirects and the extension never rewrites their chain.

Chrome can navigate tabs without broad tab-reading permission. Grant only
`storage` and the exact policy origin needed by the prototype. Keep the
credential in `storage.session` with trusted-extension-only access. Do not
grant `history`, `cookies`, all-site host access or external page messaging.
See [Tabs permissions](https://developer.chrome.com/docs/extensions/reference/api/tabs)
and [session storage](https://developer.chrome.com/docs/extensions/reference/api/storage).

## Paired policy channel for the future live feature

Create a narrow browser-session route rather than expanding ordinary public
dashboard reads. Session creation and revocation use existing authenticated
dashboard access and origin checks. The owner enables a session explicitly.
The dashboard creates a random single-use pairing code valid for two minutes.
It contains no admin token. Redemption must arrive directly from test Wi-Fi.

The server derives the device from the socket peer through the helper and
requires one current station and a valid address mapping. Refuse routed,
management, IPv6, proxy-header and ambiguous callers in the first version.
Bind redemption to that hardware identity. Return a distinct random browser
credential valid for at most one hour. It authorizes only this session's
policy reads and cannot call dashboard reads, writes or enrollment actions.
Store only a hash on the server. Keep sessions in bounded memory so a service
restart revokes them and requires pairing again. No pairing database migration
or stored admin credential is needed for this slice.

For every policy request, verify the credential and the actual peer identity
again. Reconnecting with a changed IP is accepted only if the current helper
mapping proves the same paired hardware address. A changed private hardware
address requires new pairing. Saved names and client-supplied addresses do not
count as proof. This inherits the repository's Wi-Fi identity boundary. It
does not prove human identity or defeat deliberate hardware-address spoofing.

Return only enabled/disabled, chance, an opaque session revision and a short
lease. Do not return device inventories, names, hardware addresses, domains,
profile IDs or the network plan. Requests contain an enrolled origin rather
than a page path or browsing history. No cookie authentication or wildcard
CORS. Restrict browser origins to the installed extension identity in addition
to credential and peer checks. Origin headers alone are not authorization.

Use the applied plan's existing selection behavior. Include browser chance
in bounded applied-plan metadata for each winning rule. A proposed profile
field defaults to 0 and requires the normal automatic schema upgrade and
import/export checks. Publish the metadata only after a successful Apply.
An older helper or missing metadata disables bounce. Do not reconstruct an
applied chance from a newly edited saved profile.

Preserve matching service-list order exactly. A service-list chance of 0
does not fall through to a general chance of 100. General mode otherwise
overrides device rules. Device rules already reflect groups and timed
overrides. If the winning rule, destination permission or current applied
state cannot be proved, return disabled. Protection in either current saved
settings or the applied plan disables bounce. Do not relax global restrictions
when returning a browser policy.

Check plan generation, current protection and verified identity in one bounded
read coordinated with Apply/Stop/Reset/Panic. Busy or partially changed state
returns disabled. Reads must not delay control writes. Missing or invalid
active-plan state is disabled. A helper restart invalidates the policy epoch.

Allow one outstanding request per browser and four per server. Cap sessions
at 32 and pending pairing codes at eight. Cap each response at 8 KiB. Limit
policy reads to two per second per session with a burst of four. Expire codes
and sessions during bounded ordinary requests. Do not add an unbounded queue.

## Freshness and stop behavior

Do not reuse an enabled response for another navigation. Give each response
a one-second maximum lease. The extension measures validity from its request
start with a monotonic clock and gives the entire lookup 750 ms. This is
conservative about transit time and avoids relying on synchronized clocks.
Validate the current session revision before issuing a browser navigation.
Reject late replies after local Off, a replaced action or browser restart.

Reset and Panic disable policy. Stopping general mode removes its policy and
can expose a currently active saved device rule just as existing traffic does.
Local Off always prevents another action. Protection changes and lost identity
stop approvals at the next verified server read. Already issued navigation
cannot be recalled. A change after approval can permit at most that one action
within the one-second lease. No browser API promises an instantaneous stop
across an already committed navigation. On wake or session uncertainty, request
fresh policy before doing anything. Never silently re-enable after pairing loss.

## Reference prototype versus live delivery

The prototype contains a disposable policy fixture that models pairing,
verified/unknown identity, applied rule priority and revisions. Its credentials
are test-only. Loopback fixture identity is explicitly simulated. It must not
be described as a verified physical Wi-Fi client or a working Pi endpoint.
Use a separate temporary Chrome profile and owned local HTML fixtures.
The fixture can exercise HTTP on loopback. Live support remains HTTPS-only.

Changing ordinary browsing is a later slice. Chrome navigation events cannot
cancel a request and their ordering relative to request events is undefined.
Request rules can match methods and page types but do not identify arbitrary
login callbacks or make a per-navigation chance decision by themselves.
See [navigation events](https://developer.chrome.com/docs/extensions/reference/api/webNavigation)
and [request rules](https://developer.chrome.com/docs/extensions/reference/api/declarativeNetRequest).
Do not combine these APIs and claim universal form/download/authentication
exclusions without a separate reviewed proof. Keep broad automatic navigation,
same-page application changes and random destinations deferred.

## Verification and delivery

| Check | Required evidence |
| --- | --- |
| DNS error | Current focused checks plus actual UDP/TCP rejection and recovery when Linux is available. Keep management access and resolver bypass limits explicit. |
| Chance | Real extension navigation at 0% and 100%. Controlled chance values exercise the boundary without a flaky statistical test. |
| Priority | Protected bypass. A matching service list at 0% beats general 100%. General beats device. Unknown or unapplied metadata disables. |
| Identity | Fixture checks for changed IP, changed hardware identity, ambiguous mappings, stale station data and replayed pairing codes. Actual helper/peer checks are separate live-release evidence. |
| Stop and limits | Late reply after Off, revision change, reset, panic, expiry, helper restart and full request/session limits. No queued delayed redirects. |
| Navigation safety | Reject private/management origins, root, query, form, callback, attachment and redirected fixture paths. Repeated root loads cannot create another action. |
| Privacy | No admin token in extension storage/messages. No paths or URLs in Pi logs. No policy response exposes another client. No history permission or synchronized state. |
| Lifecycle | Tab closure, extension disable/update, browser restart and failed navigation clear transient action state. Keep at most one action for each of four test tabs. |

Tests must exercise the actual extension and fixture server rather than copy
the selection logic into a test. Finish local prototype review before touching
the Pi. Mark helper verification, packet checks, physical browser pairing and
certificate compatibility unverified until exercised on the actual release.

Live delivery needs a reviewed helper/API change, schema compatibility checks
and a coordinated release with the integration owner. Build from clean source
in CI or a container once Docker is free. Old clients and profiles retain an
off default. An older server must refuse pairing rather than fall back to the
admin token. Revoke sessions and disable the extension before rollback. The
browser setting and saved profile field must survive export/import without
turning an off experiment on.

Normal verified HTTPS access to the policy endpoint is required. Host
permission does not bypass certificate errors or OS Local Network permission.
An owner-cleared dashboard warning does not prove extension fetch will work.
Verify the browser trusts the endpoint normally before live pairing. Do not
bypass certificate validation, install interception certificates or change OS
settings automatically. If ordinary trust is unavailable, leave live pairing
disabled and plan a separately reviewed certificate-pinned native bridge.
See [extension network requests](https://developer.chrome.com/docs/extensions/develop/concepts/network-requests).

Chrome installation and requested host/OS permissions require the owner's
explicit confirmation. The extension is installed only in the dedicated test
profile. Publish, merge and deploy only within the separately authorized
release scope. Record prototype results separately from production acceptance.

## Approved reference implementation scope

The isolated explicit-action prototype is approved for independent local
implementation and testing. It uses a simulated loopback policy fixture and
a disposable test-browser profile. The live identity endpoint and deployment
remain proposed. Production modules and other active worktrees are excluded.

## Reference verification on 2 October 2026

The actual extension and served fixture passed 18 checks after review. This
includes 0% and 100%, policy priority, simulated protected and unknown identity,
failed or invalid policy, lookup deadlines, Off during an outstanding request
and no repeated decision on home-page reload. The journey uses a disposable
Chromium profile with Playwright 1.62.1 and Chrome for Testing 148.0.7778.96.
Full lint for its Python files and JavaScript syntax checks passed.

The code is isolated under `experiments/homepage-bounce-reference` on
`codex/homepage-bounce-reference`, based on integration snapshot `6aae9d8` and
the separate DNS test commit. No production module changed. Live identity,
HTTPS pairing, profile persistence and the broader verification table above
remain requirements for a later release. These results do not cover that
future implementation or establish physical protection.

## Bounded reference review on 2 October 2026

The authorized follow-up reviews only this isolated reference. The central
reasoning is whether pairing, Off and browser lifecycle changes can invalidate
pending work safely. Routine runtime checks and documentation stay local.

Reproduce Enable during re-pairing with the actual extension before changing
its state handling. Also check replayed pairing codes, session loss, worker
restart, closing a test tab during a pending lookup and late interface replies.
Validate policy shape and freshness with controlled fixture failures. Preserve
one outstanding request and the fixed loopback destination list.

Fix only confirmed material problems. Add focused regression checks that use
the served fixture and installed extension. Keep the existing off default and
ordinary named-page fallback when policy is unavailable. A rejected session
must require explicit pairing and Enable again. Successful pairing stays off.

Use a separate local review branch from `8aa7f80`. Run the focused browser
journey, relevant fixture checks, lint and JavaScript syntax checks. Review the
complete reference diff. No production endpoint, schema, deployment or live
traffic change belongs to this scope. Rollback removes the disposable test
profile and reverts the local review commits.

The Docker VM still has fourteen unrelated containers, so the extended DNS
packet case remains unrun. No container may be stopped or deleted for this
review. Physical three-device acceptance and live identity/HTTPS pairing stay
unverified. The latest fetched integration and Pi snapshots are `aef052e` and
`d38c590`; this review does not verify them.

Confirmed before fixes in the actual browser:

- Enable during a rejected re-pair left the worker enabled with the old
  credential while the page reported pairing unavailable.
- Closing the dedicated test tab during a delayed policy lookup created a
  replacement tab after the owner closed the original.

Clear the old local session before re-pairing and refuse Enable until pairing
finishes. Successful pairing stays off. Rejecting an expired or replaced
session clears local pairing. Make Off and tab closure invalidate pending
work. Prevent an older interface reply from overwriting a newer Off result.
Extend only the disposable harness to model pairing delay and session loss.


The reviewed reference at `eeeb3e4` passed 32 browser checks and eight focused
HTTP checks. Actual worker and browser restarts were exercised. Strict pairing
shape, session loss, elapsed deadlines and the four-handler limit were also
checked. Full Ruff, JavaScript syntax and diff checks passed. Results are in
`docs/verification-results-homepage-reference-review-2026-10-02.json`.
The stated Docker and physical blockers remain unchanged. No production
module, live setting or other owner's worktree changed.
