# Erratic connections and application interruption plan

## Human review summary

Make affected connections alternate between usable and frustrating conditions
across browsing, downloads, streaming and calls. Start by correcting four
existing weaknesses: repeated outage choices, fixed service-profile behaviour,
unintended interruptions during plan changes and unclear session randomness.
Then add clustered loss, occasional long delays, changing condition stages and
short pauses followed by bursts of traffic.

Keep the existing device and service selection rules. Add connection-specific
faults later so one connection can fail while another works. Plan presets for
ChatGPT, Claude, Microsoft Teams, Google services, Netflix, Facebook and LinkedIn.
Reuse existing catalogue entries where available and verify new service groups.
Their names describe intended experiences that must be verified on real clients.
JitterSim cannot read an encrypted answer or determine that an arbitrary
encrypted download is 90% complete.

Add usage-driven worsening for a selected service on one device. Later let the
owner select a candidate list from which JitterSim chooses that device's
most-used service. Measure matching bytes or estimated active time rather than
calling DNS queries page visits. Keep escalation bounded with a reset and a
defined recovery window. Google account traffic is a separate optional target
because it is a shared sign-in dependency.

For nearly finished downloads, provide two clearly labelled designs. An
approximate mode interrupts a selected connection after a configured amount of
data or time. An exact mode is limited to a controlled transfer with verified
progress and a known total size. Do not introduce HTTPS interception or read
application contents to obtain that information.

Risk is high for changes to traffic updates, recovery and connection handling.
The main risks are affecting protected clients, discarding queued traffic,
closing unrelated browser activity and leaving a fault active after a crash.
New effects need explicit resource limits, capability checks and Linux tests
before Pi acceptance. Implementation should be split into the deliveries below.

The feature direction was agreed on 3 October 2026. The same day the owner
approved adding these ideas to the plan and explicitly requested implementation,
testing and completion of the pull request. This authorizes direct implementation
of the reviewed first-stage work. Later experiments remain planned features
until their measurement and recovery designs are verified.

The owner also approved merging if CI is blocked specifically by billing and
every required CI check passes locally. Record the billing failure and equivalent
local evidence before using that exception. A skipped check, unavailable test
environment or unrelated CI failure does not meet it. Pi deployment requires
separate authorization.

Open decisions for review are the supported ways to change a live queue, the
final resource limits on the Pi and the progress source for exact download
interruption. Recommended starting choices appear below. None requires changing
the existing HTTPS privacy boundary.

## Learning objective

Reason about independent random events, which profile wins and how timed faults
affect a real connection. Distinguish what the network can measure from what an
application knows. Passing tests establishes correctness. Explaining why an
encrypted connection cannot reveal one file's progress establishes understanding.
Dashboard wiring and documentation are supporting work.

## Evidence from the current working tree

- [Profile settings](../src/jittersim/config/models.py) contain independent
  upload and download impairments plus reroll and outage settings.
- [Random selection](../src/jittersim/engine/randomizer.py) derives choices from
  a seed, a target identity and a roll number. An omitted seed currently becomes
  zero. This makes choices repeatable rather than fresh for each session.
- [The scheduler](../src/jittersim/engine/runtime.py) advances the profile roll
  number on a profile reroll. Outage checks use that same number with an offset.
  A local check with profile rerolls disabled reproduced four identical outage
  outcomes and intervals for each of four seeds. Outage choices need their own
  advancing event identity.
- [Service-list construction](../src/jittersim/engine/service_lists.py) copies
  saved packet settings. Browser assignments and quota fallback rules also copy
  packet settings in the runtime builder. These paths need explicit coverage
  when making service behaviour change over time.
- [Traffic commands](../src/jittersim/networking/renderer.py) use independent
  random loss and do not expose burst-loss models, delay distributions or
  release windows. An apply deletes child queues before installing replacements.
