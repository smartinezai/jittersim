"""Plain-language labels for the DNS names that test devices look up.

The table is written by hand and bundled with the program. Nothing here contacts an outside
service. A name matches the longest suffix in the table so a specific entry such as
teams.microsoft.com beats its parent microsoft.com. A name nobody can place is labelled with its
registrable domain.
"""

from dataclasses import dataclass
from ipaddress import IPv4Address
from typing import Dict, Iterable, List, Tuple

from .models import normalize_domain
from .service_catalogue import SERVICE_CATALOGUE

LABEL_KINDS = ("app", "system", "tracker", "iot", "cdn", "unknown")
UNKNOWN_CATEGORY = "Unknown site"
# Names that a device looks up within this many seconds of each other count as seen together.
SEEN_WITH_WINDOW_SECONDS = 10
# A shared name is only guessed to belong to an app when at most this many apps qualify.
_SEEN_WITH_MAX_APPS = 2
_SEEN_WITH_KINDS = frozenset({"cdn", "tracker", "unknown"})
_FALLBACK_CATALOGUE_CATEGORY = "Online service"

# Second-level suffixes under which the registrable domain has three labels.
_SECOND_LEVEL = frozenset({
    "co.uk", "org.uk", "ac.uk", "gov.uk", "com.au", "net.au", "org.au", "co.nz", "co.jp",
    "co.in", "com.br", "com.mx", "co.za", "com.tr", "com.cn", "com.hk", "com.sg",
    "co.kr", "or.kr", "com.ar", "com.tw", "com.pl", "ne.jp", "or.jp", "co.id", "co.il",
    "com.my", "com.ph", "com.vn", "com.ua", "org.nz", "net.nz", "co.th",
})
# Reverse lookup names are shown whole because their labels are the address digits.
_REVERSE_SUFFIXES = ("in-addr.arpa", "ip6.arpa")

_CATALOGUE_CATEGORY = {
    "linkedin": "Social network", "microsoft_teams": "Meetings", "chatgpt": "AI assistant",
    "claude": "AI assistant", "netflix": "Streaming", "youtube": "Streaming",
    "zoom": "Meetings", "google_meet": "Meetings", "slack": "Work chat",
    "spotify": "Music", "disney_plus": "Streaming", "prime_video": "Streaming",
}

