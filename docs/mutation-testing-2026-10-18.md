# Mutation testing of the safety-critical pure logic (2026-10-18)

This page records a mutation-testing run on `main` (8c3f8171), the weak spots it found in the
test suite, and the tests that closed them. It also says how to run it again. No product code
changed.

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
- Every survivor was read. It is one of four kinds:
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
| `engine/codec.py` | 1368 | 63.4 / 73.4 | 98.3 / 99.0 | 13 logic, 10 message |
| `engine/compiler.py` | 568 | 100.0 / 100.0 | 100.0 / 100.0 | none |
| `config/models.py` | 1925 | 55.1 / 75.2 | 99.6 / 99.6 | 7 logic |
| `networking/renderer.py` | 969 | 76.5 / 76.5 | 98.5 / 98.5 | 15 logic |
| `networking/deadlines.py` | 28 | 100.0 / 100.0 | 100.0 / 100.0 | none |
| `dashboard_rules.py` | 1058 | 93.8 / 93.8 | 99.7 / 99.7 | 3 logic |
| `config/storage.py (row conversion and migrations)` | 254 | 61.8 / 78.5 | 86.2 / 95.6 | 10 logic, 25 sql |
| `updates.py (version, manifest, signature, archive, keys)` | 577 | 75.0 / 80.9 | 94.8 / 94.8 | 30 logic |
| `updates.py (release floor, trust file)` | 37 | 67.6 / 73.5 | 100.0 / 100.0 | none |
| `engine/schedules, progression, randomizer, eligibility, conditions, scenarios (not closed)` | 1051 | 78.0 / 78.0 | not rerun | the survivors are not closed |

`engine/compiler.py` already killed every mutant, so no test was added for it. The `engine_rules` run
was stopped after 1051 of its 1396 mutants, so its figures are partial. The 30 `updates.py` survivors
were not all classified: the next run should read `survivors updates` first. Before and after are
the same mutants (mutmut numbers them per function). After is the same
selection plus the new tests. A mutant that the selection never reaches counts as surviving
("no tests").

## What was added

- `tests/unit/test_codec_contract.py`: the plan wire format written out by hand for a plan with
  devices and for a general-profile plan, in both directions, and a table with every refusal of
  the decoder (each limit on both sides, each check alone, the exact message).
- `tests/unit/test_models_limits.py`: every numeric limit of `config/models.py` on both sides,
  with the exact message, plus the normalisations that were never asserted.
- `tests/unit/test_renderer_golden.py` and `tests/fixtures/renderer/`: the whole nft and tc
  output for individual, everyone-else and general-profile plans, live queue changes, address
  sets and reset. A change of the renderer must now change these files and be read in review.
  `JITTERSIM_UPDATE_GOLDEN=1` rewrites them.
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
- `updates.py`: see the survivors file of a rerun. The error-text survivors are the words of
  `UpdateError` messages that were not pinned.

## Not covered

- Address-follow scoring. There is no `engine/address_follow.py` on main. The branch
  `feat/follow-address-changes` is not merged.
- `engine/schedules.py`, `engine/progression.py`, `engine/randomizer.py`,
  `engine/eligibility.py`, `engine/conditions.py` and `engine/scenarios.py` were run once and
  the survivors were not closed (the `engine_rules` row). They are the next candidates: the
  schedule sweep, the profile interpolation and the seeded generator.
- Decorated functions (mutmut skips them), generators that depend on timing and the Linux
  checks. `stage_release`, `apply_update` and the rest of `updates.py` are too large for
  mutmut: a mutated `updates.py` grows past the 512 KiB that the installer allows for the
  manager, so the functions of the decision logic were run alone and the size limits were
  raised in the copy (not in the product). Two tests that start `updates.py` as a separate
  program cannot run in a mutated copy and are deselected for that run.

## How to run it again

```sh
python3 scripts/mutation-test.py setup                  # once: a Python 3.10+ venv with mutmut
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
