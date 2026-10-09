"""Shared checks and corpora of the API robustness tests (QA, 2026-10-09)."""

import json
import re
import traceback

from jittersim.api.server import WRITE_PATHS
from tests.qa.dashboard_support import TOKEN

# Routes that answer with a file or are handled outside the JSON body path.
UPLOAD_PATH = "/api/updates/upload"
POST_ROUTES = tuple(sorted(WRITE_PATHS - {UPLOAD_PATH}))
GET_ROUTES = (
    "/", "/favicon.ico", "/api/access", "/api/action-history", "/api/activity", "/api/activity/latest",
    "/api/backup", "/api/brand", "/api/conditions", "/api/connected-devices", "/api/dashboard-access",
    "/api/dashboard-access/moves", "/api/dashboard-access/status", "/api/device-workspace/alice",
    "/api/diagnostics", "/api/export", "/api/health", "/api/history", "/api/impact", "/api/observations",
    "/api/pattern-history", "/api/plan", "/api/random-website-analytics", "/api/router", "/api/state",
    "/api/system-health", "/api/test-sessions", "/api/test-sessions/x", "/api/traffic", "/api/trust",
    "/api/trust/ca.crt", "/api/trust/profile.mobileconfig", "/api/updates", "/api/updates/check-online/abc",
    "/api/uplink", "/api/wifi-operation", "/api/wifi-operation/request-id",
    "/api/profiles", "/api/devices", "/api/groups", "/api/schedules", "/api/scenarios", "/api/timers",
    "/api/progressions", "/api/data-limits", "/api/group-data-limits", "/api/service-quotas",
    "/api/service-assignments", "/api/service-lists", "/api/time-quotas", "/api/policies",
    "/api/everyone-else", "/api/ad-blocking", "/api/unaffected-devices", "/api/general-profile",
    "/api/connected-device-labels", "/api/traffic-settings", "/api/gateway-persistence", "/api/profiles/ranges",
)
# Public by design: the page and the health check answer without a token.
PUBLIC = ("/", "/favicon.ico", "/api/health")

# What the log says when a request ends in a server error. These four are confirmed defects of
# 2026-10-09 (see tests/qa/test_api_known_defects.py). A fuzz run may meet them again: it only
# fails on a server error it has not seen before.
KNOWN_ERROR_SIGNATURES = (
    ("ValueError", "Exceeds the limit (4300 digits)"),
    ("UnicodeEncodeError", "surrogates not allowed"),
    ("ProgrammingError", "Error binding parameter"),
    ("OverflowError", "too large to convert to SQLite INTEGER"),
)


def signature(record):
    kind, error, _ = record.exc_info
    return kind.__name__, str(error)


def is_known(record):
    kind, message = signature(record)
    return any(kind == name and part in message for name, part in KNOWN_ERROR_SIGNATURES)


def location(record):
    frames = [f for f in traceback.extract_tb(record.exc_info[2]) if "jittersim" in f.filename]
    return f"{frames[-1].filename.split('/src/')[-1]}:{frames[-1].lineno}" if frames else "?"


def judge(dashboard, method, path, status, headers, body, first_new_log, *, sent=b""):
    """Return a list of problems with one response. Empty means the response is fine."""

    problems = []
    kind = headers.get("Content-Type", "")
    new_records = dashboard.logs.records[first_new_log:]
    tracebacks = [record for record in new_records if record.exc_info]
    unknown = [record for record in tracebacks if not is_known(record)]
    if status >= 500 and status != 503 and not (tracebacks and not unknown):
        problems.append(f"HTTP {status}")
    if unknown:
        problems.append("unexpected exception " + "; ".join(
            f"{signature(r)[0]}: {signature(r)[1][:80]} at {location(r)}" for r in unknown[:2]))
    if b"Traceback" in body or b'File "' in body:
        problems.append("traceback in body")
    if TOKEN.encode() in body or TOKEN in str(headers):
        problems.append("token in response")
    for record in new_records:
        if TOKEN in record.getMessage():
            problems.append("token in log")
    if path.startswith("/api/") and path.split("?")[0] not in ("/api/backup", "/api/export", "/api/trust/ca.crt",
                                                    "/api/trust/profile.mobileconfig"):
        if not kind.startswith("application/json"):
            problems.append(f"non-JSON {kind or 'response'} (HTTP {status})")
        else:
            try:
                json.loads(body)
            except ValueError:
                problems.append("body is not JSON")
    return problems


def call(dashboard, method, path, body=None, **kwargs):
    """Send one request and return (status, headers, body, index of the first new log record)."""

    first = len(dashboard.logs.records)
    status, headers, data = dashboard.request(method, path, body=body, **kwargs)
    return status, headers, data, first


def check(dashboard, method, path, body=None, **kwargs):
    status, headers, data, first = call(dashboard, method, path, body, **kwargs)
    return judge(dashboard, method, path, status, headers, data, first), status


NASTY_STRINGS = (
    "", " ", "x" * 70, "x" * 5000, "\x00", "a\nb", "‮", "\U0001f600" * 40, "'; DROP TABLE profiles;--",
    '"; flush ruleset; #', "../../etc/passwd", "${jndi:ldap://x}", "{{7*7}}", "%s%s%s%n", "<script>alert(1)</script>",
    "1e999", "NaN", "-0", "0x10", "192.168.50.999", "ff:ff:ff:ff:ff:ff", "02:00:00:00:00:01",
)
NASTY_NUMBERS = (0, -1, 1, 2, 7, 100, 101, 255, 256, 65535, 2**31, 2**32, 2**53, -2**31, 1.5, -0.0, 1e308, 0.1)
IDENTIFIER = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}")