# (suffix, name, kind, category). Hand-written entries. A suffix that the service catalogue
# already owns must not appear here.
_EXTRA: Tuple[Tuple[str, str, str, str], ...] = (
    # Meta
    ("facebook.com", "Facebook", "app", "Social network"),
    ("fbcdn.net", "Facebook", "app", "Social network"),
    ("fbsbx.com", "Facebook", "app", "Social network"),
    ("facebook.net", "Facebook", "tracker", "Social plugin"),
    ("instagram.com", "Instagram", "app", "Social network"),
    ("cdninstagram.com", "Instagram", "app", "Social network"),
    ("messenger.com", "Messenger", "app", "Messaging"),
    ("threads.net", "Threads", "app", "Social network"),
    ("whatsapp.net", "WhatsApp", "app", "Messaging"),
    ("whatsapp.com", "WhatsApp", "app", "Messaging"),
    # Social and messaging
    ("tiktok.com", "TikTok", "app", "Short video"),
    ("tiktokcdn.com", "TikTok", "app", "Short video"),
    ("tiktokv.com", "TikTok", "app", "Short video"),
    ("byteoversea.com", "TikTok", "app", "Short video"),
    ("snapchat.com", "Snapchat", "app", "Messaging"),
    ("sc-cdn.net", "Snapchat", "app", "Messaging"),
    ("telegram.org", "Telegram", "app", "Messaging"),
    ("telegram.me", "Telegram", "app", "Messaging"),
    ("t.me", "Telegram", "app", "Messaging"),
    ("signal.org", "Signal", "app", "Messaging"),
    ("whispersystems.org", "Signal", "app", "Messaging"),
    ("discord.com", "Discord", "app", "Chat and voice"),
    ("discordapp.com", "Discord", "app", "Chat and voice"),
    ("discordapp.net", "Discord", "app", "Chat and voice"),
    ("discord.gg", "Discord", "app", "Chat and voice"),
    ("discord.media", "Discord", "app", "Chat and voice"),
    ("twitter.com", "X (Twitter)", "app", "Social network"),
    ("x.com", "X (Twitter)", "app", "Social network"),
    ("twimg.com", "X (Twitter)", "app", "Social network"),
    ("reddit.com", "Reddit", "app", "Social network"),
    ("redd.it", "Reddit", "app", "Social network"),
    ("redditmedia.com", "Reddit", "app", "Social network"),
    ("redditstatic.com", "Reddit", "app", "Social network"),
    ("pinterest.com", "Pinterest", "app", "Social network"),
    ("pinimg.com", "Pinterest", "app", "Social network"),
    ("twitch.tv", "Twitch", "app", "Live streaming"),
    ("ttvnw.net", "Twitch", "app", "Live streaming"),
    ("jtvnw.net", "Twitch", "app", "Live streaming"),
    # Shopping and travel and money
    ("amazon.com", "Amazon", "app", "Amazon services"),
    ("amazon.de", "Amazon", "app", "Amazon services"),
    ("media-amazon.com", "Amazon", "app", "Amazon services"),
    ("ssl-images-amazon.com", "Amazon", "app", "Amazon services"),
    ("amazonaws.com", "Amazon Web Services", "cdn", "Cloud hosting"),
    ("ebay.com", "eBay", "app", "Shopping"),
    ("ebay.de", "eBay", "app", "Shopping"),
    ("ebayimg.com", "eBay", "app", "Shopping"),
    ("ebaystatic.com", "eBay", "app", "Shopping"),
    ("zalando.com", "Zalando", "app", "Shopping"),
    ("zalando.de", "Zalando", "app", "Shopping"),
    ("ztat.net", "Zalando", "app", "Shopping"),
    ("booking.com", "Booking.com", "app", "Travel"),
    ("bstatic.com", "Booking.com", "app", "Travel"),
    ("airbnb.com", "Airbnb", "app", "Travel"),
    ("airbnb.de", "Airbnb", "app", "Travel"),
    ("muscache.com", "Airbnb", "app", "Travel"),
    ("paypal.com", "PayPal", "app", "Payments"),
    ("paypalobjects.com", "PayPal", "app", "Payments"),
    ("dropbox.com", "Dropbox", "app", "File storage"),
    ("dropboxapi.com", "Dropbox", "app", "File storage"),
    ("dropboxstatic.com", "Dropbox", "app", "File storage"),
    ("dropboxusercontent.com", "Dropbox", "app", "File storage"),
    # Google
    ("google.com", "Google", "system", "Google services"),
    ("google.de", "Google", "system", "Google services"),
    ("google.co.uk", "Google", "system", "Google services"),
    ("google.fr", "Google", "system", "Google services"),
    ("google.at", "Google", "system", "Google services"),
    ("google.ch", "Google", "system", "Google services"),
    ("google.nl", "Google", "system", "Google services"),
    ("google.es", "Google", "system", "Google services"),
    ("google.it", "Google", "system", "Google services"),
    ("google.pl", "Google", "system", "Google services"),
    ("gstatic.com", "Google", "system", "Google services"),
    ("googleapis.com", "Google", "system", "Google services"),
    ("googleusercontent.com", "Google", "system", "Google services"),
    ("gvt1.com", "Google", "system", "Google updates"),
    ("gvt2.com", "Google", "system", "Google updates"),
    ("android.com", "Android", "system", "Android services"),
    ("gmail.com", "Gmail", "app", "Email"),
    ("mail.google.com", "Gmail", "app", "Email"),
    ("youtubei.googleapis.com", "YouTube", "app", "Streaming"),
    ("storage.googleapis.com", "Google Cloud Storage", "cdn", "Cloud hosting"),
    ("bc.googleusercontent.com", "Google Cloud", "cdn", "Cloud hosting"),
    ("google-analytics.com", "Google Analytics", "tracker", "Web analytics"),
    ("googletagmanager.com", "Google Tag Manager", "tracker", "Tag manager"),
    ("doubleclick.net", "Google Ads", "tracker", "Advertising"),
    ("googlesyndication.com", "Google Ads", "tracker", "Advertising"),
    ("googleadservices.com", "Google Ads", "tracker", "Advertising"),
    # Apple
    ("apple.com", "Apple", "system", "Apple services"),
    ("icloud.com", "iCloud", "system", "Cloud storage"),
    ("icloud-content.com", "iCloud", "system", "Cloud storage"),
    ("mzstatic.com", "Apple", "system", "App Store media"),
    ("apple-dns.net", "Apple", "system", "Apple services"),
    ("aaplimg.com", "Apple", "system", "Apple downloads"),
    ("cdn-apple.com", "Apple", "system", "Apple downloads"),
    # Microsoft
    ("microsoft.com", "Microsoft", "system", "Microsoft services"),
    ("windows.com", "Windows", "system", "Windows services"),
    ("windowsupdate.com", "Windows Update", "system", "System updates"),
    ("msftconnecttest.com", "Windows", "system", "Connectivity check"),
    ("msftncsi.com", "Windows", "system", "Connectivity check"),
    ("live.com", "Microsoft", "system", "Microsoft account"),
    ("office.com", "Microsoft 365", "app", "Office apps"),
    ("office.net", "Microsoft 365", "app", "Office apps"),
    ("msedge.net", "Microsoft", "system", "Microsoft services"),
    ("bing.com", "Bing", "system", "Search"),
    ("msn.com", "MSN", "system", "News feed"),
    # Browsers and time
    ("mozilla.org", "Mozilla", "system", "Firefox services"),
    ("mozilla.com", "Mozilla", "system", "Firefox services"),
    ("mozilla.net", "Mozilla", "system", "Firefox services"),
    ("firefox.com", "Mozilla", "system", "Firefox services"),
    ("ntp.org", "NTP", "system", "Time sync"),
    # Reverse lookups
    ("in-addr.arpa", "Reverse DNS", "system", "Reverse lookup"),
    ("ip6.arpa", "Reverse DNS", "system", "Reverse lookup"),
    # Shared CDNs and cloud hosting
    ("akamai.net", "Akamai", "cdn", "Shared CDN"),
    ("akamai.com", "Akamai", "cdn", "Shared CDN"),
    ("akamaiedge.net", "Akamai", "cdn", "Shared CDN"),
    ("akamaihd.net", "Akamai", "cdn", "Shared CDN"),
    ("akamaized.net", "Akamai", "cdn", "Shared CDN"),
    ("edgekey.net", "Akamai", "cdn", "Shared CDN"),
    ("edgesuite.net", "Akamai", "cdn", "Shared CDN"),
    ("cloudflare.com", "Cloudflare", "cdn", "Shared CDN"),
    ("cloudflare.net", "Cloudflare", "cdn", "Shared CDN"),
    ("cloudflare-dns.com", "Cloudflare", "system", "Secure DNS"),
    ("fastly.net", "Fastly", "cdn", "Shared CDN"),
    ("fastlylb.net", "Fastly", "cdn", "Shared CDN"),
    ("fastly.com", "Fastly", "cdn", "Shared CDN"),
    ("cloudfront.net", "Amazon CloudFront", "cdn", "Shared CDN"),
    ("azureedge.net", "Microsoft Azure", "cdn", "Cloud hosting"),
    ("azurefd.net", "Microsoft Azure", "cdn", "Cloud hosting"),
    ("windows.net", "Microsoft Azure", "cdn", "Cloud hosting"),
    ("trafficmanager.net", "Microsoft Azure", "cdn", "Cloud hosting"),
    ("edgecastcdn.net", "Edgecast", "cdn", "Shared CDN"),
    ("jsdelivr.net", "jsDelivr", "cdn", "Shared CDN"),
    # Tag endpoints under an app. The longer suffix wins so the app itself stays an app.
    ("px.ads.linkedin.com", "LinkedIn Insight", "tracker", "Advertising"),
    ("snap.licdn.com", "LinkedIn Insight", "tracker", "Advertising"),
    ("analytics.tiktok.com", "TikTok Pixel", "tracker", "Advertising"),
    ("analytics.twitter.com", "X Ads", "tracker", "Advertising"),
    ("pixel.facebook.com", "Meta Pixel", "tracker", "Advertising"),
    ("ct.pinterest.com", "Pinterest Tag", "tracker", "Advertising"),
    ("ads.pinterest.com", "Pinterest Tag", "tracker", "Advertising"),
    ("bat.bing.com", "Microsoft Advertising", "tracker", "Advertising"),
    ("adservice.google.com", "Google Ads", "tracker", "Advertising"),
    ("ads.google.com", "Google Ads", "tracker", "Advertising"),
    # Trackers and ads and measurement
    ("adnxs.com", "Xandr", "tracker", "Advertising"),
    ("criteo.com", "Criteo", "tracker", "Advertising"),
    ("criteo.net", "Criteo", "tracker", "Advertising"),
    ("taboola.com", "Taboola", "tracker", "Advertising"),
    ("outbrain.com", "Outbrain", "tracker", "Advertising"),
    ("scorecardresearch.com", "Comscore", "tracker", "Audience measurement"),
    ("hotjar.com", "Hotjar", "tracker", "Session recording"),
    ("hotjar.io", "Hotjar", "tracker", "Session recording"),
    ("segment.io", "Segment", "tracker", "Web analytics"),
    ("segment.com", "Segment", "tracker", "Web analytics"),
    ("mixpanel.com", "Mixpanel", "tracker", "Web analytics"),
    ("amplitude.com", "Amplitude", "tracker", "Web analytics"),
    ("sentry.io", "Sentry", "tracker", "Error reporting"),
    ("bugsnag.com", "Bugsnag", "tracker", "Error reporting"),
    ("branch.io", "Branch", "tracker", "App attribution"),
    ("adjust.com", "Adjust", "tracker", "App attribution"),
    ("appsflyer.com", "AppsFlyer", "tracker", "App attribution"),
    ("app-measurement.com", "Firebase", "tracker", "App analytics"),
    ("demdex.net", "Adobe", "tracker", "Audience tracking"),
    ("omtrdc.net", "Adobe Analytics", "tracker", "Web analytics"),
    ("adobedtm.com", "Adobe Launch", "tracker", "Tag manager"),
    ("tiqcdn.com", "Tealium", "tracker", "Tag manager"),
    ("tealiumiq.com", "Tealium", "tracker", "Tag manager"),
    ("newrelic.com", "New Relic", "tracker", "Performance monitoring"),
    ("nr-data.net", "New Relic", "tracker", "Performance monitoring"),
    ("datadoghq.com", "Datadog", "tracker", "Performance monitoring"),
    ("cloudflareinsights.com", "Cloudflare", "tracker", "Web analytics"),
    ("onetrust.com", "OneTrust", "tracker", "Cookie banner"),
    ("cookielaw.org", "OneTrust", "tracker", "Cookie banner"),
    ("usercentrics.eu", "Usercentrics", "tracker", "Cookie banner"),
    # Smart-home and IoT vendors
    ("dreame.tech", "Dreame", "iot", "Robot vacuum"),
    ("roborock.com", "Roborock", "iot", "Robot vacuum"),
    ("ecovacs.com", "Ecovacs", "iot", "Robot vacuum"),
    ("ecouser.net", "Ecovacs", "iot", "Robot vacuum"),
    ("tuyaeu.com", "Tuya", "iot", "Smart home platform"),
    ("tuyaus.com", "Tuya", "iot", "Smart home platform"),
    ("tuya.com", "Tuya", "iot", "Smart home platform"),
    ("xiaomi.com", "Xiaomi", "iot", "Phones and smart home"),
    ("mi.com", "Xiaomi", "iot", "Phones and smart home"),
    ("miui.com", "Xiaomi", "iot", "Phones and smart home"),
    ("meethue.com", "Philips Hue", "iot", "Smart lighting"),
    ("sonos.com", "Sonos", "iot", "Smart speaker"),
    ("amazonalexa.com", "Amazon Alexa", "iot", "Voice assistant"),
    ("ring.com", "Ring", "iot", "Video doorbell"),
    ("nest.com", "Google Nest", "iot", "Smart home"),
    ("tplinkcloud.com", "TP-Link", "iot", "Smart home"),
    ("tplinkra.com", "TP-Link", "iot", "Smart home"),
    ("shelly.cloud", "Shelly", "iot", "Smart home"),
    ("home-assistant.io", "Home Assistant", "iot", "Smart home hub"),
    ("espressif.com", "Espressif", "iot", "Smart home chips"),
    ("samsungcloudsolution.com", "Samsung", "iot", "TV and smart home"),
    ("samsungcloudsolution.net", "Samsung", "iot", "TV and smart home"),
    ("smartthings.com", "SmartThings", "iot", "Smart home"),
    ("lgthinq.com", "LG ThinQ", "iot", "Smart appliances"),
    ("netatmo.net", "Netatmo", "iot", "Weather and heating"),
    ("netatmo.com", "Netatmo", "iot", "Weather and heating"),
    ("tado.com", "tado", "iot", "Smart thermostat"),
    ("myfritz.net", "FRITZ!Box", "iot", "Router remote access"),
    ("avm.de", "FRITZ!Box", "iot", "Router updates"),
    ("bosch-smarthome.com", "Bosch Smart Home", "iot", "Smart home"),
    ("miele.com", "Miele", "iot", "Smart appliances"),
    # Work tools
    ("grammarly.com", "Grammarly", "app", "Writing assistant"),
    ("notion.so", "Notion", "app", "Notes and docs"),
    ("notion.com", "Notion", "app", "Notes and docs"),
    ("figma.com", "Figma", "app", "Design tool"),
    ("atlassian.com", "Atlassian", "app", "Work tools"),
    ("atlassian.net", "Atlassian", "app", "Work tools"),
    ("jira.com", "Atlassian", "app", "Work tools"),
    ("github.com", "GitHub", "app", "Code hosting"),
    ("githubusercontent.com", "GitHub", "app", "Code hosting"),
    ("githubassets.com", "GitHub", "app", "Code hosting"),
    # German consumer sites
    ("immobilienscout24.de", "ImmoScout24", "app", "Real estate"),
    ("kleinanzeigen.de", "Kleinanzeigen", "app", "Classifieds"),
    ("ebay-kleinanzeigen.de", "Kleinanzeigen", "app", "Classifieds"),
    ("idealo.de", "idealo", "app", "Price comparison"),
    ("otto.de", "OTTO", "app", "Shopping"),
    ("check24.de", "CHECK24", "app", "Price comparison"),
    ("dhl.de", "DHL", "app", "Parcels"),
    ("dhl.com", "DHL", "app", "Parcels"),
    ("deutschebahn.com", "Deutsche Bahn", "app", "Train travel"),
    ("bahn.de", "Deutsche Bahn", "app", "Train travel"),
    ("spiegel.de", "Der Spiegel", "app", "News"),
    ("bild.de", "Bild", "app", "News"),
    ("zdf.de", "ZDF", "app", "Public TV"),
    ("ard.de", "ARD", "app", "Public TV"),
    ("tagesschau.de", "Tagesschau", "app", "News"),
    ("web.de", "WEB.DE", "app", "Email and portal"),
    ("gmx.net", "GMX", "app", "Email and portal"),
    ("t-online.de", "T-Online", "app", "Email and news"),
    ("vodafone.de", "Vodafone", "app", "Internet provider"),
    ("telekom.de", "Telekom", "app", "Internet provider"),
    ("ing.de", "ING", "app", "Bank"),
    ("sparkasse.de", "Sparkasse", "app", "Bank"),
)


