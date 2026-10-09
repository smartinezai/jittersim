# Open-source tools JitterSim could adopt

Date: 9 October 2026. Research only. Nothing in this file changes code.

The owner asked whether free, open-source tools or GitHub projects could
replace hand-built code or fix open problems. This note reviews about 120
projects against the current `main` (`acd1176`). Licence, stars, last release
and last push dates come from the GitHub API on 9 October 2026. Python
versions come from each project's `pyproject.toml` or PyPI page. Debian
versions come from packages.debian.org. Raw notes are kept outside the repo.

Nothing here was run. The machine was overloaded, so no tests, browsers or
tools were started. Where a fact is not checked, the text says so.

## Summary

- JitterSim's core is hand-built for good reasons. The `tc` and `nft`
  renderer, the Panic guard, the update manager, the local CA, the browser
  proxy and the Avahi name publisher each have a feature that no tool offers
  (for example burst loss with `gemodel`, a name-constrained CA, per-device
  marks). Section "Where custom code is the better choice" lists them.
- The best wins sit around the core. They are shared data (service domain
  lists, the Public Suffix List, MAC vendor names), developer tooling
  (shellcheck, full Ruff, pip-audit, Hypothesis, pre-commit), a way to run CI
  while hosted CI is billing-blocked, and systemd sandboxing of the root
  helper. One Pi spike (`dnsmasq --nftset`) could remove a raw-packet DNS
  parser that runs with `CAP_NET_RAW`.
- The Python 3.9 floor is the most important constraint. It rules out
  pydantic, aiohttp, Starlette, structlog, python-zeroconf, tldextract,
  python-tuf and several others. Tools that only run on a developer machine
  (mypy, Hypothesis, bandit, pip-audit) are not affected.
- The repo has no `LICENSE` file and `pyproject.toml` says `TBD`. Permissive
  tools are safe. GPL, AGPL and EUPL tools must stay outside the code and
  outside the release (see "Licence compatibility").

## Constraints that decide most answers

| Constraint | Source in the repo | Effect |
| --- | --- | --- |
| Python 3.9 must keep working | `pyproject.toml` (`requires-python = ">=3.9"`), `docs/implementation-plan-maintainability-2026-10-03.md` ("Preserve Python 3.9 support and the existing standard-library server"), `measure.py` and the Mac CLI | Libraries that need 3.10 or newer cannot be runtime dependencies. |
| Two runtime dependencies only | `pyproject.toml`: `pycryptodome`, `requests` | Every new runtime dependency adds a hashed wheel to `deploy/raspberry-pi/release-locks/`. |
| Pi 3B+ with 1 GB memory | `README.md` "Network layout" | Prefer small data files and small binaries. Measure memory before bigger lists. |
| Offline and private | README "Traffic discovery": nothing leaves the Pi | No cloud APIs. Data must be vendored or fetched by a bounded job. |
| Strict hash-based CSP, no CDN | `api/server.py` lines 362-363 | A JS library must be inlined into the page and must not use `setAttribute("style")` or `innerHTML` with style text. |
| Panic must always win | `panic_guard.py`, `networking/executor.py` allowlist | A tool must not add a second path that changes `tc` or `nft` outside the structured `Command` boundary. |
| Raspberry Pi OS is Trixie (inferred) | README asks for NetworkManager 1.50 or newer. Trixie ships 1.52.1. | Trixie packages: Python 3.13.5, `python3-nftables` 1.1.3, Unbound 1.26.1, dnsmasq 2.91, iperf3 3.18, irtt 0.9.0. Confirm with `cat /etc/os-release` on the Pi. |

Minimum Python versions read on 9 October 2026:

| Needs 3.10 or newer | Works on 3.9 |
| --- | --- |
| pydantic 2.14 (3.10), msgspec 0.22 (3.10), aiohttp 3.14 (3.10), Starlette 1.7 (3.11), structlog 26.1 (3.10), python-zeroconf 0.151 (3.10), tldextract 5.4 (3.10), python-tuf 7.0 (3.10), dnspython 2.9 (3.11), mitmproxy 12.2 (3.12), aiovodafone 3.3 (3.12), cattrs and jsonschema (3.10) | pyroute2 0.9.6, tcconfig 0.30.1, netaddr 1.3.0 (3.7), publicsuffixlist (3.5), Flask 3.1.3, Werkzeug, waitress, attrs, Bottle, ruff (a binary) |

Developer-only tools may need 3.10 or newer. CI already runs Python 3.13, and
mypy can check code as 3.9 with `--python-version 3.9`. Hypothesis, bandit and
pip-audit also need 3.10 or newer in their latest versions, so run them in a
separate tool environment, not in the Python 3.9 test matrix.

## Licence compatibility

This is a plain-words guide, not legal advice. The repo is private and has no
licence yet. Pick one before the first vendored data file, because third-party
notices are easier to add at the start.

| Licence family | Examples in this note | Rule for JitterSim |
| --- | --- | --- |
| Permissive: MIT, BSD, ISC, Apache-2.0, 0BSD, CC0, Unlicense | uPlot, Ruff, v2fly lists, StevenBlack hosts, pyroute2 (Apache option), Lucide, htmx | Safe to depend on or vendor. Keep the notice text. Add a `THIRD_PARTY_NOTICES` file. |
| File-level copyleft: MPL-2.0 | Public Suffix List, Hypothesis, axe-core, Vector | Safe to vendor unmodified with its licence text and provenance, as `tests/browser/vendor/` already does for axe-core. |
| Weak copyleft: LGPL-2.1 and 3.0 | python-zeroconf, LibreSpeed, icmplib, python-sdbus-networkmanager | Safe as a separate dependency or process. Do not copy their code into the repo. |
| Strong copyleft: GPL-2.0, GPL-3.0 | shellcheck, scapy, irtt, flent, Netdata, hagezi lists, ansible, hadolint | Run as separate developer tools or separate Pi processes. Never import into the Python code or copy into the release tarball. Install them with `apt` on the Pi instead of shipping them. |
| Network copyleft: AGPL-3.0 | Grafana | Run as a separate service only. Do not embed. |
| EUPL-1.2 | Pi-hole core and FTL | Treated like strong copyleft. Ideas only. |
| Source-available | Nmap (NPSL, not OSI) | Do not bundle or redistribute. |
| No licence found | OISD (page states none), sqm-scripts and nextdns/metadata (no licence file found) | Treat as all rights reserved. Do not copy. |
| Proprietary data | Fingerbank SQLite database | Not usable. |

