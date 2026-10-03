# Verify refusal without a polling timing assumption

The final Linux run reports one failed 0.2-second refused-client assertion.
Ten focused attempts show that refusal itself returns immediately while an
already accepted request can wait for the server's ordinary 0.5-second poll.
The test should distinguish that scheduling delay from blocking socket reads.
Keep the production server unchanged and add a direct real-socket regression
that detects blocked refusal without relying on the serving loop's timing.

Keep the existing six-client HTTP check, its expected 503 and its held Apply.
Use the class's existing one-second bound for that request. Add owned socketpair
checks for a silent peer and a full send buffer. Give the original socket a
longer blocking timeout, require refusal to finish within one second and close
both sockets plus join the worker even when an assertion fails. These checks
must fail if refusal stops setting the socket to nonblocking before I/O.

The risk is hiding the original accept-loop regression by changing a timing
limit. Prove the new direct check rejects a temporary blocking-refusal mutation
then restore the exact production source. Run the complete concurrency module
and bounded repeated Linux attempts. Review the narrow diff and rerun the full
configured release checks at the final source. Retain the failed full run and
the timing traces under their original source identities.

This changes only tests and this plan. No application, dependency, deployment,
privacy or restore behavior changes. Test resources and attempts remain bounded.
The standing 24-hour implementation authority applies to this repair.
