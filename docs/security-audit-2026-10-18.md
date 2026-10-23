# Security audit of main (2026-10-18)

Base: `main` at `8c3f8171` (the newest main on 18 October). Branch: `chore/security-audit-2026-10-18`.
Method: read the code of each area, check what the tests and earlier reviews already pin, then
prove every finding with a small reproduction on this Mac only (unit tests, socket pairs and the
in-process QA fixture of `tests/qa/dashboard_support.py` on 127.0.0.1). Nothing touched the Pi,
the router or any other host. Severity: S3 = auth bypass, secret exposure, code execution from
the Wi-Fi, irreversible loss or the household cut off. S2 = wrong result, a feature or the
dashboard denied, a broken contract. S1 = hardening.

## Summary

| ID | Sev | Area | Finding | State |
| --- | --- | --- | --- | --- |
| A1 | S2 | Hostile Wi-Fi device | The DNS watcher learns service list addresses from answers of any DNS server a client asks, so one test client can put any public address into a list that every affected device shares | **Fixed** (`networking/dns_watch.py`). A review of that fix found a second route, closed by follow-up F1 below (`jittersim-control-firewall.nft`). Known limit F4 below |
| A2 | S1 | Availability before sign-in | Each unauthenticated `GET /api/health` is one call to the root helper | **Fixed** (`api/server.py`) |
| A3 | S1 | Availability before sign-in | The browser proxy reads a CONNECT request with a timeout per read, so a client that trickles bytes holds a tunnel slot for good | **Fixed** (`networking/browser_proxy.py`) |
| A4 | S1 | Hostile web page | No `Cross-Origin-Opener-Policy` and no `Cross-Origin-Resource-Policy` | **Fixed** (`api/server.py`) |
| A5 | S2 | Availability before sign-in | The browser proxy has 32 tunnel slots for every device together. One device can hold all of them with ordinary idle tunnels | **Fixed** by follow-up: at most 16 for each client address (`networking/browser_proxy.py`) |
| A6 | S2 | Local process on the Pi | Any process of the `jittersim` user (the login user and every service) can call every helper action and can read the admin token from the dashboard's environment | **Partly fixed**: the helper accepts `dashboard-access local-add` and `local-remove` only from root (`networking/helper.py`, peer uid check). Other actions stay open to that user because the dashboard needs them, and the token in the environment is still open |
| A7 | S1 | Hostile web page | The dashboard accepts any `Host`. DNS rebinding reaches only the public page and health check, and the Origin check of the recovery routes compares the Origin with that same Host | Open, owner decision |
| A8 | S1 | Availability before sign-in | The homepage policy listener (port 8443) has 4 slots and no per-device share | Open, accepted (experimental feature) |
| A9 | S1 | Secrets | The admin token can be remembered in `localStorage` for a chosen number of hours | Accepted: opt-in, and the strict hash CSP blocks injected script |
| A10 | S1 | Headers | `img-src 'self' data:` | Accepted: a `data:` image cannot run script |

No S3 finding. The unauthenticated surface is small and well bounded: `/`, `/favicon.ico` and
`/api/health` answer without the token, and everything else checks the bearer token before the
route is looked at.

## Threat model

| Attacker | Can do | Must not be able to |
| --- | --- | --- |
| (1) A device on the test Wi-Fi | Any MAC, host name and DHCP options. Any HTTP to `192.168.50.1:8080`, `:8443` and the proxy on `:3128`. mDNS on the test Wi-Fi (AP isolation is on by default). Ask any DNS server on the internet while household DNS rules are off | Use the dashboard or the helper without the token, change what other test devices get, take Panic away, cut off the household network |
| (2) A web page in the owner's browser | Cross-origin requests, forms, `window.open`, DNS rebinding of its own name to the Pi | Read or change anything that needs the token |
| (3) A local process on the Pi | Whatever its user may do | Without the `jittersim` user or group: nothing. With it: see A6 |
| (4) A malicious release or update | Any bytes in the archive, the signature or GitHub's answers | Be staged without a trusted signature, go below the installed sequence, run code outside the Panic hash gate |
| (5) The Mac scripts | Hostile file names, refs and hosts given on the command line, a changed Pi host key | Run shell code from arguments, send the token to the wrong host, show it in a process list |

## Area verdicts and evidence

### 1. A hostile device on the test Wi-Fi

- **Dashboard without the token.** `ControlRequestHandler.do_GET` and `_handle_write`
  (`api/server.py`) refuse every `/api/` path except `/api/health` with 401 before routing.
  Probe on the fixture: `/API/state`, `/%61pi/state`, `//api/state`, `/api/./state`,
  `/api/state/` all answer 401 or 404 without data. Token variants (`bearer` in lower case is
  accepted, a trailing space, a double space, `Basic`, a NUL byte, a prefix of the token) are
  refused. The comparison is `hmac.compare_digest` on bytes (`_authorization_matches`).
