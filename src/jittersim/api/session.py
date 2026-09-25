"""Browser sign-in sessions signed with the admin token."""

import base64
import hashlib
import hmac
import secrets
import threading
from http.cookies import CookieError, SimpleCookie
from typing import Dict, List, Optional

SESSION_COOKIE = "session"
SESSION_SECONDS = 7 * 24 * 60 * 60


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


def _cookie(value: str, max_age: int, secure: bool) -> str:
    attributes = [
        f"{SESSION_COOKIE}={value}",
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


def session_from_cookie_header(header: Optional[str]) -> Optional[str]:
    """Read the session cookie's value from a Cookie request header."""

    if not header:
        return None
    cookies: SimpleCookie = SimpleCookie()
    try:
        cookies.load(header)
    except CookieError:
        return None
    morsel = cookies.get(SESSION_COOKIE)
    return morsel.value if morsel is not None else None


class SignInLimiter:
    """Refuse sign-in from an address after repeated failures."""

    max_failures = 5
    window_seconds = 15 * 60

    def __init__(self, *, max_addresses: int = 1_000) -> None:
        self._failures: Dict[str, List[float]] = {}
        self._max_addresses = max_addresses
        self._lock = threading.Lock()

    def _recent(self, address: str, now: float) -> List[float]:
        return [at for at in self._failures.get(address, ()) if now - at < self.window_seconds]

    def is_locked(self, address: str, *, now: float) -> bool:
        """Return whether this address has failed too often in the window."""

        with self._lock:
            return len(self._recent(address, now)) >= self.max_failures

    def record_failure(self, address: str, *, now: float) -> None:
        """Count one failed sign-in from this address."""

        with self._lock:
            recent = self._recent(address, now)
            recent.append(now)
            self._failures[address] = recent[-self.max_failures:]
            if len(self._failures) > self._max_addresses:
                # Forget the addresses whose last failure is oldest.
                ordered = sorted(self._failures, key=lambda key: self._failures[key][-1])
                for stale in ordered[: len(self._failures) - self._max_addresses]:
                    del self._failures[stale]

    def clear(self, address: str) -> None:
        """Forget this address's failures after it signs in."""

        with self._lock:
            self._failures.pop(address, None)
