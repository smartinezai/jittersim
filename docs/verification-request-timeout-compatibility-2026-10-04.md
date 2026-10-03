# Expired dashboard request compatibility

Two real HTTPS regressions failed before this repair on host Python3.9.
An expired incomplete body returned500 and logged an internal error. Expired
incomplete headers reached the server's unhandled-error path. The third test
accepted a complete body already buffered before its input deadline.

The request boundaries now recognize both built-in and socket timeout classes.
Both expired incomplete requests close without a failed action or helper write.
The next healthy request succeeds. Explicit release-upload408 handling is
unchanged and complete buffered bodies remain valid.

All20 connection checks pass on host Python3.9. All42 connection and release
upload checks pass on host Python3.12. The3.9 environment has a known urllib3
LibreSSL warning, so this focused HTTPS proof does not replace full release
verification on Linux with OpenSSL. Final combined Linux checks remain pending.

Original failed and successful logs are retained in the coordinator's evidence.
The tests deliberately expire the actual input reader at request parsing. They
do not claim a naturally timed physical Pi reproduction. The change adds no
new logging, settings, provider calls or request work.
