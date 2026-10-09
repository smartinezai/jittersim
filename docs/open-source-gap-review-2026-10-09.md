# Open-source gap review, bottom-up (2026-10-09)

Question: which hand-built parts of JitterSim would a mature free tool or library do better, and which hand-built parts are right as they are? This is the bottom-up code review. The top-down survey of candidate tools is in `docs/open-source-adoption-2026-10-09.md` (another session, may not have merged yet). Version, licence and release facts for the tools below come from that session's GitHub notes and are cited by repository.

Reviewed commit: `20936e2` (main after PR 95). Nothing here was run on a Pi or a router.

## Summary

- The code base is already better built than a first look suggests. Signing already uses `ssh-keygen -Y verify`, DNS filtering already uses Unbound, the ad list is already StevenBlack hosts, mDNS already goes through Avahi, and `nft`, `tc` and `ip` are already called with argument lists, never a shell, and read back with `-j` JSON.
- One clear win: the registrable-domain logic in `config/domain_labels.py` uses a hand-written list of 78 two-part suffixes where the Public Suffix List has 3,507. It gives wrong answers today (`gov.in`, `ac.in`, `org.il`, `gov.pl`, `sch.uk`). Adopt `publicsuffixlist`. Effort S.
- One clear gap that is not code: CI enforces a narrow slice of ruff, nothing checks the 2,000 lines of shell that includes a root installer, and nothing audits the locked wheels on a schedule. Adopt shellcheck, pip-audit and a decision on the ruff config. Effort S.
- Two spikes show custom code is the right answer: the DNS answer parser (as correct as dnspython on 3,000 real-shaped responses, 12 times faster, never crashed on 60,000 mutated messages) and the nftables rendering (python-nftables gives byte-for-byte the same kernel ruleset but adds no safety and puts a C library inside the Panic helper).
- The big files (server.py 6,365 lines with 295 commits touching it, updates.py 4,572, storage.py 3,927, dashboard.js 9,539) are large because of domain rules and the Panic safety model, not because a library is missing. Splitting them is a refactor, not an adoption.

## Ranked findings

Effort: S under a day, M a few days, L a week or more. Verdict: adopt, adapt (use the tool for part of the job or later), keep (custom is right).

