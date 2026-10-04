# Handle expired dashboard requests consistently

An incomplete request whose total input deadline has expired can be reported as
an internal server failure on Python 3.9. The helper does not change settings and
the next healthy client recovers. Python 3.9 has separate socket and built-in
timeout classes. Later supported interpreters use the same class. The repair
will recognize both at the ordinary request boundary so expired input closes
without an internal-error response or a failed action entry.

This is a small compatibility and reliability change. Keep request deadlines,
authentication, queue limits and explicit release-upload timeout responses.
The implementation must distinguish an expired incomplete body from a complete
body already buffered before its deadline. No saved settings, new logs, captured
requests, infrastructure or data migration are required.

1. Preserve the actual source04c Python3.9 HTTPS reproduction and original logs.
2. Independently review both ordinary response exception boundaries and the
   request handler's disconnect boundary before changing them.
3. Add focused real HTTPS checks for expired headers and incomplete bodies.
   Require no helper write, no server-error response or failed action entry
   and recovery of the next healthy request. Retain accepted complete-body behavior.
4. Make the bounded exception-handling change in a focused commit after the
   current frozen maintenance run. Leave explicit upload timeout handling intact.
5. Run focused real HTTPS checks under Python3.9 and a later supported interpreter.
6. Include this source in the next complete supported Linux release gate with
   clean builds, all configured commands, browser journeys and package checks.
7. Preserve exact source and proof identities before the final push and merge.

The current proof deliberately expires the genuine input reader at body parsing.
It is not a reproduction of naturally slow traffic on a physical Pi. Reverting
the focused commit restores the prior error-reporting behavior. Existing finite
request slots and total input deadlines continue to bound load as use grows.
