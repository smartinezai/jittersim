# Random value ranges and accurate previews

## Human review summary

Add relative percentage variation or an explicit minimum and maximum to each
upload and download feature. Show the supported range that the engine uses.
Keep applied packet percentages whole. The example 6% with 7% relative
variation therefore displays a fixed 6%. A custom range of 0% to 13% means
that exact range. Relative variation inputs may use steps of 0.01%.

Make the base-value relationship explicit. Rename enabled chance to "Chance
active per interval". Explain the initial base value before the first roll.
When the reroll interval is zero, disable ordinary randomness controls while
preserving their saved settings. Outages and progressions keep their separate
behavior.

The main risks are disagreement between the preview and selected values,
invalid ranges, old software reading new settings and interaction with
progressions or service lists. Use one server calculation for the preview and
selection. Keep authenticated access, the request-size limit and bounded
requests. A preview must not store settings or change the network.

Implementation was authorized after review of the original plan. The owner
permitted fractional percentages only if they do not break compatibility.
The final scope keeps whole packet values after the fractional implementation
failed the exact helper-source admission check. No new approved helper
fingerprint is added. The helper, renderer and update protection remain the
maintenance versions. New ranges still require the current package.

## Source and delivery

The original isolated checkpoint is `29f6ca0` on `0a41c2f`. Preserve it and
its receipts. It is historical evidence rather than the release candidate.

The final feature uses maintenance source `d43f2d1`. Its application source
matches `04c982d`. The only successor changes are an explicit SSH test route,
its diagnostic assertions and a plan. Keep the newer packaged dashboard,
storage, helper, authentication, early token retention, Panic and recovery
behavior intact while adapting the feature.

The primary checkout contains unrelated work. Do not change it. Finish in
the isolated range branch. Publication and Docker allocation are held for
the coordinated release review. No Pi deployment is part of this task.

There is no pull request risk template in the original source. Treat the
change as medium risk because saved range fields and random selection
behavior change. There are no new secrets, external services or retained
analytics. The new API response contains only the supplied form values and
supported numeric bounds.

## Controls and precision

Each of the 14 feature cards has these controls:

- Percentage variation: show "Variation ± (% of base)" and the rounded range.
- Custom range: show minimum and maximum in the feature's own units.
- Chance active per interval: a separate whole percentage from 0 to 100.

Use only the selected method. Keep inactive draft values while editing but
never apply both methods together. A custom range may activate a zero base
feature or an unset bandwidth limit after the first roll.

| Feature | Minimum | Maximum | Applied step |
| --- | --- | --- | --- |
| Delay and jitter | 0 ms | 60,000 ms | 1 ms |
| Bandwidth | 1 kbit/s | 1,000,000 kbit/s | 1 kbit/s |
| Loss, reorder, duplicate and corrupt | 0% | 100% | 1% |

Relative variation accepts 0 to 100 in steps of 0.01%. Applied packet values
stay strict integers as before. Reject fractional custom endpoints for an
integer feature. Reject booleans, non-finite numbers, unsupported precision,
missing endpoints and reversed bounds.

## Shared selection and preview

Calculate the effective bounds once on the server. Derive relative bounds
from the base value, round to the supported step and clamp to feature limits.
For custom ranges, validate the endpoints against those same limits and
steps. Select from the displayed supported range with one bounded draw.
Keep the established feature draw order and deterministic seed construction.
Seeded outcomes are repeatable within a software version.

Expose one authenticated POST preview for both directions. It is pure math
with at most 14 ranges. Bypass settings writes, scheduler locking, helper
calls and DNS. Retain existing authentication, device authorization, body
limits and worker limits.

Debounce form edits by 250 ms. Ignore stale responses. Clear stale displayed
ranges while a request is pending. Use one accessible status announcement
and describe each input with its own preview. Keep desktop and phone layouts
usable with keyboard input. Reject blank or reversed custom endpoints before
saving.

Describe the initial base value and the values after the first roll. Show a
fixed value when rounding leaves only one supported value. Explain that a
small relative variation cannot change it. Reorder applies only while delay
is above zero. At reroll zero, preserve saved settings and show the base value
used by ordinary profiles. Progressions can calculate changing values
separately and outages have their own timing.

## Persistence and compatibility

Add optional mode and endpoint fields to randomness. Omit those new fields
from relative-mode payloads so ordinary whole-valued profiles keep their
existing shape. Carry custom ranges through the API, database restart,
settings export/import and network plan encoding.

Reject custom mode for interval, outage and website randomness. Those
controls retain relative variation. Packet bases, chance and counters stay
whole. Interpolate custom and mixed ranges at the owning feature's step.
Keep the existing transition behavior when bandwidth is unlimited.

Old packages cannot read custom ranges or fractional variation settings.
Rollback requires the backup from before those settings were saved. Test
with the actual previous package, including the successful matching-backup
restore. A code-only downgrade is insufficient.

## Review and verification

Review the complete source diff for saved data, decoder validation, bounded
work, concurrency, authentication and failure recovery. Keep Panic handling
and its approved source bundle unchanged. Confirm that no source fingerprint
is changed as a side effect of this feature.

Verify these central cases:

- Base corruption 6% with 7% relative variation previews and applies 6%.
- A wider relative range preserves whole output values.
- Fractional relative variation survives export/import.
- Custom corruption 0% to 13% preserves its exact supported bounds.
- Small integer-feature variation displays a fixed value.
- Clipped boundaries stay valid at 0% and 100%.
- Zero and full active chances behave as labelled.
- Reroll zero preserves base behavior and saved randomness settings.
- Custom ranges can activate zero bases and absent bandwidth.
- Progressions preserve endpoints and interpolate mixed ranges correctly.
- Saving and reloading old profiles preserves hidden settings.
- Helper and plan round trips keep whole values and reject malformed input.
- The real Linux helper can install a plan containing range metadata.

Run the focused tests, required Ruff selection and complete host suite after
final source changes. Build a wheel from a clean source copy with the exact
declared build dependencies. Verify its packaged controls through desktop
and phone browser journeys. Inspect both methods in one visual batch and
one bounded confirmation. Run the Impeccable detector once after UI edits.
Record retained findings from the existing interface accurately.

The configured release checks additionally require Python 3.9, 3.11 and
3.13, the clean Wi-Fi image, all Linux commands, the kernel suite, installed
browser journeys, homepage reference checks and GUI accessibility checks.
Mac kernel skips and a native browser pass do not replace those Linux
receipts. Docker is limited to the coordinated serial allocation.

The owner allows a billing-only CI exception after every required local
check is green. Verify the actual hosted failure reason. Never label
unavailable hosted checks green. Failing source tests, missing environments,
unresolved review findings or an untested combined source do not satisfy
that exception. Recheck the final source before any publication or merge.

## Preserved intermediate evidence

The first source passed 3261 host cases with 40 expected kernel skips. Its
range browser journey passed. Those checks preceded later form safeguards.
The original matching-backup restore also passed.

The maintenance fractional variant collected 5340 cases: 5288 passed,
11 failed and 41 skipped. Every failure involved exact Panic source admission
or the resulting refusal before update work. Keep that failed receipt at
`/private/tmp/jittersim-ranges-maintenance-host.xml`.

Automatic approval review refused an attempted helper fingerprint addition
because it would expand a security boundary beyond this feature's current
authorization. The command did not run. Restore the original helper and keep
whole packet values instead. Preserve the rejection and earlier receipts.
The final whole-value checks must be recorded separately.