## Top 10 recommendations, ranked by value for effort

Effort: S is under a day, M is a few days, L is a week or more.

### 1. One local check command: full Ruff, shellcheck, actionlint, zizmor, pip-audit

- What: run more linters on the code that exists. Today CI runs only
  `ruff check --select E9,F63,F7,F82,F811` (`.github/workflows/tests.yml`,
  comment: "Long lines are a separate backlog"). About 1,670 lines of root-run
  shell (`deploy/raspberry-pi/*.sh`) and 257 lines of workflows have no
  linter. A few `# shellcheck disable=` comments in `deploy-from-mac.sh` suggest
  shellcheck was used by hand once.
- Tools: Ruff (MIT, v0.16.10, 50k stars), shellcheck (GPL-3.0, v0.11.0, tool
  only), actionlint (MIT, v1.7.12), zizmor (MIT, v1.30.1), pip-audit
  (Apache-2.0, v2.10.1, supports `--require-hashes` and OSV), pre-commit (MIT)
  or a plain `Makefile`.
- Replaces: nothing. It adds checks.
- Effort S. Risk low: report first, gate later. All tools run on a developer
  machine only, so licence and Python 3.9 do not matter.
- First step (not run here): in a scratch venv run
  `ruff check --statistics --select E,F,I,S,B,UP,SIM,RUF src tests`,
  `shellcheck -x deploy/raspberry-pi/*.sh`,
  `pip-audit --require-hashes -r deploy/raspberry-pi/release-locks/runtime.txt`.
  Record the counts in a short doc. Then add a `make check` target that runs
  them and a pre-commit file for the cheap ones. Ruff's `S` rules are the
  bandit rules, so bandit itself is not needed.

### 2. Run the existing workflows on a self-hosted runner

- What: hosted CI is billing-blocked, so every merge relies on hand-run local
  counterparts (`docs/planned-fixes.md` says so). The GitHub runner
  (`actions/runner`, MIT, v2.338.0) runs the same workflow files on a machine
  the owner controls.
- Facts: GitHub planned a $0.002 per minute charge for self-hosted runners
  from 1 March 2026 and postponed it with no new date (GitHub's own page,
  read 9 October 2026). Hosted runner prices were cut by up to 39% from
  1 January 2026. I could not confirm whether a self-hosted job starts while
  the account is billing-blocked. One trial job decides it.
- Replaces: nothing in the code. It restores real "CI green on the head
  commit" for the owner's merge rule.
- Effort S to M. Risk: a runner executes pull request code, so use it only
  for the owner's own branches on a machine without secrets. The kernel job
  needs a privileged container, so use a Linux host or VM. The workflows say
  `runs-on: ubuntu-latest`, so jobs would need a self-hosted label.
- Fallback: `nektos/act` (MIT, v0.2.89) runs workflows in Docker on the Mac.
  It does not match GitHub exactly (artifact upload and some actions need
  extra setup), so treat it as a convenience, not as proof.
- First step: on a throwaway branch add a one-job workflow with
  `runs-on: self-hosted` that prints `ok`, register a runner, and see whether
  the job starts while billing is blocked.

### 3. Audit and harden the systemd units, starting with the root helper

- What: `jittersim-helper.service` runs as root and sets none of
  `NoNewPrivileges`, `ProtectSystem`, `ProtectHome`, `PrivateTmp`,
  `CapabilityBoundingSet` or `RestrictAddressFamilies`. Seven of the twelve
  `.service` files set sandbox options. `access-boot`, `ap`, `control-firewall`,
  `helper` and `update` do not.
- Tool: `systemd-analyze security` (part of systemd, free). It scores each
  unit from 0.0 to 10.0, where higher means more exposed. With `--offline`
  it reads unit files without a running systemd.
- Replaces: nothing. It adds a second line of defence around the most
  privileged process.
- Effort S for the audit, M for hardening. Risk is real: the helper runs
  `nmcli`, `systemctl`, `tc`, `nft`, `tcpdump` and `iw`, so a denied
  capability could break Panic or an update. Change one option at a time and
  repeat the Panic and update checks on a spare Pi after each.
- First step: on the Pi run `systemd-analyze security jittersim-helper.service`
  and the other four units. Save the scores in a doc. Try the additive
  options first (`NoNewPrivileges=true`, `ProtectHome=true`, `PrivateTmp=true`,
  `ProtectKernelModules=true`).

### 4. Build the service catalogue from v2fly/domain-list-community

- What: `config/service_catalogue.py` (82 lines) and the hand table in
  `config/domain_labels.py` (463 lines) list service domains by hand. The
  README says "a service can use more hostnames than are listed here".
  v2fly/domain-list-community (MIT, 9.6k stars, release 20261009101206)
  keeps 1,545 category files. It has `netflix`, `youtube`, `zoom`, `spotify`,
  `slack`, `openai`, `anthropic`, `linkedin`, `disney`, `tiktok`, `whatsapp`,
  `steam`, `twitch`, `discord` and `primevideo`. A plain line matches the
  domain and its subdomains, which is the same rule JitterSim uses.
- Replaces: the hand-typed domain tuples. Not the dashboard logic.
- Effort S to M. Risk low: data only, no runtime fetch. Watch for these:
  JitterSim forbids one hostname in two services and caps a plan at 15
  combined device and service rules and 200 browser hostnames (README). v2fly
  lines can be `regexp:`, `keyword:` or `include:`, which JitterSim cannot
  express. Skip those and skip `@ads` attributes. The lists are made for
  routing in Project V, so some carry routing attributes such as `@cn`. Keep a
  hand-reviewed override file.
- First step: write a developer script that reads a pinned commit of `data/`
  for about 30 chosen services and writes one generated Python file. Add a
  test that no hostname appears twice and every name passes
  `normalize_service_hostname`. Add the MIT notice and the commit hash.

### 5. Replace the hand-written suffix tables with the Public Suffix List

- What: `domain_labels.py` holds `_SECOND_LEVEL` and `_TENANT_SUFFIXES` by
  hand to find a registrable domain such as `immobilienscout24.de`. The
  Public Suffix List (MPL-2.0, 3.0k stars) has an ICANN part and a PRIVATE
  part. The private part lists hosting suffixes such as `appspot.com`, which
  is the "tenant" case the code handles by hand.
