# Resolver event source and current limits

Actual blocked counts require an event from the resolver's decision path. The
existing question scan and a current block-list match do not provide that event.
No model, storage or API counters should be presented as actual decisions until
a producer is demonstrated with an actual isolated resolver.

The allocated base is b061d2d8 and the implementation plan is 35b128c2. This
investigation does not change network policy or any pinned source file.

## Evidence from the official resolver source

- Unbound's client-response dnstap function exports the response packet,
  addresses, ports and timestamp. It does not export the local-zone action or
  the selected view. Reply codes alone cannot distinguish policy blocking from
  ordinary nonexistent names.
- Python's local-reply callback identifies a local reply but does not receive
  the local zone or action. The always_nxdomain path supplies no EDE reason.
  Joining that callback against a block list would still infer the decision.
- log-local-actions exports the actual local zone type, client address and
  query. This is an explicit decision source. Its ordinary logging path uses
  syslog or synchronous file output, so enabling it in the existing service
  would introduce raw names into shared logs and may delay query processing.
- A private inherited nonblocking stderr pipe could carry those same events
  without disk or journal retention. A finite collector could discard protected
  clients before retention. This would still put protected names transiently
  in that pipe. It needs a clear privacy decision and proof of nonblocking
  behavior before adoption. Missing/overflowed events must lower coverage.

Sources inspected on 25 October 2026:

- https://unbound.docs.nlnetlabs.nl/en/latest/manpages/unbound.conf.html
- https://raw.githubusercontent.com/NLnetLabs/unbound/master/dnstap/dnstap.c
- https://raw.githubusercontent.com/NLnetLabs/unbound/master/services/localzone.c
- https://raw.githubusercontent.com/NLnetLabs/unbound/master/util/log.c
- https://raw.githubusercontent.com/NLnetLabs/unbound/master/pythonmod/examples/inplace_callbacks.py

## Proposed source contract if the private pipe is accepted

The producer process starts with a fixed applied-policy generation and a unique
process session. Events carry that session, sequence, generation, client IPv4,
normalized domain, timestamp, transport when known and an explicit source kind.
Only actual always_nxdomain action events produce policy_blocked. Reply lines
can separately produce dns_answer, dns_nxdomain or dns_error. Website delivery
success is never claimed. Query retry attempts stay separate from outcome
records. No answer produces unknown rather than blocked.

Read buffers, maximum line length, queued event count, input per poll, dedupe
history and output batch size must all be finite. The producer write endpoint
must be nonblocking and remain nonblocking across execution and reload. Queue
loss, input truncation, missing producer, restarts and policy changes invalidate
coverage instead of inventing complete counts. Raw lines must never be logged
or saved. Protected-address filtering must happen before aggregate persistence
and on every authenticated API read. Protection changes must purge retained
history and queued events before any newly protected data can be written.

The current local machine has no Unbound binary at PATH or normal Homebrew
locations. No source proof has yet been executed and no substitute fixture is
being called an actual resolver decision. A cached isolated resolver runtime
and acceptance of the transient private stream are prerequisites.