| # | Hotspot | Evidence | Replacement | Effort | Risk | Verdict |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | Registrable domain from a hand-written suffix list | `config/domain_labels.py:29` (`_SECOND_LEVEL`, 78 entries), `:380` `registrable_domain` | `publicsuffixlist` (ko-zu/psl, MPL-2.0, pushed 2026-10-07, pure Python, bundles the list, 380 KB installed) | S | Low. Dashboard grouping only, not in the Panic path | **Adopt** (spike 1) |
| 2 | CI and tooling gates | `.github/workflows/tests.yml` runs `ruff check --select E9,F63,F7,F82,F811`; `pyproject.toml` selects E,F,I and finds 581 issues (549 are line length); no shellcheck on `deploy/raspberry-pi/*.sh` (2,081 lines, `install.sh` runs as root); no scheduled dependency audit | shellcheck (GPL-3.0, v0.11.0), pip-audit (Apache-2.0, v2.10.1), ruff (already used), actionlint (MIT) | S | Low | **Adopt** (spike 3) |
| 3 | DNS answer parser for service lists | `networking/dns_watch.py:82-176` (about 95 lines) | dnspython `dns.message.from_wire` (ISC, Python 3.9+) | M | Medium. Wider failure modes, 12 times slower per message, one more locked wheel | **Keep** (spike 2) |
| 4 | nft and tc rendering and apply | `networking/renderer.py:196` (`_render_nft_script`), `:576`, `networking/executor.py` | python-nftables (libnftables binding, apt only), pyroute2 0.9.6 (GPL-2.0-or-later OR Apache-2.0) | L | High. A C library and netlink code inside the root helper that Panic depends on | **Keep** (spike 4) |
| 5 | Schema upgrades in SQLite | `config/storage.py:763` `_add_column_if_missing` (8 calls), `:660-680` table copy for schedules, no `user_version` or version table | `PRAGMA user_version` (stdlib) first; yoyo-migrations or Alembic only if upgrades get more complex | S for user_version, L for a framework | Low for user_version | **Adapt**: add a version number, keep custom steps |
| 6 | Release verification and rollback | `updates.py:1716` `verify_signature` already shells out to `ssh-keygen -Y verify`; the rest (staging, sequence numbers, rollback proofs, `exec` of hash-approved code at `:1064` and `:1156`) is 4,500 lines of policy | minisign (ISC), age (BSD-3), cosign (Apache-2.0), python-tuf 7.0.1 (Apache-2.0) | L | High. The signing primitive is already an OSS tool and the update chain is the riskiest path | **Keep**. Revisit python-tuf only if key rotation or a mirror becomes a requirement |
| 7 | HTTP server and request handling | `api/server.py:901` `ControlServer` (threaded, non-blocking TLS handshakes, one slot reserved for Panic), `:5125` `ControlRequestHandler`, 121 route branches | aiohttp, Starlette plus uvicorn, Flask, Bottle | L | High. None of them offers a reserved Panic slot or the per-request input deadline (`_DeadlineReader`) without rework | **Keep** the core. Split the file and move routes to a table as plain refactoring |
| 8 | Settings validation and import | `config/models.py` (1,050 lines), `engine/codec.py` (733), `config/transfer.py:182` `check_settings` (about 150 lines of cross checks) | pydantic 2.14 (MIT), msgspec (BSD-3) | L | High. The code is mostly cross-reference rules (a schedule names a profile that must exist) that a schema library does not do; error text is asserted by tests; `codec.py` is read by the proxy and helper | **Keep** |
| 9 | Router Wi-Fi control for the TG3442DE | `networking/router.py` (837 lines, 14 commits, HTML form scraping, AES-CCM decrypt, `Crypto.Cipher` at `:14`) | aiovodafone v3.3.5 (Apache-2.0, pushed 2026-10-09) | M | Unknown. Its README does not name the TG3442DE or Wi-Fi writes; it is async with aiohttp and a parser, JitterSim ships only `requests` and `pycryptodome` | **Keep**. Ask the maintainer whether the TG3442DE Wi-Fi page is covered before spending time |
| 10 | Local certificate authority | `localca.py:183` `_openssl` (the openssl CLI, name-constrained CA, leaf renewal) | mkcert (BSD-3, last release 2022), smallstep step-ca (Apache-2.0) | M | Medium. mkcert installs into system trust stores and gives no name constraints; step-ca adds a daemon | **Keep**. It already uses OpenSSL, and the name constraint is the feature that matters |
| 11 | Dashboard charts | `api/dashboard.js:1462` `drawTrafficChart`, `:8198` `drawMeasurementChart` (hand-drawn canvas), 9,539-line single script | uPlot 1.6.32 (MIT, about 45 to 50 KB) | M | Medium. The CSP only allows hashed inline script and style, so the library and its CSS must be inlined and hashed; I found no statement that uPlot works under that policy | **Adapt, later**. Decide with the dashboard overhaul, then test under the real CSP |
| 12 | Ad and tracker lists | `networking/adblocking.py:51` (StevenBlack hosts, MIT), `config/adblocking.py:55` `parse_hosts` | Hagezi (GPL-3.0 data), Blocklist Project (Unlicense) as additional lists | S | Low | **Keep** the parser. Choice of list is a product decision (see the adoption doc) |
| 13 | mDNS name publishing | `mdnsname.py` (829 lines; D-Bus to Avahi; `:124-200` edits `avahi-daemon.conf` as text) | python-zeroconf (LGPL-2.1-or-later) | M | Medium. It would run next to Avahi, which already owns port 5353 | **Keep** |
| 14 | Device identification | No MAC vendor lookup exists (`grep -i oui` finds nothing) | IEEE OUI list through netaddr (BSD-3 with IEEE data notice) or Wireshark `manuf` | S | Low | **Optional feature, not a gap.** Phones use random MACs, so the value is small |