- Options: vendor `public_suffix_list.dat` and write a matcher of about 40
  lines (wildcard and exception rules are simple), or add the
  `publicsuffixlist` package (MPL-2.0, Python 3.5 or newer, release
  1.0.2.20261007, pure Python, bundled data). `tldextract` needs Python 3.10
  and is not usable.
- Effort S. Risk low. The list changes a few times a week, so refresh it with
  each release, not at runtime (the PSL site asks for at most one download per
  day).
- First step: vendor the file with a provenance record (like
  `tests/browser/vendor/provenance.json`), write the matcher, and add a test
  that the old tables and the new matcher agree on the existing test names.

### 6. Property tests for the parsers that run as root

- What: `dns_watch.py` reads raw frames with `CAP_NET_RAW` and parses DNS
  packets by hand (`parse_dns_message` line 122, `parse_dns_frame` line 158,
  limits such as `MAX_POINTER_JUMPS`). `helper.py` parses `ip neigh`,
  `iw station dump`, DHCP lease files and `tcpdump` text. `parse_hosts`
  reads a downloaded 8 MB list. Example tests cover chosen inputs. Random and
  mutated inputs find the cases nobody thought of.
- Tool: Hypothesis (MPL-2.0, v6.168.5, 9k stars). The latest release needs
  Python 3.10, and pip picks an older release on 3.9. Use it in the
  Python 3.11 and 3.13 jobs, or a tool venv.
- Replaces: nothing. Dev-only.
- Effort M. Risk low.
- First step: add `hypothesis` to the dev extras and write one test that feeds
  random bytes and bit-flipped valid packets to `parse_dns_message`. Assert it
  never raises anything but the module's own error, returns at most
  `MAX_ANSWERS` answers and finishes fast. Then do `parse_hosts`,
  `_parse_dhcp_lease_output`, `normalize_domain` and a plan codec round trip
  (`plan_to_payload` then `plan_from_payload`).

### 7. Show a vendor name and a "private address" hint for connected devices

- What: device names and types come from DHCP names and manual labels
  (`config/device_labels.py`, 85 lines). Nothing looks up the MAC vendor.
  The IEEE registry maps the first three bytes to a company. Phones use
  randomised addresses (the README says so), so the lookup must also read the
  "locally administered" bit (second-lowest bit of the first byte) and say
  "Private Wi-Fi address" instead of a wrong vendor.
- Options: `netaddr` (BSD-3-Clause, Python 3.7 or newer, bundles an OUI
  snapshot, but the last release is 28 May 2024 so the data is old), or vendor
  the IEEE CSV with a refresh script. Check IEEE's terms before shipping the
  file in a release. Wireshark's `manuf` file is GPL-2.0 and must not be
  copied.
- Effort S. This is a new feature, not a replacement. Risk low. Memory: load
  lazily or search a sorted file, because the Pi has 1 GB.
- First step: add a pure function `vendor_for(mac)` with a vendored table,
  a randomised-address check and tests, then show the result next to the
  suggested type in the device list.

### 8. Spike: let `dnsmasq --nftset` learn service addresses

- What: `dns_watch.py` (431 lines), `networking/service_addresses.py` (86) and
  the matching helper path learn the addresses of service-list hostnames by
  watching DNS answers on a raw socket. dnsmasq can do this itself:
  `--nftset=/netflix.com/4#inet#table#set` adds each resolved address to an
  nftables set (available since dnsmasq 2.87, but it must be compiled in).
  NetworkManager already runs dnsmasq for the test Wi-Fi, JitterSim already
  writes into `/etc/NetworkManager/dnsmasq-shared.d`, and Unbound forwards to
  that dnsmasq. Unbound's own `ipset` module cannot help: it supports ipset
  only, and the nftables pull request (NLnetLabs/unbound #1196) is still open.
- Benefit: remove a root-level packet parser and a thread.
- Effort M to L. Risk medium: set timeouts versus Unbound's
  `cache-max-ttl: 300`, IPv4 only, Panic and reset must empty the sets, and
  answers Unbound serves from its own cache never reach dnsmasq.
- First step: on a spare Pi run
  `dnsmasq --version | tr ' ' '\n' | grep -x nftset`. If it prints `nftset`,
  prototype one service list with a named set and compare the set contents
  with what `dns_watch.py` learns for the same traffic. If not, close the
  idea.

### 9. Draw the two charts with uPlot

- What: `api/dashboard.js` draws the traffic chart (about lines 1285-1630) and
  the measurement chart (lines 8198-8277) on a canvas by hand, including hit
  testing. uPlot (MIT, 10.5k stars, release 1.6.32 on 14 March 2025, commits
  until 28 September 2026) gives cursor, legend values and touch zoom.
- Checked: the current `dist/uPlot.iife.min.js` (68,594 bytes) has no
  `cssText`, no `setAttribute("style"` and no `innerHTML`, so it should work
  under the strict CSP. It still needs an inline hash and its CSS inlined.
- Cost: about +68 KB on a page that is already about 550 KB. The page is already about 550 KB (earlier GUI research). uPlot adds no
  accessibility help (that research says the same), so the text alternative
  and pause control are needed either way.
- Effort M. Verdict: adopt later, after the chart interaction plan
  (`docs/implementation-plan-traffic-chart-interaction-2026-10-03.md`) is
  accepted. Do not do it only to save lines.

### 10. Measure from a phone with iperf3, LibreSpeed or irtt

- What: `measure.py` (364 lines) needs Python and `ping` on the test device.
  "Measuring from phones" is on the owner's ideas list. iperf3 (BSD-style,
  upstream 3.22, Debian 3.18) gives throughput to a phone app. LibreSpeed
  (LGPL-3.0, v6.3.0, Go server v1.1.6) gives a browser test with no app.
  irtt (GPL-2.0, v0.9.2, Debian 0.9.0) gives jitter, loss and one-way delay.
  Tests against the Pi itself show the shaped link exactly, with no internet
  needed.
- Cost: a new listening port on the test Wi-Fi. That widens the attack surface
  that the firewall now keeps small, and Panic must close it. Install from
  `apt` so no GPL binary ships in the release.
- Effort M. Verdict: adopt later, after the household trial, as an opt-in
  service that is off by default.

## Where custom code is the better choice

