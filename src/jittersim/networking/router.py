"""Small, one-shot client for the Arris TG3442DE local web interface."""

import hashlib
import json
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple
from urllib.parse import urljoin

import requests
from Crypto.Cipher import AES

ROUTER_IMPORT_MESSAGES = {
    "failed": "router Wi-Fi import failed",
    "login": "Router login failed. Check the router admin password and try again.",
    "unreachable": "Could not contact the router. Check the Pi's wired connection and try again.",
    "unsupported": (
        "This router's Wi-Fi settings page is unsupported. "
        "Use Manual Pi access-point fallback to enter the Wi-Fi name and password."
    ),
    "credentials": (
        "The router did not provide a complete Wi-Fi name and password. "
        "Use Manual Pi access-point fallback."
    ),
    "invalid": "The router's Wi-Fi response could not be verified. Try again or use manual AP settings.",
    "apply": (
        "The Pi could not apply the router's Wi-Fi settings. "
        "Use the wired management connection and manual AP settings to restore the previous Wi-Fi."
    ),
}


class RouterError(RuntimeError):
    """Raised when a router operation cannot be completed safely."""

    def __init__(self, message: str, *, reason: str = "failed") -> None:
        super().__init__(message)
        self.reason = reason if reason in ROUTER_IMPORT_MESSAGES else "failed"

    @property
    def import_message(self) -> str:
        """Return an allow-listed message without router response details."""

        return ROUTER_IMPORT_MESSAGES[self.reason]


class _EndpointUnavailable(RouterError):
    """Identify an older firmware without the JSON settings endpoint."""


@dataclass(frozen=True)
class RouterWifiSettings:
    """Describe the primary Wi-Fi settings exposed by the router."""

    ssid: str
    password: str
    enabled: Optional[bool]


@dataclass
class _Form:
    """Represent one HTML form without retaining the response body."""

    action: str
    method: str
    controls: Dict[str, str]


class _FormParser(HTMLParser):
    """Extract the simple named controls used by the TG3442DE Wi-Fi form."""

    def __init__(self) -> None:
        super().__init__()
        self.forms: List[_Form] = []
        self._form: Optional[_Form] = None
        self._select_name: Optional[str] = None
        self._selected_option: Optional[str] = None
        self._textarea_name: Optional[str] = None
        self._textarea_value: List[str] = []

    def handle_starttag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]) -> None:
        attributes = dict(attrs)
        if tag == "form":
            self._form = _Form(
                action=attributes.get("action") or "",
                method=(attributes.get("method") or "post").lower(),
                controls={},
            )
            return
        if self._form is None:
            return
        if tag == "input":
            name = attributes.get("name")
            if not name:
                return
            input_type = (attributes.get("type") or "text").lower()
            if input_type in {"button", "submit", "reset", "file"}:
                return
            if input_type in {"checkbox", "radio"} and "checked" not in attributes:
                return
            self._form.controls[name] = attributes.get("value") or ""
            return
        if tag == "select":
            self._select_name = attributes.get("name")
            self._selected_option = None
            return
        if tag == "option" and self._select_name:
            if self._selected_option is None or "selected" in attributes:
                self._selected_option = attributes.get("value") or ""
            return
        if tag == "textarea":
            self._textarea_name = attributes.get("name")
            self._textarea_value = []

    def handle_endtag(self, tag: str) -> None:
        if tag == "form" and self._form is not None:
            self.forms.append(self._form)
            self._form = None
            return
        if tag == "select" and self._select_name and self._form is not None:
            self._form.controls[self._select_name] = self._selected_option or ""
            self._select_name = None
            self._selected_option = None
            return
        if tag == "textarea" and self._textarea_name and self._form is not None:
            self._form.controls[self._textarea_name] = "".join(self._textarea_value)
            self._textarea_name = None
            self._textarea_value = []

    def handle_data(self, data: str) -> None:
        if self._textarea_name:
            self._textarea_value.append(data)


