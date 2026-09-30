# WPA3 hotspot upgrade

## Human review summary

Enable WPA3 on JitterSim while preserving its current Wi-Fi name and password.
Use WPA3 only with AES and required protection for Wi-Fi management messages.
Keep the existing name reset command and dashboard button working. The main
risk is that the Pi's built-in radio or firmware cannot host WPA3 even though
the software recognises it. Prove hardware support before changing production
defaults. Restore the current WPA2 connection if a bounded test fails.

The user requested immediate implementation then testing, publication, merge
and deployment. That authorization continues for this security upgrade. No
additional design choice is blocking. A hardware limitation may prevent the
requested deployment and must be reported from observed evidence.

## Current state

- Main is `239c787b0ee44c0e0598008e702ebc97e70075f8`.
- The Pi runs `dbd73a9fa8c565a70eb530d6e70a2a51a30df552` from the separate
  service and Pi hardening branch. Preserve its independent changes.
- The Pi is a Raspberry Pi 3 Model B Plus. It runs NetworkManager 1.52.1
  with wpa_supplicant 2.10 and the brcmfmac driver.
- The radio currently runs WPA2 with AES. WPA3 authentication is absent from
  its reported interface capabilities. A guarded physical activation check
  nevertheless passed with SAE, AES and required protection. Loaded firmware
  is 7.45.265 and matches the installed image. Capability reporting alone
  is not proof that this radio cannot host WPA3.
- Management access is through Ethernet. Keep Wi-Fi client isolation and
  the test network restrictions intact.

## Work and verification

1. Inspect official NetworkManager and Raspberry Pi source or issue reports.
   Check loaded firmware against the installed package. Create a private
   backup of the active profile. Test WPA3 with a temporary profile that cannot
   automatically reconnect. Arm an independent timed recovery before touching
   the radio. Keep attempts bounded and verify WPA2 recovery after any failure.
2. If the hardware passes, update the shared Wi-Fi controller and installer to
   use `sae`, `rsn`, `ccmp` and required management protection. Verify the live
   authentication mode rather than treating saved settings as proof. Ensure
   reset, manual edits and copied credentials cannot turn WPA3 off accidentally.
3. Keep existing secret handling. The reset path must not read or write the
   password. Backups remain root private on the Pi. New error messages and
   logs must not contain passwords or authentication tokens. No new public
   endpoint or account permissions are required.
4. Add focused coverage for security preservation, activation failure,
   restoration and prevention of unintended downgrade. Run existing tests,
   lint and clean Linux installation checks. Run the relevant browser journey
   if the dashboard behavior changes. A simulated radio cannot prove that
   the physical Pi can host WPA3.
5. Review the complete change for bounded retries, concurrent Wi-Fi writes,
   password handling and compatibility. Publish the ready branch once and
   merge after current required CI checks pass. Port the scoped change to the
   Pi release branch without overwriting unrelated work and check it there.
6. Deploy an archive built from the exact merged release. Check its checksum
   and installed module hashes. Back up code, Wi-Fi settings and saved device
   settings. Verify authenticated dashboard operations, console recovery,
   active WPA3 authentication, required management protection and preservation
   of the password and independent features. Retain a verified restore path.

## Acceptance and recovery

WPA3 acceptance requires an active WPA3 hotspot with required management
protection. An actual WPA3 client connection is a separate check and must be
reported as unverified if no suitable client is available. WPA2 clients will
need WPA3 support for the new network. Never silently retry with legacy WPA
or label a WPA2 connection as WPA3.

If activation fails, stop the temporary connection and restore the private
profile backup. Reactivate the original connection and verify its WPA2/AES
settings, SSID, password and dashboard reachability. Do not merge unsupported
defaults or leave the Pi without a working access point.

## References

- [NetworkManager security settings](https://www.networkmanager.dev/docs/api/latest/settings-802-11-wireless-security.html)
- [NetworkManager 1.52.1 Wi-Fi configuration source](https://github.com/NetworkManager/NetworkManager/blob/1.52.1/src/core/supplicant/nm-supplicant-config.c)
- [Raspberry Pi WPA3 association issue](https://github.com/raspberrypi/linux/issues/7359)
- [Raspberry Pi WPA3 firmware failure report](https://github.com/raspberrypi/linux/issues/7528)

## Physical check before implementation

A temporary non-autoconnecting profile started with SAE authentication, CCMP
encryption and required management protection. The original WPA2 profile was
restored with its name and password preserved. No clients were associated.
The first two recovery runs hit a NetworkManager reload timeout. The corrected
runner activates the original unchanged profile directly and restarts services
that stop with the helper. Its final activation and recovery checks both passed.
No firmware binary was replaced.

## CI failure and scoped recovery fix

Linux CI exposed an existing overload response race. Closing a rejected socket
with an unread request can discard its busy response. Consume at most one
bounded request before rejecting it, with a total quarter-second deadline.
Keep the worker limit unchanged. Extend the existing real-socket test with a
briefly delayed sender to prove delivery. Verify this fix on main and the Pi
branch before completing deployment.