1. Traffic shaping (`networking/renderer.py` 713 lines, `applier.py` 540,
   `executor.py` 205, `commands.py`). It builds a `prio` root with one `netem`
   or `fq_codel` child per firewall mark, uses `loss gemodel` for burst loss,
   and replaces all `nft` rules in one transaction. pyroute2's netem module
   handles reorder, corrupt and rate but has no `gemodel` (0 hits in
   `sched_netem.py`). tcconfig has no `gemodel` and no per-MAC option in its README. No
   project offers the whole design, and the allowlisted `Command` boundary is
   part of Panic's safety.
2. Panic guard and the helper protocol. Nothing comparable exists.
3. Update manager core (`updates.py` 4,572 lines). The signature check already
   uses a standard tool: `ssh-keygen -Y verify` with ed25519 keys. The
   custom part is staging, floors, locks and rollback for one appliance.
   python-tuf needs Python 3.10 and adds roles and metadata that a
   one-owner, one-device channel does not need.
4. Local CA (`localca.py` 712 lines). It issues a CA with `pathlen:0` and a
   critical `nameConstraints` limited to `.local`, `home.arpa` and the Pi's
   address ranges. That limits the damage if the CA key leaks onto a phone.
   Caddy's internal CA does not do this (issue #5759 is open). mkcert
   has had no release since April 2022.
5. mDNS name (`mdnsname.py` 829 lines). It publishes one address record per
   interface through Avahi's D-Bus API and nothing else. python-zeroconf
   registers services and needs Python 3.10.
6. Browser proxy (`networking/browser_proxy.py` 504 lines). It sets
   `SO_MARK` on each tunnel (line 209) so `tc` shapes it, checks MAC access and
   bounds lookups. mitmproxy needs Python 3.12 and is an interception tool,
   which the roadmap lists as a non-goal. Squid and tinyproxy are GPL-2.0.
   Squid can set marks by ACL (from memory, not re-checked), but neither reads
   JitterSim's plan file, so a config generator would replace the proxy
   without removing code.
7. HTTP server (`api/server.py` 6,365 lines). aiohttp and Starlette cannot run
   on 3.9. Flask, Bottle and waitress could, but the hard part is admission
   control (pending-connection accounting, `DeadlineReader`,
   `RequestWorkers`, client identity checks), which a framework does not
   provide. Splitting the file
   by area (as `docs/architecture.md` says) is the better fix.
8. Validation (`config/models.py` 1,050 lines, `engine/codec.py` 733). pydantic
   and msgspec need 3.10 and would not remove the domain rules.
9. Router scraper (`networking/router.py` 837 lines, Arris TG3442DE).
   aiovodafone (Apache-2.0) needs Python 3.12 and aiohttp, and the Home
   Assistant docs list other models, not the TG3442DE. The other tool found
   (`arris-tg3442-reboot`) was last updated in June 2020. Both are useful only
   as protocol references.
10. Kernel test lab (`tests/kernel/lab.py` 98 lines). Mininet (last release
    2021) and containerlab are far larger than a three-namespace lab.
11. DNS filtering with Unbound views (`networking/access.py` 648 lines,
    `networking/adblocking.py` 683). Blocky, AdGuard Home and Pi-hole would
    replace it, but the plan computes block and allow-only lists per device,
    DNS failures use an `nft` random rule, and the design keeps no query log.
    AdGuard Home (GPL-3.0) and Pi-hole (EUPL-1.2) cannot be embedded.

## Build-versus-adopt matrix

Columns: candidate with licence and health, what it would replace, fit and
cost, verdict. "Pushed" is the last push date. Effort is S, M or L.

### A. Traffic shaping, netem and nftables

| Candidate | Would replace | Fit and cost | Verdict |
| --- | --- | --- | --- |
| pyroute2. GPL-2.0-or-later or Apache-2.0. 1.1k stars. 0.9.6 (2026-04-15). Pushed 2026-10-09 | `tc`, `ip`, `nft` shell-outs in `renderer.py`, `applier.py`, `executor.py` | Python 3.9 ok. Pure Python. No `gemodel`. Debian has 0.7.7, pip has 0.9.6. In-process netlink bypasses the `Command` allowlist. L | Ignore for writes. Maybe later for read-only counters. |
| tcconfig. MIT. 854 stars. v0.30.1 (2025-05-08). Pushed 2026-04-13 | Same | Wraps `tc`. Delay, rate, loss, corrupt, duplicate, reorder, `--delay-distro`. No `gemodel`, no per-MAC. M | Ignore |
| Comcast. Apache-2.0. 10.5k stars. Last release 2015, pushed 2025-03-20 | Same | Global `tc` and firewall wrapper. | Ignore |
| wondershaper. GPL-2.0. Shell. Pushed 2024-07-25 | Same | One rate limit per interface. | Ignore |
| Pumba. Apache-2.0. v1.2.1 (2026-08-23) | Same | Docker, containerd and Podman containers only. | Ignore |
| Toxiproxy. MIT. 12.4k stars. v2.12.0 (2025-03-18) | Nothing | TCP proxy per upstream. Toxics: latency, bandwidth, slow_close, timeout, reset_peer, slicer, limit_data. No per-MAC. | Ignore. Borrow toxic ideas for roadmap Milestone 18. |
| sqm-scripts and CAKE. sqm-scripts has no licence file. v1.8.0 (2026-08-25) | `fq_codel` child queues in `_render_tc_commands` | JitterSim impairs links. It does not cure bufferbloat. | Ignore |
| python-nftables (libnftables). Debian `python3-nftables` 1.1.3 for armhf and arm64 | The `nft -f -` stdin script in `render_command_plan` | System Python only. The text script is already one transaction and `--check` is in use (`access.py` line 392). The nftables manual also describes JSON output with `-j`. M | Ignore |

### B. Access point and Wi-Fi