- [The browser proxy](../src/jittersim/networking/browser_proxy.py) forwards
  encrypted HTTPS CONNECT traffic. A changed active-plan generation ends
  affected tunnels. It allows at most 32 concurrent tunnels and reads bounded
  chunks. It does not identify HTTP responses or individual downloads.
- [Profile selection](../src/jittersim/networking/browser_proxy.py) gives a
  matching service list priority over the ordinary profile, including its
  outage. General mode takes priority over individual assignments while service
  lists still apply. New work must preserve and test this existing priority.
- [The service catalogue](../src/jittersim/config/service_catalogue.py) already
  includes ChatGPT, Claude, Microsoft Teams, Netflix and LinkedIn. Google Meet
  and YouTube also have entries. Google account and broader Google groups plus
  Facebook need a reviewed catalogue addition. Its hostname lists are starting
  points rather than proof of complete coverage.
- [Service quotas](../src/jittersim/config/models.py) already represent matching
  bytes and estimated active time. The runtime switches to one fallback profile
  after a threshold. This provides a starting point for several worsening
  stages but does not rank a device's most-used services. Current service quotas
  do not count browser proxy tunnels as documented in [the README](../README.md).
- [DNS observations](../src/jittersim/config/models.py) contain query counts.
  They do not establish page views, foreground use or time spent watching.
- [Service overlap checks](../src/jittersim/engine/compiler.py) reject competing
  parent and child hostname rules. Current browser matching includes subdomains.
  A broad google.com target therefore cannot honestly mean Search alone.
- [Plan decoding](../src/jittersim/engine/codec.py) validates the structured
  helper request. [Helper capability checks](../src/jittersim/networking/helper.py)
  already reject helpers that cannot support some newer plan features.
- [Storage](../src/jittersim/config/storage.py) saves profile packet settings
  and additional settings as JSON. Older code may reject newly stored fields.
- [CI](../.github/workflows/wifi-recovery.yml) runs Python 3.9, 3.11 and 3.13
  checks plus real Linux and browser checks. The clean Linux image comes from
  [the test Dockerfile](../tests/wifi/Dockerfile).

The initial analysis ran 64 selected tests covering randomness, scheduling,
rendering and service-list construction. All passed after rerunning one local
socket test outside the sandbox. These tests do not cover all proposed effects.
No physical Pi or live application acceptance was performed for this proposal.

## Delivery order

| Delivery | Result | Risk | Dependency |
| --- | --- | --- | --- |
| A | Reliable random events, service behaviour and live updates | High | Plan review |
| B | Clustered loss and occasional long delays | Medium | A |
| C | Changing condition stages and pause then release | High | A and B |
| D | Connection lottery, late interruption and service presets | High | A and C |
| E | Exact interruption of controlled downloads | High | Separate progress design review |
| F | Worsening with service usage and selection of frequently used services | High | A and verified per-device service measurement |
| Later | Additional network and application experiments | Assessed per ticket | Earlier acceptance evidence |

Each delivery needs its own concise implementation plan before production
changes. Review it independently then obtain human feedback. Keep local commits
focused. Publishing commits, changing the Pi and opening a pull request remain
separate actions.

## Delivery A reliability foundations

### Independent outage decisions

Give outage decisions their own advancing sequence. Changing the profile reroll
frequency must not decide whether outage choices advance. Keep separate random
choices for stage transitions and connection faults as those features arrive.
Repeated previews must not consume choices or start a session.

Define what happens when another outage is selected during an active outage.
Recommended behaviour is to keep the current outage's end time rather than
repeatedly extending it. A separately selected continuous-disconnection stage
can represent a longer outage. Every new bounded scenario must have a maximum
fault duration and a session end that cannot be extended by retries.

Verify outage-only profiles with several checks and deterministic seeds that
produce both selected and unselected outcomes. Cover individual and general
profiles, changes to settings, delayed scheduler ticks and session completion.

### Consistent service behaviour

