# Exact updater trust bridge

## Plan and source boundary

Start from `b061d2d84439bf906f1da8ca9ae306cfa0b27f52`. Change only
`src/jittersim/updates.py` in production: retain the eight existing complete
`PANIC_APPROVED_SOURCES` entries and append one complete reviewed bundle.
The bridge's actual `panic_guard.py`, `networking/helper.py` and
`networking/access.py` remain byte-identical to that baseline. The running old
manager can therefore admit this bridge; the final package cannot self-authorise
its own initial admission.

The final networking source was frozen/reviewed at `d605cf19`; its access file
matches the clean integrated checkout at `e61469f6` when this bridge was prepared.
The exact approved final bundle is:

| File | SHA256 |
| --- | --- |
| `src/jittersim/panic_guard.py` | `041cf171875d7b85d1c0664c2acff262542a3ac3c2b2e8fe51f80d2d59d6d639` |
| `src/jittersim/networking/helper.py` | `f1d0e704b8ad61b4ef677e25d4b1c3f40ea1e16dfceb67246568f8ca35366587` |
| `src/jittersim/networking/access.py` | `5c335f3b72b90e8eb7bff3cef135fe4f6986402fa529261f1af0ebe53eb4a219` |

The bridge still ships the old access digest
`41a0f6f96cf2a20616d7ac613dd95acf2cd31085a13018ce14667dea2e11e1f3`.
Labels, versions and lineage do not replace these exact three-file checks.
No gate implementation, dynamic learning, wildcard, authentication surface,
version, reservation, package, signing, installation or remote operation changes.

## Review and validation procedure

Freeze the updater/test source for independent review before executing focused
checks. New self-contained tests require an unchanged canonical digest of the
first eight entries, one exact ninth entry, and unchanged guard bytes when clear
or latched. Existing source-admission tests exercise the actual apply refusal
before service stops and the after-stop reread/intent rules.

Repeat `/tmp/js-pinned-bridge-proof.py` using actual Git blobs from `b061d2d8`,
this real bridge updater, and final `d605cf19`. Unlike that earlier provisional
proof, do not extend an allowlist in memory. Prove that the real old manager
rejects the final source and accepts the old-consumer bridge; the real bridge
manager admits old, bridge and final sources; inspect/provision leaves established
clear and latched guard bytes unchanged. Record exact manager/source digests,
commands and results. This proves source admission mechanics, not two signed
installations or physical acceptance.

Prior mechanism evidence `/tmp/js-pinned-bridge-review.md` and its provisional
`82cf49e5` tuple are historical only; that provisional tuple is not added here.
## Executed focused checks

Root independently cleared the six-line production tuple addition before the
checks below. The frozen source/test commit is
`966fa89c69622c053913c4930d4665717f28ce9e`, tree
`18e3ea2ed62c4bb583d3b3aab5ea975ab82bec15`. Only evidence documentation was added
after those checks; updater and test bytes remain unchanged.

The private installed Python 3.12 updater matched that frozen source byte for
byte. The shared wrapper ran:

```sh
PIP_NO_INDEX=1 PIP_FIND_LINKS=/tmp/jittersim-fixture-build-wheels \
  /private/tmp/claude-501/jittersim-heavy.sh rerun --python 3.12 --no-advisory \
  --results-dir test-results/exact-updater-trust-bridge/native --echo \
  tests/unit/test_exact_updater_trust_bridge.py tests/unit/test_update_panic_source.py
```

Result: **17 passed**, zero failures/errors/skips: five unique bridge cases and
12 existing before-stop/refusal/after-stop preservation cases. This run's native
launcher was PID 19099; it exited with status 0. Local summary and XML are under
`test-results/exact-updater-trust-bridge/native/local-ci-20261025-105016-rerun`.
Gating lint (`--no-advisory`) passed separately, and `git diff --check` passed.
The prior provisional proof and existing 12-test receipts are not relabelled as
this exact bridge's execution.

The real source admission proof ran without modifying either approval list:

```sh
.venv-local-ci-3.12/bin/python docs/exact-updater-trust-bridge-proof.py \
  b061d2d84439bf906f1da8ca9ae306cfa0b27f52 \
  966fa89c69622c053913c4930d4665717f28ce9e \
  d605cf19aaf387260cf09426642d463058dab056
```

The [exact proof result](exact-updater-trust-bridge-proof-2026-10-25.json) records
all three refs, three observed bundles and both executing updater digests.
The actual installed-source manager from `2b101abd` has the same updater digest
as `b061d2d8` (`21c57752dbe2d4512bfbffb9daee1d3d32c589d84b8cece8d62ab43053d1f129`).
The bridge updater digest is
`e0cb9bc8cc5629bb5516058dd190759988a883d09751bea69fc70af68b6a84da`.
The old manager admitted the old-consumer bridge and refused the final bundle;
the real bridge manager admitted old, bridge and final. Established clear and
latched guard bytes stayed unchanged through inspection/provision. The bridge's
actual three pinned files were checked again against the baseline after execution.

These are bounded offline source/intent checks, not a signed update installation,
physical acceptance or full test matrix. No version/order reservation, signature,
package, Pi/router, service, network or remote action was performed. Independent
full review remains a publication gate owned by the coordinator.