- **Device allowlist.** A device can copy the MAC of a device that may use the dashboard. That
  only passes the second check: the token is still required. Known limit of MAC identity.
- **A1 (fixed).** `networking/dns_watch.py` reads every outgoing frame on `wlan0` from UDP port
  53 and its docstring said a client cannot forge one. But while household DNS rules are off the
  Pi forwards any DNS answer a client asked for from another server, and that answer also leaves
  `wlan0` from port 53. Service list sets are shared by every affected device
  (`renderer.py`, `ip daddr @<list>` in the forward chain, and the outage rule drops it).
  Reproduction on main `8c3f8171` (scratch script, the watcher of main loaded from `git show`):

  ```
  w.observe(frame_for(dns_message("www.netflix.com", [a_record(..., "8.8.8.8")]),
                      source="203.0.113.53"), OUTGOING)
  main 8c3f8171 sends to the helper: [{'streaming': [IPv4Address('8.8.8.8')]}]
  ```

  Consequence: a test client points `www.netflix.com` at `8.8.8.8` (or any public address) on a
  DNS server it runs, asks it directly, and from then on every other affected test device has
  that address shaped like the streaming list, or dropped while the list has a random outage.
  Fix: an answer counts only when its IPv4 source is the Pi's own address `192.168.50.1`, or
  while the published access policy has DNS rules (`AccessPlan.has_dns_rules`). Then the access
  table redirects every test Wi-Fi lookup to the Pi's resolver and drops direct DNS and DoT
  (`networking/access.py`, the `dns` and `forward` chain rules), so every answer that the Pi
  makes comes from the Pi's resolver, even when it carries the address the client asked. Ad
  blocking rewrites only lookups sent to `192.168.50.1`, so those answers come from it too. An
  unreadable policy trusts only `192.168.50.1`. Tests: `tests/unit/test_dns_watch.py`
  (`ForwardedAnswerTests`, `RedirectFileTests`). They fail on main.

  This fix alone did not close A1 (follow-up F1, found when PR 159 was reviewed). "Every answer
  that leaves comes from the resolver" holds only for packets the Pi makes. The Pi also forwards
  packets that a client makes. Those leave `wlan0` as well, and the access table's rules match
  destination port 53 only. While household DNS rules are on, a client can send a UDP packet
  from its own address with source port 53 to itself or to another device through the Pi. It is
  forwarded `wlan0` to `wlan0`, the watcher sees an outgoing frame from port 53 and trusts every
  source while the rules are on. The watcher cannot tell such a packet from a forwarded answer.
  While the rules are off the watcher trusts only the source `192.168.50.1`, so this route needs
  the rules on (a packet that arrives on `wlan0` with the Pi's own address as its source is a
  martian packet that the kernel drops by default: from the kernel documentation, not run here).
  F1 below closes the route in the forward chain of the management firewall, for both states.
- **mDNS spoofing.** AP isolation keeps one test client's mDNS from the others. On the household
  network anyone can answer for `netlab.local`, but the browser then needs a certificate from the
  Pi's name-constrained CA, so the token is not sent unless the owner clicks through a warning.
- **DHCP host names** are shown with `textContent` only. The two `innerHTML` uses
  (`access.js`, `workflows.js` `workflowCard`) take fixed markup.
- **Browser proxy SSRF.** `_resolve_public_addresses` keeps only `is_global` addresses and the
  connection uses the resolved address, so `CONNECT localhost:443`, the Pi, the router or a
  rebinding name cannot reach a private address. Only port 443 is accepted.

### 2. A hostile web page

- **CSRF.** Every write needs `Authorization: Bearer`. A cross-site form or `fetch` cannot set it
  without a CORS preflight, and `OPTIONS` answers 405 with no CORS headers. Probe: a cross-site
  `text/plain` POST to `/api/normal-access` without the token gets 401.
- **DNS rebinding (A7).** The dashboard does not check `Host`. Probe: `GET /` with
  `Host: evil.example:8080` answers 200 (the public page) and `GET /api/state` with a foreign
  Host and Origin and the token answers 200. A rebinding page has no token (the token lives in
  the dashboard origin's storage or in the owner's head), so it reaches only `/` and
  `/api/health`. The extra Origin check of the router, homepage and dashboard-access writes
  compares the Origin with the request's own Host, which a rebinding page controls, so it adds
  nothing against rebinding. Proposed (owner decision because a wrong list locks out an address
  the owner uses): accept only the configured local name, `pi.home.arpa`, `192.168.50.1`, the
  Ethernet address and `localhost` as Host, or at least compare Origin with that list.
