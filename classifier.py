"""
LLM classifier for Swedish court news items — Google Gemini backend.

Uses Google's Gemini API (free tier) to decide whether a given case is
relevant to the user's areas of interest:

  A. Public procurement (LOU, LUF, LUK, LUFS, LOV)
  B. Commercial contract disputes — especially construction standards
     (AB 04, ABT 06, ABK 09, AB-U 07, ABT-U 07, AMA, AFU, etc.)
  C. Access to public documents and secrecy (offentlighet och sekretess)
     that relates to public contracts and tenders
  D. Cases highly noticed in Swedish media (only applied to lower-instance
     courts; for HFD/HD all matches on A–C are notified directly)

Returns a structured JSON verdict.

Requires environment variable GEMINI_API_KEY. Get one for free at
https://aistudio.google.com/ — no credit card needed.

Default model is gemini-2.5-flash. Override with env var GEMINI_MODEL
if you want a different one (e.g. gemini-2.0-flash, gemini-2.5-flash-lite).
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass

import requests


MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
API_BASE = "https://generativelanguage.googleapis.com/v1beta"
TIMEOUT_S = 30

SYSTEM_PROMPT = """Du är en jurist-assistent som triagerar nyhetsnotiser från svenska domstolar.

Du får titel + brödtext från en notis (oftast prövningstillstånd eller dom/beslut).
Avgör om målet faller inom NÅGON av följande kategorier:

A. OFFENTLIG UPPHANDLING — LOU, LUF, LUK, LUFS, LOV, ramavtal, överprövning av upphandling/avtal, skadestånd enligt upphandlingslagstiftningen, etc.

B. KOMMERSIELLA AVTALSTVISTER — särskilt entreprenadrätt och standardavtal som AB 04, ABT 06, ABK 09, AB-U 07, ABT-U 07, AMA, ABM, ABS, NL 17, NLM, Orgalime, ALEM, m.fl. Även andra större kommersiella tvister (köprätt mellan näringsidkare, leverans, distribution, agenturavtal, IT-avtal, M&A-tvister). Konsumenttvister räknas EJ.

C. OFFENTLIGHET OCH SEKRETESS — endast när målet rör handlingsoffentlighet eller sekretess i samband med offentliga upphandlingar, anbud, ramavtal eller offentliga kontrakt. Andra sekretessmål (t.ex. socialtjänst, polis) räknas EJ.

D. STORT MEDIALT INTRESSE — endast om notisen själv tydligt indikerar mycket stor medial uppmärksamhet (kända företag, känd händelse). Sätt vanligtvis "media_signal": false här; det avgörs senare via separat mediasök.

Svara ENDAST med giltig JSON enligt schemat:
{
  "match": <true/false>,
  "categories": ["A", "B", "C", "D"],
  "case_type": "<provningstillstand|dom|beslut|other>",
  "summary_sv": "<2-3 meningar på svenska som sammanfattar målet och varför det är relevant>",
  "media_signal": <true/false>,
  "reasoning": "<kort förklaring av bedömningen, max 1 mening>"
}

Var STRIKT — undvik falska positiva. Om det inte är klart att målet handlar om A/B/C, sätt match=false. Konsumenttvister, brottmål utan upphandlings-/avtalsanknytning, sociala mål, migrationsmål, skattemål etc. ska EJ matcha (om de inte också rör upphandling eller offentliga kontrakt)."""


@dataclass
class Verdict:
    match: bool
    categories: list[str]
    case_type: str
    summary_sv: str
    media_signal: bool
    reasoning: str
    raw: dict


def classify(title: str, body: str) -> Verdict:
    """Classify a court news item. Returns Verdict.

    Falls back to a safe non-match if the API call fails or the response
    can't be parsed.
    """
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        return _error_verdict("GEMINI_API_KEY not set")

    body = (body or "").strip()
    if len(body) > 6000:
        body = body[:6000] + "\n[...trunkerat...]"

    user_msg = f"TITEL:\n{title}\n\nBRÖDTEXT:\n{body}"

    url = f"{API_BASE}/models/{MODEL}:generateContent"
    payload = {
        "systemInstruction": {"parts": [{"text": SYSTEM_PROMPT}]},
        "contents": [{"role": "user", "parts": [{"text": user_msg}]}],
        "generationConfig": {
            "temperature": 0.1,
            "maxOutputTokens": 600,
            "responseMimeType": "application/json",
        },
    }

    try:
        resp = requests.post(
            url,
            params={"key": api_key},
            json=payload,
            timeout=TIMEOUT_S,
        )
        if resp.status_code >= 300:
            return _error_verdict(f"gemini_http_{resp.status_code}: {resp.text[:300]}")
        data = resp.json()
        text = _extract_text(data)
        if not text:
            return _error_verdict(f"gemini_empty_response: {json.dumps(data)[:300]}")
        parsed = _extract_json(text)
        return Verdict(
            match=bool(parsed.get("match")),
            categories=[c for c in parsed.get("categories", []) if isinstance(c, str)],
            case_type=str(parsed.get("case_type", "other")),
            summary_sv=str(parsed.get("summary_sv", "")).strip(),
            media_signal=bool(parsed.get("media_signal")),
            reasoning=str(parsed.get("reasoning", "")).strip(),
            raw=parsed,
        )
    except Exception as exc:  # noqa: BLE001
        return _error_verdict(f"classifier_error: {exc!s}")


def _extract_text(data: dict) -> str:
    candidates = data.get("candidates") or []
    for cand in candidates:
        content = cand.get("content") or {}
        parts = content.get("parts") or []
        for part in parts:
            text = part.get("text")
            if text:
                return text
    return ""


def _extract_json(text: str) -> dict:
    """Extract the first JSON object from text — tolerates fences/preamble.

    With responseMimeType=application/json Gemini usually returns clean
    JSON, but we keep this defensive parser for safety.
    """
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    if fence:
        return json.loads(fence.group(1))
    brace = re.search(r"\{.*\}", text, re.S)
    if brace:
        return json.loads(brace.group(0))
    raise ValueError(f"no JSON object in classifier response: {text[:200]!r}")


def _error_verdict(reason: str) -> Verdict:
    """Fail-closed verdict: don't notify, but log the reason."""
    return Verdict(
        match=False,
        categories=[],
        case_type="other",
        summary_sv="",
        media_signal=False,
        reasoning=reason,
        raw={"error": reason},
    )