| Candidate | Would replace | Fit and cost | Verdict |
| --- | --- | --- | --- |
| RaspAP. GPL-3.0. 5.2k stars. 3.5.8 (2026-10-02). PHP | `create-ap-profile.py` (199), `networking/wifi.py` (249), `api/wifi_operations.py` (224) | A web GUI around hostapd and dnsmasq. It would compete with the NetworkManager profile and the `nft` rules. GPL-3.0. Not tested here. | Ignore. Borrow UI ideas only. |
| create_ap. BSD-2-Clause. Archived. Pushed 2023-12-13 | Same | Unmaintained. | Ignore |
| linux-router. LGPL-2.1. 2.1k stars. 0.8.2 (2026-07-08). Shell | Same | A script. Overlaps NetworkManager. | Ignore |
| OpenWrt. GPL-2.0-only. v25.12.5 (2026-07-01) | NetworkManager, dnsmasq config, `nft` management, the installer | New OS, new update path. The `boa` project shows the route (see N). L | Ignore for the Pi. Reconsider only if the Pi 3B+ radio is the limit. |
| python-sdbus-networkmanager. LGPL-2.1. 47 stars. Release 2023-06-04 | `nmcli` calls | Little activity. The `nmcli` calls are bounded and tested. | Ignore |
| `mac80211_hwsim` kernel module | Nothing. It adds tests | Simulates 802.11 radios (two by default) so hostapd and a client can run with no hardware. It needs a kernel with the module, so a privileged Linux VM. Not checked on hosted runners. L | Adopt later for Wi-Fi recovery tests |

### C. DHCP, DNS and ad blocking

| Candidate | Would replace | Fit and cost | Verdict |
| --- | --- | --- | --- |
| Unbound. BSD-3-Clause. v1.26.1 (2026-09-16). Already used | Nothing | Keep. Debian's package depends on libpython3.13, so it probably has the Python module (not verified). A Python hook inside the resolver would put DNS behind a Python crash, so do not use it. | Keep |
| dnsmasq `--nftset` (dnsmasq 2.91 in Trixie) | `dns_watch.py` (431), `service_addresses.py` (86), helper address path | See recommendation 8. Must be compiled in. M to L | Adopt later, after the spike |
| Blocky. Apache-2.0. v0.35.0 (2026-09-05). Go | `access.py` DNS rendering and `adblocking.py` (683) | Has client groups (from its docs, not re-checked). JitterSim needs per-device allow-only lists and no query log. L | Ignore. Borrow client-group ideas. |
| AdGuard Home. GPL-3.0. 37k stars. v0.107.79 | Same | GPL-3.0, cannot embed. | Ignore |
| Pi-hole and FTL. EUPL-1.2. 61k stars. v6.4.3 | Same | Copyleft. Known to keep a query database by default (not re-checked). | Ignore |
| CoreDNS. Apache-2.0. v1.14.7 | Same | General purpose. No gain over Unbound views. | Ignore |
| StevenBlack hosts. MIT. 31k stars. v3.16.120. Already used | Nothing | Keep (`BLOCKLIST_URL` in `adblocking.py`). | Keep |
| hagezi lists. GPL-3.0. 27k stars. Light 54.7k, Normal 158.8k, Pro 194.7k entries. RPZ format works with Unbound | Choice of list | Data under GPL-3.0: the Pi may download it at runtime, but do not vendor it in the release. Measure Unbound memory first (the Pi has 1 GB, and the 8 MB `MAX_LIST_BYTES` limit applies). S | Adopt later as an optional list choice |
| Block List Project. Unlicense. v2026.10.05 | Choice of list | Many category files. | Adopt later, optional |
| OISD | Choice of list | The page names no licence. | Ignore until licensed |

### D. Service lists, domain labels and suffixes

| Candidate | Would replace | Fit and cost | Verdict |
| --- | --- | --- | --- |
| v2fly/domain-list-community. MIT. 9.6k stars | `service_catalogue.py` (82), parts of `domain_labels.py` (463) | See recommendation 4. S to M | Adopt now |
| Public Suffix List. MPL-2.0. Or `publicsuffixlist` (MPL-2.0, Python 3.5 or newer) | `_SECOND_LEVEL`, `_TENANT_SUFFIXES` | See recommendation 5. S | Adopt now |
| tldextract. BSD-3-Clause. 5.4.0 (2026-10-03) | Same | Needs Python 3.10 and fetches the list online by default. | Ignore |
| nextdns/metadata | Service list data | No licence file. Last push 2023-01-20. | Ignore |

### E. Device discovery and identification

| Candidate | Would replace | Fit and cost | Verdict |
| --- | --- | --- | --- |
| IEEE OUI data via netaddr (BSD-3-Clause, 1.3.0 of 2024-05-28) or a vendored CSV | Nothing (new) | See recommendation 7. S | Adopt now |
| scapy. GPL-2.0. v2.8.0 (2026-10-02) | `dns_watch.py` parser | GPL-2.0, cannot ship. Fine as a dev-time test oracle only. | Ignore for runtime |
| arp-scan. GPL-3.0. Last release 2022-12-13 | `ip neigh` and `iw station dump` parsers in `helper.py` | Active probing adds traffic. Passive learning is enough. | Ignore |
| Nmap. NPSL (not OSI) | Same | Licence blocks bundling. | Ignore |
| netdisco. Apache-2.0. Archived, last release 2021 | Same | Stale. | Ignore |
| NetAlertX. GPL-3.0. 7.3k stars. v26.10.0 | Same | A full app (PHP and Python). Borrow "new device" alert ideas only. | Ignore |
| Fingerbank | Device type guessing | Cloud API sends data off the Pi (300 requests per hour free). The SQLite database is proprietary and several gigabytes. | Ignore |
| python-zeroconf. LGPL-2.1 or later. 0.151.5 (2026-09-28) | `mdnsname.py` | See "Where custom code is better", item 5. | Ignore |

### F. Browser and DNS proxy

| Candidate | Would replace | Fit and cost | Verdict |
| --- | --- | --- | --- |
| mitmproxy. MIT. 45k stars. v12.2.3 | `browser_proxy.py` (504) | Needs Python 3.12. Interception is a non-goal. Heavy. | Ignore |
| Squid. GPL-2.0. SQUID_7_7 | Same | Large. Can mark by ACL (not re-checked) but cannot read the plan file. | Ignore |
| tinyproxy. GPL-2.0. 1.11.3 | Same | Small, but no per-plan marks seen in its docs (not checked in detail). | Ignore |

### G. Speed, latency and loss measurement

