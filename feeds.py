"""
RSS feed configuration for domstol.se monitoring.

The domstol.se RSS endpoints use the pattern:
    https://www.domstol.se/feed/56?searchPageId=<PAGE_ID>&scope=news

PAGE_ID 2693 is the all-courts news feed (Sveriges Domstolar). It contains
items from every court including HFD and HD, so a single feed is enough.
The per-court feeds are listed below in case you want to monitor a subset
or apply different logic per court.

For each item, we determine the court from the URL path
(e.g. /hogsta-forvaltningsdomstolen/ → HFD, /hogsta-domstolen/ → HD).
"""

# Master feed — covers all courts in Sveriges Domstolar
ALL_COURTS_FEED = "https://www.domstol.se/feed/56?searchPageId=2693&scope=news"

# Per-court feeds (kept for reference / alternative configurations)
PER_COURT_FEEDS = {
    "HFD": "https://www.domstol.se/feed/56?searchPageId=1092&scope=news",
    "HD":  "https://www.domstol.se/feed/56?searchPageId=1122&scope=news",
}

# URL-path → (court_code, court_name, is_supreme) lookup.
# is_supreme = True means we skip the media-relevance check (always notify
# if legal categories match). For lower-instance courts we additionally
# require a media-relevance signal (Google News hit OR legal-category hit).
COURT_LOOKUP = {
    "hogsta-forvaltningsdomstolen": ("HFD", "Högsta förvaltningsdomstolen", True),
    "hogsta-domstolen":             ("HD",  "Högsta domstolen", True),
    # Hovrätter
    "svea-hovratt":                 ("Svea HovR", "Svea hovrätt", False),
    "gota-hovratt":                 ("Göta HovR", "Göta hovrätt", False),
    "hovratten-over-skane-och-blekinge": ("HovR SoB", "Hovrätten över Skåne och Blekinge", False),
    "hovratten-for-vastra-sverige": ("HovR VS", "Hovrätten för Västra Sverige", False),
    "hovratten-for-nedre-norrland": ("HovR NN", "Hovrätten för Nedre Norrland", False),
    "hovratten-for-ovre-norrland":  ("HovR ON", "Hovrätten för Övre Norrland", False),
    # Kammarrätter
    "kammarratten-i-stockholm":   ("KamR Sthlm", "Kammarrätten i Stockholm", False),
    "kammarratten-i-goteborg":    ("KamR Gbg",   "Kammarrätten i Göteborg", False),
    "kammarratten-i-jonkoping":   ("KamR Jkpg",  "Kammarrätten i Jönköping", False),
    "kammarratten-i-sundsvall":   ("KamR Sundsvall", "Kammarrätten i Sundsvall", False),
}


def identify_court(url: str) -> tuple[str, str, bool]:
    """Return (court_code, court_name, is_supreme) for a domstol.se URL.

    Falls back to a generic (other) classification if the court can't be
    identified from the URL path (some news items live under /nyheter/
    without a court prefix).
    """
    lowered = url.lower()
    for slug, info in COURT_LOOKUP.items():
        if f"/{slug}/" in lowered:
            return info
    return ("OTHER", "Annan domstol", False)
