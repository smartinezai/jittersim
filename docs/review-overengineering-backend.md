# Backend over-engineering review (origin/main 36e0d2ce, 2026-10-17)

Scope: `src/jittersim/**/*.py`, `deploy/`, `scripts/`. Read-only detached worktree of that commit.
Tools: vulture 2.x, radon (cc, mi), grep, an AST pass over function lengths.

## Status on 18 October 2026 (main `8c3f8171`)

The review below is a dated record. Its line numbers and open-PR columns are those of `36e0d2ce`.
What has happened since:

- Item 8 (verified dead code) is done: PR 147 removed all nine symbols.
- Item 9 (test-only code) is done in part: PR 147 removed `activity_events` and `load_active_plan`.
  It kept the others on purpose, because each backs tens of test assertions or is a documented
  constant (`list_timers`, `get_service_list`, `saved_devices`, `pending_count`, `busy` and
  `begin`, `keep_certificate_identity`, `PANIC_REPOSITORY_STEPS`, `LABEL_KINDS`, `FAILURE`,
  `PACKET_HOST`).
- Items 1 to 7 and 10 to 13 are open. Nothing was started on them.
- Of the pull requests named in the tables, PR 138, 139, 140, 142, 143 and 146 have merged. PR 111
  and 121 are open. The Panic-pinned `helper.py` changed again in PR 138, so its function sizes
  moved.

## Status on 23 October 2026 (main `03fe31d8`)

- Item 8 again: the only dead Python left was `clear_everyone_exceptions` (`storage.py`), `_HOST_LABEL`
  (`mdnsname.py`) and two unused imports in `api/server.py` (`secrets`, `normalize_mac_address`).
  All four are removed.
- Item 9: `list_timers`, `reset_prune_clock` and `pending_count` are gone (their tests use
  `list_active_timers`, the module variable and a small test helper). These stay on purpose because
  each backs many assertions, is a documented constant, is pinned or runs standalone:
  `get_service_list`, `begin`, `saved_devices`, `keep_certificate_identity`, `PANIC_REPOSITORY_STEPS`,
  `LABEL_KINDS`, `FAILURE`, `PACKET_HOST`.
- Item 11 is done: `UpdateBudget(seconds, message)` replaces `NameBudget`, with the same time and the
  same message for the dashboard name command.
- Item 7 stays open on purpose. Outside the pinned and standalone files only `adblocking.atomic_write`
  is left. It sets the group before it writes the data, so merging it into the pinned
  `access.atomic_write` would change behaviour.

## Verdict (5 lines)

1. Partly. The backend is **over-large and under-factored more than it is over-abstracted**: there are almost no one-implementation interfaces or forwarding layers, and verified dead code is tiny (about 50 lines).
2. The real weight is in five places: a 5,000-line `ControlServer` god class with two if-chain routers (668 and 847 lines), a Python copy of the dashboard's JavaScript rules with parity tests, a heavy Mac-side release ceremony, migration and "legacy" code for one Pi that has likely moved past it, and a 1,500-line local-CI copy of 261 lines of workflow YAML.
3. Several things that look excessive earn their keep: the standalone 4.6k-line `updates.py` (it must run as `/usr/local/lib/jittersim-update.py` with only the stdlib when the package is broken), the D-Bus mDNS publisher (Avahi cannot bind records to one interface otherwise), the hand-written plan codec (privilege boundary), and the DB triggers that guard against older code after a rollback.
4. Duplication is real but mostly small: about 12 hand-written atomic-write helpers, 5 separate deadline/budget mechanisms, 3 deadline-bounded HTTP clients. Some of it is forced by standalone execution (`python3 -I`, the identity-module sandbox in `updates.identity_modules`) or by pinned files.
5. Realistic removable total without touching safety behaviour: about 2,500-3,500 source/script lines plus about 1,000 test lines, most of it from items 1-5 below. Tests (109.6k lines, 2.4x the 45.6k source) contain some source-text pinning but are mostly behavioural.

## Ranked simplifications

Columns: what to do, estimated lines removed, risk (S/M/L), pinned Panic file involved, open PR already touching the file(s).

