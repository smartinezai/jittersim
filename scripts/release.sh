#!/bin/sh
# Cut, check, sign and publish a JitterSim release from the owner's Mac with one command.
#
#   scripts/release.sh 0.3.0
#
# It runs every step it can and stops only where a person must act: merging the release PR
# (asked), testing the exact package on the Pi (physical acceptance), and publishing (asked).
# Run the same command again to continue. The signing key never leaves this Mac. Plain POSIX
# sh so it runs with the bash 3.2 that macOS ships. See docs/releasing.md.
set -eu

REPOSITORY=smartinezai/jittersim
REPO_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
key="$HOME/.ssh/jittersim_release_ed25519"
home=
dry_run=no
yes=no
notes_file=

usage() {
    cat <<EOF
usage: scripts/release.sh [--key PATH] [--home DIR] [--notes-file FILE] [--yes] [--dry-run] X.Y.Z

Cuts release vX.Y.Z of $REPOSITORY from origin/main:
  1. reserves the version, sets it in pyproject.toml and src/jittersim/__init__.py and adds
     GitHub's generated notes to CHANGELOG.md in a release commit
  2. runs the local checks on that commit and signs a local verification record
     (it stands in for hosted CI only while hosted GitHub Actions cannot run)
  3. opens the release PR and merges it after asking
  4. builds and signs the exact package (release-system.py prepare)
  5. stops until you installed that package on the Pi and tested it, then asks for the
     acceptance evidence and signs the approval
  6. publishes the GitHub release with the two files the Pi downloads, after asking
  7. records the published release and moves main to the next patch version

  --key PATH        the release signing key (default: ~/.ssh/jittersim_release_ed25519).
                    A passphrase key is added to ssh-agent once and asks here, on this Mac.
  --home DIR        where packages and evidence are kept (default: ~/jittersim-releases)
  --notes-file FILE use these release notes instead of GitHub's generated notes
  --yes             do not ask before merging and publishing (acceptance is still asked)
  --dry-run         do every local step in a throwaway clone of this checkout's HEAD with
                    fake checks: no push, PR, tag push, GitHub release or Pi
  -h, --help        show this help
EOF
}

say() { printf '==> %s\n' "$*"; }
fail() {
    printf 'release: %s\n' "$1" >&2
    exit "${2:-1}"
}
ask() {
    [ "$yes" = yes ] && return 0
    ask_always "$1"
}
ask_always() {
    printf '%s [y/N] ' "$1"
    answer=
    read -r answer </dev/tty || return 1
    case $answer in y|Y|yes|YES) return 0 ;; esac
    return 1
}
prompt() {
    printf '%s: ' "$1" >&2
    reply=
    read -r reply </dev/tty || reply=
    printf '%s' "$reply"
}