def _catalogue_entries() -> Tuple[Tuple[str, str, str, str], ...]:
    entries = []
    for service in SERVICE_CATALOGUE:
        category = _CATALOGUE_CATEGORY.get(service.key, _FALLBACK_CATALOGUE_CATEGORY)
        for domain in service.domains:
            entries.append((domain, service.name, "app", category))
    return tuple(entries)


TABLE_SOURCE: Tuple[Tuple[str, str, str, str], ...] = _catalogue_entries() + _EXTRA
_TABLE: Dict[str, Tuple[str, str, str]] = {
    suffix: (name, kind, category) for suffix, name, kind, category in TABLE_SOURCE
}


@dataclass(frozen=True)
class DomainLabel:
    kind: str
    name: str
    category: str
    known: bool

    def as_dict(self) -> Dict[str, object]:
        return {
            "kind": self.kind,
            "name": self.name,
            "category": self.category,
            "known": self.known,
        }


def _is_ipv4_address(name: str) -> bool:
    try:
        IPv4Address(name)
    except ValueError:
        return False
    return True


def _is_reverse_name(name: str) -> bool:
    return any(name == suffix or name.endswith("." + suffix) for suffix in _REVERSE_SUFFIXES)


def registrable_domain(domain: str) -> str:
    """Return the last two labels or the last three under a known second-level suffix.

    A bare IPv4 address and a reverse lookup name are returned whole.
    """

    normalized = normalize_domain(domain)
    if _is_ipv4_address(normalized) or _is_reverse_name(normalized):
        return normalized
    labels = normalized.split(".")
    if len(labels) <= 2:
        return ".".join(labels)
    if ".".join(labels[-2:]) in _SECOND_LEVEL:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