Define one effective changing profile for each active scope. Device profiles
remain independent by default. The general profile remains shared. A service
list uses one shared pattern for its affected clients. A browser assignment
uses the pattern of its selected device and assignment. Quota fallback profiles
receive the same supported behaviour after activation.

Preserve current profile priority. A service-list effect replaces the ordinary
profile on matching traffic. Do not silently add two speed limits or two outage
decisions to the same traffic. Protection excludes a client before scenario
selection, traffic accounting or event recording. Existing global access rules
and dashboard-access checks retain their current meaning.

Decide how profile website rules apply to each service path. If a path cannot
support a setting, reject that combination with a clear message rather than
silently ignoring it. Do not add a queue per connected service-list client.

### Updates without unintended connection failures

Distinguish a change in packet settings from a change in target membership,
access rules or protection. Explore supported Linux operations that update a
compatible live queue without deleting all child queues. Measure whether the
operation preserves queued packets, counters and the running random model.

Keep the current full-replacement path where required, including transitions
that cannot safely update in place. Do not remove the queue-clearing behaviour
without reproducing the existing duplication transition checks. Report any
unavoidable interruption instead of presenting it as a smooth update.

The browser proxy must distinguish a harmless packet-setting update from a
change that makes an existing connection unsafe or invalid. Retain valid
unrelated tunnels. Recheck protection and access when relevant state changes.
An explicit connection-failure stage can still end its selected tunnels.

Keep one owner for timed changes and serialize them with Apply, Reset, Panic
and protection changes. Admit bounded transition work so resets cannot wait
behind an accumulated event queue. Sample and preserve quota counters across
both live updates and replacement updates.

### Session randomness and recorded stages

Offer two explicit choices: a new pattern for each session or a saved pattern
that can be repeated. Preserve existing saved profiles' random behaviour during
upgrade. Do not silently change an omitted seed in older profiles to a fresh
seed. New sessions get a recorded session identity and chosen seed.

Record actual stage changes with relative time, target, chosen settings and
outcome. Repeating a saved sequence reuses those settings and timing. It does
not promise identical packet events or identical application results when
traffic differs. Optional packet seeds depend on supported Linux capabilities.

Recommended starting limits for the new scenario mode are 16 stages, a session
duration of at most 24 hours and a single fault duration of at most 60 seconds.
Use at least one second between scheduler-driven stage changes. Short packet
timing belongs in Linux traffic controls. These are proposed new-mode limits
for review rather than claims about existing profile limits.

## Delivery B clustered loss and occasional long delays

Expose clustered packet loss with a small set of understandable controls:
how often bad periods occur, how persistent they are and how much traffic is
lost during them. Keep independent loss available. Verify measured burst
lengths as well as average loss because different patterns can have the same
average. Define which loss setting wins when both are entered.

Expose a delay pattern that produces mostly ordinary delays with occasional
long delays. Offer a simple choice between ordinary variation and a long tail.
Keep the existing delay and jitter settings understandable. Validate supported
combinations before changing traffic. Delay distributions and persistence are
approximate models rather than a promise to reproduce a particular radio.

Use existing Linux facilities where they meet the design. Add capability checks
for the installed kernel, traffic-control version and required distribution
files. Unsupported hardware must refuse the requested effect with a useful
message. It must not substitute an unrelated effect or report success.

## Delivery C changing stages and pause then release

Represent a bounded sequence of conditions such as healthy, stuttering,
briefly recovered, paused and slow. Stage duration can vary within stated
limits. Choose delay, loss and speed together for each condition. Keep severity,
frequency and persistence separately understandable.

A pause-and-release stage temporarily holds traffic then allows a bounded
amount through. It differs from an outage that drops traffic. Define the
maximum held bytes or packets, maximum hold time and overflow behaviour before
enabling it. Reset discards remaining synthetic backlog rather than releasing
an uncontrolled burst. Global reset remains able to recover every queue.

The first proposed presets are:

| Preset | Intended experience to verify |
| --- | --- |
| Almost usable | Mostly healthy traffic interrupted by rare long delays and clusters of loss |
| Stop and go | Short pauses followed by bursts with varying stage lengths |
| Works until busy | Sustained traffic causes deterioration followed by delayed recovery |

The load-sensitive preset depends on Delivery D. Do not mark it available
merely because the other two presets work. Give every running session a visible
end time and a Stop action. Reset and Panic stop new stage transitions.

## Delivery D connection faults and service presets

### Connection lottery and late interruption

Choose a bounded fault outcome once per new selected connection: normal,
slow, paused or ended. Keep that outcome stable for its lifetime unless a
scenario explicitly changes it. Start in the managed browser proxy so the
selection and lifetime are measurable. Direct native-app traffic needs a
separate reviewed design before claiming the same feature.

Allow a connection to start normally then pause, slow down or close after a
selected age or forwarded-byte threshold. Count upload and download separately.
Thresholds are per connection and direction. Do not reuse a device's cumulative
quota as a download-progress estimate. The proxy must not skip or remove bytes
from an ongoing encrypted stream to imitate packet loss.

Connection closure must be tested as closure. Do not label it a TCP reset
unless that result is verified. Browsers may resume an interrupted download.
Measure and report that behaviour rather than promising a forced restart.

Preserve the current 32-tunnel limit initially. Enforce per-client fairness
within that limit so long pauses cannot occupy every tunnel and impair other
clients through resource exhaustion. Bound pending resolution work, held data,
fault timers and tracked connections. Release each resource on expiry, error,
configuration change and reset. Do not add a sleeping worker per event.

### Load-sensitive deterioration and partial services

Use existing bounded traffic samples to start deterioration after sustained
load. Require a recovery threshold and minimum recovery time so one noisy
sample cannot cause rapid switching. Trigger from the selected affected scope
only. Protected traffic must not trigger another client's deterioration.

Selected supporting hostnames can have different conditions from the main
service. Verify real hostname coverage before creating a preset. Shared server
addresses, encrypted DNS, VPNs and reused connections limit direct matching.
Browser hostname selection is more precise but covers only proxied traffic.
Label the scope and the gaps on each preset.

### ChatGPT and Claude experiments

These are proposed network experiences rather than exact knowledge of an
answer, upload or conversation:

| Preset | Intended experience to verify | Scope limit |
| --- | --- | --- |
| Answer in bursts | Output pauses then arrives in larger pieces | Encrypted traffic timing cannot identify individual tokens |
| Starts then stops | A response starts then its connection pauses or ends | Timing is approximate without application progress |
| Slow to send | Sending or uploading becomes intermittently slow while receiving remains usable | Other traffic on the same connection can also change |
| Brief recovery | Activity resumes for a short window then deteriorates again | Client retries and buffering determine the visible result |
| Attachment stalls late | An attachment begins well then stops near completion | Exact percentage is limited to Delivery E |

Reuse the existing service entries. Verify a current browser session's required
hostnames without collecting prompt contents, answers, file names or credentials.
Do not expand a catalogue entry to unrelated services merely because they share
a provider. Local test pages can mimic streamed output without paid model API
calls. Live service acceptance uses the user's own test sessions only when
separately authorized.

### Microsoft Teams experiments

Proposed experiences are brief one-way audio gaps, bursts of late media,
intermittent poor video and screen-share stalls while the meeting remains
connected. Another preset can let a call connect successfully then deteriorate.

Teams media can travel over UDP and can use direct peer paths or relays.
Its media may not match the browser CONNECT hostnames. Start with a selected
test device when exact service isolation is unavailable. A service-only preset
requires verified media coverage and tests of fallback paths. Port-based
selection alone is not proof that traffic belongs to Teams or that audio and
video are isolated. Do not claim audio-only faults from a whole-device profile.

### Google and other service experiments

Plan separate Google targets for account sign-in, Search, Gmail, Drive and Docs,
YouTube and Meet. A whole-Google preset can be an explicit bundle of verified
groups. Do not treat one broad domain as proof of coverage of every Google
service. A separate account target should explain that other sites using Sign
in with Google may also be affected during sign-in. Existing sessions on those
sites can continue because their session handling is separate.

