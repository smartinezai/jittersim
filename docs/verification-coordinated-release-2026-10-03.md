# Coordinated JitterSim verification on 3 October 2026

## Result and release boundary

The checked source is `acccfa91c9f2b15514faff313c0b572c609f8d27`.
Its installed wheel matches every one of the 64 packaged application files.
Wheel SHA-256:
`c7595ab452d06987a8b66bb3eb38ea9109bfefdbeb0165a2c48eaba220627092`.
The production source, tests and workflows are frozen during verification.
A later report commit changes documentation only.

This combines the ready service-list, hardening, router and traffic changes
with reviewed DNS, access, helper response, general-profile, confirmation,
database rollback and keyboard-focus repairs. The GUI baseline adds bounded
test tooling. The update, signing, remote command, trusted HTTPS and neutral
sign-in work remains in its separately reviewed stack. Main and Pi delivery
still need that work and a verified recovery path.

## Configured checks

| Check | Actual environment | Result |
| --- | --- | --- |
| Full Python suite and Ruff | Linux Python 3.9.25 | 3,196 passed and 39 expected kernel skips. Ruff passed in a separately verified recovery container |
| Full Python suite and Ruff | Fresh release image, Linux Python 3.11.16 | 3,196 passed and 39 expected kernel skips |
| Full Python suite and Ruff | Linux Python 3.13.15 | 3,196 passed and 39 expected kernel skips |
| Extra Python compatibility checks | macOS Python 3.9.6 and 3.12.13 | 3,196 passed and 39 expected kernel skips in each environment |
| Actual kernel suite | Isolated Bookworm Linux, Python 3.11.16 | 39 passed |
| Network smoke check | Isolated Linux with required local network device | Passed |
| Clean release Dockerfile build | Fresh declared system and Python dependencies on cached minimal Python base | Passed |
| Configured Linux release commands | Separate clean-image containers with configured capabilities | All 12 passed |
| Homepage fixture and locked npm install | Linux Python 3.13.15 and Node 22.23.1 | Passed |
| Ordinary browser modules | Linux Python 3.13.15, Playwright 1.63.0 and Chromium 153.0.8010.12 | All 19 passed |
| GUI unit checks | Linux Python 3.13.15 | All 61 passed |
| Screenshot capture | Same configured Linux browser | All 40 states complete |
| Accessibility capture | Same configured Linux browser and axe 4.13.0 | All 40 states complete with existing findings |
| Independent artifact review | Installed source, complete file lists, hashes, image sizes and strict runtime guards | Passed |

The kernel checks are opt-in and account for the 39 skips in ordinary runs.
They were executed separately. The first Python 3.9 verifier used the wrong Ruff binary path after the
full suite passed. The original JUnit and output were preserved. Only Ruff
was rerun with the verified fresh executable and it passed. No failed
container exit or missing subprocess exit code was rewritten as success.

The initial smoke setup used no network and
therefore lacked `eth0`. Supplying the required device in the owned isolated
container made the unchanged smoke check pass.

Samantha directly instructed the release owners to execute configured checks
locally when GitHub account limits prevent jobs. GitHub currently reports
zero-step account payment or spending-limit failures. These are unavailable
hosted checks and supply no passing or failing application result.

The Linux matrix and browser evidence uses aarch64 rather than the hosted
Ubuntu runner's x86_64 architecture. The kernel and browser base runtimes are
immutable cached images. Current application and test files are hash checked.
Python 3.9 and 3.13 dependencies were resolved and installed independently.
The release Dockerfile was built without reused build layers. Package host
addresses were supplied to that build only. The shared VM was unchanged.

## GUI observations and further improvements

The configured GUI matrices record no external requests, console errors,
page errors or policy errors. An independent review checked every state,
source and engine identity, all output hashes, credential masking and selected
phone and desktop screenshots. The accessibility reports remain observations.
They contain 50 repeated minor or moderate findings across 16 states involving
skip links, landmark uniqueness and empty table headers. No accessibility
conformance claim is made.

An extra macOS capture under Chromium 148 failed its strict console guard
when the browser requested a missing favicon. Dashboard and public health
requests returned 200. That supplementary failure remains recorded. Adding
an explicit favicon is a small further improvement. The configured Chromium
153 captures passed without weakening the guards.

Other non-blocking suggestions are preserving focus on separate saved-list
action rows, considering unusual numeric address spellings or broad domain
suffixes and measuring retry spacing from an operation's completion.

## Delivery and recovery

The verified ready set targets `integrate/main-into-stack`. Source reviews and
local configured checks support that integration decision. Main and Pi
release gates remain open for the unfinished update and neutral sign-in work.
The reviewed update, signing and command repair plans still require specific
human feedback under the working agreement.

The Pi's public health route returned 200 and reported reachable hardware
helper services. This read did not establish certificate trust. Known-host
management SSH to `192.168.0.17` timed out after five seconds. Installed source
and independent management recovery remain unconfirmed. No live Pi write,
radio change or deployment was performed. Physical phone and normally trusted
HTTPS pairing acceptance remain unfinished.

## Evidence

The machine-readable companion preserves the initial historical checkpoint
and final exact-source check records. Durable local logs, screenshots, reports,
the wheel and a per-file hash inventory are stored in
`test-results/coordinated-release-2026-10-03/` in the primary repository.
Keep them through at least 10 October 2026 as the local replacement for the
unavailable GitHub artifact uploads. Every owned test container was removed.
Unrelated containers and active worktrees were preserved.