| Candidate | Would replace | Fit and cost | Verdict |
| --- | --- | --- | --- |
| iperf3. BSD-style. 3.22 upstream, 3.18 Debian | Nothing (new) | Small apt package. Needs a port open to the test Wi-Fi. M | Adopt later (rec. 10) |
| LibreSpeed. LGPL-3.0. 15k stars. v6.3.0 (2026-09-12) | Nothing (new) | Browser test hosted on the Pi. Separate process. M | Adopt later (rec. 10) |
| irtt. GPL-2.0. v0.9.2 (2026-07-17). Debian 0.9.0-2 | Ping loops in `measure.py` (364) | Gives jitter, loss and one-way delay. Needs a client binary on the test device. M | Adopt later, optional |
| M-Lab ndt-server. Apache-2.0. v0.26.0 (2026-10-06). ndt7-js. Cloudflare speedtest (MIT, v1.14.1) | Nothing | Aimed at internet endpoints. | Ignore |
| flent. GPL-3.0. v2.3.0. SmokePing. GPL-2.0. icmplib. LGPL-3.0, no release since 2023 | Nothing | Lab tools. | Ignore |

### H. Dashboard, charts and exports

The owner's earlier GUI research (`gui-plan-draft-2026-10-03.md` in the root
checkout, untracked) already compared UI libraries. I did not redo that
analysis. I rechecked licences and release dates, and I checked uPlot's
published file for CSP-blocked patterns.

| Candidate | Would replace | Fit and cost | Verdict |
| --- | --- | --- | --- |
| uPlot. MIT. v1.6.32 | Hand-drawn canvas charts (about 420 lines) | See recommendation 9. M | Adopt later |
| Chart.js. MIT. v4.5.1. ECharts. Apache-2.0. 6.1.0 | Same | Larger, no gain for 5 series. | Ignore |
| Preact (MIT, 11.0.1), Lit (BSD-3-Clause), Alpine (MIT, v3.17.4), htmx (0BSD, v4.0.0), Svelte (MIT) | `dashboard.js` (9,539 lines of vanilla JS) | A rewrite of a 9.5k-line page is L. The earlier research found Alpine needs `unsafe-eval` and htmx expects HTML fragments. | Ignore |
| Pico CSS (MIT, archived), Open Props (last release 2023-09-29), Tailwind (MIT, v4.3.3) | `dashboard.css` (852 lines) | Would double or restyle the CSS. | Ignore |
| Lucide. ISC. 1.54.0 | Nothing (new) | 8 to 12 inline SVG icons as a second cue next to colour. | Trial (earlier research agrees) |
| prometheus node_exporter. Apache-2.0. v1.12.1 | Nothing (new) | Has `qdisc` and `netdev` collectors. | Adopt later as a documented optional export, not bundled |
| Grafana (AGPL-3.0), Netdata (GPL-3.0) | Nothing | Heavy. Copyleft. | Ignore |

### I. Updates, signing, packaging and release

| Candidate | Would replace | Fit and cost | Verdict |
| --- | --- | --- | --- |
| minisign. ISC. 0.12. age. BSD-3-Clause. v1.3.2. cosign. Apache-2.0. v3.1.3 | The `ssh-keygen -Y verify` step in `updates.py` | Already a standard tool. No gain. | Ignore |
| python-tuf. Apache-2.0. v7.0.1 | Release metadata, floor and signing logic (`updates.py` 4,572, `release-system.py` 869, `release_uploads.py` 422) | Needs Python 3.10. Built for repositories with many clients. | Ignore |
| RAUC (LGPL-2.1), SWUpdate (GPL-2.0), Mender (Apache-2.0) | The update manager | Image or A/B-partition updates. JitterSim updates an app on stock Raspberry Pi OS. | Ignore |
| nfpm (MIT, v2.47.0), fpm (MIT-style, v1.18.0) | Parts of the installer | A `.deb` only adds file tracking. The installer already handles wheels, units and rollback. | Ignore |
| Ansible. GPL-3.0 | `install.sh` (409 lines) | Adds a Mac-side dependency for little gain. | Ignore |
| pi-gen and rpi-image-gen (BSD-3-Clause, v2.8.0, 2026-08-13) | Nothing (new) | Could build a flashable image with JitterSim preinstalled for the planned spare-Pi acceptance run. L | Adopt later |

### J. Logging and observability

| Candidate | Would replace | Fit and cost | Verdict |
| --- | --- | --- | --- |
| journald (already used via syslog and `logging`) | Nothing | Keep. | Keep |
| structlog. MIT or Apache-2.0. 26.1.0 | `logging` use | Needs Python 3.10. No gain. | Ignore |
| Vector. MPL-2.0. v0.59.0. Rust | Nothing | Heavy. Nothing to ship logs to. | Ignore |

### K. Configuration, HTTP and storage

| Candidate | Would replace | Fit and cost | Verdict |
| --- | --- | --- | --- |
| pydantic (MIT, 2.14.0), msgspec (BSD-3-Clause, 0.22.0), jsonschema, cattrs | `config/models.py` (1,050), `engine/codec.py` (733) | All need Python 3.10. They would not remove the rules. | Ignore. Revisit if the 3.9 floor is dropped. |
| attrs. MIT. Python 3.9 or newer | Dataclasses | No gain. | Ignore |
| aiohttp (Apache-2.0), Starlette and uvicorn (BSD-3-Clause) | `api/server.py` (6,365) | Need Python 3.10 or 3.11. | Blocked |
| Flask (BSD-3-Clause, 3.1.3), Bottle (MIT, 0.13.4), waitress (ZPL-2.1) | Routing in `api/server.py` (roughly 126 `path ==` style checks by a rough `grep`) | Run on 3.9, but admission control stays custom. L | Ignore |
| SQLite migrations | The `_migrate_*` methods in `config/storage.py` (3,927 lines) | The file never sets `PRAGMA user_version`. A 40-line in-house runner using the stdlib is enough. No library needed. S | Adopt later, in-house |

### L. Certificates and names

