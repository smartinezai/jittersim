# Reuse saved access plans in API responses

Access responses rebuild the saved policy even when it has not changed. With
2000 names the existing repository cache saves about 4 ms or 41% of response
building time on this Mac. Use it at the two existing saved-plan sites. Keep
fresh helper reads and untrusted-policy validation on every response. There is
no new cache state and no open design question.

## Scope and behavior

- Replace the saved-plan expression in the access reader and the persisted-plan
  expression in the access writer. Remove their unused pure-builder import.
- The writer still saves any edited options or MAC policy before reading the
  plan. Every cache lookup reads current settings under the repository lock.
- Keep helper parsing, full policy comparisons, summary conversion and fresh
  payloads unchanged. Keep versions and live helper addresses in responses.
- Keep authentication, control revisions, refusal checks, unsaved validation and
  imports unchanged. No API payload cache or compatibility fallback is needed.
- Retain the existing limits of separate settings/version reads and database
  connections. This change does not provide a new snapshot guarantee.

## Risk and compatibility

Risk class B because only existing HTTP API policy reads change. Household risk
is low because the validated plans and write guards stay the same. No pinned
file, helper behavior, schema, deployment setting or wire format changes. The
existing repository and its subclasses already provide this accessor. Reverting
the change restores uncached API builds without altering saved data.

## Verification

- Warm full and summary responses share saved compilation while reading and
  parsing the helper policy each time, including changed or malformed input.
- Same-count saved edits and helper failures remain visible after a warm hit.
- Device quick actions and Disable compile the newly persisted settings. Stale
  versions and control refusals still prevent writes to the helper.
- Reuse the existing summary, saved-plan cache, served script and access-control
  coverage. Run the focused tests and narrow lint before independent review.
- Source stays frozen after review. Full local gates require a separate slot.
  No GUI work is needed unless a concrete page behavior concern appears.