class TG3442DERouter:
    """Use the TG3442DE web interface for one authenticated operation."""

    _WIFI_PAGE = "/?wifi_general&mid=WifiGeneral"
    _WIFI_DATA = "/php/wifi_general_data.php"
    _MAX_RESPONSE_BYTES = 1024 * 1024
    _SSID_NAMES = ("wifi_ssid24", "wifi_ssid5", "wifi_ssid6")
    _PASSWORD_NAMES = ("wifi_password24", "wifi_password5", "wifi_password6")
    _STATE_NAMES = (
        "wifi_on_state",
        "wifi_network_24_state",
        "wifi_network_5_state",
        "wifi_network_6_state",
        "main_wifi24_state",
        "main_wifi5_state",
        "main_wifi6_state",
    )

    def __init__(
        self,
        *,
        address: str,
        password: str,
        timeout_seconds: float = 10.0,
        session_factory: Callable[[], requests.Session] = requests.Session,
    ) -> None:
        if not re.fullmatch(r"(?:\d{1,3}\.){3}\d{1,3}", address):
            raise RouterError("router address must be an IPv4 address")
        if not isinstance(password, str) or not password:
            raise RouterError("router admin password is required")
        if timeout_seconds <= 0 or timeout_seconds > 60:
            raise ValueError("router timeout must be greater than 0 and no more than 60")
        self.base_url = f"http://{address}"
        self.password = password
        self.timeout_seconds = timeout_seconds
        self.session = session_factory()
        self.csrf_nonce = ""
        self._session_key: Optional[bytes] = None
        self._session_iv: Optional[bytes] = None

    def close(self) -> None:
        """Close the short-lived router session."""

        try:
            try:
                self.session.post(
                    f"{self.base_url}/php/logout.php",
                    timeout=self.timeout_seconds,
                )
            except requests.RequestException:
                pass
        finally:
            self.password = ""
            self.csrf_nonce = ""
            self._session_key = None
            self._session_iv = None
            self.session.cookies.clear()
            self.session.close()

    def login(self) -> None:
        """Authenticate without persisting the router password."""

        self.csrf_nonce = ""
        self._session_key = None
        self._session_iv = None
        page = self._request("get", "/")
        session_id = _required_script_value(page, "currentSessionId")
        try:
            iv = bytes.fromhex(_required_script_value(page, "myIv"))
            salt = bytes.fromhex(_required_script_value(page, "mySalt"))
            key = hashlib.pbkdf2_hmac("sha256", self.password.encode("utf-8"), salt, 1000, 16)
            cipher = AES.new(key, AES.MODE_CCM, nonce=iv)
        except ValueError:
            raise RouterError(
                "router login page has invalid session data", reason="login"
            ) from None
        auth_data = "loginPassword"
        cipher.update(auth_data.encode("ascii"))
        plaintext = json.dumps({"Password": self.password, "Nonce": session_id}).encode("utf-8")
        encrypted, digest = cipher.encrypt_and_digest(plaintext)
        response = self._request(
            "post",
            "/php/ajaxSet_Password.php",
            json={
                "EncryptData": (encrypted + digest).hex(),
                "Name": "admin",
                "AuthData": auth_data,
            },
            headers={"Content-Type": "application/json"},
        )
        try:
            result = json.loads(response)
            if not isinstance(result, dict) or result.get("p_status") != "AdminMatch":
                raise RouterError("router login failed", reason="login")
            nonce = _decrypt_ccm(result["encryptData"], key=key, iv=iv, auth_data="nonce")
            if (
                not 1 <= len(nonce) <= 256
                or not nonce.isascii()
                or any(ord(char) < 33 or ord(char) > 126 for char in nonce)
            ):
                raise ValueError("invalid nonce")
        except (KeyError, TypeError, ValueError):
            raise RouterError("router login returned an invalid session", reason="login") from None
        self.csrf_nonce = nonce
        self._session_key = key
        self._session_iv = iv

        credential_script = self._request("get", "/base_95x.js")
        credential = _credential_from_script(credential_script)
        if credential:
            self.session.cookies.set("credential", credential, path="/")
        self.session.headers.update(
            {
                "X-Requested-With": "XMLHttpRequest",
                "csrfNonce": self.csrf_nonce,
                "Origin": self.base_url,
                "Referer": f"{self.base_url}/",
            }
        )
        self._request("post", "/php/ajaxSet_Session.php")

    def read_wifi_settings(self) -> RouterWifiSettings:
        """Read the primary SSID, password and radio state."""

        try:
            data = self._read_wifi_data()
        except _EndpointUnavailable:
            form, html = self._wifi_form()
            return self._settings_from_form(form, html)
        return _settings_from_wifi_data(data)

    def _read_wifi_data(self) -> Dict[str, Any]:
        if self._session_key is None or self._session_iv is None:
            raise RouterError("router login is required", reason="login")
        response = self._request(
            "get",
            self._WIFI_DATA,
            params=json.dumps({"IsManualOperation": "false"}, separators=(",", ":")),
        )
        try:
            envelope = json.loads(response)
        except ValueError:
            raise _EndpointUnavailable("router settings endpoint is unavailable") from None
        if not isinstance(envelope, dict) or "wifidata" not in envelope:
            raise RouterError("router Wi-Fi settings endpoint is unsupported", reason="unsupported")
        try:
            plaintext = _decrypt_ccm(
                envelope["wifidata"],
                key=self._session_key,
                iv=self._session_iv,
                auth_data="wifidata",
            )
            settings = json.loads(plaintext)
            if not isinstance(settings, dict):
                raise ValueError("invalid settings object")
        except (TypeError, ValueError):
            raise RouterError("router Wi-Fi response is invalid", reason="invalid") from None
        return settings

    def set_wifi_enabled(self, *, enabled: bool) -> RouterWifiSettings:
        """Enable or disable all primary router Wi-Fi radios."""

        form, _ = self._wifi_form()
        state = "1" if enabled else "0"
        present = [name for name in self._STATE_NAMES if name in form.controls]
        if not present:
            raise RouterError("router Wi-Fi form did not expose radio controls")
        for name in present:
            form.controls[name] = state
        self._request(
            "post",
            urljoin(f"{self.base_url}/", form.action or self._WIFI_PAGE),
            data=form.controls,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Referer": f"{self.base_url}{self._WIFI_PAGE}",
            },
        )
        updated_form, _ = self._wifi_form()
        if _wifi_enabled(updated_form.controls) != enabled:
            raise RouterError("router did not confirm the requested Wi-Fi state")
        return RouterWifiSettings(
            ssid="",
            password="",
            enabled=enabled,
        )

    def _settings_from_form(self, form: _Form, html: str) -> RouterWifiSettings:
        pairs = zip(self._SSID_NAMES, self._PASSWORD_NAMES)
        for ssid_name, password_name in pairs:
            ssid = form.controls.get(ssid_name) or _first_script_value(html, (ssid_name,))
            password = form.controls.get(password_name) or _first_script_value(
                html, (password_name,)
            )
            if _valid_wifi_credentials(ssid, password):
                break
        else:
            raise RouterError(
                "router did not expose the primary Wi-Fi credentials", reason="credentials"
            )
        return RouterWifiSettings(
            ssid=ssid,
            password=password,
            enabled=_wifi_enabled(form.controls),
        )

    def _wifi_form(self) -> Tuple[_Form, str]:
        html = self._request("get", self._WIFI_PAGE)
        parser = _FormParser()
        parser.feed(html)
        expected = set(self._STATE_NAMES + self._SSID_NAMES + self._PASSWORD_NAMES)
        for form in parser.forms:
            if expected.intersection(form.controls):
                return form, html
        raise RouterError("router Wi-Fi settings form was not found", reason="unsupported")

    def _request(self, method: str, path: str, **kwargs: object) -> str:
        response = None
        try:
            response = self.session.request(
                method,
                urljoin(f"{self.base_url}/", path),
                timeout=self.timeout_seconds,
                allow_redirects=False,
                stream=True,
                **kwargs,
            )
            if response.status_code in (404, 405):
                raise _EndpointUnavailable("router settings endpoint is unavailable")
            if response.status_code in (401, 403) or 300 <= response.status_code < 400:
                raise RouterError("router session is not authenticated", reason="login")
            response.raise_for_status()
            chunks = []
            size = 0
            for chunk in response.iter_content(chunk_size=16384):
                size += len(chunk)
                if size > self._MAX_RESPONSE_BYTES:
                    raise RouterError("router response is too large", reason="invalid")
                chunks.append(chunk)
            return b"".join(chunks).decode(response.encoding or "utf-8", errors="replace")
        except requests.RequestException:
            raise RouterError("could not contact the router", reason="unreachable") from None
        finally:
            if response is not None:
                response.close()