number=
while [ "$#" -gt 0 ]; do
    case $1 in
        --key) [ "$#" -ge 2 ] || fail "--key needs a value" 2; key=$2; shift 2 ;;
        --home) [ "$#" -ge 2 ] || fail "--home needs a value" 2; home=$2; shift 2 ;;
        --notes-file) [ "$#" -ge 2 ] || fail "--notes-file needs a value" 2; notes_file=$2; shift 2 ;;
        --yes) yes=yes; shift ;;
        --dry-run) dry_run=yes; shift ;;
        -h|--help) usage; exit 0 ;;
        -*) usage >&2; exit 2 ;;
        *) [ -z "$number" ] || { usage >&2; exit 2; }; number=${1#v}; shift ;;
    esac
done
[ -n "$number" ] || { usage >&2; exit 2; }
printf '%s\n' "$number" | LC_ALL=C grep -Eq '^(0|[1-9][0-9]{0,5})\.(0|[1-9][0-9]{0,5})\.(0|[1-9][0-9]{0,5})$' \
    || fail "use a version like 0.3.0" 2
tag="v$number"
major=${number%%.*}
rest=${number#*.}
minor=${rest%%.*}
patch=${rest#*.}
next="$major.$minor.$((patch + 1))"

for tool in git python3 ssh-keygen shasum; do
    command -v "$tool" >/dev/null 2>&1 || fail "$tool is missing"
done
if [ "$dry_run" = yes ]; then
    [ -n "$home" ] || home=$(mktemp -d "${TMPDIR:-/tmp}/jittersim-release-dry.XXXXXX")
else
    command -v gh >/dev/null 2>&1 || fail "the GitHub CLI (gh) is missing: brew install gh"
    [ -n "$home" ] || home="$HOME/jittersim-releases"
fi
state="$home/$number"
src="$state/source"
evidence="$state/evidence"
package="$state/package"
mkdir -p "$state"
chmod 700 "$home" "$state" 2>/dev/null || true

rs() { python3 "$src/deploy/raspberry-pi/release-system.py" --source "$src" "$@"; }
set_version() {
    sed -i.bak "s/^version = \".*\"\$/version = \"$1\"/" "$src/pyproject.toml"
    sed -i.bak "s/^__version__ = \".*\"\$/__version__ = \"$1\"/" "$src/src/jittersim/__init__.py"
    rm -f "$src/pyproject.toml.bak" "$src/src/jittersim/__init__.py.bak"
}

# --- The signing key: here only. A passphrase is asked once by ssh-add in this terminal.
[ -f "$key" ] && [ -f "$key.pub" ] \
    || fail "no release signing key at $key. Create it once with deploy/raspberry-pi/make-release-key.sh"
[ -O "$key" ] || fail "the signing key $key belongs to another user"
[ "$(ls -lLd "$key" | cut -c 5-10)" = '------' ] || fail "fix the key permissions: chmod 600 $key"
if ! ssh-keygen -y -P '' -f "$key" >/dev/null 2>&1; then
    fingerprint=$(ssh-keygen -l -f "$key.pub" | cut -d ' ' -f 2)
    if ! ssh-add -l 2>/dev/null | grep -qF "$fingerprint"; then
        say "The signing key has a passphrase. ssh-agent keeps it for one hour."
        ssh-add -t 3600 "$key" </dev/tty || fail "the signing key could not be added to ssh-agent"
    fi
fi

if [ "$dry_run" = no ]; then
    login=$(gh api user -q .login 2>/dev/null) || fail "sign in to GitHub first: gh auth login"
    [ "$login" = "${REPOSITORY%%/*}" ] || fail "releases are published by ${REPOSITORY%%/*}, not $login"
    git -C "$REPO_ROOT" fetch -q origin main --tags
    if git -C "$REPO_ROOT" ls-remote --exit-code --tags origin "refs/tags/$tag" >/dev/null 2>&1 \
        && [ ! -f "$state/release-commit" ]; then
        fail "$tag already exists on GitHub. Choose a newer version."
    fi
fi

# --- 1. The release commit on a release branch.
if [ ! -f "$state/release-commit" ]; then
    [ ! -e "$src" ] || fail "an unfinished start of $tag is in $state. Remove it with 'git worktree remove --force $src', 'git branch -D release/$tag' and 'rm -rf $state', then run again"
    if [ "$dry_run" = yes ]; then
        base=$(git -C "$REPO_ROOT" rev-parse HEAD)
        git clone -q --no-hardlinks "$REPO_ROOT" "$src"
        git -C "$src" checkout -q -b "release/$tag" "$base"
        git -C "$src" config user.name >/dev/null || git -C "$src" config user.name "Release dry run"
        git -C "$src" config user.email >/dev/null || git -C "$src" config user.email "release@example.invalid"
        latest=$(git -C "$src" tag --list 'v*')
    else
        git -C "$REPO_ROOT" worktree add -q -b "release/$tag" "$src" origin/main
        latest=$(git -C "$REPO_ROOT" ls-remote --tags origin 'v*' | sed -n 's|.*refs/tags/\(v[^^]*\)$|\1|p')
    fi
    previous=$(printf '%s\n' "$latest" | python3 -c '
import re, sys
found = [tuple(map(int, m.groups())) for m in (re.fullmatch(r"v(\d+)\.(\d+)\.(\d+)", line.strip()) for line in sys.stdin) if m]
print("v%d.%d.%d" % max(found) if found else "")')
    if [ -n "$previous" ]; then
        python3 -c 'import sys; a, b = (tuple(map(int, v.lstrip("v").split("."))) for v in sys.argv[1:]); sys.exit(a <= b)' \
            "$number" "$previous" || fail "$tag is not newer than the latest release $previous"
    fi
    say "Preparing $tag (previous release: ${previous:-none})"
    if python3 -c 'import json, sys; sys.exit(json.load(open(sys.argv[1]))["sequence_floor"] is not None)' \
        "$src/docs/releases/registry.json"; then
        # Development builds use their build time as release order, so nothing older than now
        # can be installed anywhere. A stable order of at least now is newer than all of them.
        rs initialize --floor "$(date +%s)" --confirm-inspected
    fi
    rs reserve "$number"
    set_version "$number"
    mkdir -p "$evidence"
    if [ -n "$notes_file" ]; then
        cp "$notes_file" "$evidence/notes.md"
    elif [ "$dry_run" = yes ]; then
        printf 'Dry run release notes for %s.\n' "$tag" >"$evidence/notes.md"
    else
        set -- -X POST "repos/$REPOSITORY/releases/generate-notes" -f "tag_name=$tag" -f target_commitish=main
        [ -z "$previous" ] || set -- "$@" -f "previous_tag_name=$previous"
        gh api "$@" -q .body >"$evidence/notes.md" || fail "GitHub could not generate the release notes"
    fi
    python3 - "$src/CHANGELOG.md" "$tag" "$(date +%Y-%m-%d)" "$evidence/notes.md" <<'EOF'
import sys
from pathlib import Path
path, tag, day, notes = Path(sys.argv[1]), sys.argv[2], sys.argv[3], Path(sys.argv[4]).read_text().strip()
text = path.read_text() if path.exists() else "# Changelog\n\nEvery JitterSim release. The newest is first.\n"
if f"\n## {tag} " in text or f"\n## {tag}\n" in text:
    sys.exit(f"CHANGELOG.md already has {tag}")
section = f"## {tag} - {day}\n\n{notes}\n\n"
lines = text.splitlines(keepends=True)
at = next((i for i, line in enumerate(lines) if line.startswith("## ")), len(lines))
head = "".join(lines[:at]).rstrip("\n") + "\n\n"
path.write_text(head + section + "".join(lines[at:]))
EOF
    git -C "$src" add pyproject.toml src/jittersim/__init__.py CHANGELOG.md docs/releases/registry.json
    git -C "$src" commit -q -m "Release $tag"
    git -C "$src" rev-parse HEAD >"$state/release-commit"
fi
release_commit=$(cat "$state/release-commit")

# --- 2. Local checks, signed. They run on the release commit before it is merged, so a failing
# check never leaves a release commit on main. A record counts for any commit with the same tree.
check_and_record() {
    checked=$1
    record_dir=$2
    git -C "$src" checkout -q --detach "$checked"
    mkdir -p "$record_dir/logs"
    python=${JITTERSIM_RELEASE_PYTHON:-}
    if [ "$dry_run" = no ] && [ -z "$python" ]; then
        if [ ! -x "$state/venv/bin/python" ]; then
            say "Creating the check environment in $state/venv"
            "$(command -v python3.12 || command -v python3)" -m venv "$state/venv"
            "$state/venv/bin/pip" install -q -e "$src[dev]" 'playwright==1.63.0'
            "$state/venv/bin/python" -m playwright install chromium
        fi
        python="$state/venv/bin/python"
    fi
    : >"$record_dir/checks.tsv"
    run_check() {
        name=$1
        shift
        log="$record_dir/logs/$name.log"
        say "Check $name: $*"
        status=0
        if [ "$dry_run" = yes ] && [ "${JITTERSIM_RELEASE_FAKE_CHECKS:-}" = 1 ]; then
            printf 'fake check for the dry run: %s\n' "$*" >"$log"
        else
            (cd "$src" && "$@") >"$log" 2>&1 || status=$?
        fi
        printf '%s\t%s\t%s\t%s\n' "$name" "$*" "$status" "$(shasum -a 256 "$log" | cut -d ' ' -f 1)" \
            >>"$record_dir/checks.tsv"
        [ "$status" -eq 0 ] || fail "check $name failed (exit $status). See $log. Fix it on main, then delete $state and the release/$tag branch and run again"
    }
    run_check lint "${python:-python3}" -m ruff check --select E9,F63,F7,F82,F811 src tests
    run_check python-tests "${python:-python3}" -m pytest -q -p no:cacheprovider
    run_check browser-journeys "${python:-python3}" -m tests.browser.journeys
    if [ -f "$src/scripts/local-ci.sh" ]; then
        run_check local-ci sh scripts/local-ci.sh
    fi
    python3 - "$record_dir/checks.tsv" "$record_dir/results.json" "$dry_run" <<'EOF'
import json, sys
rows = [line.rstrip("\n").split("\t") for line in open(sys.argv[1]) if line.strip()]
runner = "scripts/release.sh" + (" --dry-run (fake checks)" if sys.argv[3] == "yes" else "")
checks = [{"name": n, "command": c[:500], "exit_code": int(s), "log_sha256": h} for n, c, s, h in rows]
json.dump({"runner": runner, "checks": checks}, open(sys.argv[2], "w"), indent=2)
EOF
    rs record-local --ref "$checked" --results "$record_dir/results.json" --key "$key" \
        --output "$record_dir/local-verification.json"
}
tree_of() { git -C "$src" rev-parse --verify "$1^{tree}"; }
if [ ! -f "$evidence/local-verification.json" ]; then
    check_and_record "$release_commit" "$evidence"
fi
record="$evidence/local-verification.json"

# --- 3. The release PR, merged into main.
if [ ! -f "$state/merged-commit" ]; then
    if [ "$dry_run" = yes ]; then
        printf '%s\n' "$release_commit" >"$state/merged-commit"
        say "Dry run: would push release/$tag, open the release PR and merge it"
    else
        git -C "$src" push -q -u origin "release/$tag"
        pr=$(gh pr list --repo "$REPOSITORY" --head "release/$tag" --state open --json number -q '.[0].number')
        if [ -z "$pr" ]; then
            gh pr create --repo "$REPOSITORY" --base main --head "release/$tag" --title "Release $tag" \
                --body "Sets the version to $number, adds the $tag notes to CHANGELOG.md and reserves the release order. The local checks passed on this commit and are signed in the release evidence. Created by scripts/release.sh." >/dev/null
            pr=$(gh pr list --repo "$REPOSITORY" --head "release/$tag" --state open --json number -q '.[0].number')
        fi
        ask "Merge release PR #$pr into main now?" || { say "Stopped. Run scripts/release.sh $number again to continue."; exit 0; }
        gh pr merge "$pr" --repo "$REPOSITORY" --merge >/dev/null
        merged=$(gh pr view "$pr" --repo "$REPOSITORY" --json mergeCommit -q .mergeCommit.oid)
        printf '%s\n' "$merged" | LC_ALL=C grep -Eq '^[0-9a-f]{40}$' || fail "PR #$pr is not merged yet"
        git -C "$REPO_ROOT" fetch -q origin main
        git -C "$REPO_ROOT" merge-base --is-ancestor "$release_commit" "$merged" \
            || fail "the merge does not contain the release commit"
        printf '%s\n' "$merged" >"$state/merged-commit"
    fi
fi
commit=$(cat "$state/merged-commit")
if [ "$(tree_of "$commit")" != "$(tree_of "$release_commit")" ]; then
    # main changed while the PR waited. Check exactly what was merged.
    record="$evidence/merged/local-verification.json"
    [ -f "$record" ] || check_and_record "$commit" "$evidence/merged"
fi
git -C "$src" checkout -q --detach "$commit"
[ "$(sed -n 's/^version = "\(.*\)"$/\1/p' "$src/pyproject.toml")" = "$number" ] \
    || fail "main at $commit does not say version $number. Another change replaced it"

# --- 4. The exact package, signed.
if [ ! -f "$package/release.json" ]; then
    if [ "$dry_run" = yes ]; then
        sequence=$(python3 -c 'import json, sys; print([r for r in json.load(open(sys.argv[1]))["reservations"] if r["version"] == sys.argv[2]][0]["sequence"])' \
            "$src/docs/releases/registry.json" "$number")
        git -C "$src" tag -a "$tag" "$commit" -m "JitterSim $number"
        mkdir -m 700 "$package"
        (cd "$src" && JITTERSIM_RELEASE_SEQUENCE=$sequence sh deploy/raspberry-pi/build-release.sh \
            "$commit" "$package/jittersim-release.tar.gz" "$key" >/dev/null)
        rs inspect "$package/jittersim-release.tar.gz" --trusted-key "$key.pub" >"$package/release.json"
    else
        rs prepare --ref "$commit" --version "$number" --output "$package" --key "$key" \
            --local-evidence "$record"
    fi
fi
say "Package: $package/jittersim-release.tar.gz"

if [ "$dry_run" = yes ]; then
    say "Dry run: would stop here until the package is tested on the Pi, then approve and publish"
    git -C "$src" checkout -q -b "release/$tag-record" "$commit"
    set_version "$next"
    git -C "$src" commit -q -am "Start $next development"
    say "Dry run finished. Everything is in $state"
    exit 0
fi

# --- 5. Physical acceptance of the exact package, then the owner's signed approval.
if [ ! -f "$package/approval.json" ]; then
    if ! python3 -c 'import json, sys; sys.exit(not all(g["passed"] for g in json.load(open(sys.argv[1]))["gates"].values()))' \
        "$package/acceptance.json"; then
        cat <<EOF

Install this exact package on the Pi and test it before it is approved:

  jittersim remote update upload --archive $package/jittersim-release.tar.gz
  jittersim remote update install --wait 900

Then check the Pi with docs/release-checklist-template.md: dashboard and shaping work,
upgrade from the previous release, Restore previous installation and back, saved settings
kept, management access independent of the test Wi-Fi, safety state unchanged.

EOF
        ask_always "Have you installed and tested this exact package on the Pi?" \
            || { say "Run scripts/release.sh $number again when it is tested."; exit 0; }
        target=$(prompt "Tested Pi (model / OS / architecture / Python)")
        [ -n "$target" ] || fail "name the tested Pi"
        set --
        for gate in physical upgrade recovery saved_settings management_access safety_state; do
            text=$(prompt "Evidence that '$gate' passed (empty stops)")
            [ -n "$text" ] || fail "every gate needs evidence. Nothing was approved"
            set -- "$@" --gate "$gate=$text"
        done
        { cat "$evidence/notes.md"; printf '\nInstall from the dashboard Updates page or run sudo jittersim update on the Pi. A failed install restores the previous installation automatically.\n'; } >"$evidence/release-notes.md"
        rs accept --bundle "$package" --notes-file "$evidence/release-notes.md" --target "$target" "$@"
    fi
    say "Signing the approval of $tag with $key"
    rs approve --bundle "$package" --evidence "$package/acceptance.json" --key "$key" --confirm-version "$number"
fi

# --- 6. Publish.
if [ ! -f "$package/publication.json" ]; then
    rs publish --bundle "$package" --trusted-key "$key.pub" --confirm-version "$number" --dry-run
    ask "Publish $tag on GitHub as the latest release? Pis with the online check will offer it." \
        || { say "Not published. Run scripts/release.sh $number again to publish."; exit 0; }
    rs publish --bundle "$package" --trusted-key "$key.pub" --confirm-version "$number"
fi

# --- 7. Record the release and move main to the next patch version.
if [ ! -f "$state/recorded" ]; then
    git -C "$REPO_ROOT" fetch -q origin main
    cp "$src/docs/releases/registry.json" "$state/registry.json"
    git -C "$src" checkout -q -- docs/releases/registry.json
    git -C "$src" checkout -q -B "release/$tag-record" origin/main
    cp "$state/registry.json" "$src/docs/releases/registry.json"
    python3 - "$package" "$src/docs/releases/$tag.json" <<'EOF'
import json, sys
from pathlib import Path
package = Path(sys.argv[1])
release = json.loads((package / "release.json").read_text())
publication = json.loads((package / "publication.json").read_text())
keep = ("version", "commit", "sequence", "archive_sha256", "signature_sha256", "locks_sha256", "checks")
record = {name: release[name] for name in keep}
record.update(tag=publication["tag"], release_id=publication["release_id"])
Path(sys.argv[2]).write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")
EOF
    set_version "$next"
    git -C "$src" add docs/releases/registry.json "docs/releases/$tag.json" pyproject.toml src/jittersim/__init__.py
    git -C "$src" commit -q -m "Record the $tag release and start $next development"
    git -C "$src" push -q -f -u origin "release/$tag-record"
    pr=$(gh pr list --repo "$REPOSITORY" --head "release/$tag-record" --state open --json number -q '.[0].number')
    if [ -z "$pr" ]; then
        gh pr create --repo "$REPOSITORY" --base main --head "release/$tag-record" --title "Record the $tag release" \
            --body "Marks $tag published in the release registry, keeps its identity and check receipts in docs/releases/$tag.json and moves main to $next so development builds stay newer than $tag. Created by scripts/release.sh." >/dev/null
        pr=$(gh pr list --repo "$REPOSITORY" --head "release/$tag-record" --state open --json number -q '.[0].number')
    fi
    if ask "Merge PR #$pr (release record and version $next) now?"; then
        gh pr merge "$pr" --repo "$REPOSITORY" --merge >/dev/null
    fi
    : >"$state/recorded"
    git -C "$REPO_ROOT" worktree remove --force "$src" 2>/dev/null || true
fi
say "$tag is published. The Pi offers it under Updates or with: sudo jittersim update"
say "Package and evidence are kept in $state"