Skipped as clearly worse: scapy (GPL-2.0, much larger than 95 lines of parser), tcconfig (wraps the same `tc` commands the code already builds, so it adds a layer without removing the kernel quirks), Pi-hole and AdGuard Home (replace the product rather than a part of it).

## Spikes

All spikes ran on Python 3.9.6 (the same minor as the Pi's lowest supported version) in a virtualenv outside the repo, or in the existing `jittersim-kernel-tests` image (Debian 12, nftables 1.0.6, kernel 6.8) with only `NET_ADMIN`. Nothing touched a Pi or router. None is merged.

### Spike 1: Public Suffix List for registrable domains (adopt)

Branch `spike/public-suffix-list-2026-10-09` (commit `e81e202`). It adds `publicsuffixlist` to `registrable_domain` and 7 new test cases.

- Size of the gap: the hand list has 78 two-part suffixes. The ICANN part of the Public Suffix List has 3,507 two-part suffixes and 16 wildcard rules. For the major country domains alone, 1,158 two-part suffixes are missing from the hand list.
- Wrong answers today (name, current result, correct result): `www.example.gov.in` gives `gov.in` (should be `example.gov.in`); `shop.example.ac.in` gives `ac.in`; `www.foo.org.il` gives `org.il`; `x.gov.pl` gives `gov.pl`; `www.example.nic.in` gives `nic.in`; `a.alpha.sch.uk` gives `alpha.sch.uk` (the list has `*.sch.uk`, so `alpha.sch.uk` is itself a suffix).
- Differential run over 745 names (every catalogue and table domain, every hand-listed suffix with sample subdomains, and the names in the existing test file): 5 differences. Three are `sch.uk` names where the Public Suffix List is right. Two are `com.ru` names where the hand list is right, because `com.ru` is in the private section that the spike deliberately leaves out. The first attempt used the whole list including the private section and gave 23 differences, mostly CDN names such as `akamai.net`, `fastly.net` and `googleapis.com`, which JitterSim wants to group as one owner. So the rule is: ICANN part only (`only_icann=True`), keep the existing `_TENANT_SUFFIXES` and AWS patterns, keep `com.ru` as an overlay entry.
- Tests: `tests/unit/test_domain_labels.py` plus the new file: 473 passed. The related `test_dashboard_traffic_labels.py` and `test_dashboard_traffic_groups.py` with the first two: 622 passed. The 7 new cases fail on main (5 of 7 fail, 2 already pass) and pass on the branch.
- Cost: import and load 24.5 ms, 4.2 MB resident memory, lookup 6.0 µs against 11.1 µs for the hand code on the Mac.
- Caveat for adoption: the branch keeps the `_SECOND_LEVEL` check first, so `sch.uk` stays wrong there. A real change should delete `_SECOND_LEVEL` except `com.ru`, update the tests that import it (`tests/unit/test_domain_labels.py`), and add the wheel to `release-locks/runtime.txt` and `runtime-py39.txt` with its hash. The list data is dated by release (`1.0.2.20261007`), so a release should bump it now and then.

Commands:

```
python3 -m venv venv && venv/bin/pip install pytest requests pycryptodome publicsuffixlist
PYTHONPATH=src venv/bin/python -m pytest tests/unit/test_domain_labels.py tests/unit/test_domain_labels_public_suffix_spike.py
```

### Spike 2: dnspython against the hand-written DNS parser (keep custom)

Branch `spike/dns-parser-dnspython-2026-10-09` (commit `1ded20e`, script `spikes/dns_parser_vs_dnspython.py`).

- 3,000 generated valid responses (random names, 0 to 5 A records, optional CNAME): `parse_dns_message` and `dns.message.from_wire` agree on all 3,000.
- 60,000 randomly mutated messages (byte flips, truncation, inserted bytes): 0 crashes in the hand parser. 47,952 agree. In 10,493 it accepts a message that dnspython rejects, because it takes the records it can read and stops at the first bad one, which is the right behaviour for a watcher that must not stall. The other 1,555 differences I sampled are the same kind: how many records survive a damaged message, and how unusual bytes in names are written.
- Speed on the Mac: 7.8 µs per message against 95.8 µs for dnspython. Every DNS answer on the Wi-Fi interface is parsed, so this is the hot loop.
- Cost of adopting: one more locked wheel (1.3 MB installed, ISC, requires Python 3.9 or later) and an exception model to handle in a thread that must not die.
- Verdict: the parser is about 95 lines, hardened with limits (`MAX_POINTER_JUMPS`, `MAX_NAME_LENGTH`, `MAX_ANSWERS`) and cheaper. Keep.

### Spike 3: tooling run against the repo (adopt the gates)

No branch needed; the commands were run on main.

- shellcheck v0.11.0 (docker image `koalaman/shellcheck:stable`) on the 9 shell files in `deploy/raspberry-pi`: 9 findings, no errors, 5 warnings and 4 info (SC1007 four times, SC1091 three times, SC3067 and SC2012 once each). The shell is clean. A CI gate would keep it that way.
- pip-audit 2.10 on `release-locks/runtime.txt`: no known vulnerabilities.
- ruff 0.16.10 with the repo's own `pyproject.toml` rules (E, F, I): 581 findings (549 E501 line too long, 30 import order, 2 unused imports). With the CI subset: none. So the config and the gate disagree. Either set the config to what CI enforces or enforce the config.
- bandit 1.8.6 on `src`: 2 high, 20 medium, 45 low, and none of the high or medium ones is a real defect. B613 at `config/device_names.py:10` is the deliberate list of bidirectional control characters. B413 at `networking/router.py:14` is pycryptodome (maintained) being mistaken for the old PyCrypto. 15 B608 findings in `config/storage.py` build SQL from table-name constants or placeholder lists. The two B102 `exec` calls at `updates.py:1064` and `:1156` load staged code after a hash check against approved sources; they are intentional, worth knowing about, and not something to "fix". A bandit gate would need a baseline or about 25 `# nosec` notes, so it is not recommended as it stands.
- ESLint 9 on `src/jittersim/api/*.js`: not conclusive. The six files are joined into one inline script at serve time, so a per-file run reports 550 undefined names that are really shared globals. It also showed 61 unused-variable warnings and 4 `==` uses (`workflows.js:345,346,535,537`). A real run needs the joined bundle as input.
- Not run: semgrep, actionlint, zizmor (workflow files are pinned by commit hash already).

### Spike 4: python-nftables and pyroute2 (keep custom)

Branch `spike/python-nftables-2026-10-09` (commit `05ebb2b`, scripts in `spikes/`).

- Method: a pytest plugin captured every nft script `_render_nft_script` produced while four renderer test files ran (478 calls, 47 distinct). In the kernel-test image with dummy interfaces, each script was applied with `nft -f -`, the ruleset read back as JSON, the tables deleted, then the same script applied through `nftables.Nftables().cmd()` and read back again.
- Result: 47 of 47 scripts give identical rulesets (kernel handles removed from the comparison, since they count up). A deliberately broken script gives the same error text from both, with the library returning the message without a file name.
- Time: 4.0 ms per script for the `nft` process against 0.3 ms for the library. This is irrelevant for a call made on a user action.
- Why keep the subprocess: the library is a C library loaded by ctypes into the root helper. A crash or hang there takes the helper down, and the Panic design depends on the helper staying up and on commands being killable with a timeout (`Command.timeout_seconds`). `python3-nftables` is also an apt package, not a locked wheel, so it would sit outside the hash-pinned release lock. The existing code already reads state back as JSON (`networking/applier.py:458`, `nft -j list table`) and already uses `nft --check` (`networking/access.py:392`), so the main benefits are already there.
- pyroute2 0.9.6 on the same image: the prio root and fq_codel child qdisc were created with one call each and matched `tc`. The netem child was rejected twice (a unit error on `rate`, then a type error), after which I stopped. The message API takes kernel units (microseconds, bytes per second, fractions of 2 to the 32) that `tc` converts today, so JitterSim would have to rebuild that conversion. The kernel quirks the renderer handles (a duplicating netem refused while another netem exists, hex class numbers) stay either way. Not a safe swap.

## Recommended order of adoption

1. Public Suffix List (finding 1). Small, fixes real wrong answers, off the Panic path.
2. CI gates (finding 2): shellcheck on `deploy/raspberry-pi`, pip-audit on the lock files on a schedule, and one ruff rule set that CI and `pyproject.toml` agree on. Hosted CI is blocked by the billing limit, so also add them to the local verification script.
3. SQLite `user_version` (finding 5), so a future migration can tell which step an old database is on. No new dependency.
4. Lint the joined dashboard script with ESLint as an optional local check (finding 11 groundwork), then decide on uPlot together with the dashboard overhaul.
5. Everything marked keep stays as it is. If server.py grows further, split it by route group first.

## Owner decisions needed

- O1. Approve adding `publicsuffixlist` (one pure-Python wheel, MPL-2.0) to the runtime lock files.
- O2. Pick one ruff rule set: the narrow one CI runs, or the one in `pyproject.toml` (581 findings, almost all line length).
- O3. Approve shellcheck and pip-audit as gates (shellcheck is GPL-3.0 and only run as a tool, never shipped).
- O4. Whether to ask the aiovodafone maintainers about TG3442DE Wi-Fi support before any router work.
- O5. Whether the dashboard overhaul should bring in a chart library (uPlot) and what test proves it under the strict CSP.

## What this review did not cover

Python 3.9 end of life, the Docusaurus site, the browser test stack, and the extension code under `extensions/`. No dependency was verified against an ARM Pi; the Public Suffix List and dnspython numbers are from a Mac and will be slower on a Pi (the ratio between hand code and library should hold).

## Sources

- Public Suffix List parser: [publicsuffixlist on PyPI](https://pypi.org/project/publicsuffixlist/), [ko-zu/psl](https://github.com/ko-zu/psl), list data at [publicsuffix/list](https://github.com/publicsuffix/list)
- dnspython: [PyPI](https://pypi.python.org/pypi/dnspython), [dns.message docs](https://dnspython.readthedocs.io/en/stable/message-make.html)
- nftables JSON and Python binding: [libnftables-json(5)](https://man.archlinux.org/man/libnftables-json.5.en), [python-nftables tutorial](https://ral-arturo.org/2020/11/22/python-nftables-tutorial.html), [firewalld on libnftables JSON](https://firewalld.org/2019/09/libnftables-JSON)
- pyroute2: [svinota/pyroute2](https://github.com/svinota/pyroute2), kernel tc netlink spec at [docs.kernel.org](https://docs.kernel.org/6.12/networking/netlink_spec/tc.html)
- aiovodafone: [chemelli74/aiovodafone](https://github.com/chemelli74/aiovodafone), [Debian package](https://packages.debian.org/da/sid/python3-aiovodafone)
- uPlot: [npm listing](https://npmjs.com/package/uplot), [leeoniya/uPlot](https://github.com/leeoniya/uPlot), CSP background at [MDN script-src](https://developer.mozilla.org/en-US/Web/HTTP/Headers/Content-Security-Policy/script-src)
- Tools run: [shellcheck](https://github.com/koalaman/shellcheck), [pip-audit](https://github.com/pypa/pip-audit), [bandit](https://github.com/PyCQA/bandit), [ruff](https://github.com/astral-sh/ruff), [ESLint](https://eslint.org/)
- Other candidates considered: [mkcert](https://github.com/FiloSottile/mkcert), [smallstep/certificates](https://github.com/smallstep/certificates), [python-tuf](https://github.com/theupdateframework/python-tuf), [minisign](https://github.com/jedisct1/minisign), [python-zeroconf](https://github.com/python-zeroconf/python-zeroconf), [pydantic](https://github.com/pydantic/pydantic), [tcconfig](https://github.com/thombashi/tcconfig), [StevenBlack/hosts](https://github.com/StevenBlack/hosts), [hagezi/dns-blocklists](https://github.com/hagezi/dns-blocklists)
- Repository facts, licences and release dates: GitHub API notes of the top-down survey session (2026-10-09), summarised in `docs/open-source-adoption-2026-10-09.md`.
