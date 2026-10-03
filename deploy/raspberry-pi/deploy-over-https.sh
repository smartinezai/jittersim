#!/bin/sh
# Deploy one commit to the Pi through its HTTPS dashboard instead of SSH. It works from any
# network that reaches https://PI:8080, also the Pi's own test Wi-Fi where SSH is blocked on
# purpose. deploy-from-mac.sh runs this for you when SSH does not work. It builds and signs the
# release, uploads it to /api/updates/upload, installs it and waits until
# the Pi answers with the new release. Git, ssh-keygen, curl, tar and Python 3 are needed.
# The admin token comes from JITTERSIM_TOKEN or the macOS Keychain item jittersim-admin-token
# (create it with --save-token). The token is never put on a command line, printed or saved
# by this script. The Pi's certificate is checked against the file given by --ca-cert only,
# like the `jittersim` command line does. Plain POSIX sh.
set -eu

KEYCHAIN_ITEM=jittersim-admin-token
DEFAULT_SIGNING_KEY="$HOME/.ssh/jittersim_release_ed25519"
DEFAULT_PORT=8080
TEST_WIFI_ADDRESS=192.168.50.1
UPLOAD_SECONDS=300
INSTALL_WAIT_SECONDS=${JITTERSIM_INSTALL_WAIT_SECONDS:-900}

usage() {
    cat <<USAGE
usage: deploy-over-https.sh [--ref REF] [--url https://PI:8080] [--ca-cert FILE]
                            [--signing-key PATH] [--stage-only] [--dry-run]
       deploy-over-https.sh --save-token

Builds and signs the release of REF, uploads it over HTTPS then installs it and
waits until the Pi runs it. Use --stage-only to upload without installing.
The Pi stages only a release signed by a key it trusts and newer
than what it ran before, so the admin token alone cannot install other code.

  --url URL        the dashboard (default: JITTERSIM_URL, the address saved by
                   'jittersim connect', https://$TEST_WIFI_ADDRESS:$DEFAULT_PORT on the Pi's test Wi-Fi,
                   or https://HOST:$DEFAULT_PORT for --host)
  --ca-cert FILE   a copy of /etc/jittersim/dashboard.crt (default: JITTERSIM_CA_CERT or the
                   file saved by 'jittersim connect')
  --save-token     store the admin token in the macOS Keychain (item $KEYCHAIN_ITEM). The
                   Keychain asks for it with a hidden prompt.
  --stage-only     upload the release without installing it
  --install        install and wait (the default). Kept for existing commands
  --dry-run        print every command and run nothing
On a Mac on the Pi's test Wi-Fi, first open the dashboard in a browser on this Mac and choose
Allow this device once.
USAGE
}

fail() {
    printf 'deploy-over-https: %s\n' "$1" >&2
    exit "${2:-1}"
}

monotonic_milliseconds() {
    python3 -c 'import time; print(int(time.clock_gettime(time.CLOCK_MONOTONIC) * 1000))'
}

seconds_from_milliseconds() {
    printf '%s.%03d' "$(( $1 / 1000 ))" "$(( $1 % 1000 ))"
}

ref=HEAD
url=${JITTERSIM_URL:-}
cert=${JITTERSIM_CA_CERT:-}
host=
signing_key=$DEFAULT_SIGNING_KEY
install=yes
dry_run=no
save_token=no
while [ "$#" -gt 0 ]; do
    case $1 in
        --ref|--url|--ca-cert|--signing-key|--host)
            [ "$#" -ge 2 ] || fail "$1 needs a value" 2
            case $1 in
                --ref) ref=$2 ;;
                --url) url=$2 ;;
                --ca-cert) cert=$2 ;;
                --signing-key) signing_key=$2 ;;
                --host) host=$2 ;;
            esac
            shift 2
            ;;
        --install) install=yes; shift ;;
        --stage-only) install=no; shift ;;
        --dry-run) dry_run=yes; shift ;;
        --save-token) save_token=yes; shift ;;
        -h|--help) usage; exit 0 ;;
        *) usage >&2; exit 2 ;;
    esac
done

if [ "$save_token" = yes ]; then
    command -v security >/dev/null 2>&1 || fail "this needs the macOS Keychain (the security command)" 2
    printf 'The Keychain now asks for the admin token with a hidden prompt.\n'
    security add-generic-password -U -a "${USER:-jittersim}" -s "$KEYCHAIN_ITEM" -w \
        || fail "the token was not saved in the Keychain"
    printf 'Saved the admin token in the Keychain item %s.\n' "$KEYCHAIN_ITEM"
    exit 0
fi

