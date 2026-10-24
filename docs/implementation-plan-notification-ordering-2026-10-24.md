# Preserve the current notification after delayed refreshes

Base: main53ec72bb94f09a33aa5b55971c5446163c24e419. Separate codex/notification-ordering worktree; PR232 remains frozen. Coordinator owns the merge writer and confirmed refreshAfterSuccess-only production ownership plus unique native tests/this plan. Latest coordinator allowance45% remaining; preserve10%.

A deferred refresh from an earlier successful action can reject after a newer action's notice and replace it with the older action text. Existing messageSerial and adminTokenRevision guards protect other workflows but this catch is unconditional. Read-only served-source probes reproduce the overwrite; the existing timer, dismissal, Panic and token/ad-blocking guards otherwise behave as intended.

Snapshot messageSerial and adminTokenRevision immediately after the success notice. Suppress only a delayed refresh notice whose message or token ownership changed. A current failure still reports in full, including after manual dismissal or current authentication failure. Guard traffic sampling restart by token revision only; a newer notice must not prevent legitimate same-token work.

Add a unique Node-backed native regression against the served function. Record baseline failures, then verify newer success/error, token change, immediate failure, overlapping refreshes, dismissal and sampling restart. Run affected existing Node workflow tests and lint through the real shared wrapper, plus independent exact-head source review. Reuse PR232 accessibility evidence; no CSS/HTML/navigation changes, notification queue, serialisation framework, browser/GUI repeat, hardware contact or pinned files. Normal-push draft only; no merge.