def label_domain(domain: str) -> DomainLabel:
    """Return the label of the longest table suffix that matches the name."""

    labels = normalize_domain(domain).split(".")
    for start in range(len(labels)):
        entry = _TABLE.get(".".join(labels[start:]))
        if entry is not None:
            name, kind, category = entry
            return DomainLabel(kind, name, category, True)
    return DomainLabel("unknown", registrable_domain(domain), UNKNOWN_CATEGORY, False)


def seen_with(observations: Iterable[Tuple[str, int]]) -> Dict[str, List[str]]:
    """Guess which apps a shared name belongs to from one device's first-seen times.

    Each pair is a name and the time it was first seen. Only names labelled cdn or tracker or
    unknown get a hint. The hint lists the apps whose own names were first seen within the window
    of this name. An app with the same name as this label never counts. The hint is dropped when
    more than two apps qualify because the guess is too vague.
    """

    labelled = [(domain, first_seen, label_domain(domain)) for domain, first_seen in observations]
    app_times: Dict[str, List[int]] = {}
    for _, first_seen, label in labelled:
        if label.kind == "app":
            app_times.setdefault(label.name, []).append(first_seen)
    hints: Dict[str, List[str]] = {}
    for domain, first_seen, label in labelled:
        if label.kind not in _SEEN_WITH_KINDS:
            continue
        near = []
        for name, times in app_times.items():
            if name == label.name:
                continue
            distance = min(abs(time - first_seen) for time in times)
            if distance <= SEEN_WITH_WINDOW_SECONDS:
                near.append((distance, name))
        near.sort(key=lambda item: (item[0], item[1].lower(), item[1]))
        if near and len(near) <= _SEEN_WITH_MAX_APPS:
            hints[domain] = [name for _, name in near]
    return hints
