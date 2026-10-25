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
Focused execution results will be recorded after independent source review.
