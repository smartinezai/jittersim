"""Small, one-shot client for the Arris TG3442DE local web interface."""

from dataclasses import dataclass
from html.parser import HTMLParser
import hashlib
import json
import re
from typing import Callable, Dict, List, Optional, Sequence, Tuple
from urllib.parse import urljoin

import requests
from Crypto.Cipher import AES


class RouterError(RuntimeError):
    """Raised when a router operation cannot be completed safely."""


@dataclass(frozen=True)
class RouterWifiSettings:
    """Describe the primary Wi-Fi settings exposed by the router."""

    ssid: str
    password: str
    enabled: bool


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
            self.session.close()

    def login(self) -> None:
        """Authenticate without persisting the router password."""

        page = self._request("get", "/")
        session_id = _required_script_value(page, "currentSessionId")
        iv = bytes.fromhex(_required_script_value(page, "myIv"))
        salt = bytes.fromhex(_required_script_value(page, "mySalt"))
        key = hashlib.pbkdf2_hmac("sha256", self.password.encode("ascii"), salt, 1000, 16)
        auth_data = "loginPassword"
        cipher = AES.new(key, AES.MODE_CCM, nonce=iv)
        cipher.update(auth_data.encode("ascii"))
        plaintext = json.dumps(
            {"Password": self.password, "Nonce": session_id}
        ).encode("ascii")
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
        if "AdminMatch" not in response:
            raise RouterError("router login failed")
        try:
            encrypted_nonce = json.loads(response)["encryptData"]
            cipher = AES.new(key, AES.MODE_CCM, nonce=iv)
            plain_nonce = cipher.decrypt(bytes.fromhex(encrypted_nonce))
            self.csrf_nonce = plain_nonce[:32].decode("ascii")
        except (KeyError, TypeError, ValueError, UnicodeDecodeError) as error:
            raise RouterError("router login returned an invalid session") from error

        credential_script = self._request("get", "/base_95x.js")
        credential = _credential_from_script(credential_script)
        if credential:
            self.session.cookies.set("credential", credential, path="/")
        self.session.headers.update(
            {
                "X-Requested-With": "XMLHttpRequest",
                "csrfNonce": self.csrf_nonce,
                "Origin": f"{self.base_url}/",
            }
        )
        self._request("post", "/php/ajaxSet_Session.php")

    def read_wifi_settings(self) -> RouterWifiSettings:
        """Read the primary SSID, password and radio state."""

        form, html = self._wifi_form()
        ssid = _first_control(form, self._SSID_NAMES) or _first_script_value(
            html, self._SSID_NAMES
        )
        password = _first_control(form, self._PASSWORD_NAMES) or _first_script_value(
            html, self._PASSWORD_NAMES
        )
        if not ssid or not password:
            raise RouterError("router did not expose the primary Wi-Fi credentials")
        return RouterWifiSettings(
            ssid=ssid,
            password=password,
            enabled=_wifi_enabled(form.controls),
        )

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
        ssid = _first_control(form, self._SSID_NAMES) or _first_script_value(
            html, self._SSID_NAMES
        )
        password = _first_control(form, self._PASSWORD_NAMES) or _first_script_value(
            html, self._PASSWORD_NAMES
        )
        if not ssid or not password:
            raise RouterError("router did not expose the primary Wi-Fi credentials")
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
        raise RouterError("router Wi-Fi settings form was not found")

    def _request(self, method: str, path: str, **kwargs: object) -> str:
        try:
            response = self.session.request(
                method,
                urljoin(f"{self.base_url}/", path),
                timeout=self.timeout_seconds,
                **kwargs,
            )
            response.raise_for_status()
        except requests.RequestException as error:
            raise RouterError("could not contact the router") from error
        return response.text


def _required_script_value(html: str, name: str) -> str:
    value = re.search(
        rf"\bvar\s+{re.escape(name)}\s*=\s*[\"']([^\"']+)[\"']",
        html,
    )
    if value is None:
        raise RouterError("router login page is missing required session data")
    return value.group(1)


def _credential_from_script(script: str) -> str:
    match = re.search(
        r"createCookie\(\s*[\"']credential[\"']\s*,\s*[\"']([^\"']*)[\"']",
        script,
        re.IGNORECASE | re.DOTALL,
    )
    return match.group(1) if match else ""


def _first_control(form: _Form, names: Sequence[str]) -> str:
    for name in names:
        value = form.controls.get(name)
        if value:
            return value
    return ""


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


def _wifi_enabled(controls: Dict[str, str]) -> bool:
    states = [controls[name] for name in TG3442DERouter._STATE_NAMES if name in controls]
    return bool(states) and any(value not in {"0", "false", "off"} for value in states)
