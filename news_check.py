"""
Google News relevance check for lower-instance court items.

For HFD/HD items, every legal-category match is notified directly.
For lower-instance courts (hovrätt, kammarrätt, etc.) we additionally
require either:
  - a legal-category hit on A/B/C, OR
  - a media-relevance hit (Google News finds RECENT and SUBSTANTIAL
    Swedish coverage of the case)

This module queries Google News' Swedish RSS endpoint with a query
that requires the case number (quoted, exact match). We only count
results published within the last RECENT_DAYS days, and we require
at least MIN_RECENT_HITS total hits with at least one from a major
Swedish outlet — this filters out single-mention coverage by
small blogs, syndication, or unrelated cases.
"""

from __future__ import annotations

import re
import urllib.parse
from datetime import datetime, timedelta, timezone

import feedparser
import requests

GOOGLE_NEWS_RSS = "https://news.google.com/rss/search"
RECENT_DAYS = 7
MIN_RECENT_HITS = 2
USER_AGENT = "domstol-monitor/1.0 (+contact: peter@riigalaw.se) python-requests"

# Major Swedish news outlets. We require at least one hit from this
# list to flag a case as "highly noticed in Swedish media".
MAJOR_OUTLETS = {
    "dn.se",
    "svd.se",
    "svt.se",
    "sverigesradio.se",
    "sr.se",
    "aftonbladet.se",
    "expressen.se",
    "sydsvenskan.se",
    "gp.se",
    "di.se",
    "dagensjuridik.se",
    "tv4.se",
    "omni.se",
    "altinget.se",
    "europaportalen.se",
}


def has_media_coverage(title: str, case_number: str | None = None, *, timeout: float = 10.0) -> bool:
    """Return True if recent substantial Swedish news coverage exists.

    Requires a real case number — title-based queries are too broad
    and produce false positives. Returns True only if:
      - the case number appears in at least MIN_RECENT_HITS news items
        published in the last RECENT_DAYS days, AND
      - at least one of those items is from a major Swedish outlet.
    """
    if not case_number:
        return False

    query = f'"{case_number}"'
    params = {
        "q": query,
        "hl": "sv",
        "gl": "SE",
        "ceid": "SE:sv",
    }
    url = f"{GOOGLE_NEWS_RSS}?{urllib.parse.urlencode(params)}"
    try:
        resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=timeout)
        resp.raise_for_status()
    except requests.RequestException:
        return False

    parsed = feedparser.parse(resp.content)
    cutoff = datetime.now(timezone.utc) - timedelta(days=RECENT_DAYS)

    total_hits = 0
    major_outlet_hits = 0
    for entry in parsed.entries:
        published = _entry_datetime(entry)
        if not published or published < cutoff:
            continue
        total_hits += 1
        link = (entry.get("link") or "").lower()
        if any(outlet in link for outlet in MAJOR_OUTLETS):
            major_outlet_hits += 1

    return total_hits >= MIN_RECENT_HITS and major_outlet_hits >= 1


def _build_query(title: str, case_number: str | None) -> str:
    """Kept for backward compatibility; current check uses case number only."""
    if case_number:
        return f'"{case_number}"'
    cleaned = re.sub(r"[^\wåäöÅÄÖ\s\-]", " ", title)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned[:120]


def _entry_datetime(entry) -> datetime | None:
    for key in ("published_parsed", "updated_parsed"):
        t = getattr(entry, key, None) or entry.get(key)
        if t:
            try:
                return datetime(*t[:6], tzinfo=timezone.utc)
            except (TypeError, ValueError):
                continue
    return None


# Case number patterns seen on domstol.se (e.g. "Mål nr 1260-26", "T 1234-25", "B 5678-24").
_CASE_NUMBER_RE = re.compile(r"\b(?:Mål\s*nr?\s*)?([A-ZÅÄÖ]?\s*\d{3,5}-\d{2})\b", re.IGNORECASE)


def extract_case_number(text: str) -> str | None:
    if not text:
        return None
    m = _CASE_NUMBER_RE.search(text)
    if not m:
        return None
    return re.sub(r"\s+", " ", m.group(1)).strip()