| # | Where | What to delete or merge | Est. lines | Risk | Pinned | Open PR |
|---|---|---|---|---|---|---|
| 1 | `src/jittersim/dashboard_rules.py` (395), `src/jittersim/impact_rules.py` (346), `remote_client.py:314-360` (`js_number`, `js_round`), `tests/unit/test_dashboard_rules_parity.py` (297), `tests/unit/test_impact_rules.py` (352) | The terminal re-implements the dashboard's JavaScript display rules "one to one" and emulates JS number formatting and `Math.round`, kept honest only by parity tests. Compute the sidebar status and the Impact verdict once on the server (a JSON field), let both page and CLI render it. Or let `jittersim impact`/`status` print the raw numbers the API already returns. Docstrings are also stale (they still say the script lives in `api/server.py`). | ~740 src + ~650 tests | L (changes page logic) | No | #146 touches the sidebar status in `dashboard.js` and `tests/browser/sidebar_status.py`: do after it |
| 2 | `src/jittersim/api/server.py:975-6053` (`ControlServer`), `response_for` at :1820 (668 lines, CC 146), `_write_response_for` at :2568 (847 lines, CC 233) | Replace the two path if-chains with a `{(method, path): handler}` table and keep moving route groups into `api/*.py` the way `api/access.py`, `api/workflows.py`, `api/trust.py` already do. Collapse 195 copies of `HTTPStatus.X, "application/json", _json_bytes(...)` into one `_json(status, data)` helper and the 48 `except HelperClientError` blocks into one helper call wrapper. Mostly restructuring, not deletion. | ~400-600 net | M | No | #121, #143 |
| 3 | `deploy/raspberry-pi/release-system.py` (1075), `publish-release.sh`, `check-release-manifest.sh`, `release-policy.json`, `scripts/release.sh` (397) | Mac-side release ceremony for a single owner: a git-locked version registry with `reserve`/`retire`, a signed local verification record, signed physical acceptance, signed approval, a verified draft publish. The Pi's safety already comes from the archive signature plus the monotonic release floor (both stay). Collapse to: tag, build, sign, gate on CI or one local-CI record, `gh release create`. Keep `sign-release.sh`, `build-release.sh`, and the Pi-side checks. | ~500-700 | M (owner process choice) | No | #139 touches both `release-system.py` and `release.sh`: wait |
| 4 | Migration and legacy code: `updates.py:755-916` (legacy certificate adoption: `_adoption_*`, `publish_certificate_adoption`, `recover_incomplete_certificate_adoption`) and its callers `localca.py:516-575, 646-650`; `updates.py:3474-3480` and `:3925` (legacy `backup` generation); `localname.py:81-98` (`_legacy_name`, earlier config key); `deploy/raspberry-pi/homepage-profile-compat.py` (98) + `homepage-compat.conf` + `updates.py:4349-4355`; `deploy/raspberry-pi/upgrade-notice.py` (46); `deploy/raspberry-pi/retire-services.py` (79, with `OBSOLETE_SERVICES = ()` kept "for the next release": speculative) | Retire once the one Pi and every rollback target are past these transitions. Confirm first on the Pi: no `legacy` certificate identity, no `/var/lib/jittersim-updates/backup`, no `.home.arpa` earlier name, and the oldest retained recovery generation already post-dates each migration. | ~450 | M | Partly: `PANIC_LEGACY_PATH` handling in `panic_guard.py` and `updates.py:1174-1231` is the same kind of code, needs Panic review + bundle | #111, #138 (`updates.py`) |
| 5 | `scripts/local-ci.sh` (1126), `scripts/local_ci_exec.py` (191), `scripts/local_ci_report.py` (221), `scripts/local-ci-changed-map.txt` | 1,540 lines that replay 261 lines of `.github/workflows/*.yml` and can drift from them. Put each job's commands in one place both use (a `Makefile` or `noxfile.py` the workflows call), or run the workflows locally with `nektos/act` (MIT, open source). Earns some keep while hosted CI is blocked, so this is "reduce", not "delete". | ~800 | M | No | #121, #141, #143, #146 |
| 6 | `src/jittersim/journal_policy.py` (331), `updates.py:1106-1145` (`configure_outage_journal`), `install.sh:409-416`, tests `test_outage_journal_policy.py`, `test_outage_proof_safety.py` | A 331-line ownership/state/inspect/remove manager for one journald drop-in. A static `deploy/raspberry-pi/20-jittersim-outage.conf` installed into `/usr/local/lib/systemd/journald.conf.d/` gives the same result: systemd's own precedence already lets `/etc` override it, and removing the file restores the default. | ~330 src + ~200 tests | S-M | No | #142 changes this file (volatile journal): do after it, or fold into it |
| 7 | Atomic writes: `networking/adblocking.py:76,100`, `localname.py`, `localca.py`, `mdnsname.py:200`, `networking/dashboard_access.py`, `remote_client.py`, `api/server.py`, `measure.py`, `networking/dns_watch.py`, `release_uploads.py`, `deploy/raspberry-pi/release-system.py:96` | About 12 hand-written temp-file + fsync + `os.replace` helpers. Move the in-package ones into one `jittersim/fileio.py`. Keep separate copies where forced: `updates.py` and `journal_policy.py` run standalone with `python3 -I`, `localca`/`localname`/`mdnsname` are executed inside `updates.identity_modules` (only those three modules exist there), and `networking/access.py`, `helper.py`, `panic_guard.py` are pinned. | ~80-150 | S | Only if you include `access.py`/`helper.py` (needs Panic review + bundle). Do not. | #121 (`storage.py` area only) |
| 8 | Verified dead code (grep: zero callers in src, deploy, scripts, JS and tests): `api/server.py:430` `_impairment_payload` (2), `config/models.py:90` `_normalize_string_list` (9), `config/storage.py:1058` `clear_device_start_scope` (3), `:2492` `list_progressions` (13), `:2645` `restart_usage_counters` (6), `:86` `DEFAULT_ISP_DOWNLOAD_KBIT`, `updates.py:2000` `_remove_quietly` (5), `config/device_labels.py:16` `CONNECTED_DEVICE_TYPE_NAMES`, `networking/wifi.py:12` `WPA3_SETTINGS` | Delete. | ~50 | S | No | #121 (`storage.py`), #111/#138 (`updates.py`), #140 (`wifi.py`) |
| 9 | Test-only production code (callers only in tests): `storage.py:2392` `list_timers` (10), `:3004` `get_service_list` (11), `engine/activity.py:96` `activity_events` (4), `networking/browser_proxy.py:61` `load_active_plan` (4), `networking/dashboard_access.py:147` `saved_devices` (3), `networking/dns_watch.py:271` `pending_count` (2), `api/wifi_operations.py:92,123` `busy`/`begin` (6), `updates.py:644` `keep_certificate_identity` (7, the inner `_keep_certificate_locked` is live), `api/access.py:35` `PANIC_REPOSITORY_STEPS`, `config/domain_labels.py:17` `LABEL_KINDS`, `mdnsname.py:49` `FAILURE`, `dns_watch.py:35` `PACKET_HOST` | Move the helpers into the tests that use them, or test through the public path. | ~60 | S | No | as #8 |
| 10 | Tests pinning text, not behaviour: `tests/unit/test_deployment.py` (75 `assertIn` on `install.sh`, unit files, nft text), `test_dashboard_name_deploy.py` (32), `test_updates.py` (32), `test_one_address.py` (23), 17 unit test files read `.py`/`.sh` source text | Keep the security invariants (private ranges in the firewall, unit ordering). Replace wording checks with `nft -c -f`, `systemd-analyze verify`, shellcheck, or running the script against a temp root. | ~200 tests | S | No | #142 (`test_deployment.py`, `test_dashboard_name_deploy.py`) |
| 11 | `dashboard_name_cli.py:16` `NameBudget` | A copy of `updates.UpdateBudget` with 120 s instead of 600 s. Give `UpdateBudget` a seconds argument. | ~12 | S | No | none |
| 12 | `config/storage.py:253` `_initialize_schema` (433 lines) plus `_add_column` probing at :813 | Schema state is detected by probing `sqlite_master`. A numbered migration list keyed on `PRAGMA user_version` would read better. Partly earns its keep: the triggers protect against older code writing after a rollback, which `user_version` alone does not solve. Low priority. | ~100 | M-L | No | #121 |
| 13 | `updates.py:237-916` certificate-identity subsystem (680 lines) | Circular coupling: `localca.py` imports from `updates.py` while `updates.py` execs `localca.py` in a sandbox. Moving the cert record/slot code next to `localca` would clarify ownership but must stay importable by the standalone manager. Restructure, not delete. | ~0-100 | L | No | #111, #138 |

