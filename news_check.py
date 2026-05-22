"""
Google News relevance check for lower-instance court items.

For HFD/HD items, every legal-category match is notified directly.
For lower-instance courts (hovrätt, kammarrätt, etc.) we additionally
require either:
  - a legal-category hit on A/B/C, OR
  - a media-relevance hit (Google News finds recent Swedish coverage)

This module queries Google News' Swedish RSS endpoint with a query
derived from the case (case number + main keywords from the title).
We only count results published within the last RECENT_DAYS days.
"""

from __future__ import annotations

import re
import urllib.parse
from datetime import datetime, timedelta, timezone

import feedparser
import requests

GOOGLE_NEWS_RSS = "https://news.google.com/rss/search"
RECENT_DAYS = 14
USER_AGENT = "domstol-monitor/1.0 (+https://github.com/) python-requests"


def has_media_coverage(title: str, case_number: str | None = None, *, timeout: float = 10.0) -> bool:
    """Return True if recent Swedish news coverage exists for this case.

    Uses a fairly conservative query: the case number (most distinctive)
    OR the most informative noun phrase from the title.
    """
    query = _build_query(title, case_number)
    if not query:
        return False
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
    for entry in parsed.entries:
        published = _entry_datetime(entry)
        if published and published >= cutoff:
            return True
    return False


def _build_query(title: str, case_number: str | None) -> str:
    if case_number:
        # Quote the case number for an exact match.
        return f'"{case_number}"'
    # Fall back to the title with stopwords trimmed.
    cleaned = re.sub(r"[^\wåäöÅÄÖ\s\-]", " ", title)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned[:120]


def _entry_datetime(entry) -> datetime | None:
    for key in ("published_parsed", "updated_parsed"):
        t = getattr(entry, key, None) or entry.get(key)  # type: ignore[union-attr]
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
