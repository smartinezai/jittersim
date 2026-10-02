"""The one dashboard address, and the local certificate that makes it trusted.

The dashboard has one neutral address (`https://netlab.local:8080` by default)
that the Pi announces on the router network and on the test Wi-Fi. A browser
keeps its remembered sign-in per address, so using the same one on both networks
means signing in once. The trust downloads, the announcement status and the
details here need the admin token and a registered device. The address itself is
neutral and also appears in the public sign-in page.
"""

import hashlib
import json
import ssl
from pathlib import Path
from typing import Any, Optional, Tuple

from jittersim import localca, localname, mdnsname

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


def dashboard_name_report(server: Any, *, neutral: bool = False) -> dict:
    """Whether the name is announced on each network, in words for the dashboard.

    The announcement is optional. When it fails the dashboard still opens by its
    IP address, and this says what is wrong and how to fix it. The neutral form
    leaves out the commands to run on the Pi, which name the product, and the
    network details. Diagnostics carries the full report.
    """
    name = getattr(server, "local_name", None) or localname.DEFAULT_LOCAL_NAME
    status = mdnsname.read_status(
        getattr(server, "name_status_path", None) or mdnsname.STATUS_FILE, name=name)
    # The report belongs to the name that was announced. After a rename it is
    # old until the publisher has restarted with the new name.
    if status.get("name") != name:
        status = mdnsname.stopped_report(name)
    if neutral:
        return {"name": name, "address": easy_address(server),
                "state": status["state"], "message": status["message"]}
    return {
        "name": name,
        "address": easy_address(server),
        "state": status["state"],
        "message": status["message"],
        "fix": status["fix"],
        "networks": [
            {"network": item.get("link"), "address": item.get("address"), "state": item.get("state")}
            for item in status["links"]
        ],
    }


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
            "name": getattr(server, "local_name", None) or localname.DEFAULT_LOCAL_NAME,
            "announce": dashboard_name_report(server, neutral=True),
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