- **Homepage extension channel.** `extensions/homepage-bounce/background.js` accepts messages
  only from its own popup (`sender.url`), has no `externally_connectable` and talks only to
  `https://netlab.local:8443`. The policy server (`api/homepage.py`) answers CORS only for a
  `chrome-extension://` origin with a 32-letter id, needs a one-time 128-bit pairing code bound to
  that id, then a 256-bit capability, and checks the Host, a single Origin and a JSON body of at
  most 8 KiB. Nothing to fix.
- **A4 (fixed).** Responses now carry `Cross-Origin-Opener-Policy: same-origin` (a site that opens
  the dashboard keeps no handle on it) and `Cross-Origin-Resource-Policy: same-origin` (no other
  site can embed a dashboard answer). Test: `CrossOriginHeaderTests`.

### 3. A local process on the Pi (A6, known)

`/run/jittersim` is `0750 root:jittersim`, `helper.sock` is `0660`, the database folder is
`0750 jittersim`, `control.env` is `0600 root`. A process of another user reaches nothing.
But the `jittersim` user is both the Raspberry Pi Imager login user (`install.sh` requires it,
`deploy-from-mac.sh` logs in as `jittersim@`) and the user of the dashboard, the browser proxy
and the DNS watcher. Every such process can:

- call every helper action, including `dashboard-access local-add` (`helper.py`
  `_handle_dashboard_access`, no peer check) and the update actions. This is finding 5 of
  2026-10-09 and the proposal in `design-helper-action-authorization-2026-10-09.md` is still
  unimplemented;
- read the admin token from `/proc/<dashboard pid>/environ`, because systemd passes
  `control.env` as environment (`JITTERSIM_ADMIN_TOKEN`) and the same user may read it. This
  part is new to the record. Shown from the code, not run on a Pi.

**Status (2026-10-23): applied in a narrower form.** The helper reads the caller's user id
with `SO_PEERCRED` and refuses `local-add` and `local-remove` with reason `root-required`
unless it is 0. A caller whose id cannot be read is refused too. `local-list` is not
checked: review note F3 found that the dashboard (user `jittersim`) reads it in
`api/server.py`, so refusing it would break the dashboard. No other action changes,
Panic included. Tests: `tests/unit/test_helper_peer_check.py`. The proposal below is kept
for the record.

Original proposal for the pinned `src/jittersim/networking/helper.py`. It is option 2 of the
design note and closed `local-*` against every caller that is not root:

```diff
@@ class HelperRequestHandler(socketserver.StreamRequestHandler):
         action = payload.get("action") if isinstance(payload, dict) else None
+        if (action == "dashboard-access"
+                and payload.get("operation") in ("local-add", "local-remove", "local-list")
+                and _peer_uid(self.request) != 0):
+            # Only the root CLI on the Pi may change the saved device list directly.
+            self._send_response({"ok": False, "error": "Run this as root on the Pi."})
+            return
         # The request is complete. Only now does it take an operation slot.
@@
+def _peer_uid(connection) -> Optional[int]:
+    """The user id of the process on the other end of a Unix socket, or None."""
+    try:
+        raw = connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
+    except (AttributeError, OSError):
+        return None
+    return struct.unpack("3i", raw)[1]
```

(`import struct` added at the top.) Tests to add with it: a fake connection whose `getsockopt`
returns uid 1000 is refused `local-add`, uid 0 is answered, and `status`, `register-self` and
`protected-sync` from the dashboard's uid still work. For the token, the lasting fix is option 4
of the design note (separate service users) plus a token file the dashboard reads at start
instead of an environment variable. Both change `install.sh` and units, so they belong to that
work, not to this audit.

### 4. A malicious release or update artifact

Verified, nothing to fix: `stage_release` (`updates.py`) copies the archive and signature into a
private folder, runs `ssh-keygen -Y verify` with a fixed `PATH` on that copy, then matches the
key inside the signature against the trusted list, before any byte of the archive is parsed.
Expansion is capped before `tarfile` reads metadata. `validate_archive` refuses absolute paths,
`..`, links, devices, unknown top-level names, duplicates after normalisation and a file with
entries below it. Every member is written by `extractfile` into the private folder, never with
`extractall`. The sequence must be above the high-water mark (`release_floor` keeps the highest
ever installed, a rollback does not lower it). The online check sends the GitHub token only to
`api.github.com` and drops it on every redirect (`_download`), refuses redirect targets outside
the asset hosts, and takes exactly two assets of a final release. Uploaded releases
(`release_uploads.root_inputs`) take a 32-hex identifier and open every file relative to
directory descriptors. The Panic hash gate (`PANIC_APPROVED_SOURCES`) was reviewed separately
and is not repeated here.

