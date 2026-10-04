# Restrict older router Wi-Fi forms

## Human review summary

The current encrypted router path sends a fixed set of Wi-Fi settings. Its
older-firmware fallback sends every captured form field. If an older form
contains an unrelated action then the fallback can send that action while
changing Wi-Fi. The exported CLIENTTOOL event does not prove this happened.

Refuse a legacy Wi-Fi write when the form contains an unrecognized field.
Recognize only the radio controls and Wi-Fi name, password and security fields
already understood by this client. Preserve read-only access to older forms.
Some older forms will require changing Wi-Fi through the router's own page.
The current encrypted protocol remains supported.

Risk is high because unintended router actions can disrupt household access.
The critical incident request and the owner's additional permission to make
useful fixes on 2026-10-04 authorize this scoped follow-up. Implement locally
without changing the frozen release source or making live router requests.
The release owner decides its checked integration and installation order.

## Implementation and review

1. Add a regression test using a legacy form with the disconnect parameter
   observed in the export. Prove that no write occurs. Check that an ordinary
   unrecognized field is also refused rather than relying on one blocked name.
2. Refuse the form before modifying or submitting it if any captured field is
   outside the fixed known set. Use the existing unsupported-control message.
   Keep the existing supported legacy on/off test and read-only import behavior.
3. Add an encrypted-protocol regression test that includes unrelated disconnect
   and reset fields in a settings response. Verify that the posted settings
   omit them and only the expected Wi-Fi state changes.
4. Run router and helper checks plus relevant lint and compatibility checks.
   Review the diff for secret exposure, compatibility and effects on retries.
   Record exact evidence and keep this follow-up separate from the frozen batch.

## Release and recovery

No database, dependency or build change is needed. The normal source release
and installer carry the fix. Tests use local fixtures and never contact the
household router. No application data is newly retained. The only compatibility
change is refusing writes on an older form this client does not fully recognize.

If accepted into a release then rerun its required checks against that changed
source. Source rollback restores the prior behavior and does not recover an
already stuck router. Persistent operation records remain with the separate
outage logging chat. Neither this guard nor those records identify CLIENTTOOL
or establish the cause of the two reported outages.
