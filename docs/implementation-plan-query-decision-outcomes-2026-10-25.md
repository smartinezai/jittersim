# Actual DNS decisions in Traffic

## Human review summary

This completes the owner request from21October, not a new membership label.
Traffic must distinguish observed query attempts, current saved-list membership
and actual resolver outcomes. Missing responses and old observations remain
unknown. A repeated query is not evidence that blocking failed.

The installed Unbound configuration makes blocking decisions in local zones.
The existing capture/model only records questions and counts. Its renderer is
Panic-pinned networking/access.py and the helper entry is also pinned. Current
authority explicitly preserves these restrictions. No pinned edits, changed
DNS/firewall behavior or raw query logging will be made without a specific
authorisation and independent safety/privacy review. Instrumentation cannot be
faked by joining names against the current block list.

## Concrete bounded implementation

1. Establish a real producer in an isolated resolver fixture. Prove that each
   blocked event identifies the actual resolver policy decision, client, name
   and policy generation. Validate a nonblocking bounded delivery mechanism.
   Do not enable global query logging as a shortcut: protected-device names
   must not enter retained logs or Traffic history. The producer design is an
   explicit prerequisite, not a completed proof.
2. Add a bounded decision model and aggregate storage separate from settings.
   Preserve existing query count, timestamps and exact-list membership. Keep
   retries as attempts and track unavailable/dropped telemetry honestly.
3. Read actual events through authenticated private discovery APIs. Correlate
   only within a finite query window and policy generation. An unanswered
   query, encrypted DNS, arbitrary NXDOMAIN and a current membership match
   cannot be labelled blocked. Old rows have unknown outcome.
4. Render separate outcome counts and coverage/last-observed status in Traffic.
   Define successful DNS answers separately from successful website requests.
   No extension is required. Retain protected-device exclusions, expiry,
   authentication, private-lock clearing and existing scan semantics.
5. Add practical health monitoring: collector available/unavailable, last event,
   bounded queue overflow/lost coverage and retention. Do not add credentials,
   browsing paths or raw protected-device names to diagnostics.

## Required evidence

Use actual local Unbound decisions with synthetic clients/names: successful
answer, policy block, ordinary nonexistent name, DNS failure injection, cached
answer, retries, missing response, policy edits, duplicate IDs, TCP/UDP, telemetry
unavailable/overflow and protected devices with optional ad blocking. Check
transaction/retention limits, old-data compatibility and private UI clearing.
Run focused storage/API/parser tests and a Traffic browser journey, then
independent complete-delta review. No live Pi, installer, signing, merge or
release action is part of this local implementation stage.

## Combined release boundaries

Graph source is independently owned at314a803e and will be preserved through
normal integration after review. Existing normal stable GitHub Check/download/
Install must be verified rather than duplicated. Unpublished candidate delivery
is a different recorded gap. Signing-key use and real physical acceptance remain
owner-only. Never attest missing acceptance or overwrite an existing signed tag.
Version selection follows the final saved-settings/installer impact, not a guess.

## Immediate blockers

- Specific authority to modify the minimum pinned resolver/helper hooks and
  update the reviewed Panic bundle is not yet present.
- A real producer with protected-device privacy and bounded nonblocking delivery
  must be demonstrated before downstream counts can claim resolver decisions.
- Signing and physical acceptance must be performed by the authorised owner.

The allowance window currently reports1percent used, with a2percent remaining
reserve requirement. No reset was performed.