### 5. The Mac scripts and the remote CLI

Verified, nothing to fix: `deploy-from-mac.sh` quotes every remote word with its own `quote`,
passes `--` before the host, uses `BatchMode=yes` and the user's `known_hosts` as it is (an
unknown or changed host key fails, it is never accepted silently) and `IdentitiesOnly=yes`.
`deploy-over-https.sh` keeps the token out of the environment of git and the build and gives it
to `curl --config -` on standard input, refuses redirects and proxies, trusts only `--ca-cert`,
and checks the token's characters. `--save-token` lets the Keychain prompt for the token (`-w`
last). `mktemp -d` with `trap` cleanup everywhere. No `curl | sh`, no `eval`. `shellcheck -S
warning` reports only `CDPATH= cd` (intended) and `test -O` (the scripts run on macOS). The
remote CLI (`remote_client.py`) accepts only `https://HOST:PORT`, trusts only the given CA file,
refuses redirects, ignores environment proxies, and refuses a config or CA file that another
user can write.

### 6. Secrets

Verified: the admin token is 256 bits from `openssl rand -hex 32`, stored `0600 root`
(`install.sh`), compared in constant time, never logged (the request log has no path or address,
and `tests/qa/api_support.py` fails a run that finds the token in a body, a header or a log line),
never in a URL or an export (probe: `/api/export` does not contain it). The GitHub token is
`0600 root`, written in one step and refused when another user may read it. The CA key is `0600
root` and the CA is name-constrained. Router passwords come with each request and stay in memory only
for the router operation that uses them. Operations are matched by an HMAC with a random
per-process key, not by the password. Open: A6 (environment) and A9.

### 7. Input validation of the API

Already pinned by `tests/qa/test_api_robustness.py` and `test_api_known_defects.py` (every route
fuzzed: digit limits, lone surrogates, lists as identifiers, integers beyond SQLite) and by the
body limits in `_read_json_body` (64 KiB, 1 MiB for imports, a Content-Length is required, JSON
objects only). Identifier routes (`/api/device-workspace/<id>`, `/api/test-sessions/<id>`,
`/api/updates/check-online/<id>`) look up the database, not files. SQL: every value is a
parameter; the f-string statements in `config/storage.py` use fixed table and column names only.
Downloads use fixed file names. Nothing new found.

### 8. CSP and headers

`default-src 'none'`, hash-only `script-src` and `style-src` (no `unsafe-inline`, no
`unsafe-eval`), `connect-src 'self'`, `base-uri 'none'`, `form-action 'self'`,
`frame-ancestors 'none'`, plus `X-Frame-Options: DENY`, `nosniff`, `Referrer-Policy:
no-referrer`. API answers are `no-store`, the page is `no-cache` with an ETag. Added A4. HSTS is
left out on purpose: the dashboard is also reached by IP address, where browsers ignore it.

### 9. Availability before sign-in

- **Dashboard workers.** Without the token an attacker can keep the 16 ordinary workers busy
  (PR 123 states this as accepted). The 2 Panic workers take only a whole request for
  `/api/normal-access` with a valid token, and eviction takes from the peer with the most waiting
  connections. Pinned by `tests/integration/test_capacity_stalled_clients.py` and
  `tests/unit/test_dashboard_pending_queue.py`, which pass on this head. No new hole found.
- **A2 (fixed).** Probe on the QA fixture, 16 threads for 6 s without the token:
  `12785 health requests -> 12785 helper pings`. The helper's own reserve kept ordinary reads
  answering on the Mac (503 of 503), so no starvation was shown, but on a Pi 3B+ it is root CPU on
  demand of any test device. Now an answer without the token reuses the last helper answer for 1 s
  and only one such ping runs at a time. With the token every request still asks the helper, so
  the update health check sees the live state. Tests: `PublicHealthReuseTests`.
- **A3 (fixed).** `_read_connect_request` set a 10 s timeout per `recv`. A client sending one byte
  every few seconds held a slot until 72 KiB arrived. Test `ProxyRequestDeadlineTests` (0.5 s
  deadline, one byte every 0.1 s): on main the read did not end for over 5 minutes, now it ends at
  the deadline.