def _required_script_value(html: str, name: str) -> str:
    value = re.search(
        rf"\bvar\s+{re.escape(name)}\s*=\s*[\"']([^\"']+)[\"']",
        html,
    )
    if value is None:
        raise RouterError("router login page is missing required session data", reason="login")
    return value.group(1)


def _credential_from_script(script: str) -> str:
    match = re.search(
        r"createCookie\(\s*[\"']credential[\"']\s*,\s*[\"']([^\"']*)[\"']",
        script,
        re.IGNORECASE | re.DOTALL,
    )
    return match.group(1) if match else ""


def _first_script_value(html: str, names: Sequence[str]) -> str:
    for name in names:
        match = re.search(
            rf"[\"']?{re.escape(name)}[\"']?\s*[:=]\s*[\"']([^\"']+)[\"']",
            html,
            re.IGNORECASE,
        )
        if match:
            return match.group(1)
    return ""


def _radio_state(value: object) -> Optional[bool]:
    if isinstance(value, bool):
        return value
    if isinstance(value, (str, int)):
        normalized = str(value).lower()
        if normalized in {"1", "true", "on"}:
            return True
        if normalized in {"0", "false", "off"}:
            return False
    return None


def _wifi_enabled(controls: Dict[str, str]) -> Optional[bool]:
    states = [
        _radio_state(controls[name]) for name in TG3442DERouter._STATE_NAMES if name in controls
    ]
    if any(state is True for state in states):
        return True
    if states and all(state is False for state in states):
        return False
    return None


