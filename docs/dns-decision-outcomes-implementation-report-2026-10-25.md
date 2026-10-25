# Actual resolver outcome delivery

The producer reads actual Unbound local-zone action and reply records through a
private inherited nonblocking pipe. An always_nxdomain action counts as a block
decision. An ordinary NXDOMAIN reply never proves policy blocking. NOERROR
means a DNS answer, not a successful website request. The counters are separate
observed lower bounds and are not a partition of captured query attempts.

## Scope and boundaries

The new config/dns_outcomes.py holds a current-epoch RAM store with at most 512
rows, 64 events per batch and a five-minute window. There is no new database
schema, durable browsing history, raw query log or aggregate file in /run.
Restart and expired windows return unknown. Existing query counts and saved
block-list membership keep their meanings. Uninstrumented rows have
`dns_outcomes: null`. Coverage is always partial with unknown sender loss.

The new networking/dns_decisions.py parses at most 4096 bytes per read and
1024 bytes per line. Oversized or partial lines cannot be reinterpreted after a
reset. The supervisor drains even when the dashboard disappears or refuses
forwarding. It never waits for the dashboard's socket to become writable.
SIGTERM stops and reaps the actual resolver child. Service control-group cleanup
remains the fallback for uncatchable process death.

networking/dns_decision_ipc.py uses a private Unix sequence-packet socket with
1024-byte packets. The root producer is bound to the root-owned PID/session
record, kernel credentials and a fresh receiver nonce. The producer drains
pre-acknowledgement pipe bytes and discards parser fragments before acknowledging
an epoch. Wrong credentials, nonce, session, generation, replay, truncation and
unexpected passed descriptors are refused. Received descriptors are closed.

Local root is trusted and already controls resolver configuration. The PID record
binds the expected service rather than treating an arbitrary privileged packet
as a producer. The private directory is provisioned by the root service as the
dashboard user with mode 0700. The receiver validates it, uses a nonblocking owner
lock and never creates or changes the helper's root-owned parent. Shutdown only
unlinks the socket inode this receiver created. The root forwarder checks the
socket's configured user, mode and directory before connecting.

The dashboard polls a finite batch during authenticated Traffic reads using the
same fresh helper snapshot. There is no additional helper polling worker. Only
unique verified reachable unprotected MAC/IP identities enter aggregates. Unknown,
stale, changed or ambiguous identities are dropped. A configuration change clears
RAM and partial source state. That producer session is suspended until an actual
service restart, so old kernel packets cannot become a new privacy epoch.

ConfigRepository checks configuration version, current saved protection and
current access-policy digest under BEGIN IMMEDIATE with a zero SQLite busy wait.
This also fences a second repository process. Every API read checks current
protection and identity again. Telemetry does not change desired settings or
configuration version. A busy repository drops telemetry rather than delaying
network control.

## Executed evidence before freeze

Native Python 3.12: 39 tests passed and three Linux credential tests were skipped.
The same two new test modules ran in the actual isolated Linux runtime: 42 tests
passed with no skips. The image lacked pytest. Two setup attempts failed before
tests started, then cached pure Python test dependencies were mounted read-only.
Nothing was installed or downloaded and no shared runtime was changed.

The genuine source and lifecycle fixture uses cached Unbound 1.17.1 in image
8bafe59b5ebe on the dedicated Docker context. The container used no network or
capabilities, a read-only root/source, one CPU, 256 MB and a 32 MB temporary
filesystem. The actual shared heavy Docker slot was held with lockf and only the
owned container was cleaned. Source proof covered:

- Explicit policy action, real UDP/TCP answers, a cache answer and ordinary
  nonexistent names with no fabricated blocked classification.
- A stalled/full pipe with 500 DNS replies still delivered, reader process
  death, closed readers and SIGPIPE without killing the resolver.
- Nonblocking descriptor flags after exec and reload, plus a loaded generation
  in each event identity.
- The actual supervisor, root credential/nonce handshake, rejection of unknown
  loopback identity and another 500 DNS replies after collector shutdown.
- SIGTERM reaping the actual Unbound child, PID record cleanup and no raw
  supervisor output.

The test fixture intentionally uses loopback queries. It proves actual resolver
production and process safety. Synthetic 192.168.50 identities separately test
aggregation, privacy and API behavior. No physical Pi or home network was used.

The source fixture is tests/qa/dns_decision_source.py. Exact commands and output
are retained in test-results/dns-decision-source/, including the failed setup
receipts. The final pre-freeze Linux receipt is final-linux-cached-support.log.
Focused existing backend/API tests and exact-head receipts follow code freeze.

## Coordinated source hooks

The separately owned resolver/service commit must render log-queries no,
log-replies yes, log-local-actions yes, log-tag-queryreply yes, no syslog/file
logging and `jittersim-dns-<actual plan digest>` as its identity. Its DNS service
launches this module's supervisor, provisions the private child directory and
keeps the helper parent ownership intact. It supplies control-group cleanup and
write access to the root PID record's runtime directory. No pinned helper change
is needed. These hooks require independent review with this bridge before use.
No installation, signing, merge to the release branch or real device action is
included in this backend work.