- **A5 (fixed by the follow-up PR).** The proxy had 32 slots shared by all devices and no
  per-device share. A tunnel stays open while it carries a byte every 300 s. One device could
  therefore hold every slot with 32 ordinary `CONNECT example.com:443` tunnels, and A3 does not
  change that. Now each client address holds at most 16 of the 32 slots (a browser normally needs
  far fewer), counted from accept to close, so a connection that has not sent its request yet
  counts too. The 17th connection of one address is closed without an answer, the same way a
  full proxy refuses, and logs `browser tunnel rejected: one device already holds its 16 of 32
  tunnel slots` (no address, MAC address or request text, at most once in 30 s). A slot comes
  back when the connection ends, fails or cannot start a worker. Behind a second router on the
  test Wi-Fi, all of its devices share one address and one share. Tests:
  `ProxyClientShareTests` and `ProxyClientShareSocketTests`.
- **Helper pool.** Incomplete input never holds an operation slot, and one kept slot serves only
  Panic and its emergency device check (`_uses_panic_reserve`). The browser proxy shares one
  in-flight `clients` scan. Nothing new found.
- **Database.** Observations are bounded (20,000 in all, 50 per device) and only the owner's
  token starts a scan.
- **A8 (accepted).** The homepage listener serves 4 connections of at most 0.75 s each, so a
  flood delays only the homepage test feature.

### 10. Newer work

PR 138 (carry-forward Panic batch), PR 152 and PR 154 on main were covered by the areas above.
No open draft was audited.

## Fixes in this branch

| ID | Files | Test |
| --- | --- | --- |
| A1 | `src/jittersim/networking/dns_watch.py` (`frame_source`, `ServiceWatcher._trusted_source`, `_RedirectFile`, `--access-policy`) | `tests/unit/test_dns_watch.py` |
| A2 | `src/jittersim/api/server.py` (`health_response`, `PUBLIC_HEALTH_REUSE_SECONDS`) | `tests/unit/test_security_audit_2026_10_18.py` |
| A3 | `src/jittersim/networking/browser_proxy.py` (`_read_connect_request`) | same |
| A4 | `src/jittersim/api/server.py` (`_send_response`) | same |
| A5 (follow-up PR) | `src/jittersim/networking/browser_proxy.py` (`_ClientSlots`, `BrowserProxyServer.process_request`) | same |
| F1 (follow-up PR) | `deploy/raspberry-pi/jittersim-control-firewall.nft` (forward chain, `udp sport 53 counter drop`) | `tests/unit/test_dashboard_name_deploy.py` (rule text), `tests/kernel/test_access.py` (packets) |

No Panic-pinned or test-pinned file was changed.

## Follow-ups from the review of PR 159

- **F1 (fixed): a client packet from port 53 passes the forward chain.** See A1 above for the
  route. The management firewall now drops `iifname <test interface> udp sport 53` in its forward
  chain (`counter drop`, comment `deny forged DNS answers from test Wi-Fi`). It does not touch an
  answer a client is meant to get, because those never take this path: the Pi's own resolver
  answers from the output chain (also when household DNS rules redirect the lookup to port
  5335), and an answer from the router or the internet arrives on the uplink, where the rule's
  `iifname` does not match. A device that sends from source port 53 on purpose,
  such as a DNS server run on a test device for the internet, no longer works through the Pi. The
  kernel lab test sends one datagram between the two lab clients through the Pi: it arrives from
  an ordinary source port and is dropped from port 53, and the Pi's own resolver still answers a
  client.
- **F4 (known limit): hard-coded DNS loses service list learning while household DNS rules are
  off.** With the rules off, the watcher trusts only answers from `192.168.50.1`. A device that
  asks a DNS server of its own choice (a fixed `8.8.8.8` in its network settings, or an app with
  its own resolver) gets answers from that server, which the watcher ignores on purpose (A1). The
  device is still shaped by the addresses that other devices and the Pi's own lookups taught
  the list, but its own answers add nothing. With household DNS rules on, every
  lookup goes through the Pi's resolver, so the device is learned from again. This is written in
  the user guide under Service lists.

## Residual risk

- A6 stays the largest risk: a code-execution bug in the browser proxy or the DNS watcher (both
  parse test Wi-Fi traffic) or anything run as the login user controls the helper and can read the
  token. The fix is the user split of the 2026-10-09 design note plus the patch above.
- A1 relies on the published access policy telling the truth about the redirect. If writing the
  policy succeeded but the nft change failed, forwarded answers would count again until the next
  apply. It also relies on the management firewall rule of F1: without it a client packet from
  port 53 counts as an answer while the rules are on. Direct DNS over TCP is not read at all, so
  it cannot add addresses.
- MAC identity can be copied. The token, not the device list, is the authority.
- A7 waits for an owner decision.
