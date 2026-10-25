# Keep live traffic history across counter restarts

Base: b061d2d8 (current origin/main). The installed 1.0.0 source at
2b101abd has the same graph sampling code. PR252 and dirty original checkouts
remain separate. No repository AGENTS.md or relevant local SKILL.md was found.

The graph currently clears its entire history if any selected byte or packet
counter decreases. Counter replacement can be genuine; the graph must not
invent a rate across it or erase earlier valid measurements. The actual cause
of repeated decreases on the owner's Pi is not verified by local fixtures.

One writer owns dashboard.js and focused chart unit/browser tests. Keep the
existing sampling cadence, sign-in, pause/resume, selector resets and clock
reversal handling. Calculate each direction/packet delta independently; a
negative delta is unavailable for that interval, never zero. Keep bounded
history and null-valued gaps, render unavailable values explicitly, and resume
from the new baseline. A packet-only restart must not discard valid byte rates.

Verify regression failure on unchanged source, focused chart/assembly/budget
units, lint and the affected Chromium journey using local synthetic fixtures
and one shared browser wrapper slot. Self-review and record exact source and
receipts. No API/helper/networking/package/version changes, deployment, merge,
signing or reserve reset. Stop at the supported 2% allowance boundary.
