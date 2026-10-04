"""Small, one-shot client for the Arris TG3442DE local web interface."""

import hashlib
import json
import logging
import re
import time
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple
from urllib.parse import urljoin

import requests
from Crypto.Cipher import AES

from .wifi_security import router_security

LOGGER = logging.getLogger(__name__)

ROUTER_IMPORT_MESSAGES = {
    "security": "The router's Wi-Fi security type is missing or unsupported. No Pi Wi-Fi change was made.",
    "changed": "Router Wi-Fi changed after it was checked. Check its security again before copying.",
    "legacy": "Copying older WPA requires confirmation of its weak security.",
    "outdated": "The networking helper is out of date. Reinstall the release before copying Wi-Fi.",
    "restored": "Wi-Fi copy failed or its security is unsupported on this Pi. Previous settings were restored.",
    "restore-failed": "Wi-Fi copy failed and recovery failed. Use management access to repair jittersim-ap.",
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


ROUTER_WIFI_MESSAGES = {
    "failed": "Router Wi-Fi change failed. Check the router status before retrying.",
    "login": "Router login failed. Check the router admin password and try again.",
    "unreachable": "Could not contact the router. Check the Pi's wired connection and try again.",
    "unsupported": (
        "This router's Wi-Fi controls are unsupported. "
        "Use Open router Wi-Fi settings to change them directly."
    ),
    "invalid": "The router response could not be verified. No Wi-Fi change was sent.",
    "disabled": (
        "Both router Wi-Fi bands are disabled. "
        "Use Open router Wi-Fi settings to enable a band first."
    ),
    "uncertain": (
        "The router may have changed Wi-Fi, but JitterSim could not confirm both radios. "
        "Wait for a fresh router status before retrying."
    ),
}


ROUTER_CHECK_MESSAGES = {
    "failed": "Could not check the router password. Try again in a moment.",
    "login": "Router login failed. Check the router admin password and try again.",
    "unreachable": "Could not contact the router. Check the Pi's wired connection and try again.",
    "outdated": (
        "The networking helper is out of date. "
        "Restart the JitterSim services or reinstall the release."
    ),
}


class RouterError(RuntimeError):
    """Raised when a router operation cannot be completed safely."""

    def __init__(self, message: str, *, reason: str = "failed") -> None:
        super().__init__(message)
        self.reason = (
            reason
            if reason in ROUTER_IMPORT_MESSAGES or reason in ROUTER_WIFI_MESSAGES
            else "failed"
        )

    @property
    def import_message(self) -> str:
        """Return an allow-listed message without router response details."""

        return ROUTER_IMPORT_MESSAGES.get(self.reason, ROUTER_IMPORT_MESSAGES["failed"])

    @property
    def wifi_message(self) -> str:
        """Return fixed toggle guidance without retaining router details."""

        return ROUTER_WIFI_MESSAGES.get(self.reason, ROUTER_WIFI_MESSAGES["failed"])

    @property
    def check_message(self) -> str:
        """Return fixed password-check guidance without changing-Wi-Fi wording."""

        return ROUTER_CHECK_MESSAGES.get(self.reason, ROUTER_CHECK_MESSAGES["failed"])


class _EndpointUnavailable(RouterError):
    """Identify an older firmware without the JSON settings endpoint."""


@dataclass(frozen=True)
class RouterWifiSettings:
    """Describe the primary Wi-Fi settings exposed by the router."""

    ssid: str
    password: str
    enabled: Optional[bool]
    security: Optional[str] = None
    band: Optional[str] = None


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
    _WIFI_WRITE = "/php/ajaxSet_wifi_general_data.php"
    _OPERATION_TIMEOUT_SECONDS = 120.0
    _CONFIRMATION_ATTEMPTS = 8
    _CONFIRMATION_DELAY_SECONDS = 10.0
    _WIFI_WRITABLE_FIELDS = (
        "BandSteerEnable",
        "SplitSSIDEnable",
        "Enable",
        "SSID",
        "SSIDAdvertisementEnabled",
        "ModeEnabled",
        "Enable5G",
        "SSID5G",
        "SSIDAdvertisementEnabled5G",
        "ModeEnabled5G",
        "EnableGuest",
        "SSIDGuest",
        "ModeEnabledGuest",
        "IsolationEnabledGuest",
        "EnableWiFiFunction",
    )
    _WIFI_FLAG_FIELDS = (
        "BandSteerEnable",
        "SplitSSIDEnable",
        "Enable",
        "SSIDAdvertisementEnabled",
        "Enable5G",
        "SSIDAdvertisementEnabled5G",
        "EnableGuest",
        "IsolationEnabledGuest",
        "EnableWiFiFunction",
    )
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
    _LEGACY_WIFI_FIELDS = frozenset(
        _STATE_NAMES + _SSID_NAMES + _PASSWORD_NAMES
        + ("wifi_security24", "wifi_security5", "wifi_security6")
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
        self._deadline: Optional[float] = None
        self._wifi_master_from_page = False

    def close(self) -> None:
        """Close the short-lived router session."""

        try:
            try:
                self._request("post", "/php/logout.php")
            except RouterError:
                pass
        finally:
            self.password = ""
            self.csrf_nonce = ""
            self._session_key = None
            self._session_iv = None
            self.session.cookies.clear()
            self.session.headers.pop("csrfNonce", None)
            self.session.close()

    def login(self, *, preserve_deadline: bool = False) -> None:
        """Authenticate without persisting the router password."""

        self.csrf_nonce = ""
        self._session_key = None
        self._session_iv = None
        if not preserve_deadline:
            self._deadline = time.monotonic() + self._OPERATION_TIMEOUT_SECONDS
        self._remaining_timeout()
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

    def limit_operation(self, seconds: float) -> None:
        """Bound the entire next read including login and logout."""
        if not 0 < seconds <= self._OPERATION_TIMEOUT_SECONDS:
            raise ValueError("invalid router operation limit")
        self._deadline = time.monotonic() + seconds

    def read_wifi_enabled(self) -> bool:
        """Read radio activity without requiring importable Wi-Fi credentials."""
        try:
            data = self._read_verified_wifi_data()
        except _EndpointUnavailable:
            form, _ = self._wifi_form()
            state = _wifi_enabled(form.controls)
            if state is None:
                raise RouterError("router radio state is unknown", reason="unsupported")
            return state
        master = _radio_state(data.get("EnableWiFiFunction"))
        bands = [_radio_state(data.get(name)) for name in ("Enable", "Enable5G")]
        if master is None or any(state is None for state in bands):
            raise RouterError("router radio state is unknown", reason="unsupported")
        return master and any(bands)

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

    def _read_verified_wifi_data(self) -> Dict[str, Any]:
        settings = self._read_wifi_data()
        self._wifi_master_from_page = "EnableWiFiFunction" not in settings
        if self._wifi_master_from_page:
            settings["EnableWiFiFunction"] = self._read_master_wifi_state()
        return settings

    def _read_master_wifi_state(self) -> bool:
        try:
            page = self._request("get", "/", headers={"Cache-Control": "no-cache"})
        except _EndpointUnavailable:
            raise RouterError("router master page is unavailable", reason="unsupported") from None
        states = []
        for quote, value in re.findall(
            r"\b_ga\s*\.\s*isWifiEnabled\s*=\s*([\"'])" r"([^\"']*)\1\s*;", page,
        ):
            state = _radio_state(value)
            if state is None:
                raise RouterError("router master state is unknown", reason="unsupported")
            states.append(state)
        for match in re.finditer(r"\b_ga\s*=\s*", page):
            try:
                data, _ = json.JSONDecoder().raw_decode(page[match.end():].lstrip())
            except (ValueError, RecursionError):
                continue
            if isinstance(data, dict) and "isWifiEnabled" in data:
                state = _radio_state(data["isWifiEnabled"])
                if state is None:
                    raise RouterError("router master state is unknown", reason="unsupported")
                states.append(state)
        if not states or any(state is not states[0] for state in states):
            raise RouterError("router master state could not be verified", reason="unsupported")
        return states[0]

    def set_wifi_enabled(self, *, enabled: bool) -> RouterWifiSettings:
        """Change primary radios and confirm them without altering other settings."""

        if not isinstance(enabled, bool):
            raise RouterError("invalid requested radio state", reason="invalid")
        try:
            current = self._read_verified_wifi_data()
        except _EndpointUnavailable:
            return self._set_legacy_wifi_enabled(enabled=enabled)
        payload = self._wifi_write_payload(current, enabled=enabled)
        if _wifi_state_matches(current, payload):
            return RouterWifiSettings(ssid="", password="", enabled=enabled)
        if not enabled and self._wifi_master_from_page and (
            payload["SplitSSIDEnable"] or payload["EnableGuest"]
        ):
            raise RouterError("configured band choices cannot be preserved", reason="unsupported")
        if self._session_key is None or self._session_iv is None:
            raise RouterError("router login is required", reason="login")
        cipher = AES.new(self._session_key, AES.MODE_CCM, nonce=self._session_iv, mac_len=16)
        cipher.update(b"wifidata")
        ciphertext, tag = cipher.encrypt_and_digest(json.dumps(payload).encode("utf-8"))
        refreshed = False
        started = time.monotonic()
        step = "write"
        attempts = 0
        last_reason = "mismatch"
        radio_flags = (None, None, None)
        try:
            try:
                self._request(
                    "post",
                    self._WIFI_WRITE,
                    json={
                        "EncryptData": (ciphertext + tag).hex(),
                        "Name": "admin",
                        "AuthData": "wifidata",
                    },
                    headers={"Content-Type": "application/json"},
                    wait_for_wifi_restart=True,
                )
            except RouterError as error:
                if error.reason != "unreachable":
                    raise
            for attempt in range(self._CONFIRMATION_ATTEMPTS):
                step = "confirm"
                attempts = attempt + 1
                updated = None
                try:
                    try:
                        updated = self._read_verified_wifi_data()
                    except RouterError as error:
                        if error.reason != "login" or refreshed:
                            raise
                        refreshed = True
                        self.session.cookies.clear()
                        self.session.headers.pop("csrfNonce", None)
                        self.login(preserve_deadline=True)
                        updated = self._read_verified_wifi_data()
                except _EndpointUnavailable:
                    last_reason = "unavailable"
                except RouterError as error:
                    last_reason = error.reason
                    if error.reason != "unreachable":
                        raise
                if updated is not None:
                    radio_flags = tuple(
                        _radio_state(updated.get(name))
                        for name in ("EnableWiFiFunction", "Enable", "Enable5G")
                    )
                    if _wifi_state_matches(updated, payload):
                        return RouterWifiSettings(ssid="", password="", enabled=enabled)
                    last_reason = "mismatch"
                if attempt + 1 < self._CONFIRMATION_ATTEMPTS:
                    time.sleep(min(self._CONFIRMATION_DELAY_SECONDS, self._remaining_timeout()))
        except RouterError as error:
            LOGGER.warning(
                "router Wi-Fi confirmation failed: step=%s reason=%s enabled=%s "
                "attempts=%s elapsed=%.1f radios=%s",
                step, error.reason, enabled, attempts, time.monotonic() - started, radio_flags,
            )
            raise RouterError("router change could not be confirmed", reason="uncertain") from None
        LOGGER.warning(
            "router Wi-Fi confirmation failed: step=%s reason=%s enabled=%s "
            "attempts=%s elapsed=%.1f radios=%s",
            step, last_reason, enabled, attempts, time.monotonic() - started, radio_flags,
        )
        raise RouterError("router radios did not confirm the requested state", reason="uncertain")

    def _wifi_write_payload(self, current: Dict[str, Any], *, enabled: bool) -> Dict[str, Any]:
        if any(name not in current for name in self._WIFI_WRITABLE_FIELDS):
            raise RouterError("router Wi-Fi write fields are incomplete", reason="unsupported")
        payload = {name: current[name] for name in self._WIFI_WRITABLE_FIELDS}
        for name, value in payload.items():
            if (
                not isinstance(value, (str, int, bool))
                or isinstance(value, str)
                and len(value) > 256
            ):
                raise RouterError("router Wi-Fi write fields are invalid", reason="invalid")
        for name in self._WIFI_FLAG_FIELDS:
            state = _radio_state(payload[name])
            if state is None:
                raise RouterError("router radio state is unknown", reason="unsupported")
            payload[name] = int(state)
        for name in ("ModeEnabled", "ModeEnabled5G", "ModeEnabledGuest"):
            payload[name] = _wifi_security_mode(payload[name])
        if not payload["SplitSSIDEnable"]:
            payload["Enable"] = payload["Enable5G"] = 1
        elif enabled and not (payload["Enable"] or payload["Enable5G"]):
            raise RouterError("both configured router bands are disabled", reason="disabled")
        payload["EnableWiFiFunction"] = int(enabled)
        # The master switch controls effective radio activity. Band choices stay configured.
        # The firmware omits unchanged passphrases, which preserves stored keys.
        return payload

    def _set_legacy_wifi_enabled(self, *, enabled: bool) -> RouterWifiSettings:
        form, _ = self._wifi_form()
        if not form.controls.keys() <= self._LEGACY_WIFI_FIELDS:
            raise RouterError("router Wi-Fi form has unverified controls", reason="unsupported")
        state = "1" if enabled else "0"
        present = [name for name in self._STATE_NAMES if name in form.controls]
        if not present or any(_radio_state(form.controls[name]) is None for name in present):
            raise RouterError("router Wi-Fi form has unknown radio controls", reason="unsupported")
        for name in present:
            form.controls[name] = state
        try:
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
            if any(
                _radio_state(updated_form.controls.get(name)) is not enabled for name in present
            ):
                raise RouterError("router did not confirm every radio")
        except RouterError:
            raise RouterError("router change could not be confirmed", reason="uncertain") from None
        return RouterWifiSettings(ssid="", password="", enabled=enabled)

    def _remaining_timeout(self) -> float:
        deadline = getattr(self, "_deadline", None)
        remaining = self.timeout_seconds if deadline is None else deadline - time.monotonic()
        if remaining <= 0:
            raise RouterError("router operation timed out", reason="unreachable")
        return min(self.timeout_seconds, remaining)

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
            security=router_security(form.controls.get("wifi_security" + ssid_name[len("wifi_ssid"):])),
            band={"24": "2.4 GHz", "5": "5 GHz", "6": "6 GHz"}.get(ssid_name.removeprefix("wifi_ssid")),
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

    def _request(
        self, method: str, path: str, *, wait_for_wifi_restart: bool = False, **kwargs: object
    ) -> str:
        response = None
        try:
            request_timeout = self._remaining_timeout()
            if wait_for_wifi_restart:
                if self._deadline is None:
                    raise RouterError("router login is required", reason="login")
                remaining = self._deadline - time.monotonic()
                if remaining <= 0:
                    raise RouterError("router operation timed out", reason="unreachable")
                write_budget = min(40.0, remaining / 2)
                connect = min(self.timeout_seconds, write_budget / 2)
                request_timeout = (connect, write_budget - connect)
            response = self.session.request(
                method,
                urljoin(f"{self.base_url}/", path),
                timeout=request_timeout,
                allow_redirects=False,
                stream=True,
                **kwargs,
            )
            if response.status_code in (404, 405):
                raise _EndpointUnavailable("router settings endpoint is unavailable")
            if response.status_code in (401, 403) or 300 <= response.status_code < 400:
                raise RouterError("router session is not authenticated", reason="login")
            response.raise_for_status()
            content = bytearray()
            # Check the deadline even when the router sends a slow response.
            for chunk in response.iter_content(chunk_size=1):
                self._remaining_timeout()
                content.extend(chunk)
                if len(content) > self._MAX_RESPONSE_BYTES:
                    raise RouterError("router response is too large", reason="invalid")
            self._remaining_timeout()
            return content.decode(response.encoding or "utf-8", errors="replace")
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


def _wifi_state_matches(settings: Dict[str, Any], payload: Dict[str, Any]) -> bool:
    master = _radio_state(settings.get("EnableWiFiFunction"))
    if master is not bool(payload["EnableWiFiFunction"]):
        return False
    bands = tuple(_radio_state(settings.get(name)) for name in ("Enable", "Enable5G"))
    configured = (bool(payload["Enable"]), bool(payload["Enable5G"]))
    if master is False and not payload["SplitSSIDEnable"]:
        return bands == (False, False) or bands == configured
    return bands == configured


def _wifi_security_mode(value: object) -> str:
    """Translate the TG3442 read codes to its writable security names."""

    modes = {0: "None", 4: "WPA2-Personal", 5: "WPA-WPA2-Personal"}
    if type(value) is int and value in modes:
        return modes[value]
    if isinstance(value, str):
        if value in modes.values():
            return value
        if value in {"0", "4", "5"}:
            return modes[int(value)]
    raise RouterError("router Wi-Fi security mode is unsupported", reason="unsupported")


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
    pairs = [("SSID", "Passphrase", "Enable", "ModeEnabled", "2.4 GHz"),
             ("SSID5G", "Passphrase5G", "Enable5G", "ModeEnabled5G", "5 GHz")]
    if _radio_state(data.get("Enable")) is False and _radio_state(data.get("Enable5G")) is True:
        pairs.reverse()
    for ssid_key, password_key, state_key, security_key, band in pairs:
        if _radio_state(data.get(state_key)) is False:
            continue
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
    return RouterWifiSettings(ssid=ssid, password=password, enabled=enabled,
                              security=router_security(data.get(security_key)), band=band)


def safe_router_import_message(message: str) -> str:
    """Allow only known guidance through the API and its logs."""

    if message == "unknown action":
        return ROUTER_IMPORT_MESSAGES["outdated"]
    return (
        message if message in ROUTER_IMPORT_MESSAGES.values() else ROUTER_IMPORT_MESSAGES["failed"]
    )


def safe_router_check_message(message: str) -> str:
    """Allow only known password-check guidance through the API and its logs."""

    if message == "unknown action":
        return ROUTER_CHECK_MESSAGES["outdated"]
    return (
        message if message in ROUTER_CHECK_MESSAGES.values() else ROUTER_CHECK_MESSAGES["failed"]
    )


def safe_router_wifi_message(message: str) -> str:
    """Return fixed toggle guidance and treat unknown helper failures as uncertain."""

    return (
        message if message in ROUTER_WIFI_MESSAGES.values() else ROUTER_WIFI_MESSAGES["uncertain"]
    )
