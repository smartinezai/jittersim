# Local accessibility test engine

`axe.min.js` is the unmodified axe-core 4.13.0 browser test engine. It is loaded
only by the test runner. The dashboard wheel and deployed dashboard do not
include it. No runtime request to a third-party host is needed.

The upstream file is covered by MPL-2.0 in `LICENSE.txt`. Preserve the notices
in `LICENSE-3RD-PARTY.txt` too. `provenance.json` records the source, published
archive and integrity checks.

When updating the engine, review the upstream release, replace the file and
both license notices together then update the expected version/hash in the
runner. Repeat the report integrity tests and complete browser matrix. Existing
findings are observations. Missing states or broken scans must fail.
