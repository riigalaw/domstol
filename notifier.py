"""
Email notifier. Supports two backends:

1. Resend (recommended) — set RESEND_API_KEY and RESEND_FROM
2. SMTP (e.g. Microsoft 365, Gmail app password) — set SMTP_HOST,
   SMTP_PORT, SMTP_USER, SMTP_PASS, SMTP_FROM

Resend is preferred because there's no app-password gymnastics and the
free tier (100 emails/day) is more than enough for this use case.

The destination address comes from NOTIFY_TO.
"""

from __future__ import annotations

import html
import os
import smtplib
import ssl
from dataclasses import dataclass
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import requests


@dataclass
class EmailMessage:
    subject: str
    html_body: str
    text_body: str


def build_email(*, court_name: str, court_code: str, title: str, link: str,
                case_number: str | None, categories: list[str],
                summary_sv: str, case_type: str, media_signal: bool) -> EmailMessage:
    cats_label = ", ".join(categories) if categories else "—"
    case_line = f"Mål nr {case_number}" if case_number else ""
    media_line = "Media: aktuell rapportering hittad" if media_signal else ""

    subject = f"[{court_code}] {title}"[:150]

    text_body = "\n".join(
        line for line in [
            title,
            "",
            court_name,
            case_line,
            f"Typ: {case_type}",
            f"Kategorier: {cats_label}",
            media_line,
            "",
            summary_sv,
            "",
            link,
        ] if line is not None
    )

    # Pre-compute conditional snippets (Python disallows backslashes inside
    # f-string expressions, so we keep them out of the braces).
    case_number_html = (" &middot; " + html.escape(case_number)) if case_number else ""
    media_html = (
        '&nbsp;|&nbsp; <span style="color:#a0521b">' + media_line + "</span>"
        if media_signal else ""
    )
    summary_html_safe = html.escape(summary_sv).replace("\n", "<br>")
    link_safe = html.escape(link)

    html_body = f"""<!doctype html>
<html><body style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif;color:#111;max-width:640px;margin:0 auto;padding:16px;line-height:1.45;">
  <div style="font-size:12px;letter-spacing:0.04em;text-transform:uppercase;color:#666;margin-bottom:4px;">{html.escape(court_name)}{case_number_html}</div>
  <h2 style="margin:0 0 12px 0;font-size:18px;line-height:1.3;">{html.escape(title)}</h2>
  <div style="font-size:13px;color:#444;margin-bottom:14px;">
    <strong>Typ:</strong> {html.escape(case_type)} &nbsp;|&nbsp;
    <strong>Kategorier:</strong> {html.escape(cats_label)}
    {media_html}
  </div>
  <p style="margin:0 0 16px 0;font-size:15px;">{summary_html_safe}</p>
  <p style="margin:0;"><a href="{link_safe}" style="display:inline-block;background:#15174d;color:#fff;text-decoration:none;padding:10px 16px;border-radius:6px;font-size:14px;">Öppna på domstol.se</a></p>
  <p style="margin:24px 0 0 0;font-size:12px;color:#888;">Automatisk bevakning — domstol.se RSS. Klassificerad av Claude.</p>
</body></html>"""
    return EmailMessage(subject=subject, html_body=html_body, text_body=text_body)


def send(msg: EmailMessage) -> None:
    """Send via Resend if configured, otherwise SMTP. Raises on failure."""
    to_addr = os.environ.get("NOTIFY_TO")
    if not to_addr:
        raise RuntimeError("NOTIFY_TO is not set")

    resend_key = os.environ.get("RESEND_API_KEY")
    if resend_key:
        _send_resend(msg, to_addr, resend_key)
        return

    if os.environ.get("SMTP_HOST"):
        _send_smtp(msg, to_addr)
        return

    raise RuntimeError("No email backend configured (set RESEND_API_KEY or SMTP_*)")


def _send_resend(msg: EmailMessage, to_addr: str, api_key: str) -> None:
    from_addr = os.environ.get("RESEND_FROM", "Domstolsbevakning <onboarding@resend.dev>")
    resp = requests.post(
        "https://api.resend.com/emails",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json={
            "from": from_addr,
            "to": [to_addr],
            "subject": msg.subject,
            "html": msg.html_body,
            "text": msg.text_body,
        },
        timeout=20,
    )
    if resp.status_code >= 300:
        raise RuntimeError(f"Resend error {resp.status_code}: {resp.text}")


def _send_smtp(msg: EmailMessage, to_addr: str) -> None:
    host = os.environ["SMTP_HOST"]
    port = int(os.environ.get("SMTP_PORT", "587"))
    user = os.environ.get("SMTP_USER")
    password = os.environ.get("SMTP_PASS")
    from_addr = os.environ.get("SMTP_FROM", user or "no-reply@localhost")

    mime = MIMEMultipart("alternative")
    mime["Subject"] = msg.subject
    mime["From"] = from_addr
    mime["To"] = to_addr
    mime.attach(MIMEText(msg.text_body, "plain", "utf-8"))
    mime.attach(MIMEText(msg.html_body, "html", "utf-8"))

    context = ssl.create_default_context()
    with smtplib.SMTP(host, port, timeout=30) as server:
        server.starttls(context=context)
        if user and password:
            server.login(user, password)
        server.sendmail(from_addr, [to_addr], mime.as_string())
