"""Talk to the Pi's HTTPS dashboard from another computer such as a Mac.

The client only uses a certificate-verified HTTPS connection to the address it was given.
It never follows a redirect and reads at most a bounded answer. It gives up when the
dashboard does not answer within the timeout or keeps sending an answer for longer than that.
The admin token comes from a hidden prompt every time or from JITTERSIM_ADMIN_TOKEN for
scripts. It is never stored, printed or sent anywhere but the dashboard. The saved
connection file holds only the dashboard address and the certificate path.
"""

import getpass
import http.client
import json
import os
import socket
import ssl
import stat
import time
import unicodedata
import warnings
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import (
    HTTPRedirectHandler,
    HTTPSHandler,
    ProxyHandler,
    Request,
    build_opener,
)

URL_ENV = "JITTERSIM_URL"
CA_CERT_ENV = "JITTERSIM_CA_CERT"
TOKEN_ENV = "JITTERSIM_ADMIN_TOKEN"
CONFIG_RELATIVE_PATH = Path(".config") / "jittersim" / "cli.json"
MAX_CONFIG_BYTES = 4096
MAX_RESPONSE_BYTES = 4 * 1024 * 1024
MAX_ERROR_BYTES = 64 * 1024
MAX_MESSAGE_CHARS = 400
DEFAULT_TIMEOUT_SECONDS = 20.0
ERROR_READ_SECONDS = 5.0
EXAMPLE_URL = "https://192.168.0.17:8080"

CERTIFICATE_MESSAGE = (
    "Certificate check failed: make sure the SHA-256 fingerprint of the --ca-cert file "
    "matches the one the installer printed."
)
NAME_MISMATCH_MESSAGE = (
    "The dashboard's certificate does not cover {host}. Use an address it covers such as the "
    "Pi's name.local or the Ethernet address the Pi had when the installer ran. If the Pi has "
    "a new address run the installer on the Pi again and copy the new certificate."
)
EXPIRED_MESSAGE = (
    "The dashboard's certificate has expired. Run the installer on the Pi again to make a new "
    "one and copy the new /etc/jittersim/dashboard.crt."
)
NOT_YET_VALID_MESSAGE = (
    "The dashboard's certificate is not valid yet. Check the date and time on this computer "
    "and on the Pi."
)
# OpenSSL and LibreSSL verify codes (X509_V_ERR_*) that need a different sentence.
_CERT_NOT_YET_VALID = 9
_CERT_HAS_EXPIRED = 10
_HOSTNAME_MISMATCH = 62
_IP_ADDRESS_MISMATCH = 64
# Zero-width joiners keep emoji sequences together and are harmless on a terminal.
_ALLOWED_FORMAT_CHARACTERS = frozenset("‌‍")
TOKEN_REFUSED_MESSAGE = "The dashboard refused the admin token. Check it and try again."
NOT_ALLOWED_MESSAGE = (
    "The dashboard does not allow this computer yet. On Pi Wi-Fi run "
    "'jittersim dashboard-access allow-self' first or connect through the router network."
)
REDIRECT_MESSAGE = (
    "The dashboard answered with a redirect. Redirects are refused so the token is never "
    "sent anywhere else."
)
UNDERSTOOD_MESSAGE = "The dashboard sent an answer this command does not understand."