case $ref in ''|-*) fail "refusing ref '$ref'" 2 ;; esac
script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
repo=$(git -C "$script_dir" rev-parse --show-toplevel) || fail "run this from a JitterSim checkout"
commit=$(git -C "$repo" rev-parse --verify --quiet "$ref^{commit}") || fail "unknown ref '$ref'" 2
short=$(git -C "$repo" rev-parse --short "$commit")
case $short in *[!0-9a-f]*|'') fail "unexpected commit name '$short'" ;; esac

# The saved connection file of 'jittersim connect' (two plain lines of JSON).
saved() {
    file="$HOME/.config/jittersim/cli.json"
    [ -f "$file" ] || return 0
    sed -n "s/^ *\"$1\": *\"\\([^\"]*\\)\".*/\\1/p" "$file" | head -n 1
}
if [ -z "$url" ]; then
    if ifconfig 2>/dev/null | grep -q 'inet 192\.168\.50\.'; then
        url="https://$TEST_WIFI_ADDRESS:$DEFAULT_PORT"
    elif [ -n "$(saved url)" ]; then
        url=$(saved url)
    elif [ -n "$host" ]; then
        url="https://${host#*@}:$DEFAULT_PORT"
    else
        fail "tell me where the dashboard is: --url https://PI:$DEFAULT_PORT (or run 'jittersim connect' once)" 2
    fi
fi
case $url in
    https://*) ;;
    *) fail "the dashboard address must start with https://" 2 ;;
esac
url=${url%/}
[ -n "$cert" ] || cert=$(saved ca_cert)
[ -n "$cert" ] || fail "give the Pi's dashboard certificate with --ca-cert FILE (a copy of /etc/jittersim/dashboard.crt). Without it the connection cannot be trusted." 2
if [ "$dry_run" = no ]; then
    [ -f "$cert" ] || fail "the certificate file $cert does not exist" 2
    [ -f "$signing_key" ] || fail "no release signing key at $signing_key. Create one with deploy/raspberry-pi/make-release-key.sh" 2
fi

printf 'Commit: %s (%s)\nDashboard: %s\n' "$commit" "$short" "$url"
if [ "$dry_run" = yes ]; then
    work="${TMPDIR:-/tmp}/jittersim-https.XXXXXX"
else
    work=$(mktemp -d "${TMPDIR:-/tmp}/jittersim-https.XXXXXX")
    trap 'rm -rf "$work"' EXIT
    trap 'exit 1' HUP INT TERM
fi
archive="$work/jittersim-release-$short.tar.gz"
printf '+ sh deploy/raspberry-pi/build-release.sh %s %s %s\n' "$commit" "$archive" "$signing_key"

token=
if [ "$dry_run" = no ]; then
    # Take the token into a shell variable and drop it from the environment at once, so that
    # git, ssh-keygen and the build never inherit it. Only curl's standard input gets it.
    token=${JITTERSIM_TOKEN:-${JITTERSIM_ADMIN_TOKEN:-}}
    unset JITTERSIM_TOKEN JITTERSIM_ADMIN_TOKEN
    if [ -z "$token" ] && command -v security >/dev/null 2>&1; then
        token=$(security find-generic-password -s "$KEYCHAIN_ITEM" -w 2>/dev/null) || token=
    fi
    [ -n "$token" ] || fail "no admin token. Set JITTERSIM_TOKEN or store it once with: deploy/raspberry-pi/deploy-from-mac.sh --save-token"
    # No space, quote, backslash, control character or line break: one plain header value.
    newline='
'
    case $token in
        *"$newline"*) token_bad=yes ;;
        *) token_bad=no ;;
    esac
    if [ "$token_bad" = yes ] || printf '%s' "$token" | LC_ALL=C grep -q '[^!-~]\|["\\]'; then
        fail "the admin token has characters that cannot be right. Store it again with --save-token"
    fi
    if [ "$install" = yes ]; then
        case $INSTALL_WAIT_SECONDS in
            ''|0*|*[!0-9]*) fail "the install wait must be a whole number from 1 to 900 seconds" ;;
        esac
        [ "${#INSTALL_WAIT_SECONDS}" -le 3 ] && [ "$INSTALL_WAIT_SECONDS" -le 900 ] \
            || fail "the install wait must be a whole number from 1 to 900 seconds"
        monotonic_milliseconds >/dev/null 2>&1 \
            || fail "Python 3 is needed to keep the install wait within its time limit"
    fi
    (cd "$repo" && sh deploy/raspberry-pi/build-release.sh "$commit" "$archive" "$signing_key" >/dev/null) \
        || fail "building or signing the release failed"
fi

# One call to the dashboard. The token goes to curl on its standard input as a header line so it
# never shows in a process list. Redirects are refused and only --ca-cert is trusted.
api() {
    method=$1
    path=$2
    shift 2
    limit=$1
    shift
    code=$(printf 'header = "Authorization: Bearer %s"\n' "$token" | curl --silent --show-error \
        --config - --cacert "$cert" --noproxy '*' --max-redirs 0 --http1.1 \
        --connect-timeout 10 --max-time "$limit" --output "$work/answer" \
        --write-out '%{http_code}' --request "$method" "$@" "$url$path" 2>"$work/curl.err") \
        || code=000
    case $code in
        [0-9][0-9][0-9]) printf '%s' "$code" ;;
        *) printf '000' ;;
    esac
}