Inside pinned files (needs Panic review + bundle, do only when a Panic batch is open anyway):

- `networking/helper.py:334` `HelperService._handle_payload` (280 lines, CC 92) is an if-chain over about 67 actions. A dispatch table would cut it. `:713` `_handle_dashboard_access` (147 lines, CC 62). 17 lock/condition/event objects in one module.
- `networking/access.py:62` `atomic_write` duplicates `networking/adblocking.py:76`.
- `panic_guard.py` legacy upgrade inspection (`inspect_upgrade`, `provision_upgrade`) can go with item 4 once no Pi holds legacy Panic state.

## Things that look complex but earn their keep

- `updates.py` as one 4.6k-line stdlib-only file: it is copied to `/usr/local/lib/jittersim-update.py` and must work when the installed package is broken. Splitting it into a package would break that. Splitting `apply_update` (563 lines, CC 110) into phase functions inside the same file is still worth it.
- `mdnsname.py` D-Bus publisher (829 lines): its docstring is right that Avahi's CLI and hosts file cannot bind a record to one interface.
- `engine/codec.py:218` `plan_from_payload` (516 lines, CC 152): full re-validation at the root helper boundary is correct. It is long, not over-engineered. Splitting it per rule type would help reading.
- `request_workers.py`, `networking/worker_slots.py`, `socket_deadlines.py`, `networking/deadlines.py`: small and each used. `worker_slots` (56 lines, one user: `api/homepage.py`) overlaps `RequestWorkers` and could merge, but the payoff is about 40 lines.
- Separate deadline contextvars in `localca.py:55` and `mdnsname.py:60`: forced by `updates.identity_modules`, which loads only `localname`, `localca`, `mdnsname`.
- `command.py` / `remote_cli.py` / `cli.py` split: keeps the Mac CLI from importing Pi networking code. `remote_cli` functions flagged by vulture are registered by a decorator (false positives).
- Configuration-only mode (`helper_client is None`, 40 branches in `server.py`): used by the browser test fixture.
- The many small `engine/*` modules (23-69 lines): real logic, not forwarding layers.
- Vulture false positives ignored: socketserver and `BaseHTTPRequestHandler` hooks (`service_actions`, `do_*`, `log_message`, `send_error`, `version_string`, `get_request`), `HTMLParser` hooks in `networking/router.py`, `SIGNED_RELEASES` (grepped by `deploy-from-mac.sh:443`), `PANIC_DATABASE_ALLOWANCE_SECONDS` (documented constant, used by tests).