class RemoteError(Exception):
    """A failure whose message is safe to print: it never holds a token or a password."""

    def __init__(
        self,
        message: str,
        *,
        exit_code: int = 1,
        status: Optional[int] = None,
        data: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.exit_code = exit_code
        self.status = status
        self.data = data or {}


@dataclass(frozen=True)
class Connection:
    """Where the dashboard is and which certificate it must present."""

    url: str
    ca_cert: Path


def config_path(environ: Optional[Mapping[str, str]] = None) -> Path:
    """Return ~/.config/jittersim/cli.json for the current user."""

    environ = os.environ if environ is None else environ
    home = environ.get("HOME") or str(Path.home())
    return Path(home) / CONFIG_RELATIVE_PATH


def validate_url(raw: Any) -> str:
    """Return https://HOST[:PORT] or refuse the address as a usage error."""

    if (
        not isinstance(raw, str)
        or not raw
        or any(ord(character) < 33 or ord(character) == 127 for character in raw)
    ):
        raise RemoteError(
            f"Give the dashboard address as https://HOST:PORT, for example {EXAMPLE_URL}.",
            exit_code=2,
        )
    try:
        parsed = urlsplit(raw)
    except ValueError as error:
        raise RemoteError(
            f"Give the dashboard address as https://HOST:PORT, for example {EXAMPLE_URL}.",
            exit_code=2,
        ) from error
    if parsed.scheme.lower() != "https":
        raise RemoteError(
            f"Plain HTTP is refused. Use the Pi's HTTPS address, for example {EXAMPLE_URL}.",
            exit_code=2,
        )
    try:
        hostname = parsed.hostname
        port = parsed.port
    except ValueError as error:
        raise RemoteError("The dashboard address has an invalid port.", exit_code=2) from error
    unclear = (
        not hostname
        or "@" in parsed.netloc
        or parsed.path not in ("", "/")
        or "?" in raw
        or "#" in raw
    )
    if not unclear:
        # Nothing may hide next to the host and the port such as "[::1]x:80".
        host = f"[{hostname}]" if ":" in hostname else hostname
        unclear = parsed.netloc.lower() != (host + (f":{port}" if port is not None else ""))
    if unclear:
        raise RemoteError(
            "Use only https://HOST:PORT for the dashboard address: no user name, password, "
            "path, query or fragment.",
            exit_code=2,
        )
    return "https://" + parsed.netloc


def read_config(path: Path) -> Dict[str, str]:
    """Read the saved address and certificate path. A missing file gives nothing.

    The file decides where the token is sent so it must belong to this user and nobody else
    may write it. A token found in it is never used.
    """

    not_regular = f"{path} is not a regular file. Run 'jittersim connect' again."
    try:
        if not stat.S_ISREG(path.lstat().st_mode):
            raise RemoteError(not_regular)
        # The checks below look at the file that was opened, never at a name that a link
        # could point somewhere else in between.
        descriptor = os.open(
            str(path), os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
        )
    except FileNotFoundError:
        return {}
    except OSError as error:
        raise RemoteError(f"Could not read {path}.") from error
    with os.fdopen(descriptor, "rb") as handle:
        info = os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode):
            raise RemoteError(not_regular)
        if info.st_uid != os.geteuid() or info.st_mode & 0o022:
            raise RemoteError(
                f"{path} must belong to you and must not be writable by others. "
                f"Run: chmod 600 {path}"
            )
        try:
            raw = handle.read(MAX_CONFIG_BYTES + 1)
            if len(raw) > MAX_CONFIG_BYTES:
                raise ValueError("too large")
            data = json.loads(raw)
        except (OSError, ValueError, RecursionError) as error:
            raise RemoteError(f"{path} is not a valid JitterSim connection file.") from error
    if not isinstance(data, dict):
        raise RemoteError(f"{path} is not a valid JitterSim connection file.")
    return {
        key: data[key]
        for key in ("url", "ca_cert")
        if isinstance(data.get(key), str) and data[key]
    }


def write_config(path: Path, connection: Connection) -> None:
    """Save only the address and the certificate path with owner-only permissions."""

    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    body = json.dumps(
        {"url": connection.url, "ca_cert": str(connection.ca_cert)}, indent=2
    ) + "\n"
    temporary = path.with_name(path.name + ".tmp")
    descriptor = os.open(
        str(temporary), os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    try:
        os.fchmod(descriptor, 0o600)
        os.write(descriptor, body.encode("utf-8"))
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    os.replace(str(temporary), str(path))


def resolve_connection(
    url: Optional[str],
    ca_cert: Optional[Path],
    *,
    environ: Optional[Mapping[str, str]] = None,
    path: Optional[Path] = None,
) -> Connection:
    """Pick the address and certificate: options first, then the environment, then the file."""

    environ = os.environ if environ is None else environ
    raw_url = url or environ.get(URL_ENV) or None
    raw_cert = str(ca_cert) if ca_cert else environ.get(CA_CERT_ENV) or None
    if raw_url is None or raw_cert is None:
        saved = read_config(path or config_path(environ))
        raw_url = raw_url or saved.get("url")
        raw_cert = raw_cert or saved.get("ca_cert")
    if raw_url is None or raw_cert is None:
        raise RemoteError(
            "Tell the command where the dashboard is: run 'jittersim connect --url "
            f"{EXAMPLE_URL} --ca-cert PATH' once or pass --url and --ca-cert.",
            exit_code=2,
        )
    return Connection(validate_url(raw_url), checked_certificate(Path(raw_cert)))


def checked_certificate(path: Path) -> Path:
    """The --ca-cert file decides who gets the token: nobody else may be able to change it."""

    certificate = path.expanduser()
    try:
        info = certificate.stat()
    except OSError:
        info = None
    if info is None or not stat.S_ISREG(info.st_mode):
        raise RemoteError(f"The certificate file {certificate} does not exist.", exit_code=2)
    if info.st_uid not in (os.geteuid(), 0) or info.st_mode & 0o022:
        raise RemoteError(
            f"The certificate file {certificate} decides where the admin token goes so it must "
            f"belong to you and must not be writable by others. Run: chmod 644 {certificate}",
            exit_code=2,
        )
    return certificate


def valid_token(token: str) -> bool:
    return bool(token) and all(33 <= ord(character) <= 126 for character in token)


def read_token(environ: Optional[Mapping[str, str]] = None) -> str:
    """Ask for the admin token with a hidden prompt unless JITTERSIM_ADMIN_TOKEN is set."""

    environ = os.environ if environ is None else environ
    token = environ.get(TOKEN_ENV) or ""
    if not token:
        token = hidden_prompt("JitterSim admin token (hidden): ")
    token = token.strip()
    if not valid_token(token):
        raise RemoteError("Enter a valid JitterSim admin token.")
    return token


def hidden_prompt(prompt: str) -> str:
    """Read a secret without showing it. A terminal that would echo it is refused."""

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", getpass.GetPassWarning)
            return getpass.getpass(prompt)
    except getpass.GetPassWarning as error:
        raise RemoteError(
            "This terminal cannot hide what you type. Use a terminal with a hidden prompt."
        ) from error
    except (EOFError, KeyboardInterrupt) as error:
        raise RemoteError("Cancelled. Nothing was sent.") from error


def js_number(value: Any) -> str:
    """Write a number the way the dashboard's JavaScript does: 2.0 is "2" and 0.5 is "0.5"."""

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, int):
        return str(value)
    if value != value:
        return "NaN"
    if value in (float("inf"), float("-inf")):
        return "Infinity" if value > 0 else "-Infinity"
    if value == 0:
        return "0"
    sign = "-" if value < 0 else ""
    digits_tuple = Decimal(repr(abs(value))).normalize().as_tuple()
    digits = "".join(str(digit) for digit in digits_tuple.digits)
    exponent = int(digits_tuple.exponent)
    size = len(digits)
    point = exponent + size
    if size <= point <= 21:
        text = digits + "0" * (point - size)
    elif 0 < point <= 21:
        text = digits[:point] + "." + digits[point:]
    elif -6 < point <= 0:
        text = "0." + "0" * (-point) + digits
    else:
        shown = point - 1
        mantissa = digits[0] + ("." + digits[1:] if size > 1 else "")
        text = mantissa + "e" + ("+" if shown >= 0 else "-") + str(abs(shown))
    return sign + text


