"""
LLM classifier for Swedish court news items.

Uses Anthropic's Claude API (Haiku) to decide whether a given case is
relevant to the user's areas of interest:

  A. Public procurement (LOU, LUF, LUK, LUFS, LOV)
  B. Commercial contract disputes — especially construction standards
     (AB 04, ABT 06, ABK 09, AB-U 07, ABT-U 07, AMA, AFU, etc.)
  C. Access to public documents and secrecy (offentlighet och sekretess)
     that relates to public contracts and tenders
  D. Cases highly noticed in Swedish media (only applied to lower-instance
     courts; for HFD/HD all matches on A–C are notified directly)

Returns a structured JSON verdict.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass

import anthropic


MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-haiku-4-5-20251001")

SYSTEM_PROMPT = """Du är en jurist-assistent som triagerar nyhetsnotiser från svenska domstolar.

Du får titel + brödtext från en notis (oftast prövningstillstånd eller dom/beslut).
Avgör om målet faller inom NÅGON av följande kategorier:

A. OFFENTLIG UPPHANDLING — LOU, LUF, LUK, LUFS, LOV, ramavtal, överprövning av upphandling/avtal, skadestånd enligt upphandlingslagstiftningen, etc.

B. KOMMERSIELLA AVTALSTVISTER — särskilt entreprenadrätt och standardavtal som AB 04, ABT 06, ABK 09, AB-U 07, ABT-U 07, AMA, ABM, ABS, NL 17, NLM, Orgalime, ALEM, m.fl. Även andra större kommersiella tvister (köprätt mellan näringsidkare, leverans, distribution, agenturavtal, IT-avtal, M&A-tvister). Konsumenttvister räknas EJ.

C. OFFENTLIGHET OCH SEKRETESS — endast när målet rör handlingsoffentlighet eller sekretess i samband med offentliga upphandlingar, anbud, ramavtal eller offentliga kontrakt. Andra sekretessmål (t.ex. socialtjänst, polis) räknas EJ.

D. STORT MEDIALT INTRESSE — endast om notisen själv tydligt indikerar mycket stor medial uppmärksamhet (kända företag, känd händelse). Sätt vanligtvis "media_signal": false här; det avgörs senare via separat mediasök.

Svara ENDAST med giltig JSON enligt schemat:
{
  "match": <true/false>,                 // true om någon av A/B/C träffar
  "categories": ["A", "B", "C", "D"],    // de kategorier som träffar (tomma om ingen)
  "case_type": "<provningstillstand|dom|beslut|other>",
  "summary_sv": "<2-3 meningar på svenska som sammanfattar målet och varför det är relevant>",
  "media_signal": <true/false>,          // sätt true endast om titeln/texten själv tydligt indikerar stor medial uppmärksamhet
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


def classify(title: str, body: str, *, client: anthropic.Anthropic | None = None) -> Verdict:
    """Classify a court news item. Returns Verdict.

    Falls back to a safe non-match if the API call fails or the response
    can't be parsed.
    """
    client = client or anthropic.Anthropic()

    # Trim body to keep the prompt small and cheap. The notis pages are
    # usually short (a few paragraphs). 6000 chars is more than enough.
    body = (body or "").strip()
    if len(body) > 6000:
        body = body[:6000] + "\n[...trunkerat...]"

    user_msg = f"TITEL:\n{title}\n\nBRÖDTEXT:\n{body}"

    try:
        resp = client.messages.create(
            model=MODEL,
            max_tokens=600,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_msg}],
        )
        text = "".join(block.text for block in resp.content if hasattr(block, "text"))
        data = _extract_json(text)
        return Verdict(
            match=bool(data.get("match")),
            categories=[c for c in data.get("categories", []) if isinstance(c, str)],
            case_type=str(data.get("case_type", "other")),
            summary_sv=str(data.get("summary_sv", "")).strip(),
            media_signal=bool(data.get("media_signal")),
            reasoning=str(data.get("reasoning", "")).strip(),
            raw=data,
        )
    except Exception as exc:  # noqa: BLE001
        # Fail closed (no notification) but log enough to debug.
        return Verdict(
            match=False,
            categories=[],
            case_type="other",
            summary_sv="",
            media_signal=False,
            reasoning=f"classifier_error: {exc!s}",
            raw={"error": str(exc)},
        )


def _extract_json(text: str) -> dict:
    """Extract the first JSON object from text — tolerates fences/preamble."""
    # Strip code fences if present.
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    if fence:
        return json.loads(fence.group(1))
    # Otherwise grab the first {...} block.
    brace = re.search(r"\{.*\}", text, re.S)
    if brace:
        return json.loads(brace.group(0))
    raise ValueError(f"no JSON object in classifier response: {text[:200]!r}")
