"""Browser sign-in sessions signed with the admin token."""

import base64
import hashlib
import hmac
import re
import secrets
import threading
from typing import Dict, List, Optional

SESSION_COOKIE = "session"
SESSION_SECONDS = 7 * 24 * 60 * 60
# Longer Cookie headers are ignored rather than parsed.
MAX_COOKIE_HEADER_CHARS = 8_192
_SESSION_VALUE_PATTERN = re.compile(r"^\d{1,12}\.[A-Za-z0-9_-]{1,64}\.[A-Za-z0-9_-]{1,64}$")


def _signature(admin_token: str, payload: str) -> str:
    # A derived key keeps session signatures apart from other uses of the token.
    key = hashlib.sha256(b"session:" + admin_token.encode("utf-8")).digest()
    digest = hmac.new(key, payload.encode("utf-8"), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def issue_session(admin_token: str, *, now: int) -> str:
    """Return a new cookie value that stays valid for SESSION_SECONDS."""

    payload = f"{now + SESSION_SECONDS}.{secrets.token_urlsafe(16)}"
    return f"{payload}.{_signature(admin_token, payload)}"


def session_is_valid(admin_token: Optional[str], value: Optional[str], *, now: int) -> bool:
    """Check a cookie value's signature and expiry against the admin token."""

    if not admin_token or not value:
        return False
    parts = value.split(".")
    if len(parts) != 3 or not parts[0].isdigit() or not parts[1]:
        return False
    expected = _signature(admin_token, f"{parts[0]}.{parts[1]}")
    # Compare bytes: compare_digest rejects str values with non-ASCII characters.
    if not hmac.compare_digest(parts[2].encode("utf-8"), expected.encode("utf-8")):
        return False
    return now < int(parts[0])


def session_cookie_name(*, secure: bool) -> str:
    """Name the cookie for the transport the dashboard serves."""

    # Browsers accept __Host- cookies only over HTTPS, with Path=/ and no Domain,
    # which also keeps this cookie apart from other apps' "session" cookies.
    return f"__Host-{SESSION_COOKIE}" if secure else SESSION_COOKIE


def _cookie(value: str, max_age: int, secure: bool) -> str:
    attributes = [
        f"{session_cookie_name(secure=secure)}={value}",
        "Path=/",
        f"Max-Age={max_age}",
        "HttpOnly",
        "SameSite=Strict",
    ]
    if secure:
        attributes.append("Secure")
    return "; ".join(attributes)


def session_cookie(value: str, *, secure: bool) -> str:
    """Return the Set-Cookie value that signs this browser in."""

    return _cookie(value, SESSION_SECONDS, secure)


def cleared_session_cookie(*, secure: bool) -> str:
    """Return the Set-Cookie value that signs this browser out."""

    return _cookie("", 0, secure)


def session_from_cookie_header(
    header: Optional[str], *, name: str = SESSION_COOKIE
) -> Optional[str]:
    """Read the session cookie's value from a Cookie request header."""

    # Parsed by hand: SimpleCookie stops at the first cookie it can't read, so
    # another app's odd cookie on the same address would sign this browser out.
    if not header or len(header) > MAX_COOKIE_HEADER_CHARS:
        return None
    for part in header.split(";"):
        key, separator, value = part.strip().partition("=")
        if separator and key == name and _SESSION_VALUE_PATTERN.fullmatch(value):
            return value
    return None


class SignInLimiter:
    """Refuse sign-in from an address after repeated failures."""

    max_failures = 5
    window_seconds = 15 * 60

    def __init__(self, *, max_addresses: int = 1_000) -> None:
        self._failures: Dict[str, List[float]] = {}
        self._locked_until: Dict[str, float] = {}
        self._max_addresses = max_addresses
        self._lock = threading.Lock()

    def _recent(self, address: str, now: float) -> List[float]:
        return [at for at in self._failures.get(address, ()) if now - at < self.window_seconds]

    def is_locked(self, address: str, *, now: float) -> bool:
        """Return whether this address must wait before signing in again."""

        with self._lock:
            until = self._locked_until.get(address)
            if until is None:
                return False
            if now < until:
                return True
            del self._locked_until[address]
            return False

    def record_failure(self, address: str, *, now: float) -> None:
        """Count one failed sign-in; the fifth within the window locks the address."""

        with self._lock:
            recent = self._recent(address, now)
            recent.append(now)
            if len(recent) >= self.max_failures:
                # A lock always lasts the whole window from the failure that caused it.
                self._locked_until[address] = now + self.window_seconds
                self._failures.pop(address, None)
            else:
                self._failures[address] = recent
            for table in (self._failures, self._locked_until):
                if len(table) > self._max_addresses:
                    # Forget the addresses whose last entry is oldest.
                    def last(key):
                        entry = table[key]
                        return entry[-1] if isinstance(entry, list) else entry

                    for stale in sorted(table, key=last)[: len(table) - self._max_addresses]:
                        del table[stale]

    def clear(self, address: str) -> None:
        """Forget this address's failures after it signs in."""

        with self._lock:
            self._failures.pop(address, None)
            self._locked_until.pop(address, None)