Current hostname matching includes subdomains. A google.com rule covers
accounts.google.com and meet.google.com as well. Keep overlapping groups in one
selected bundle initially or reject the conflicting selection. Independent
Search and account controls may require reviewed exact-host matching. Changes
to matching must preserve the compiler's overlap checks and catalogue integrity.

| Service target | Proposed experience to verify |
| --- | --- |
| Google account sign-in | The chooser or sign-in action responds slowly or times out while unrelated traffic works |
| Google Search | Search responses hesitate or arrive intermittently |
| Gmail | Opening messages or synchronizing intermittently stalls |
| Drive and Docs | File transfer or synchronization stalls after a healthy start |
| YouTube and Netflix | Playback starts normally then buffering becomes more frequent with usage |
| Facebook | The feed appears while later media and updates become unreliable |
| LinkedIn | Initial content appears while later loading and uploads intermittently stall |

These are network experiments. Do not claim control over login state, selected
accounts, message delivery, saved documents, feed positions or media content.
No account credentials, cookies or tokens are read or changed. Exact moments
such as submitting a form remain unavailable without a separately reviewed
client progress source. Live acceptance must distinguish a network stall from
an application's own loading or saving behaviour.

## Delivery E nearly finished downloads

Add a proposed Almost finished preset with a chosen interruption point between
90% and 99%, a bounded pause or closure and an optional limited recovery window.
Keep exact and approximate modes separate in the product.

| Mode | Available evidence | Honest description |
| --- | --- | --- |
| Approximate connection mode | Forwarded bytes or elapsed time on a selected tunnel | Interrupt after this amount of traffic or time |
| Controlled progress mode | Verified transferred content bytes and total content length for one isolated test transfer | Interrupt at the selected completion percentage |
| Arbitrary encrypted download | No individual response size or transfer identity | Exact percentage unavailable |

Counting encrypted bytes is not the same as measuring file content. TLS adds
overhead. A single HTTP/2 connection can carry several requests. Retries, cached
content, compression and parallel downloads further change the relationship.
Do not label a tunnel threshold as 90% of a real file.

Recommended first exact demonstration is a synthetic download from a controlled
test origin and client. Its progress source must identify one transfer and its
size. A separate design review must choose between ending the transfer at its
test origin and signalling a scoped JitterSim fault. The former demonstrates
application failure. The latter needs careful timing and target matching to
prove a network failure at the selected percentage.

Progress signals must be authenticated, limited to one selected transfer and
rejected after expiry. Bind them to the current session and verified device.
No signal may choose an arbitrary network target or bypass the private-destination
restrictions of the proxy. A claimed progress update is not proof that the
browser has received or written those bytes. Define and measure the chosen
progress point. For unknown total sizes, report exact mode as unavailable.

Test zero-length and tiny files, 90% and 99% boundaries, unknown sizes, stale
signals, duplicate signals, parallel transfers, retries and range-based resume.
Verify unrelated transfers continue. Compare normal completion, recoverable
pause and interruption without changing delivered file contents.

## Delivery F worsening with service usage

### A selected service gets worse with use

Let a device's selected service move through several worsening stages as its
measured usage increases. Start with a manually selected service before adding
automatic selection. Existing byte and time quota measurements are a starting
point. Keep this rule separate from existing saved quotas so enabling an
experiment does not rewrite a quota or change its reset schedule.

Offer matching bytes and estimated active time as distinct measures. Recommend
active time for streaming because video quality changes the data transferred
per minute. Neither measure proves that a person watched, read or visited a
particular page. Background requests, prefetching and download buffering can
contribute. Show the selected measure beside the active stage.

The following Netflix example is illustrative rather than a measured preset:

| Estimated active time in the selected window | Proposed condition |
| --- | --- |
| First 15 minutes | Healthy |
| 15 to 30 minutes | Occasional long delays |
| 30 to 60 minutes | More persistent delay and clustered loss |
| Over 60 minutes | Bounded pauses with brief recovery windows |

Stage severity has a configured ceiling. New bursts cannot extend one pause
indefinitely. Within a window, lower delivered speed must not reverse a stage
merely because the fault reduces the measured traffic. Recovery follows an
explicit new window, manual reset or agreed idle period. Start with a finite
session window and manual usage reset. Review rolling recovery separately.

### Select a device's most-used service

Offer a separate automatic-selection mode over a list of candidate services
enabled by the owner. Recommend one selected service per device initially with
at most ten candidates per device and a bounded 24-hour lookback. These limits
are proposed for review and remain subject to the existing 15 combined queue
limit. No per-page tracking or unrestricted discovery is needed.

Rank by the chosen measure and show the winner, window and evidence. Do not
mix byte totals and active seconds into an unexplained score. A byte ranking
naturally favours large transfers. An active-time ranking estimates use but can
include background traffic. Keep Google account and shared sign-in dependencies
out of automatic selection by default. They can be selected explicitly.

Use a minimum evidence threshold before choosing a target. Resolve ties
consistently. Freeze the winner for one bounded experiment interval so the
fault's own reduction in traffic cannot make the target switch repeatedly.
Reconsider at the next interval with a stated recovery policy. If measurements
are unavailable or ambiguous, do not escalate a guessed target.

### Measurement and profile priority

Count one service once per verified device. Direct traffic currently uses
observed DNS and server-address matching. Shared addresses can make that
ambiguous. Proxy traffic needs its own bounded per-device service totals.
Existing shared service-list counters alone cannot rank each individual
device's use. Do not count both sides of a proxy tunnel as two transfers or
describe retransmitted network bytes as newly consumed file content.

Collect only device identity, selected service, time window, aggregate bytes
and estimated active seconds. Cap retained buckets and candidate counts.
Recommended ranking retention is 24 hours with automatic pruning by both age
and total row count. Candidate measurements must not allocate a shaping queue
for every candidate. Delivery F's own plan must settle the counter and retained
row budgets before implementation. Maintain totals at a bounded sampling
rate rather than rescanning full DNS history on every tick. No protected client
contributes to ranking, triggers another client's fault or retains usage history
after protection is enabled.

A per-device worsening rule cannot be implemented as a shared service-list
queue because that would worsen the service for other clients too. Its design
must specify a bounded device-and-service scope and respect existing profile
priority. Start by rejecting conflicts with a shared service list or general
mode rather than silently overriding them. Support for combinations needs its
own reviewed priority design. Exceeding the queue limit must produce a clear
refusal rather than broadening the affected scope.

### Verification and release

Verify that Netflix use on one device worsens only its selected Netflix traffic.
Another device and another service remain unaffected. Cover measurement gaps,
protected clients, shared addresses, proxy totals, double counting, counter
replacement, stage thresholds, quota interactions, ties and an empty ranking.

Network counters can include retry traffic. Verify that retries cannot cause
unbounded worsening, rapid switching between winners or faults that remain
active after expiry. Check fresh and returning devices plus unavailable
identity information. Keep observations
labelled as estimates. Confirm selection, recovery and history deletion on the
Pi before making the feature available.

## Later planned features

These ideas are recorded for later tickets rather than included in the first
implementation. Each needs its own review and acceptance criteria.

- Fail new connections while established connections remain usable.
- Add intermittent one-way faults to ordinary device profiles.
- Delay or drop selected DNS lookups with explicit caching and encrypted-DNS
  limits. Avoid sticky false answers in the first version.
- Fail large packets while small transfers continue. Test client recovery
  before describing this as a faithful path-size failure.
- Add selected UDP and browser-protocol interruptions with verified application
  coverage and observable fallback behaviour.
