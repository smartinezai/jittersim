# Dependency audit of the release locks, 9 October 2026

`scripts/local-ci.sh lint` (advisory section, PR #114 and #116) reports 22 known vulnerabilities in
`deploy/raspberry-pi/release-locks/`. This note triages them. It says which ones the Pi can actually
reach, what was changed and what was left, and why.

## Result

- The 22 entries are 13 distinct advisories. `pip-audit` lists some advisories twice with different
  descriptions. `runtime.txt` (Python 3.10 and newer) has none. `runtime-py39.txt` has 6 advisories
  in 2 packages (requests, urllib3). `build.txt` has 7 advisories in 3 packages (setuptools, wheel,
  pip).
- 10 of the 13 cannot be reached by how JitterSim uses the package. 3 can be reached in principle and
  each needs a hostile peer (see "Reachability").
- One fix is safe and was made: `wheel` 0.45.1 to 0.46.3. It is in draft PR #117 and is not merged
  here. It changes the release inputs, so it needs the independent review.
- The other 12 advisories have fixes that need Python 3.10 or newer (or a major version). The Python
  3.9 floor, one shared `build.txt` and the "no major bumps" rule stop them. They are documented and
  left. The owner decides how to proceed (see "Options").

Read on 9 October 2026. Tool versions: pip-audit 2.10.1, uv 0.12.24, pip-tools 7.6.2 (installed, not
needed), Python 3.12.13.

## How it was checked

```
python3.12 -m venv .venv && .venv/bin/pip install pip-audit uv pip-tools
.venv/bin/pip-audit --require-hashes --disable-pip -r deploy/raspberry-pi/release-locks/<lock>.txt --format json -o <file>
```

This is the documented command of the local CI runner. Advisory text, severity (GitHub advisory
database rating) and links come from `api.osv.dev` and PyPI JSON (`https://pypi.org/pypi/<name>/json`),
which also gave the `requires_python` of every release. The reachability notes come from reading the
pinned wheels (downloaded with `pip download --require-hashes` into a scratch folder and unpacked
there) and this repository.

Why these tools: `pip-audit` is the PyPA tool, it reads hash-locked files without installing them and
the local CI already uses it. `uv pip compile --generate-hashes` (and `pip-compile`) were used to
cross-check hashes. Their output is not copied into the locks: `prepare-locked-wheels.py` accepts
only one line per package (`name==x.y.z --hash=sha256:...`, regular expression `LINE`) and the lock
convention is the hashes of every wheel of that release and no sdist hash (checked for every line of
the three locks against PyPI JSON). Multi-line output with comments and sdist hashes would be refused.

## How the locks are used

- `deploy/raspberry-pi/prepare-locked-wheels.py` runs from `install.sh` and from the update
  preparation (`src/jittersim/updates.py`, read only). It makes a temporary builder virtual
  environment, installs `build.txt` into it, downloads the runtime lock from `https://pypi.org/simple`
  with `--require-hashes --only-binary=:all: --no-deps`, builds the JitterSim wheel from the signed
  source with `pip wheel --no-index --no-build-isolation --no-deps` and checks the set offline.
- Python 3.9 uses `runtime-py39.txt`. Everything else uses `runtime.txt`. `build.txt` is one file for
  every Python (3.9 to 3.13) because a lock line cannot carry a version marker.
- Only the build tools (pip, setuptools, wheel, packaging) live in `build.txt`. They run in the
  temporary builder and are not installed into the dashboard environment. The first `pip download` of
  `build.txt` and the offline installs use the pip bundled with the operating system Python, which no
  lock covers. This audit does not see those bundled tools and the Pi was not contacted.
- `requests` and `urllib3` (runtime) are used in two places: `networking/router.py` (plain HTTP to the
  router address, `stream=True`, `iter_content(chunk_size=1)`, `allow_redirects=False`) and
  `networking/adblocking.py` (HTTPS download of the StevenBlack hosts file, `stream=True`,
  `allow_redirects=False`, run in a subprocess with a deadline).

## Findings

Severity is the GitHub advisory rating. "Needs" is `requires_python` of the first fixed release.
PYSEC and CVE ids are the aliases pip-audit printed.

| Package in lock | Advisory | Severity | Fixed in (needs) | Reachable for JitterSim | Action |
| --- | --- | --- | --- | --- | --- |
| setuptools 80.9.0 (build) | GHSA-h35f-9h28-mq5c, CVE-2026-59890, PYSEC-2026-3447: `MANIFEST.in` exclusions bypassed in sdist on macOS (Unicode NFC/NFD) | Moderate | 83.0.0 (3.10, major) | No | Left |
| wheel 0.45.1 (build) | GHSA-8rrh-rw8j-w5fx, CVE-2026-24049, PYSEC-2026-2047: `wheel unpack` changes permissions outside the folder | High | 0.46.2 (3.9) | No | Fixed in PR #117 (0.46.3) |
| pip 25.3 (build) | GHSA-6vgw-5pg2-w6jp, CVE-2026-1703, PYSEC-2026-1796: path traversal when extracting a crafted wheel | Low | 26.0 (3.9, year-based major) | No | Left |
| pip 25.3 (build) | GHSA-58qw-9mgm-455v, CVE-2026-3219, PYSEC-2026-2875: tar and ZIP confusion in one archive | Moderate | 26.1 (3.10) | No | Left |
| pip 25.3 (build) | GHSA-jp4c-xjxw-mgf9, CVE-2026-6357, PYSEC-2026-2876: self-update check imports modules after a wheel install | Moderate | 26.1 (3.10) | No | Left |
| pip 25.3 (build) | GHSA-wf93-45jw-7689, CVE-2026-8643, PYSEC-2026-196: console script names allow installing outside the folder | Moderate | 26.1.2 (3.10) | No | Left |
| pip 25.3 (build) | GHSA-qwm4-qh6w-59xr, CVE-2026-13346, PYSEC-2026-3721: doubly encoded URLs from an index write files elsewhere | Moderate | 26.2 (3.10) | In principle: needs a malicious index | Left |
| requests 2.32.5 (py39) | GHSA-gc5v-m9x4-r6x2, CVE-2026-25645, PYSEC-2026-2275: predictable temp file in `extract_zipped_paths()` | Moderate | 2.33.0 (3.10) | No | Left |
| urllib3 2.6.3 (py39) | GHSA-qccp-gfcp-xxvc, CVE-2026-44431, PYSEC-2026-141: sensitive headers kept on cross-origin redirects in the low-level proxy API | High | 2.7.0 (3.10) | No | Left |
| urllib3 2.6.3 (py39) | GHSA-mf9v-mfxr-j63j, CVE-2026-44432, PYSEC-2026-142: decompression bomb limits bypassed (Brotli, `drain_conn()`) | High | 2.7.0 (3.10) | No | Left |
| urllib3 2.6.3 (py39) | GHSA-vxq7-64xx-v4gw, CVE-2026-97689, PYSEC-2026-4177: unbounded chunk size line is buffered when streaming | High | 2.8.0 (3.10) | In principle: needs a hostile server | Left |
| urllib3 2.6.3 (py39) | GHSA-gh4c-6fx4-qh6g, CVE-2026-97688, PYSEC-2026-4176: endless loop on a chunked deflate stream with trailing bytes | Moderate | 2.8.0 (3.10) | In principle: needs a hostile server | Left |
| urllib3 2.6.3 (py39) | GHSA-8988-9cw3-xx77, CVE-2026-97687, PYSEC-2026-4175: HTTPS proxy TLS settings ignored or overridden | High | 2.8.0 (3.10) | No | Left |

Every fixed release above is a pure Python wheel (`py3-none-any`), so aarch64 wheels are not the
problem. `requires_python` of every file of the fixing releases is `>=3.10`, except `wheel` 0.46.x
(`>=3.9`) and `pip` 26.0 (`>=3.9`). The newest releases that still install on Python 3.9 are setuptools
82.0.1 (still vulnerable), pip 26.0.1 (fixes only CVE-2026-1703), requests 2.32.5 and urllib3 2.6.3
(both the pinned versions).

## Reachability

Build tools (`build.txt`).

- setuptools: the advisory is about `MANIFEST.in` exclusions when building an sdist on macOS APFS or
  HFS+. The repository has no `MANIFEST.in`. Nothing in `deploy/`, `scripts/` or `.github/` builds an
  sdist (`grep` for `sdist`, `python -m build`, `bdist_wheel`). The Pi builds a wheel on Linux.
- wheel: the advisory is in the `wheel unpack` command. Nothing in the repository calls it (`grep` for
  `wheel unpack`, `wheel pack`, `wheel convert` found nothing).
- pip 1703 and 8643 act when pip installs a wheel. The locked pip 25.3 installs nothing: the
  builder installs `build.txt` with the pip that came with the OS Python. The checker and the install
  use the pip of their own new environments. 3219 needs an sdist or archive and the script passes
  `--only-binary=:all:`. 6357 needs the self-update check and the script passes
  `--disable-pip-version-check` on every pip call (line 70).
- pip 13346 is on a used path. The advisory says it "only materially impacts users running `pip
  download` with the `--only-binary` option" and needs a malicious index. The script does exactly
  that against `https://pypi.org/simple`, but hashes still must match and an attacker would need to
  take over PyPI or the TLS connection. Low likelihood. It is fixed only by pip 26.2 (Python 3.10).

Runtime on Python 3.9 (`runtime-py39.txt`).

- requests 2.32.5: `extract_zipped_paths()` is called only at `requests/adapters.py:300` when no CA
  bundle path was given, and it returns an existing path unchanged (`utils.py:262-264`). The CA file
  from `certifi` exists. The advisory itself says normal use is not affected.
- urllib3 141: requests calls `urlopen(..., redirect=False, assert_same_host=False)`
  (`adapters.py:649-650`) and both JitterSim call sites use `allow_redirects=False`.
- urllib3 142: the Brotli case needs the Brotli package, which is not in the lock. The other case
  needs `drain_conn()`. urllib3 calls it only after a redirect or a status retry
  (`poolmanager.py:493,502`, `connectionpool.py:903-939`). Redirects are off and requests uses
  `Retry(0, read=False)` (`adapters.py:186`).
- urllib3 4175 needs an HTTPS forwarding proxy with its own TLS settings. JitterSim configures none.
- urllib3 4177 and 4176 are on a used path: both modules stream the body, so a chunked reply goes
  through `read_chunked()`. They need a hostile peer: a device that can answer for the router on the
  local network (plain HTTP) or a compromised GitHub raw host (TLS). The impact is memory growth or a
  stuck request thread. The download runs in a subprocess that is killed at its deadline. The router
  path checks its deadline only between chunks, so it cannot stop a loop inside urllib3.
  `runtime.txt` (urllib3 2.8.0) is not affected.

## What changed

PR #117 (draft, not merged): `build.txt` `wheel==0.45.1` becomes `wheel==0.46.3` and
`pyproject.toml` `build-system.requires` is kept equal. Why 0.46.3: it is the newest 0.46 release (the
fix is 0.46.2), needs `packaging>=24.0` (the lock has 26.3), supports Python 3.9 and is one
pure-Python wheel, so one hash covers every platform. `wheel` is a 0.x package, so this is a minor
bump inside major version 0. Results: `pip-audit` 22 entries become 21 (`build.txt` 13 to 12). The
real `prepare-locked-wheels.py` run, the lock tests and the ruff gate pass, as listed in the PR. The
full test suite and real Pi hardware are still open. The change only removes scanner noise because
the advisory is not reachable.

## Options

1. Accept the rest for now. Nothing in the list is exploitable without a hostile network peer or a
   malicious package index. Re-run the audit before each release.
2. Split the build lock like the runtime lock: keep today's `build.txt` content as `build-py39.txt`
   and let Python 3.10 and newer use new pins (for example setuptools 84.0.0, pip 26.2.1, wheel 0.48.0).
   A `pip-audit` run on those three pins found no vulnerabilities. It needs a code change in
   `prepare-locked-wheels.py` (`locks()` picks the file) and `release-system.py` (the three lock names
   and `locks_sha256`), a matching `pyproject.toml` decision and a build check of the setuptools major
   bump. It leaves the Python 3.9 build lock as it is. Not started.
3. Move the Pi to Python 3.10 or newer (Raspberry Pi OS Bookworm ships 3.11) and later retire
   `runtime-py39.txt`. That would clear all 9 runtime entries. Upstream Python 3.9 reached end of life
   in October 2025. This drops the stated 3.9 floor (`README.md`, CI matrix 3.9, 3.11, 3.13), so it is
   the owner's decision. The Python version on the Pi was not checked.
4. Reduce the two reachable urllib3 items in code without touching the lock, for example by asking
   the router for `Accept-Encoding: identity` (helps only the deflate loop) and a hard deadline around
   the router read. Not tested against the real router. Not started.

## Re-running

```
scripts/local-ci.sh lint
.venv/bin/pip-audit --require-hashes --disable-pip -r deploy/raspberry-pi/release-locks/build.txt
```

After any lock change: `python -I deploy/raspberry-pi/prepare-locked-wheels.py --source . --check`,
then a real `--source . --output <empty folder>` run on Python 3.9 and 3.12 (or
`scripts/local-ci.sh release-checks`), the lock tests (`tests/unit/test_stable_releases.py`,
`test_update_preparation.py`, `test_signed_releases.py`, `test_release_one_step.py`) and the full
suite.
