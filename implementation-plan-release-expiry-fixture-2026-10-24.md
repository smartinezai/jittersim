# Bound the expired-key release fixture

The expired-key test starts an isolated agent but leaves its setup and cleanup subprocesses unbounded. Its failing askpass helper always supplies a nonempty wrong passphrase. The release code opens the controlling terminal, so redirecting stdin does not remove the interactive path. A terminal-attached run can therefore behave differently from a headless run. The owner has authorised synthetic temporary keys and an isolated fixture agent for diagnosis and this test only.

## Scope and decisions

- Change only the expired-key test in `tests/unit/test_release_one_step.py` and a uniquely named fixture helper if useful.
- Reproduce with a synthetic key, isolated agent and fixture-owned terminal. Compare headless execution without reading or using any owner key, agent or release state.
- Replace endless wrong replies with an explicit cancellation that OpenSSH can finish deterministically.
- Bound every fixture subprocess. Own and clean up each test process group and isolated agent, including timeout paths.
- Keep the actual production release script and ensure_key function under the test. Preserve proof that the key expires during checks and no local verification record is written after reacquisition fails.
- Do not change production scripts or run signing, publication, browser, Docker or full-suite jobs.

## Verification and risks

This is test-only risk class A with no household network risk. Use bounded wrapper commands and a private copied offline runtime. Capture the current repeated-reply behaviour as a bounded diagnostic, then run the corrected expired-key test in synthetic terminal and headless modes. Verify no half record, the intended cancellation path and guaranteed fixture cleanup. Run relevant fake/static checks and lint only. The exact cause of the owner's previous process is not inferred from its process name.

A synthetic key and agent are disposable local test resources. No signature should be produced. Any unexpected entry into record-local must fail before invoking signing. Keep production code unchanged and report diagnostic limits explicitly.
