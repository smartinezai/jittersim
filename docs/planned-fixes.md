# Planned fixes

This page lists known problems that nobody has fixed yet. New features are in [planned-features.md](planned-features.md). Add a problem here as soon as you see it so it is not forgotten. Move it to the fixed list when the fix ships.

Last updated: 2026-10-02.

How to read an entry: the title then the status and size in brackets then what goes wrong then what the fix should be.

- **Status** is one of: planned (nobody has started) or in progress (a branch exists) or needs a decision (the owner has to choose first).
- **Size** is small or medium or large.

## Found while using the Pi

- **Start says no device profiles are saved when devices were skipped** (planned, small). On 2026-10-02 the message after Start read "No device profiles or service lists are saved yet" while the sidebar said "3 devices with a profile". The three saved devices had no hardware address. With protected devices present JitterSim skips a saved device it cannot identify, which is the intended safe behaviour, and it names them at the end of the message. Fix: say "3 saved devices were skipped because JitterSim cannot tell which device they are" and tell the person what to do: press Assign profile on the device's row when it connects, or press the Save MAC button from the protected device change (draft PR "Pause the profile of a protected device and show it"). The sidebar line under the Start button should say the same in short: "3 devices with a profile, 3 skipped (no hardware address)".

- **Reset Wi-Fi name shows "Load failed"** (planned, small). The Pi restarts its Wi-Fi in the middle of the request. If you are connected through that same Wi-Fi the answer never arrives and the browser shows its own "Load failed" even though the reset worked. Fix: the Pi answers "reset accepted" first and restarts the radio a second or two later. If the connection still drops the page says "The Wi-Fi is restarting. Rejoin JitterSim in about 30 seconds" and waits for the Pi to come back. The same applies to every action that restarts the Wi-Fi.
- **Two networks with one name** (planned, small). After Copy router Wi-Fi to Pi the Pi's test network and the router's own network share a name and a computer may join the Pi's without any hint. Fix: the page warns when the two names are equal. The sidebar or the header shows which network the browser is using (the Pi's test Wi-Fi or another one) so a deploy attempt does not fail with a timeout that looks like a Pi fault.
- **Deploying from the test Wi-Fi times out** (planned, small). SSH from the Pi's test Wi-Fi is blocked on purpose. The deploy script says so only after the timeout. Fix: check the address first and stop with the plain message.

## Network and recovery

- **The filtered DNS service did not start on the Pi** (fixed in the integration branch, not yet released). The unit allowed only two network socket types and Unbound needs a third to list the network interfaces, so Block globally saved the name but could not enforce it. The fix adds `AF_NETLINK`. A temporary drop-in file was added by hand on the owner's Pi on 2026-10-02 and can stay until the next release.
- **The dashboard logs an error every two seconds while the network is reset** (planned, small). Reading the live traffic counters fails when the network tables do not exist and the page log fills with a full error trace. Fix: log it once and answer "not running" quietly.


- **Outage latch is lost on update or reboot** (needs a decision, small). The latch lives in a runtime folder, so profiles can come back on after a Pi restart although an outage was meant to keep them off. Decide first whether a restart should keep profiles off.
- **Panic can answer busy** (planned, small). Panic waits at most three seconds for the helper. During a long access apply it can answer busy. Fix: let Panic interrupt an access apply, or lengthen the wait with a clear message.
- **Two brief traffic pauses per install or update** (planned, small). The guard table and the resolver restart pause test traffic for about a second, twice. Fix: do both in one step.
- **MAC blocked devices lose DHCP** (needs a decision, small). A device on the MAC block list cannot get an address. Decide whether it should still get one so it can reach the sign-in page.
- **The first dashboard update into a release that has signed or new steps runs the old recovery program** (planned, medium). It skips the watcher and the name units and the firewall render. Documented: install with the installer once.
- **Browser proxy start race** (planned, small). Reported twice in reviews and never reproduced on a real Pi.

## Security

- **The Wi-Fi password is visible in the process list** (planned, small). It is passed to the network tool as an argument for a moment. Fix: pass it through a file or standard input.
- **A partial policies save resets the missing sections** (planned, small). Saving only one section of the access rules clears the others. Fix: merge into the stored policy.
- **Restore previous installation needs only the admin token** (needs a decision, small). Already true before the signed releases. A leaked token could roll back to the previous installation. Decide whether it needs a second step.

## Dashboard

- **Unnamed client** (planned, small). A saved device with no label shows "Unnamed client" in the sidebar and in pickers. Show the announced host name or the address instead.
- **Old activity entries keep "Client 192.168.50.x"** (planned, small). New entries use device names. Old ones stay as written. Fix: show the current name when the device is known.
- **Base profile shows the profile id** (planned, small). The Devices page prints the id where the name should be.
- **Unused page code** (planned, small). The page keeps an unused `keepUnaffected` variable and `tests/browser/apply_clarity.py` an unused import.
- **/favicon.ico returns 404** (planned, small).
- **Stored single-label names are not migrated** (planned, small). Names such as `com` are now refused when saved but entries saved before stay and are reported as malformed until removed. Fix: a one-time cleanup with a plain message.

## Tests

- **Browser journeys wait on timing** (planned, medium). Two journeys failed in CI on slow runs (a message that arrived late and a busy helper). Review every journey for fixed waits and wait for the real state.
- **Two flaky unit tests** (planned, small). `test_body_bytes_cannot_extend_the_input_deadline` fails on Python 3.9 because `TimeoutError` is not `socket.timeout` there. `test_dashboard_connections` is timing sensitive under load.
- **A test class sits after the `if __name__` block** (planned, small). In `tests/unit/test_deployment.py` the new class is skipped by `python -m unittest` on that file. pytest runs it.
- **Local and CI browser versions differ** (planned, small). Pin both to one Playwright version.
- **README guard covers only two READMEs** (planned, small). It does not scan the other documents for old button names.