# Plain words for a failed call. $1 is the HTTP status and the answer is in $work/answer.
explain() {
    case $1 in
        000)
            if grep -qi "certificate\|SSL" "$work/curl.err" 2>/dev/null; then
                fail "the Pi's certificate does not match $cert. Copy /etc/jittersim/dashboard.crt from the Pi again. If the Pi has a new address run the installer on the Pi again."
            fi
            fail "could not reach the dashboard at $url. Check that the Pi is on and that this Mac is on its router network or its test Wi-Fi."
            ;;
        401) fail "the dashboard refused the admin token. Store the right one with --save-token." ;;
        403) fail "the dashboard does not allow this Mac yet. Open $url in a browser on this Mac and choose Allow this device, then run this again." ;;
    esac
    message=$(sed -n 's/.*"error": *"\([^"]*\)".*/\1/p' "$work/answer" 2>/dev/null | head -n 1)
    fail "the dashboard answered HTTP $1${message:+: $message}"
}

field() {
    sed -n "s/.*\"$1\": *\"\\([^\"]*\\)\".*/\\1/p" "$work/answer" | head -n 1
}

if [ "$dry_run" = yes ]; then
    printf '+ curl --cacert %s GET %s/api/updates\n' "$cert" "$url"
    printf '+ curl --cacert %s POST %s/api/updates/upload (archive and signature, %s)\n' "$cert" "$url" "$short"
    if [ "$install" = yes ]; then
        printf '+ curl --cacert %s POST %s/api/updates/install\n' "$cert" "$url"
        printf '+ curl --cacert %s GET %s/api/updates (until the Pi runs %s)\n' "$cert" "$url" "$short"
    fi
    printf 'Dry run: nothing was built, uploaded or installed.\n'
    exit 0
fi

# Look first: the token and the Allow this device rule are checked before the big upload.
status=$(api GET /api/updates 30)
[ "$status" = 200 ] || explain "$status"
running=$(field installed)
if [ "$running" = "$short" ]; then
    printf 'The Pi already runs release %s. Nothing to upload or install.\n' "$short"
    exit 0
fi

cat "$archive" "$archive.sig" >"$work/upload"
archive_bytes=$(wc -c <"$archive" | tr -d ' ')
printf 'Uploading release %s (%s bytes) over HTTPS.\n' "$short" "$archive_bytes"
status=$(api POST /api/updates/upload "$UPLOAD_SECONDS" \
    --header "Content-Type: application/octet-stream" \
    --header "X-Jittersim-Archive-Bytes: $archive_bytes" --data-binary "@$work/upload")
[ "$status" = 200 ] || explain "$status"
staged=$(field staged_version)
[ -n "$staged" ] || fail "the dashboard accepted the upload but did not say which release it staged"
printf 'The Pi checked the signature and staged release %s.\n' "$staged"

if [ "$install" = no ]; then
    printf 'Install it with: deploy-from-mac.sh --install, or choose Install staged release in the dashboard.\n'
    exit 0
fi
status=$(api POST /api/updates/install 60 --header "Content-Type: application/json" \
    --data "{\"version\": \"$staged\", \"confirm\": true}")
[ "$status" = 200 ] || [ "$status" = 202 ] || explain "$status"
printf 'Install queued. Waiting for the Pi to run release %s.\n' "$staged"
wait_deadline=$(( $(monotonic_milliseconds) + INSTALL_WAIT_SECONDS * 1000 ))
while :; do
    remaining=$((wait_deadline - $(monotonic_milliseconds)))
    [ "$remaining" -gt 0 ] || break
    call_milliseconds=$remaining
    [ "$call_milliseconds" -le 15000 ] || call_milliseconds=15000
    status=$(api GET /api/updates "$(seconds_from_milliseconds "$call_milliseconds")")
    remaining=$((wait_deadline - $(monotonic_milliseconds)))
    [ "$remaining" -gt 0 ] || break
    if [ "$status" = 200 ] && [ "$(field installed)" = "$staged" ]; then
        printf 'The Pi now runs release %s.\n' "$staged"
        exit 0
    fi
    sleep_milliseconds=$remaining
    [ "$sleep_milliseconds" -le 5000 ] || sleep_milliseconds=5000
    sleep "$(seconds_from_milliseconds "$sleep_milliseconds")"
done
fail "the Pi does not run release $staged after $INSTALL_WAIT_SECONDS seconds. On the Pi check: jittersim update check"