- Share an incident across a group with optional individual recovery variation.
- Explore delayed connection closure and idle-connection expiry after the
  initial connection-lifetime design is accepted.

## Data and resource limits

Save scenario configuration, the selected seed, relative stage times and
bounded aggregate outcomes. Keep saved configuration in the existing private
backup format. Keep transient connections and queued packets out of backups.
Do not add prompt, response, document, message or call-content capture.

Recommended initial event-history limits are 1,000 entries per session and
10,000 entries overall with seven-day retention. Prune automatically by both
age and count. Report omitted history so truncation cannot be mistaken for a
complete replay. Exact values remain subject to the Pi resource review.
Protection changes remove a client's active faults and transient observations.
Deleting a saved scenario removes its own history and references automatically.

Preserve current queue, domain, plan-size and concurrent-connection limits.
Reject combinations that exceed them before saving or applying a plan. New
features must not multiply queues by stage count, hostname count or each packet.
Define the held-data budget before Delivery C or D is approved.

Use monotonic elapsed time for new session durations. Daily schedules retain
their existing calendar semantics. Skip expired stages after a delayed tick
rather than processing an unbounded backlog. After process restart, the new
scenario mode must recover to the ordinary selected profile rather than resume
an old pause or connection fault. Persist that decision consistently with the
saved apply/reset state and verify recovery when the dashboard is unavailable.

## Local development and verification

Use configuration-only mode and fake clocks for state changes on the Mac.
Write focused tests for independent event choices, preview side effects,
profile priority, limits, session expiry, failures and reset races. Do not add
unit tests that merely repeat field mappings or dashboard text.

Run real Linux traffic checks in a disposable container before Pi acceptance.
Measure loss clusters, long delays, held-data limits, queue update behaviour,
counter continuity and unaffected traffic. Use enough samples to distinguish
the selected model from ordinary random variation. Avoid fragile tests that
require one exact random packet sequence or exact millisecond timing.

Use synthetic transfers and streams for automated browser tests. Verify pause,
recovery, retries and unrelated activity. Live ChatGPT, Claude and Teams
behaviour needs separate observed acceptance. No paid model API calls are part
of this work.

Compare conditions with similar average loss and speed. Measure page completion,
longest period without progress, streaming stalls, call interruptions and time
to recover. Record client versions and matching coverage. These measurements
test the proposed experiences rather than establishing a universal annoyance
score.

## Deployment and compatibility

Extend model validation, storage loading, plan decoding, helper capabilities
and proxy understanding together. Preserve old profiles with explicit defaults.
Any database upgrade must run automatically and be repeatable through the
existing startup path. No manual per-install database editing or broad backfill
is needed. Test opening old backups and running an upgrade twice.

Use a distinct supported plan capability for new live-update and fault settings.
Do not allow an old helper or proxy to ignore a requested fault. Preflight must
check the actual installed Linux features and distribution data. Verify any new
build inputs in the clean test container. Run the existing Python, Linux and
browser CI jobs when code changes are ready.

Prepare deployment through the normal Pi installer with a private backup and
verified Ethernet management access. Order service upgrades so incompatible
dashboard, helper and proxy versions cannot apply a partial design. If a new
recovery service is needed, it must be installed and retired automatically.
No deployment is authorized by this planning document alone.

## Rollback and failure recovery

Reset and Panic must cancel selected faults, prevent new transitions and remove
only project-owned traffic state. Test helper failures, failed publication of
the active plan, proxy restart, dashboard restart and Pi reboot. A process crash
must not leave a new timed fault active indefinitely. The recovery design must
work without a functioning dashboard and must be accepted before deployment.

Recommended recovery design gives the helper an expiry for each temporary
effect as well as the session end. The helper enforces expiry independently of
dashboard polling. A helper crash needs a bounded system-service recovery path
that uses the existing reset operation. Recovery must not reapply an obsolete
snapshot of access rules or protection. Test this design before enabling stages
that can hold traffic. The exact service integration is part of Delivery A's
review.

