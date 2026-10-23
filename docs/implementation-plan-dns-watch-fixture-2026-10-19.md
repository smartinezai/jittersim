# DNS watcher Linux fixture repair

The Linux packet fixture still expects a remote DNS server to teach service addresses when household DNS rules are off. The watcher now deliberately trusts only the Pi resolver in that state. Repair the fixture without changing runtime code or firewall rules.

- Send a matching remote DNS answer and verify that traffic remains unshaped.
- Send a real outgoing answer from the Pi resolver through a packet socket on the test interface and require the learned address to become shaped.
- Keep the client-forgery negative and send the unrelated-name negative from the trusted resolver.
- Close the additional socket with the existing cleanup.

Review the source change and run only this integration fixture in the existing Linux image when the shared Docker slot is available. Record the test file hash before and after the repair. Earlier full-suite evidence remains evidence for the frozen runtime commit, with this test-only follow-up recorded separately.