## Metrics

### Lines of code (Python, non-blank and blank)

| Package | Lines |
|---|---|
| `src/jittersim/` top-level modules | 13,485 |
| `src/jittersim/networking/` | 10,284 |
| `src/jittersim/api/` (Python only) | 9,246 |
| `src/jittersim/config/` | 6,828 |
| `src/jittersim/engine/` | 5,788 |
| **src total** | **45,631** |
| `deploy/raspberry-pi/` (py + sh, without README) | ~4,300 |
| `scripts/` | ~1,940 |
| tests (unit 79.5k, browser 12.0k, integration 10.3k, kernel 4.3k, qa 2.1k, wifi 0.4k) | 109,570 |

Largest files: `api/server.py` 7,515; `updates.py` 4,641; `config/storage.py` 4,042; `networking/helper.py` 3,654 (pinned); `engine/runtime.py` 2,156; `remote_cli.py` 1,271; `cli.py` 1,164; `scripts/local-ci.sh` 1,126; `deploy/raspberry-pi/release-system.py` 1,075; `config/models.py` 1,050.

Maintainability index (radon mi) is 0.00 (rank C) for `server.py`, `cli.py`, `models.py`, `storage.py`, `codec.py`, `runtime.py`, `helper.py`, `router.py`, `remote_cli.py`, `updates.py`.

### Top 15 most complex functions (radon cc; length from AST)

| CC | Lines | Function |
|---|---|---|
| 233 | 847 | `api/server.py:2568` `ControlServer._write_response_for` |
| 152 | 516 | `engine/codec.py:218` `plan_from_payload` |
| 146 | 668 | `api/server.py:1820` `ControlServer.response_for` |
| 110 | 563 | `updates.py:3874` `apply_update` |
| 92 | 280 | `networking/helper.py:334` `HelperService._handle_payload` (pinned) |
| 86 | 398 | `engine/compiler.py:50` `compile_plan` |
| 80 | 367 | `engine/runtime.py:1173` `RuntimeScheduler._build_plan_with_lookups` |
| 68 | ~95 | `updates.py:395` `verify_certificate_identity` |
| 65 | 155 | `engine/runtime.py:1958` `RuntimeScheduler._sample_quota_usage` |
| 64 | 143 | `config/transfer.py:182` `check_settings` |
| 62 | 147 | `networking/helper.py:713` `HelperService._handle_dashboard_access` (pinned) |
| 53 | - | `api/workflows.py:188` `write_workflow` |
| 52 | - | `dashboard_rules.py:19` `sidebar_status` (JS mirror, item 1) |
| 49 | 192 | `engine/runtime.py:751` `RuntimeScheduler._tick` |
| 49 | - | `engine/timeline.py:10` `device_timeline` |

109 functions in src/deploy/scripts have CC of D (21) or worse.

### Dead-code candidates (vulture at 60%, then grep across src, deploy, scripts, JS/HTML and tests)

- Dead (no caller anywhere): see item 8, about 50 lines.
- Test-only: see item 9, about 60 lines.
- False positives: see the "earn their keep" list.

### Open PRs touching backend files (`gh pr list --state open`)

`server.py`: #121, #143. `updates.py`: #111, #138. `storage.py`: #121. `helper.py`, `api/access.py`, `runtime.py`: #138. `journal_policy.py`, `mdnsname.py`: #142. `release-system.py`, `release.sh`: #139. `local-ci.sh`: #121, #143, #146. `wifi.py`, `applier.py`: #140.