Before rolling back, stop new sessions and verify normal traffic under the new
release. Preserve the private database and service backup. Older code may reject
new JSON settings, so test a compatible configuration snapshot or an automatic
reverse upgrade. A code-only downgrade is insufficient. If normal traffic or
compatible saved settings cannot be verified, use the existing local recovery
path before replacing services.

## Required review and acceptance

There is no pull-request template in the current `.github` directory. Use the
repository's existing plan convention of stating risk explicitly. Ask for an
independent networking review of queue changes and a security review of any new
helper action, progress endpoint or connection targeting before readiness.

| Area | Required evidence |
| --- | --- |
| Random choices | Outage-only profiles advance independently. Repeatable sessions and fresh sessions behave as labelled. Preview consumes no choices. |
| Scope | Device, group, general, service-list, browser-assignment and fallback paths have explicit supported behaviour and preserve priority. |
| Protection | Protected clients avoid every new fault, trigger, counter and observation while another client is affected. |
| Live updates | Supported updates preserve intended tunnels and counters. Necessary replacements are visible and bounded. Duplication transitions still work. |
| Resource use | Full queues, 32 occupied proxy slots, resolver backlog and excessive history remain bounded with usable recovery. |
| Concurrency | Reset, Panic, protection changes and session expiry cannot race with obsolete updates or progress signals. |
| Download accuracy | Exact mode identifies one controlled transfer. Approximate mode never claims a percentage. Resume and parallel transfer outcomes are recorded. |
| Applications | Observed browser and native-client results support each available preset. Unsupported paths are labelled accurately. |
| Usage selection | Per-device totals support the selected measure. Background activity and retries are labelled honestly. Winners remain stable and faults affect only their selected scope. |
| Infrastructure | Fresh install, repeated upgrade, old backup, mixed-version refusal and clean-container checks pass. |
| Recovery | Failed apply, failed cleanup, service restarts, Pi reboot and tested rollback preserve a working management path and remove expired faults. |

For physical acceptance use at least two affected clients and one protected
client with concurrent traffic. Check upload and download separately. Record
what could not be tested. Complete a senior review of the full change before
publishing ready commits or submitting a pull request.

## Sources for the proposed behaviours

- [Linux network emulator documentation](https://github.com/iproute2/iproute2/blob/main/man/man8/tc-netem.8)
  describes burst-loss models, delay distributions, release slots and timing
  limits. Support on the installed Pi remains to be checked.
- [Toxiproxy fault documentation](https://github.com/Shopify/toxiproxy#toxics)
  gives examples of connection timeouts, delayed closure and byte thresholds.
  It does not establish exact encrypted download progress.
- [HTTP content length](https://www.rfc-editor.org/rfc/rfc9110.html#section-8.6)
  describes representation lengths and when they can be available.
- [HTTP/2 streams](https://www.rfc-editor.org/rfc/rfc9113.html#section-5)
  explains why several requests can share one connection.
- [Teams call flows](https://learn.microsoft.com/en-us/microsoftteams/microsoft-teams-online-call-flows)
  describes direct and relayed media paths plus UDP and TCP connectivity.
- [Google sign-in integration](https://developers.google.com/identity/siwg/integrate)
  describes the Google-hosted sign-in dependency and separate website session
  handling. It supports checking effects on new sign-ins without claiming to
  control existing website sessions.
- [Netflix data usage](https://help.netflix.com/en/node/87)
  explains that transferred data varies with playback quality. It supports
  treating bytes and estimated active time as different usage measures.

## Review feedback

Implementation approval is recorded above. The live-update design will retain
the existing full replacement for changes of queue type, target membership or
duplication. Only unchanged classification with existing non-duplicating netem
queues is eligible for an in-place change. Each command failure still performs
the existing bounded cleanup. Verify this in Linux before release.

Controlled download progress and per-device service ranking remain separate
later designs. Their proposed experience is recorded without claiming those
measurements are already available. No Pi configuration was changed while
preparing this plan.
