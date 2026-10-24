# Prepared device profiles evidence

Profile-only Devices card on top of reviewed API `47a1fc1d`. Original PR240,
PR242 and PR243 are unchanged. This branch preserves the separately published
dashboard-update backlog commit `b5523688` by normal merge ancestry.

Manual MAC/name/profile entry saves an inert prepared choice. The list renders
20 rows per page, at most256 returned records. Edit and confirmed Delete use CAS.
Review shows the consistent current profile, complete settings, configuration
version and pending revision. Separate confirmed Use posts only the three pending
review fields to the existing Apply URL; the server checks fresh same-MAC identity.
Prepared protection can only be labelled/deleted. No discovery, auto-activation,
changed-MAC matching, new framework or backend/helper/storage change.

The independent [JavaScript/privacy/action review](review.md) is CLEAR at48c3d3f4.
Coordinator subsequently inspected and cleared the only runtime correction:
e95e9d23 raises specificity of the card's existing44px button minimum above the
incumbent desktop compact rule. It changes no global rule or safety count.
[Source identity and measured budgets](source-identity.json) cover the final runtime.
Root measured and approved exactly eight non-strict record changes; all seven
strict counters, definitions, schema and2% tolerance are preserved. The CSS fix
fits those approved allowances. No further product edits followed.

## Actual checks

| Evidence | Result and scope |
| --- | --- |
| [Final consumer browser receipt](receipts/212712-summary.md) | PASS on979b6a4c; desktop1440×1000,320×568 with verified18px root/Verdana,375×812,390×844; CRUD/CAS, full review, explicit strict Use,409/no automatic retry, actual synthetic-helper Use and retained choice, keyboard/Cancel/Escape/focus,44px buttons, no horizontal page overflow, bounded20-row pagination. Desktop additionally covers deferred Review→Panic and same-token admission loss→late Review→recheck with empty private inputs. |
| [Other affected browser receipt](receipts/212103-summary.md) | `device_apply_start` and `dashboard_access` PASS ona0f6bd97, before the independently cleared one-line CSS fix confined to the new card. They were not broadly repeated. That mixed receipt also preserves the original consumer failure; it is not wholly green. |
| [Offline and budget receipt](receipts/offline-touch-fix.log) |187 targeted tests PASS:163 consumer/auth/Panic/assembly/navigation/CI-registration cases plus24 static budget cases; final production source ise95e9d23-equivalent. Later changes are browser fixtures/screenshots/docs only. |
| [Lint](receipts/lint-final.log) | PASS; final evidence-head lint also passed. User-guide/planned-features relative references and diff whitespace checked. |

Card-scoped axe at all four widths found zero violations for its configured WCAG2A,
2AA and2.1AA tags. No full-page axe matrix or incomplete/manual findings capture is
claimed. Page-error collection was empty. Synthetic screenshots are not evidence
of physical identity, router discovery or real traffic effects. Real phones,
Safari/WebKit, hardware, assistive technology and200% OS text remain unverified.

The earlier receipts are retained:212103 found the genuine desktop36px specificity
defect;212350 correctly blocked the test's script-tag axe injection under CSP;
212424 correctly refused activation because the prior viewport's Panic latch was
still set in the shared synthetic helper. The latter two were fixture-only fixes:
use the established axe evaluation harness and explicit synthetic Start between
viewports.212602 then passed;212712 confirmed clean captures after acknowledging
only the asserted known success toast. Production CSP and safety refusal stay intact.

## Screenshots

- [Desktop card](screenshots/prepared-1440-card.png)
- [320px large-text viewport with Panic](screenshots/prepared-320.png)
- [320px complete card](screenshots/prepared-320-card.png)
- [375px viewport](screenshots/prepared-375.png)
- [390px viewport](screenshots/prepared-390.png)

All four widths and original notification-visible states are retained with
[raster provenance](screenshots/provenance.json). Complete-card screenshots can
include the persistent safety bar at the bottom edge; viewport captures show the
Use control clear of it. The author inspected the actual desktop/card/mobile
captures in one bounded pass and its confirmation. No visual polishing followed.

Every wrapper reached a terminal result and released this lane's shared slot.
No full browser/native/GUI matrix, hosted CI, merge, deployment, Pi/router access,
release signing or owner-only operation is claimed. Hosted Actions remained
billing-blocked on the preceding drafts; fresh hosted execution is not implied.