def _decrypt_ccm(encrypted: object, *, key: bytes, iv: bytes, auth_data: str) -> str:
    if not isinstance(encrypted, str) or len(encrypted) > 2 * TG3442DERouter._MAX_RESPONSE_BYTES:
        raise ValueError("invalid encrypted response")
    ciphertext = bytes.fromhex(encrypted)
    if len(ciphertext) <= 16:
        raise ValueError("invalid encrypted response")
    cipher = AES.new(key, AES.MODE_CCM, nonce=iv, mac_len=16)
    cipher.update(auth_data.encode("ascii"))
    return cipher.decrypt_and_verify(ciphertext[:-16], ciphertext[-16:]).decode("utf-8")


def _valid_wifi_credentials(ssid: object, password: object) -> bool:
    return (
        isinstance(ssid, str)
        and isinstance(password, str)
        and 1 <= len(ssid.encode("utf-8")) <= 32
        and 8 <= len(password) <= 63
        and not any(ord(char) < 32 for char in ssid + password)
    )


def _settings_from_wifi_data(data: Dict[str, Any]) -> RouterWifiSettings:
    pairs = [("SSID", "Passphrase", "Enable"), ("SSID5G", "Passphrase5G", "Enable5G")]
    if _radio_state(data.get("Enable")) is False and _radio_state(data.get("Enable5G")) is True:
        pairs.reverse()
    for ssid_key, password_key, _ in pairs:
        ssid, password = data.get(ssid_key), data.get(password_key)
        if _valid_wifi_credentials(ssid, password):
            break
    else:
        raise RouterError("router did not expose complete Wi-Fi credentials", reason="credentials")
    states = [_radio_state(data.get(name)) for name in ("Enable", "Enable5G")]
    master_state = _radio_state(data.get("EnableWiFiFunction"))
    enabled = None
    if "EnableWiFiFunction" in data and master_state is None:
        enabled = None
    elif master_state is False:
        enabled = False
    elif any(state is True for state in states):
        enabled = True
    elif all(state is False for state in states):
        enabled = False
    return RouterWifiSettings(ssid=ssid, password=password, enabled=enabled)


def safe_router_import_message(message: str) -> str:
    """Allow only known guidance through the API and its logs."""

    return (
        message if message in ROUTER_IMPORT_MESSAGES.values() else ROUTER_IMPORT_MESSAGES["failed"]
    )
