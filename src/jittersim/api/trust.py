"""Offer the local certificate authority so a device can trust the dashboard.

Everything here needs the admin token and a registered device. The easy address
itself is a neutral name and also appears in the public health check.
"""

import hashlib
import json
import ssl
from pathlib import Path
from typing import Any, Optional, Tuple

from jittersim import localca, localname

TRUST_DOWNLOADS = {
    "/api/trust/ca.crt": ("application/x-x509-ca-cert", "local-dashboard.crt"),
    "/api/trust/profile.mobileconfig": ("application/x-apple-aspen-config", "local-dashboard.mobileconfig"),
}
TRUST_PATHS = frozenset(("/api/trust",) + tuple(TRUST_DOWNLOADS))
# Three short steps for each kind of device. They name only the neutral names.
TRUST_STEPS = {
    "mac": [
        "Download the Apple profile and open it. System Settings shows it under Privacy & Security, Profiles.",
        "Choose Install and enter your Mac password.",
        "Close the browser tab and open the easy address again. The warning is gone.",
    ],
    "iphone": [
        "Download the Apple profile and choose Allow. Then open Settings, Profile Downloaded, Install.",
        "Open Settings, General, About, Certificate Trust Settings and turn on full trust for Local Dashboard Authority.",
        "Open the easy address in Safari. The warning is gone.",
    ],
}


def _json(value: Any, status: int = 200) -> Tuple[int, str, bytes]:
    return status, "application/json", (json.dumps(value, allow_nan=False) + "\n").encode()


def easy_address(server: Any) -> str:
    port = server.server_address[1]
    name = getattr(server, "local_name", None) or localname.DEFAULT_LOCAL_NAME
    return "https://%s%s" % (name, "" if port == 443 else ":%d" % port)


def _authority(server: Any) -> Optional[bytes]:
    path = getattr(server, "trust_ca_path", None)
    if path is None:
        return None
    try:
        return localca.certificate_download(Path(path))
    except (OSError, localca.TrustError):
        return None


def fingerprint(certificate: bytes) -> str:
    digest = hashlib.sha256(ssl.PEM_cert_to_DER_cert(certificate.decode("ascii"))).hexdigest().upper()
    return ":".join(digest[index:index + 2] for index in range(0, len(digest), 2))


def read_trust_api(server: Any, path: str) -> Optional[Tuple[int, str, bytes]]:
    if path not in TRUST_PATHS:
        return None
    certificate = _authority(server)
    if path == "/api/trust":
        return _json({
            "available": certificate is not None,
            "address": easy_address(server),
            "fingerprint": fingerprint(certificate) if certificate else None,
            "steps": TRUST_STEPS,
        })
    if certificate is None:
        return _json({"error": "The certificate is not available on this Pi yet."}, 404)
    if path == "/api/trust/ca.crt":
        return 200, TRUST_DOWNLOADS[path][0], certificate
    try:
        return 200, TRUST_DOWNLOADS[path][0], localca.apple_profile(Path(server.trust_ca_path))
    except (localca.TrustError, ValueError):
        return _json({"error": "The profile could not be prepared."}, 500)
