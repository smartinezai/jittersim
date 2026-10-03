# Correct remaining browser recovery fixtures

## Review summary

The complete browser diagnostic found three test-only recovery mismatches.
The reconnect fixture unregisters its lost-reply interceptor before that
callback finishes. The router-copy test expects the old warning sentence.
The observation test expects a raw browser error that the reviewed reconnect
interface deliberately replaces with useful connection guidance.

Preserve the actual application, one-write counts, confirmation checks,
credentials, drafts and negative failure behavior. No recovery assertion is
removed. These fixes have low risk and receive independent diff review.

## Changes and checks

1. Wait within a fixed five-second limit for the actual lost-reply callback
   to finish before removing its route. Still require exactly one request.
2. Assert the real warning about Wi-Fi restart, temporary disconnection,
   choosing the changed network and independent management recovery.
   Retain cancellation, acceptance and no password/clipboard disclosure.
3. Require the useful lost-connection message and the absence of raw browser
   errors. Keep one request, re-enabled action and unchanged saved profile.
4. Run complete reconnect, router-copy and observation modules. Retain their
   original failed logs and source. Review all changes before freezing.
5. A separately failing extension navigation journey needs its own diagnosis.
   Do not hide that failure inside these fixture corrections.
