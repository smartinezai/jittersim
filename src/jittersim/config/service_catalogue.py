"""Well-known services and the hostnames they are known to use.

Each entry is a starting point. A service can use more hostnames than are listed
here, so verify the names a real session uses and add the missing ones to the
service list as custom hostnames. A hostname also matches its subdomains.
No hostname appears in two entries, so any mix of services can be listed.
"""

from dataclasses import dataclass
from typing import Iterable, Tuple

from .models import ValidationError, normalize_service_hostname


@dataclass(frozen=True)
class CatalogueService:
    key: str
    name: str
    domains: Tuple[str, ...]


SERVICE_CATALOGUE = (
    CatalogueService("linkedin", "LinkedIn", ("linkedin.com", "licdn.com")),
    CatalogueService(
        "microsoft_teams",
        "Microsoft Teams",
        (
            "teams.microsoft.com",
            "teams.live.com",
            "teams.cloud.microsoft",
            "teams.cdn.office.net",
            "skype.com",
            "lync.com",
        ),
    ),
    CatalogueService(
        "chatgpt",
        "ChatGPT",
        ("chatgpt.com", "openai.com", "oaistatic.com", "oaiusercontent.com"),
    ),
    CatalogueService("claude", "Claude", ("claude.ai", "claude.com", "anthropic.com")),
    CatalogueService(
        "netflix",
        "Netflix",
        ("netflix.com", "nflxvideo.net", "nflximg.net", "nflxext.com", "nflxso.net"),
    ),
    CatalogueService(
        "youtube", "YouTube", ("youtube.com", "googlevideo.com", "ytimg.com", "youtu.be")
    ),
    CatalogueService("zoom", "Zoom", ("zoom.us", "zoom.com", "zoomgov.com")),
    CatalogueService("google_meet", "Google Meet", ("meet.google.com",)),
    CatalogueService("slack", "Slack", ("slack.com", "slack-edge.com", "slack-msgs.com")),
    CatalogueService("spotify", "Spotify", ("spotify.com", "scdn.co", "spotifycdn.com")),
    CatalogueService(
        "disney_plus",
        "Disney+",
        ("disneyplus.com", "disney-plus.net", "bamgrid.com", "dssott.com"),
    ),
    CatalogueService(
        "prime_video", "Prime Video", ("primevideo.com", "aiv-cdn.net", "pv-cdn.net")
    ),
)
_BY_KEY = {service.key: service for service in SERVICE_CATALOGUE}


def catalogue_service(key: str) -> CatalogueService:
    """Return one catalogue entry or reject an unknown key."""

    service = _BY_KEY.get(key) if isinstance(key, str) else None
    if service is None:
        raise ValidationError("service is not in the catalogue")
    return service


def expand_service_domains(services: Iterable[str], hostnames: Iterable[str]) -> Tuple[str, ...]:
    """Return catalogue hostnames in catalogue order then custom hostnames, without repeats."""

    domains = []
    for key in services:
        domains.extend(catalogue_service(key).domains)
    domains.extend(normalize_service_hostname(hostname) for hostname in hostnames)
    return tuple(dict.fromkeys(domains))
