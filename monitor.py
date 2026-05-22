"""
domstol.se → email monitor.

Run as a cron job (e.g. every 10 minutes on GitHub Actions). Each run:

  1. Loads the deduplication state (state/seen.json).
  2. Pulls the all-courts news RSS feed from domstol.se.
  3. For each item not yet seen:
       - Fetches the case page HTML and extracts title + body.
       - Classifies it via Claude (categories A/B/C/D).
       - For non-supreme courts that did NOT hit a legal category,
         runs a Google News check; sends only if media coverage exists.
       - Sends an email summary to NOTIFY_TO if relevant.
  4. Writes the updated state file back.

The script is idempotent: every item it processes is added to seen,
whether or not it triggered a notification, so we never spam on retry.

Environment variables:
  ANTHROPIC_API_KEY   — required, for the classifier
  NOTIFY_TO           — required, destination email
  RESEND_API_KEY      — recommended (Resend email backend)
  RESEND_FROM         — optional, defaults to onboarding@resend.dev
  SMTP_HOST/PORT/USER/PASS/FROM — alternative SMTP backend
  DRY_RUN             — if "1", don't send emails (used for smoke tests)
  BACKFILL            — if "1", on first run mark all current feed items
                        as seen WITHOUT sending; useful when first deploying
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
import time
from collections import deque
from html import unescape
from pathlib import Path
from typing import Iterable

import feedparser
import requests
from bs4 import BeautifulSoup

from classifier import classify
from feeds import ALL_COURTS_FEED, identify_court
from news_check import extract_case_number, has_media_coverage
from notifier import build_email, send

LOG = logging.getLogger("monitor")
logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)

STATE_PATH = Path(__file__).parent / "state" / "seen.json"
STATE_MAX_IDS = 5000

USER_AGENT = "domstol-monitor/1.0 (+contact: peter@riigalaw.se) python-requests"


def main() -> int:
    state = load_state()
    seen: set[str] = set(state.get("seen", []))
    order: deque[str] = deque(state.get("order", []), maxlen=STATE_MAX_IDS)

    backfill = os.environ.get("BACKFILL") == "1"
    dry_run = os.environ.get("DRY_RUN") == "1"

    feed = fetch_feed(ALL_COURTS_FEED)
    if not feed.entries:
        LOG.warning("Feed returned no entries (status=%s)", getattr(feed, "status", "?"))
        return 0

    LOG.info("Feed has %d entries; %d already seen", len(feed.entries), len(seen))

    new_items = [e for e in feed.entries if entry_id(e) not in seen]
    LOG.info("%d new items to process", len(new_items))

    notified = 0
    for entry in new_items:
        eid = entry_id(entry)
        try:
            handled = process_entry(entry, dry_run=dry_run, backfill=backfill)
            if handled:
                notified += 1
        except Exception as exc:  # noqa: BLE001
            LOG.exception("Failed to process %s: %s", eid, exc)
            # We don't mark failures as seen — try again next run.
            continue
        seen.add(eid)
        order.append(eid)

    # Prune to bound the state file size.
    if len(order) >= STATE_MAX_IDS:
        order_set = set(order)
        seen &= order_set

    save_state({"seen": sorted(seen), "order": list(order)})
    LOG.info("Done. notified=%d, new_seen=%d, total_seen=%d",
             notified, len(new_items), len(seen))
    return 0


# ---------------------------------------------------------------------------
# Feed / page fetching
# ---------------------------------------------------------------------------

def fetch_feed(url: str):
    LOG.info("Fetching feed %s", url)
    resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=30)
    resp.raise_for_status()
    return feedparser.parse(resp.content)


def fetch_page(url: str) -> str:
    LOG.debug("Fetching page %s", url)
    resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=30)
    resp.raise_for_status()
    return resp.text


def extract_article_text(html: str) -> tuple[str, str]:
    """Return (title, body_plaintext) from a domstol.se article page."""
    soup = BeautifulSoup(html, "html.parser")

    # Title: prefer og:title meta, fall back to <h1>
    title = ""
    og = soup.find("meta", attrs={"property": "og:title"})
    if og and og.get("content"):
        title = og["content"].strip()
    if not title:
        h1 = soup.find("h1")
        if h1:
            title = h1.get_text(strip=True)

    # Body: the main <article> or main content area.
    body_el = soup.find("article") or soup.find("main") or soup.body
    if body_el is None:
        return title, ""

    # Remove nav/footer/share blocks. domstol.se pages have lots of
    # boilerplate that doesn't help classification.
    for tag in body_el.find_all(["nav", "footer", "script", "style", "form", "header"]):
        tag.decompose()
    text = body_el.get_text(separator="\n", strip=True)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return title, text


# ---------------------------------------------------------------------------
# Per-entry pipeline
# ---------------------------------------------------------------------------

def process_entry(entry, *, dry_run: bool, backfill: bool) -> bool:
    """Return True if a notification was sent."""
    link = entry.link
    if not link:
        LOG.warning("Entry without link, skipping: %r", entry)
        return False

    eid = entry_id(entry)
    LOG.info("Processing %s", link)

    if backfill:
        LOG.info("BACKFILL mode → marking as seen without sending: %s", link)
        return False

    court_code, court_name, is_supreme = identify_court(link)

    # The feed already gives us a title and short description; we still
    # fetch the full page to feed the classifier richer content.
    title = unescape(getattr(entry, "title", "")).strip()
    summary_html = unescape(getattr(entry, "summary", "")).strip()

    try:
        page_html = fetch_page(link)
        page_title, page_body = extract_article_text(page_html)
        title = page_title or title
        body = page_body or BeautifulSoup(summary_html, "html.parser").get_text(" ", strip=True)
    except requests.RequestException as exc:
        LOG.warning("Could not fetch page (%s): %s — classifying on feed snippet", link, exc)
        body = BeautifulSoup(summary_html, "html.parser").get_text(" ", strip=True)

    verdict = classify(title, body)
    LOG.info("Classifier verdict for %s: match=%s cats=%s reason=%s",
             link, verdict.match, verdict.categories, verdict.reasoning)

    # Pre-filter: anything classified as a legal-category match goes through.
    relevant = verdict.match

    # For non-supreme courts WITHOUT a legal-category hit, fall back to
    # the media-coverage check. This catches the "highly noticed in
    # Swedish media" category for lower-instance items.
    media_hit = verdict.media_signal
    if not is_supreme and not relevant:
        case_number = extract_case_number(title + " " + body)
        if has_media_coverage(title, case_number):
            media_hit = True
            relevant = True
            LOG.info("Media coverage check hit for %s (case=%s)", link, case_number)

    if not relevant:
        LOG.info("Not relevant — skipping notification for %s", link)
        return False

    case_number = extract_case_number(title + " " + body)
    msg = build_email(
        court_name=court_name,
        court_code=court_code,
        title=title,
        link=link,
        case_number=case_number,
        categories=verdict.categories,
        summary_sv=verdict.summary_sv or _fallback_summary(body),
        case_type=verdict.case_type,
        media_signal=media_hit,
    )

    if dry_run:
        LOG.info("DRY_RUN — would send: %s", msg.subject)
        return True

    send(msg)
    LOG.info("Sent email: %s", msg.subject)
    # Small courtesy delay to avoid hammering Resend in a burst.
    time.sleep(0.5)
    return True


def _fallback_summary(body: str) -> str:
    body = body.strip().replace("\n", " ")
    return (body[:280] + "…") if len(body) > 280 else body


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------

def entry_id(entry) -> str:
    """Stable per-item id. Prefer Atom <id>, fall back to link."""
    for attr in ("id", "guid", "link"):
        value = getattr(entry, attr, None) or entry.get(attr) if isinstance(entry, dict) else getattr(entry, attr, None)
        if value:
            return str(value).strip()
    # As a last resort, hash the title + published.
    return f"{getattr(entry, 'title', '')}|{getattr(entry, 'published', '')}"


def load_state() -> dict:
    if not STATE_PATH.exists():
        return {"seen": [], "order": []}
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        LOG.warning("State file unreadable (%s) — starting fresh", exc)
        return {"seen": [], "order": []}


def save_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(STATE_PATH)


if __name__ == "__main__":
    sys.exit(main())