| Candidate | Would replace | Fit and cost | Verdict |
| --- | --- | --- | --- |
| mkcert. BSD-3-Clause. v1.4.4 (2022-04-26) | `localca.py` (712) | No name constraints. Stale. | Ignore |
| step-ca. Apache-2.0. v0.30.2. Caddy. Apache-2.0. v2.11.7 | Same | Daemons. Caddy has no built-in name constraints (issue #5759). L | Ignore |
| Avahi over D-Bus (already used) | Nothing | Keep. | Keep |

### M. Testing, linting, security tooling and CI

| Candidate | Would replace | Fit and cost | Verdict |
| --- | --- | --- | --- |
| Ruff full rules. MIT. v0.16.10 | The 5-group CI lint | Dev-only. S | Adopt now (rec. 1) |
| shellcheck (GPL-3.0, v0.11.0, Debian 0.10.0), shfmt (BSD-3-Clause) | Nothing | Tool only. S | Adopt now (rec. 1) |
| actionlint (MIT, v1.7.12), zizmor (MIT, v1.30.1) | Nothing | Lint the three workflows. S | Adopt now (rec. 1) |
| pip-audit. Apache-2.0. v2.10.1. osv-scanner. Apache-2.0. v2.6.0 | Nothing | Check the hashed lock files. S | Adopt now (rec. 1) |
| pre-commit (MIT, v4.6.2), Makefile or just (CC0, 1.58.0) | Hand-run command lists in verification docs | S | Adopt now (rec. 1) |
| Hypothesis. MPL-2.0. v6.168.5 | Nothing | Dev-only. M | Adopt now (rec. 6) |
| mypy (MIT, tool run on 3.13 with `--python-version 3.9`), pyright (MIT) | Nothing | Gradual. Start with `commands.py`, `executor.py`, `panic_guard.py`, `access.py`. M | Adopt later |
| bandit. Apache-2.0. 1.9.4 | Nothing | Ruff `S` rules cover it. | Ignore |
| semgrep. LGPL-2.1. v1.180.0 | Nothing | Heavy. Rule licences not checked. | Ignore for now |
| schemathesis. MIT. v4.30.0 | Nothing | Needs an OpenAPI file. None exists. | Ignore until a schema exists |
| vulture (MIT) | Nothing | Dead-code report for a 41k-line tree. | Adopt later, optional |
| hadolint. GPL-3.0 | Nothing | Three small Dockerfiles. | Ignore |
| Self-hosted runner. MIT. v2.338.0. act. MIT. v0.2.89 | Billing-blocked hosted CI | See recommendation 2. S to M | Adopt now |
| Mininet (BSD-3-Clause), containerlab (BSD-3-Clause), Chaos Mesh (Apache-2.0) | `tests/kernel/lab.py` (98) | Far larger than the need. | Ignore |
| Playwright and axe-core | Already used | Keep. | Keep |

### N. Similar projects (ideas, not code)

| Project | Why it matters | Ideas to borrow |
| --- | --- | --- |
| boa (`jonathaneoliver/infinite-streaming-boa`). MIT. Go. Pushed 2026-10-07. 0 stars | The closest match found. Per-MAC rate, latency, jitter and loss on OpenWrt, plus radio control (channel moves, roaming tests). | A scripted "valley" pattern that steps a cap down and back up. Its comparison table and its "things that will mislead you" list. Its argument for why a proxy is a different instrument. |
| PiNC (`phuedx/pinc`). MIT. Pushed 2017-12-13 | A Pi access point where each device gets a profile of `tbf` plus `netem` | Confirms the per-device profile design. Old and Node-based. |
| `kriziw/netem`. MIT. Pushed 2026-10-09 | A Flask UI for `netem` WAN emulation | UI layout ideas for impairment controls. |
| Headless Pi impairment gist by Graunephar | A Pi that impairs Wi-Fi and Ethernet | Setup notes. No per-device control seen. |
| Network Link Conditioner, Charles, Facebook ATC (archived 2018), WANem, piem, WiFry, znail, Octobox | Named in boa's comparison table | Not checked by me. |
| OpenWrt LuCI and Pi-hole web | Router and DNS dashboards | Apply, confirm and revert workflow (from the earlier GUI research). Client groups. Ideas only, because of licence. |

## Open questions and owner decisions

1. Licence for JitterSim itself. Needed before the first vendored data file.
2. Python floor policy. Keeping 3.9 protects the Mac CLI and `measure.py`
   (the docs mention Python 3.9.6 on the Mac). The Pi side should run
   Python 3.13 on Trixie (inferred, not checked). Raising the Pi-side floor
   would unlock pydantic, aiohttp and structlog, but I found little benefit
   in them for this code and the maintainability plan says to preserve 3.9.
   Recommendation: keep 3.9.
3. Which machine hosts the self-hosted runner, and whether the owner accepts
   running branch code there.
4. Checks that need the Pi: `dnsmasq --version`, `cat /etc/os-release`,
   `systemd-analyze security` for five units, Unbound memory with a 200k-name
   list.
5. One trial job to learn whether self-hosted jobs start while hosted CI is
   billing-blocked.

## Sources

All pages were read on 9 October 2026. Repository facts came from the GitHub
API (`api.github.com/repos/OWNER/REPO` and `/releases/latest`).

JitterSim files read: `README.md`, `docs/architecture.md`,
`deploy/raspberry-pi/README.md`, `docs/planned-features.md`,
`docs/planned-fixes.md`, `ROADMAP.md`, `pyproject.toml`, `.github/workflows/tests.yml`,
`deploy/raspberry-pi/*.service`, and the modules named in the text.

Platform and policy:
- https://resources.github.com/actions/2026-pricing-changes-for-github-actions/
  (redirects to https://github.com/resources/insights/2026-pricing-changes-for-github-actions)
- https://github.blog/changelog/2025-12-16-coming-soon-simpler-pricing-and-a-better-experience-for-github-actions/
- https://packages.debian.org/trixie/network-manager
- https://packages.debian.org/trixie/python3
- https://packages.debian.org/trixie/python3-nftables
- https://packages.debian.org/trixie/python3-pyroute2
- https://packages.debian.org/trixie/unbound
- https://packages.debian.org/source/trixie/dnsmasq
- https://packages.debian.org/trixie/iperf3
- https://packages.debian.org/trixie/irtt
- https://packages.debian.org/trixie/shellcheck
- https://www.kernel.org/doc/html/latest/networking/mac80211_hwsim/mac80211_hwsim.html
- https://man7.org/linux/man-pages/man1/systemd-analyze.1.html
- https://www.netfilter.org/projects/nftables/manpage.html
- https://wiki.nftables.org/wiki-nftables/index.php/Scripting
- https://lists.thekelleys.org.uk/pipermail/dnsmasq-discuss/2021q3/015680.html
- https://github.com/NLnetLabs/unbound/pull/1196
- https://github.com/NLnetLabs/unbound/pull/1162

Traffic shaping and Wi-Fi:
- https://github.com/svinota/pyroute2
- https://github.com/svinota/pyroute2/blob/master/pyroute2/netlink/rtnl/tcmsg/sched_netem.py
- https://github.com/thombashi/tcconfig
- https://github.com/tylertreat/comcast
- https://github.com/alexei-led/pumba
- https://github.com/Shopify/toxiproxy
- https://github.com/tohojo/sqm-scripts
- https://github.com/magnific0/wondershaper
- https://github.com/RaspAP/raspap-webgui
- https://github.com/oblique/create_ap
- https://github.com/garywill/linux-router
- https://github.com/openwrt/openwrt
- https://github.com/python-sdbus/python-sdbus-networkmanager

DNS, lists and data:
- https://github.com/NLnetLabs/unbound
- https://github.com/0xERR0R/blocky
- https://github.com/AdguardTeam/AdGuardHome
- https://github.com/pi-hole/pi-hole
- https://github.com/pi-hole/FTL
- https://github.com/coredns/coredns
- https://github.com/StevenBlack/hosts
- https://github.com/hagezi/dns-blocklists
- https://github.com/blocklistproject/Lists
- https://oisd.nl/
- https://github.com/nextdns/metadata
- https://github.com/v2fly/domain-list-community
- https://github.com/publicsuffix/list
- https://publicsuffix.org/list/
- https://github.com/publicsuffix/list/wiki/Format
- https://github.com/ko-zu/psl and https://pypi.org/project/publicsuffixlist/
- https://github.com/john-kurkowski/tldextract
- https://github.com/netaddr/netaddr and https://pypi.org/project/netaddr/

Discovery, proxy and measurement:
- https://github.com/secdev/scapy
- https://github.com/royhills/arp-scan
- https://github.com/nmap/nmap and https://nmap.org/npsl/
- https://github.com/home-assistant-libs/netdisco
- https://github.com/netalertx/NetAlertX
- https://www.fingerbank.org/license
- https://github.com/python-zeroconf/python-zeroconf
- https://github.com/mitmproxy/mitmproxy
- https://github.com/squid-cache/squid
- https://github.com/tinyproxy/tinyproxy
- https://github.com/esnet/iperf
- https://github.com/librespeed/speedtest and https://github.com/librespeed/speedtest-go
- https://github.com/heistp/irtt
- https://github.com/m-lab/ndt-server and https://github.com/m-lab/ndt7-js
- https://github.com/cloudflare/speedtest
- https://github.com/tohojo/flent
- https://github.com/oetiker/SmokePing
- https://github.com/ValentinBELYN/icmplib
- https://github.com/chemelli74/aiovodafone
- https://www.home-assistant.io/integrations/vodafone_station
- https://pypi.org/project/arris-tg3442-reboot/

Dashboard, charts and exports:
- https://github.com/leeoniya/uPlot
- https://github.com/chartjs/Chart.js
- https://github.com/apache/echarts
- https://github.com/preactjs/preact
- https://github.com/lit/lit
- https://github.com/alpinejs/alpine
- https://github.com/bigskysoftware/htmx
- https://github.com/sveltejs/svelte
- https://github.com/picocss/pico
- https://github.com/argyleink/open-props
- https://github.com/tailwindlabs/tailwindcss
- https://github.com/lucide-icons/lucide
- https://github.com/prometheus/node_exporter
- https://github.com/grafana/grafana
- https://github.com/netdata/netdata

Updates, packaging and certificates:
- https://github.com/jedisct1/minisign
- https://github.com/FiloSottile/age
- https://github.com/sigstore/cosign
- https://github.com/theupdateframework/python-tuf
- https://github.com/rauc/rauc
- https://github.com/mendersoftware/mender
- https://github.com/sbabic/swupdate
- https://github.com/goreleaser/nfpm
- https://github.com/jordansissel/fpm
- https://github.com/ansible/ansible
- https://github.com/RPi-Distro/pi-gen
- https://github.com/raspberrypi/rpi-image-gen
- https://github.com/FiloSottile/mkcert
- https://github.com/smallstep/certificates
- https://github.com/caddyserver/caddy and https://github.com/caddyserver/caddy/issues/5759

Configuration, HTTP, logging:
- https://github.com/pydantic/pydantic
- https://github.com/jcrist/msgspec
- https://github.com/aio-libs/aiohttp
- https://github.com/encode/starlette and https://github.com/encode/uvicorn
- https://github.com/pallets/flask
- https://github.com/bottlepy/bottle
- https://github.com/hynek/structlog
- https://github.com/vectordotdev/vector
- PyPI JSON pages used for Python versions: https://pypi.org/pypi/NAME/json
  for pydantic, msgspec, aiohttp, flask, bottle, waitress, zeroconf, attrs,
  cattrs, jsonschema, hypothesis, netaddr, pyroute2, tcconfig, dnspython,
  publicsuffixlist, scapy and psutil.

Testing, linting and CI:
- https://github.com/astral-sh/ruff and https://github.com/astral-sh/ruff-pre-commit
- https://docs.astral.sh/ruff/rules/ (prefix `S` is flake8-bandit)
- https://github.com/koalaman/shellcheck and https://github.com/shellcheck-py/shellcheck-py
- https://github.com/mvdan/sh
- https://github.com/rhysd/actionlint
- https://github.com/zizmorcore/zizmor
- https://github.com/pypa/pip-audit
- https://github.com/google/osv-scanner
- https://github.com/pre-commit/pre-commit
- https://github.com/casey/just
- https://github.com/HypothesisWorks/hypothesis
- https://github.com/python/mypy and https://github.com/microsoft/pyright
- https://github.com/PyCQA/bandit
- https://github.com/semgrep/semgrep
- https://github.com/schemathesis/schemathesis
- https://github.com/jendrikseipp/vulture
- https://github.com/hadolint/hadolint
- https://github.com/nektos/act
- https://github.com/actions/runner
- https://github.com/mininet/mininet
- https://github.com/srl-labs/containerlab
- https://github.com/chaos-mesh/chaos-mesh
- https://github.com/dequelabs/axe-core
- https://github.com/microsoft/playwright-python

Similar projects:
- https://github.com/jonathaneoliver/infinite-streaming-boa
- https://github.com/phuedx/pinc
- https://github.com/kriziw/netem
- https://gist.github.com/Graunephar/8ff90eb0f41089046b54a6f4f9fe3429
