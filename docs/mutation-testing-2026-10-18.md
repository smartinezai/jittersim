# Mutation testing of the safety-critical pure logic (2026-10-18)

This page records a mutation-testing run on `main` (8c3f8171), the weak spots it found in the
test suite, and the tests that closed them. It also says how to run it again. No product code
changed.

PR 149 (a device runs its own profile beside Everyone) changed the codec, compiler, renderer,
storage and dashboard rules after this run. The branch was merged with that main, the codec tests
were brought up to date and the rows that PR 149 touched were run again. See "After PR 149".

A second round on 2026-10-23 finished the `engine_rules` run and classified the last 30
`updates.py` survivors. See "Addendum 2026-10-23".

## What mutation testing is

A mutation tool changes the source a little: a `<` becomes `<=`, a constant moves by one, a
branch is dropped, a string changes. Each change is a mutant. The tests run against every
mutant. A mutant that makes a test fail is killed. A mutant that leaves every test green
survived, and a survivor means that no test holds the behaviour that the change broke.

Coverage cannot show this. A line that runs in a test can still have no assertion about what it
does. The plan decoder is a good example: it was covered, yet a limit could move by one without
a failing test.

## The tool

mutmut 3.8.0 (https://github.com/boxed/mutmut). It is the maintained mutation tool for pytest. It
mutates a copy of the source, runs only the tests that reach the mutated function and runs
several mutants at once. cosmic-ray was the other candidate. It changes the files in place
unless a second service is set up, which is not safe next to hash-pinned files.

mutmut needs Python 3.10 or newer, so it lives in a separate tools venv and not in the project
dependencies. `scripts/mutation-test.py setup` makes it.

## Method

- `scripts/mutation-test.py run MODULE` copies `src`, `tests` and the few folders that some tests
  read into a work folder (`/tmp/jittersim-mutation/MODULE` by default), writes a `[tool.mutmut]`
  section for that module and runs mutmut there. The checkout is never written. A mutated file
  cannot be committed by accident. The hash-pinned files (`panic_guard.py`, `networking/helper.py`,
  `networking/access.py`, `networking/dashboard_access.py`, `networking/deadlines.py`,
  `diagnostic_records.py`, `diagnostic_privacy.py`) were never edited. `deadlines.py` was mutated
  only in the copy.
- Each module has its own test selection (`MODULES` in the script). mutmut learns which tests reach
  which function and runs only those for each mutant.
- Every survivor of every module was read. A read survivor is one of four kinds:
  - a real gap: a test or an assertion was written so that the mutant dies;
  - error text: the mutant changes only the words of an error message or the label that a
    validator prints (`"XX...XX"`, upper case, `None`). The report counts these apart. Where a
    table of limits was written anyway, the exact message is part of it, so most of them died too;
  - SQL case: `select` for `SELECT`, or `row["NAME"]` for `row["name"]`. SQLite does not care, so
    the mutant behaves like the original;
  - equivalent: the change cannot alter any result (for example a limit that an earlier check
    already enforces, or a check that no input can reach). These are listed below with their
    reason.
- The kill rate is killed mutants divided by killed plus surviving mutants. A timeout counts as
  killed. "Behaviour" leaves out the error-text and SQL-case survivors.

## Results

| Module | Mutants | Before: all / behaviour | After: all / behaviour | Survivors after |
| --- | --- | --- | --- | --- |
| `engine/codec.py` (rerun, after PR 149) | 1370 | 63.4 / 73.4 | 98.2 / 99.0 | 14 logic, 10 message |
| `engine/compiler.py` (rerun, after PR 149) | 561 | 100.0 / 100.0 | 100.0 / 100.0 | none |
| `config/models.py` | 1925 | 55.1 / 75.2 | 99.6 / 99.6 | 7 logic |
| `networking/renderer.py` (rerun, after PR 149) | 993 | 76.5 / 76.5 | 98.5 / 98.5 | 15 logic |
| `networking/deadlines.py` | 28 | 100.0 / 100.0 | 100.0 / 100.0 | none |
| `dashboard_rules.py` (rerun, after PR 149) | 1127 | 93.8 / 93.8 | 99.7 / 99.7 | 3 logic |
| `config/storage.py (row conversion and migrations)` (rerun, after PR 149) | 254 | 61.8 / 78.5 | 86.2 / 95.6 | 10 logic, 25 sql |
| `updates.py (version, manifest, signature, archive, keys)` | 577 | 75.0 / 80.9 | 96.2 / 96.2 (94.8 before the addendum) | 22 logic, all equivalent |
| `updates.py (release floor, trust file)` | 37 | 67.6 / 73.5 | 100.0 / 100.0 | none |
| `engine/schedules, progression, randomizer, eligibility, conditions, scenarios` (whole run, see the addendum) | 1396 | 79.2 / 79.2 | 96.5 / 96.5 | 49 logic: 14 note wording, 35 equivalent |

The five rows marked "rerun" were measured again on this branch after it was merged with main
(0b9fe90f, which contains PR 149). "Before" is the first run on 8c3f8171 and the mutant counts
differ a little because PR 149 changed those files. The other rows (`config/models.py`,
`networking/deadlines.py`, `updates.py`, the engine rules) are from the first run. PR 149 did not
change those files. Main later changed `updates.py` for the signed update notes, the status and
the online checks (#111), but not one of the functions that the two `updates` rows mutate (`only`
in `MODULES`), so those figures still hold. `git diff 8c3f8171 HEAD` is empty for the other three.

`engine/compiler.py` already killed every mutant, so no test was added for it. The first `engine_rules`
run was stopped after 1051 of its 1396 mutants (78.0 percent). The addendum below ran all 1396.
Before and after are the same mutants (mutmut numbers them per function). After is the same
selection plus the new tests. A mutant that the selection never reaches counts as surviving
("no tests").

## After PR 149

PR 149 let a device run its own profile beside Everyone. It removed two refusals of the plan
codec (a general profile "cannot contain individual profile rules" and "...filters") and put two
narrower ones in their place: a device may not take mark 1, which is the queue of Everyone, and a
device may not use the queue identity `general-profile`. The compiler now keeps Everyone on mark 1
and the service lists on the marks they have under Everyone alone, and puts the devices and their
quotas after the lists. The renderer excludes the exception devices from the Everyone rules.

What changed in these tests:

- Three codec tests expected the removed refusals. They now expect the messages of main.
  "General profile beside blocked devices" is no longer a refusal, so the row is gone and the
  case is part of the new payload below.
- A new row refuses a device with the queue identity of the general profile.
- A hand-written payload for Everyone beside two devices (one of them blocked, a service list
  that keeps mark 2, the devices on marks 3 and 4) is pinned in both directions, like the other
  two payloads.
- A quota on mark 2 beside Everyone and a device on mark 3 decode. The rerun found one new codec
  survivor in the PR 149 line `1 in service_marks.values()` (a mutant that tests `2` instead of
  `1` was accepted). This test kills it. It was checked with a hand-made mutant in a scratch copy.
  The codec row above is the run before this test, so it still counts that survivor: with it
  killed, the codec has 1347 of 1370 killed (98.3 / 99.0) and 13 logic survivors, the same
  13 as before PR 149. The codec was not run a second time.
- A golden scenario for Everyone beside two devices on their own profiles (one of them blocked)
  shows the exception rules (`ip saddr != { ... }` on the Everyone rules, the extra marks 4 and 5,
  the wider `prio` roots). No other golden file changed: no earlier scenario has a device beside
  Everyone, so the renderer text for them is the same as before PR 149.
- The second half of that check, `1 in service_marks.values()`, is never true for a payload that
  the decoder reads: the mark of a quota or a browser rule must be above the number of devices, so
  it is at least 2. A mutant that removed that half would be equivalent. The mutant that tests
  `2` is not, because it refuses a payload the decoder should read. The quota test above kills it.
  The compiler never makes such a payload (it puts the quotas after the devices), so the test
  writes it by hand.

The 15 renderer survivors are the same 15 equivalent mutants as before PR 149 (`key=int`,
`enter_bad >= 1`, the default of `corruption_only` and the `None` comparison of the live change).
None of them is in the code PR 149 added. The `dashboard_rules.py` survivors (3) and the `storage.py` logic
survivors (10) have the same counts as before. They were not read again one by one.

## What was added

- `tests/unit/test_codec_contract.py`: the plan wire format written out by hand for a plan with
  devices, for a general-profile plan and for Everyone beside devices, in both directions, and a table with every refusal of
  the decoder (each limit on both sides, each check alone, the exact message).
- `tests/unit/test_models_limits.py`: every numeric limit of `config/models.py` on both sides,
  with the exact message, plus the normalisations that were never asserted.
- `tests/unit/test_renderer_golden.py` and `tests/fixtures/renderer/`: the whole nft and tc
  output for individual, everyone-else and general-profile plans, live queue changes, address
  sets and reset: 36 files. The run on 8c3f8171 used 35. The 36th is Everyone beside two
  devices on their own profiles, added after PR 149 (see "After PR 149"). A change of the renderer
  must now change these files and be read in review. `JITTERSIM_UPDATE_GOLDEN=1` rewrites them
  and is ignored when the `CI` variable is set.
- `tests/unit/test_dashboard_rules_direct.py`: the display rules without Node.js.
- `tests/unit/test_deadlines.py`: the deadline scopes. mutmut does not mutate decorated
  functions, so `deadline_scope` was checked by hand-made mutants as well: 13 of 13 died.
- `tests/unit/test_storage_migrations.py`: the exact stored text of impairments and profile
  settings, the three schema upgrades (limits, partial layouts, messages) and the failed upgrade.
- `tests/unit/test_update_decisions.py`: version order, sequence rules, the signed manifest, the
  release floor (the high-water mark), the staged-release check, key parsing, the signature
  decision, archive validation and the GitHub release reader.
- `scripts/mutation-test.py`: the runner.

## What the survivors showed

Most survivors were limits and field names that no test crossed. The ones worth knowing:

- The release floor (`_raise_floor` in `updates.py`) had no test that it only goes up. A mutant that
  forgot the old value and wrote every new value survived. The floor is what stops a signed
  old release from being installed again.
- The plan decoder (`engine/codec.py`) had no test of the general-profile fields, the global
  allowlist, the MAC policy defaults, the protected addresses or the unverified device list. The
  helper trusts nothing in a plan, so these checks are its only defence.
- Almost every numeric limit in `config/models.py` could move by one without a failure.
- The renderer text (table and chain names, hook priorities, comments and tc words) was only held
  by its shape.

No mutant showed a real defect in product code, so there is no `expectedFailure` test.

## Equivalent mutants left over

Counts are in the table above. Each group below changes no result.

- `engine/codec.py`: removing `homepage_bounce_percent` from the rule dictionaries before they are
  read (the rule readers use named keys and ignore an extra key; the strict general-profile
  reader is tested). The second `_as_mapping` and `_as_list` of the browser-rule loop: an earlier
  pass over the same values already raised, so they never raise. The two "each device or service
  must have upload and download queues" checks: a count of two queues per class, no repeated
  direction and no unknown class already imply them.
- `config/models.py`: `IPv4Address(IPv4Address)` is the same address. The upper bound of an
  interval reset is checked by the line before it. A 64-character label is refused by the label
  pattern. `rstrip("XX.XX")` still strips the dot and a lower-cased name has no `X`.
  `encode("UTF-8")` is `encode("utf-8")`. The second assignment of `ServiceAssignment.domains`
  gives the same value as the first for a name that the hostname check accepts.
- `config/storage.py`: `pop(key, )` where the key always exists. SQLite writes `text` as `TEXT`.
  `cleared >= 0` records nothing because the table's CHECK ignores a zero count. Upper-case
  table names. `LIMIT 258` instead of `257`. Upper or lower case SQL and `sqlite3.Row` keys are
  counted apart as SQL case.
- `networking/renderer.py`: `sorted(..., key=int)` (an `IPv4Address` sorts by number anyway),
  `enter_bad >= 1` (both branches give the same loss model), the default of `corruption_only`
  (every caller passes it) and setting both sides of the live-change comparison to `None`
  (both sides change the same way).
- `dashboard_rules.py`: `"single": False` is overwritten before it is returned. The default name
  of a general profile is not used without a general profile. `affected[:None]` is the whole list.
- `updates.py`: see the addendum. The error-text survivors are the words of `UpdateError` messages
  that were not pinned.

## Not covered

- Address-follow scoring. There is no `engine/address_follow.py` on main. The branch
  `feat/follow-address-changes` is not merged.
- Decorated functions (mutmut skips them), generators that depend on timing and the Linux
  checks. `stage_release`, `apply_update` and the rest of `updates.py` are too large for
  mutmut: a mutated `updates.py` grows past the 512 KiB that the installer allows for the
  manager, so the functions of the decision logic were run alone and the size limits were
  raised in the copy (not in the product). Two tests that start `updates.py` as a separate
  program cannot run in a mutated copy and are deselected for that run.

## Addendum 2026-10-23: engine rules and update decisions

Run on origin/main 03fe31d8 with mutmut 3.8.0, at most four workers. Two things were finished.

**The engine rules** (`engine/schedules.py`, `conditions.py`, `progression.py`, `randomizer.py`,
`eligibility.py`, `scenarios.py`). The whole run has 1396 mutants. Before the new tests 1106 were
killed (79.2 percent, 290 survivors). After, 1347 are killed (96.5 percent, 49 survivors). The
selection for this module also got `tests/unit/test_helper_snapshot_cache.py`, which already
held `station_snapshot` (56 of the first survivors were "no tests" only because that file was
not in the selection).

**The 30 `updates.py` survivors.** 8 were real gaps and are killed: 555 of 577 (96.2 percent). The
other 22 are equivalent (below).

What the survivors showed:

- The schedule sweep (`next_schedule_change`) was only tested for a device that also had a group
  schedule. Nothing proved that the schedules of another device, another group or everyone else are
  ignored, that a turned-off schedule does not hide the ones after it, that seconds in `now` do not
  shift the boundaries, or that the first pass through a repeated hour counts. A clock change that
  lay more than one six-hour scan step away was never tested: a mutant that lost the old offset
  after the first step still passed, and so would have missed the exact second of a window that
  the clock change starts.
- The progression between profiles was held for two fields only. Dropping any other field of the
  profile or of the impairment (reorder, duplicate, bandwidth, every `*_randomness`, the outage and
  reroll settings, the switch of seed and mode at the end) changed nothing in a test.
- The seeded generator had no value pinned, so its input could change and every saved seed would
  have chosen other values. The interval, outage and website draws had no test of the bounds or of
  which profile field they read.
- The client eligibility check had no test for a device that is seen at an address that another
  client also uses, for a missing live list, or for rows of the live list that cannot be read.
- `trusted_keys` opens the list with `O_NOFOLLOW` after checking it. No test swapped the file for a
  link in between, so removing the flag passed. No test showed either that the signature reader
  refuses a stray character in the base64 body, or that the comment of a key may hold `~` (the
  last printable character).

No mutant showed a real defect in product code, so there is no `expectedFailure` test. One
observation for the owner: `_applied_feature_value` and `impairment_range_preview` keep a branch
for fractional places, but every entry of `FEATURE_LIMITS` has 0 places, so that branch never runs
(8 of the survivors below). It was left alone.

What was added:

- `tests/unit/test_engine_rules.py`: the tests above, one behaviour each. Draws are checked with a
  small generator that returns fixed numbers and records the bounds it was asked for. The clock
  tests run in the Europe/Berlin zone on the real 2026 changes.
- `tests/unit/test_update_decisions.py`: the stray character in a signature, the `~` in a key comment
  and the list that is swapped for a link after its check.
- `scripts/mutation-test.py`: `test_helper_snapshot_cache.py` joined the `engine_rules` selection.

Equivalent mutants and the reason for each (ids are mutmut's per-function numbers):

| Function | Ids | Reason |
| --- | --- | --- |
| `is_schedule_active` | 6 | A schedule cannot start and end in the same minute (the model refuses it). |
| `next_schedule_change` | 62 | The same: `end_minute == start_minute` never reaches the sweep. |
| `next_schedule_change` | 22 | The rank is only compared as 0 against non-zero. |
| `next_schedule_change` | 42, 43, 49, 50, 71, 72 | A weekly pattern always changes the winner within a week, so the horizon, the days scanned and an event exactly at `now` or at the horizon never decide. A random comparison of 3000 cases against these mutants (shifted clock-change dates included) found no difference. |
| `next_schedule_change` | 90, 91, 99 | The scan step of 6, 7 or 6 hours and 6 seconds finds the same single clock change, and a bisection bound one second wider finds the same second. |
| `next_schedule_change` | 126, 128 | The heap then holds extra copies of a schedule and `winner()` discards the inactive ones. |
| `scenario_position` | 11, 14 | At `elapsed == total` the loop over the steps finds no step and returns `None` anyway. A non-looping elapsed time is below the total, so the modulo changes nothing. |
| `_applied_feature_value` | 3, 4, 6, 7, 8, 9, 10, 11 | Every feature has 0 places. `round` equals `quantize_value` at 0 places and the other branch never runs. |
| `impairment_range_preview` | 45, 46 | `step` is `10 ** -0`, which is 1 for any base and sign. |
| `quantize_value` | 3, 5 | `ROUND_HALF_EVEN` is the default rounding of the decimal context. |
| `quantize_value` | 10 | Only the exponent of the quantum counts, and `Decimal(2).scaleb(-n)` has the same one as `Decimal(1).scaleb(-n)`. |
| `seeded_generator` | 10 | `"UTF-8"` is `"utf-8"`. |
| `seeded_generator` | 17 | The byte order defaults to big endian from Python 3.11. Python 3.9 would fail it. |
| `station_snapshot` | 48, 49, 54, 55 | They store a copy of the last good list on a helper that has its own memory. The copy is never read for such a helper (the other branch returns first). |
| `device_protection_states` | 56, 57, 89, 95, 98, 99, 100, 101, 102, 104, 105, 107, 108, 109 | Only the words of the explanatory notes change (the `XX` and case mutants). The tests pin who is protected, who is matched and why a profile waits, not the sentences. |
| `_check_key_text` | 6, 8, 9 | `KEY_TEXT` already limits the key to 68 base64 characters, so `validate` has nothing to refuse. |
| `_signature_fields` | 4, 59 | `"ASCII"` is `"ascii"`. |
| `_signature_fields` | 34 | A third field is read and not used. |
| `_signature_fields` | 39 | The byte order defaults to big endian from Python 3.11. |
| `key_fingerprint` | 10 | `rstrip("XX=XX")` would also strip a trailing `X`, but the last base64 character of a 32-byte digest has its two low bits at 0 and `X` has them at 1. |
| `parse_public_key` | 31 | `character in "XX\n\tXX"` is still true for a newline and a tab. |
| `parse_public_key` | 73 | A comment of two words has a space in it either way, and the label pattern refuses a space. |
| `read_manifest` | 13 | `"ASCII"` is `"ascii"`. |
| `release_floor`, `staged_problem` | 8, 41 | The file name in upper case. The macOS file system does not tell the cases apart. On Linux the tests that write `.jittersim-release.json` fail on it. |
| `trusted_keys` | 23, 26, 29 | They change only the default when `os.O_NOFOLLOW` does not exist. macOS and Linux have it. |
| `trusted_keys` | 37, 38 | How much of a file is read before the length check. The refused or accepted result is the same. |
| `trusted_keys` | 48 | `"ASCII"` is `"ascii"`. |
| `validate_archive` | 15, 18 | A link, an absolute name and an empty name are refused by the file type check and the top folder check as well. |
| `validate_archive` | 83 | `dispatcher` is only a destination, and the required set uses the sources. |

## How to run it again

```sh
python3 scripts/mutation-test.py setup                  # once: a venv with mutmut, made with python3.12
                                                        # (use --python to name another 3.10+)
python3 scripts/mutation-test.py list                   # the modules
python3 scripts/mutation-test.py run codec --max-children 2
python3 scripts/mutation-test.py summary codec          # kill rate
python3 scripts/mutation-test.py survivors codec        # the diff of every survivor
```

`JITTERSIM_MUTATION_DIR` and `JITTERSIM_MUTATION_VENV` move the work folder and the venv. A run
takes one to ten minutes per module, except `updates` which takes longer because its tests start
`ssh-keygen`. Run at most four mutmut workers at a time.

To add a module, add an entry to `MODULES` (source file, test files) and run it. A new test for a
module should be added to its `tests` list so later runs include it.