def js_round(value: float) -> int:
    """JavaScript's Math.round: halves go up, also for negative numbers."""

    floor = int(value // 1)
    return floor + 1 if value - floor >= 0.5 else floor


class _TooSlow(Exception):
    """The answer kept trickling in after its deadline."""


def _read_bounded(stream: Any, limit: int, deadline: float) -> bytes:
    """Read at most `limit` bytes and stop at `deadline` even when bytes keep trickling in."""

    read = getattr(stream, "read1", None) or stream.read
    chunks = []
    size = 0
    while size < limit:
        chunk = read(min(65536, limit - size))
        if not chunk:
            break
        chunks.append(chunk)
        size += len(chunk)
        if time.monotonic() > deadline:
            raise _TooSlow()
    return b"".join(chunks)


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def printable(text: Any, replacement: str = "?") -> str:
    """Text that is safe to show on a terminal.

    Control characters (C0, DEL and C1 such as ESC or U+009B), bidi overrides and other
    invisible format characters become `replacement` so a name from the dashboard can never
    move the cursor, clear the screen or reverse what follows. Other spaces become a space.
    """

    if not isinstance(text, str):
        text = str(text)
    shown = []
    for character in text:
        if character.isprintable() or character in _ALLOWED_FORMAT_CHARACTERS:
            shown.append(character)
        elif unicodedata.category(character) == "Zs":
            shown.append(" ")
        else:
            shown.append(replacement)
    return "".join(shown)


def certificate_message(error: Any, host: str) -> str:
    """Say what is wrong with the certificate: only an unknown one is about the fingerprint."""

    code = getattr(error, "verify_code", None)
    if code in (_HOSTNAME_MISMATCH, _IP_ADDRESS_MISMATCH):
        return NAME_MISMATCH_MESSAGE.format(host=printable(host))
    if code == _CERT_HAS_EXPIRED:
        return EXPIRED_MESSAGE
    if code == _CERT_NOT_YET_VALID:
        return NOT_YET_VALID_MESSAGE
    return CERTIFICATE_MESSAGE


def _clean(text: Any, secrets: Iterable[str] = ()) -> str:
    """Make a message from the dashboard safe for a terminal: one short line, no secrets."""

    if not isinstance(text, str):
        return ""
    cleaned = printable(text, " ").strip()
    for secret in secrets:
        if secret:
            cleaned = cleaned.replace(secret, "[hidden]")
    if len(cleaned) > MAX_MESSAGE_CHARS:
        cleaned = cleaned[: MAX_MESSAGE_CHARS - 1] + "…"
    return cleaned


class DashboardClient:
    """Send authenticated JSON requests to one verified HTTPS dashboard."""

    def __init__(
        self,
        connection: Connection,
        token: Optional[str],
        *,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        max_bytes: int = MAX_RESPONSE_BYTES,
        secrets: Iterable[str] = (),
    ) -> None:
        self.url = connection.url
        self.timeout = timeout
        self.max_bytes = max_bytes
        self._token = token
        self._secrets = [token or "", *secrets]
        self.host = urlsplit(self.url).hostname or self.url
        try:
            # Only this file is trusted: no system certificate authorities are loaded.
            context = ssl.create_default_context(cafile=str(connection.ca_cert))
        except (OSError, ssl.SSLError, ValueError) as error:
            raise RemoteError(
                f"The certificate file {connection.ca_cert} could not be read as a certificate."
            ) from error
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        self.context = context
        # Environment proxies are ignored: the dashboard is on the local network.
        self._opener = build_opener(
            ProxyHandler({}), _NoRedirect(), HTTPSHandler(context=context)
        )

    def add_secret(self, secret: str) -> None:
        """Remember another secret that must never appear in a message."""

        self._secrets.append(secret)

    def get(self, path: str, *, timeout: Optional[float] = None) -> Dict[str, Any]:
        return self._send("GET", path, None, timeout)

    def post(
        self, path: str, payload: Dict[str, Any], *, timeout: Optional[float] = None
    ) -> Dict[str, Any]:
        return self._send("POST", path, payload, timeout)

    def _send(
        self, method: str, path: str, payload: Optional[Dict[str, Any]], timeout: Optional[float]
    ) -> Dict[str, Any]:
        seconds = self.timeout if timeout is None else timeout
        headers = {"Accept": "application/json"}
        if self._token is not None:
            headers["Authorization"] = "Bearer " + self._token
        data = None
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = Request(self.url + path, data=data, method=method, headers=headers)
        try:
            with self._opener.open(request, timeout=seconds) as response:
                body = _read_bounded(response, self.max_bytes + 1, time.monotonic() + seconds)
        except HTTPError as error:
            raise self._refused(error) from None
        except URLError as error:
            raise self._unreachable(error.reason, seconds) from None
        except _TooSlow:
            raise RemoteError(
                f"The dashboard did not finish its answer within {js_number(seconds)} seconds."
            ) from None
        except (socket.timeout, TimeoutError):
            raise RemoteError(
                f"The dashboard did not answer within {js_number(seconds)} seconds."
            ) from None
        except ssl.SSLCertVerificationError as error:
            raise RemoteError(certificate_message(error, self.host)) from None
        except (ssl.SSLError, http.client.HTTPException, OSError):
            raise RemoteError("The connection to the dashboard failed.") from None
        if len(body) > self.max_bytes:
            raise RemoteError("The dashboard answer was too large.")
        try:
            result = json.loads(body)
        except (ValueError, RecursionError):
            raise RemoteError("The dashboard answer was not valid JSON.") from None
        if not isinstance(result, dict):
            raise RemoteError(UNDERSTOOD_MESSAGE)
        return result

    def _unreachable(self, reason: Any, seconds: float) -> RemoteError:
        if isinstance(reason, ssl.SSLCertVerificationError):
            return RemoteError(certificate_message(reason, self.host))
        if isinstance(reason, ssl.SSLError):
            return RemoteError("The secure connection to the dashboard failed.")
        if isinstance(reason, (socket.timeout, TimeoutError)):
            return RemoteError(f"The dashboard did not answer within {js_number(seconds)} seconds.")
        return RemoteError(
            f"Could not reach the dashboard at {self.url}. Check the address and that the Pi is on."
        )

    def _refused(self, error: HTTPError) -> RemoteError:
        status = error.code
        data: Dict[str, Any] = {}
        try:
            raw = (
                _read_bounded(error.fp, MAX_ERROR_BYTES + 1, time.monotonic() + ERROR_READ_SECONDS)
                if error.fp is not None else b""
            )
            if len(raw) <= MAX_ERROR_BYTES:
                parsed = json.loads(raw) if raw else {}
                data = parsed if isinstance(parsed, dict) else {}
        except (OSError, ValueError, RecursionError, http.client.HTTPException, _TooSlow):
            data = {}
        finally:
            error.close()
        if 300 <= status < 400:
            return RemoteError(REDIRECT_MESSAGE, status=status)
        if status == 401:
            return RemoteError(TOKEN_REFUSED_MESSAGE, status=status)
        if status == 403 and data.get("registration_required") is True:
            return RemoteError(NOT_ALLOWED_MESSAGE, status=status)
        message = _clean(data.get("error"), self._secrets)
        hint = _clean(data.get("hint"), self._secrets)
        text = message or f"The dashboard refused the request (HTTP {status})."
        if hint:
            text = text.rstrip(".") + ". " + hint
        safe = {key: data[key] for key in ("general_profile",) if key in data}
        return RemoteError(text, status=status, data=safe)
