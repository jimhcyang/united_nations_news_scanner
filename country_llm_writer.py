#!/usr/bin/env python3
"""
country_llm_writer.py — build INFO + EMAIL from combined per-country text
Reads:  outputs/<CUTOFF_YYYYMMDD>/text/<slug>.txt
Writes: outputs/<CUTOFF_YYYYMMDD>/{info|emails}/<slug>.txt

New:
- INFO_LIMIT in params.txt caps the number of bullets written to info files.
- Prompt strengthened to prune irrelevant/tangential press hits (esp. Guardian).
"""
from __future__ import annotations

import os, re, sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from openai import OpenAI

def _strip_outer_quotes(v: Optional[str]) -> Optional[str]:
    if v is None: return None
    v=v.strip()
    if len(v)>=2 and v[0]==v[-1] and v[0] in ("'",'"'): v=v[1:-1].strip()
    return v

def load_params_file(path: Path) -> Dict[str, str]:
    cfg={}
    if not path.exists(): return cfg
    for raw in path.read_text(encoding="utf-8").splitlines():
        s=raw.strip()
        if not s or s.startswith("#"): continue
        if "=" in s:
            k,v=s.split("=",1); cfg[k.strip().upper()]=_strip_outer_quotes(v)
    return cfg

def read_countries_file(path: Path) -> List[str]:
    if not path.exists(): return []
    return [ln.strip() for ln in path.read_text(encoding="utf-8").splitlines()
            if ln.strip() and not ln.strip().startswith("#")]

def ensure_date(date_str: str) -> str:
    try:
        return datetime.fromisoformat(date_str).date().isoformat()
    except Exception as e:
        raise SystemExit(f"Invalid CUTOFF_DATE: {date_str}. Use YYYY-MM-DD.") from e

def slugify(text: str) -> str:
    t = re.sub(r"[^a-z0-9]+","-", text.strip().lower())
    return re.sub(r"-{2,}","-", t).strip("-") or "x"

def build_prompt(country: str, cutoff: str, source_text: str,
                 min_items: int, min_words: int, max_words: int, info_limit: int) -> str:
    sys_role = (
        "You are a Joint SDG Fund brief writer with a political-anthropology lens. "
        "Audience: senior programme officers (DCO/RC system). Style: crisp, neutral, constructive; "
        "country-specific only; no other countries. Avoid links or invented facts."
    )
    task = f"""
COUNTRY: {country}
CUTOFF: {cutoff}  (use developments on/after this date only)

SOURCE (verbatim lines; this is your ONLY factual base):
---
{source_text}
---

STRICT RELEVANCE FILTER (very important)
• Include ONLY items that are clearly about {country} (named {country} actors, government decisions, UN engagement in-country,
  agreements, financing, sanctions/trade actions affecting {country}, disasters/security events in {country}, specific investments).
• DISCARD items that merely mention {country} in passing, are about another country/region, generic explainers, opinion columns,
  sports/celebrity pieces, or broad regional roundups with no {country}-specific substance.
• Treat all entry hits as potentially noisy; keep only ones with concrete {country}-specific actions or implications; mentioning other
  countries, global trends, or generic issues is permitted IF AND ONLY IF {country} is significantly mentioned.

TASKS (plain text, no markdown):
1) INFO: provide 1 to {info_limit} concise bullets about {country}, anchored in SOURCE. End each bullet with [date; source].
   Where helpful, add why it matters for RC/DCO engagement or financing windows (govt, private, philanthropic).
   No URLs. No other countries. Never invent.

2) DECIDE EMAIL:
   • Count the number of relevant {country} items AFTER applying the STRICT RELEVANCE FILTER.
   • If relevant coverage is VERY MINIMAL (fewer than max({min_items}, 3) items), set EMAIL_STATUS to SKIP and provide a one-line REASON.
   • Otherwise set EMAIL_STATUS to SEND. When substance spans multiple themes, group into major divisions and draft up to FOUR emails total,
     each with a clear GENRE (e.g., policy window, financing/investment, partnerships, humanitarian/climate, private sector, macro-fiscal,
     social protection, digital, etc.). Keep the tone collaborative and non-didactic; suggest practical next steps and light support.

3) If EMAIL_STATUS=SEND, produce for EACH email:
   • GENRE: one short label for the theme.
   • EMAIL_SUBJECT_N: one line, specific to {country}.
   • EMAIL_BODY_N: {min_words}-{max_words} words, single paragraph, natural/conversational but diplomatic.
     Briefly reference the facts and pivot to a gentle invitation or offer of support. No links. No other countries.
     Use a generic salutation if recipient unknown (e.g., “Excellency,” or “Dear Colleague,”). Close with:
     "Kind regards," and "United Nations Joint SDG Fund".

HARD OUTPUT RULES
• Do NOT use placeholders or cross-references such as "See EMAIL_1_SUBJECT" or "See above" anywhere.
• Always write real text for subjects and bodies.
• For backward compatibility, you MUST also output flat fields EMAIL_SUBJECT and EMAIL_BODY that exactly duplicate Email 1
  (copy the full text, not a reference).
• EMAIL_SUBJECT must be a single line (no bullets, no lists). EMAIL_BODY must be a single paragraph.

OUTPUT FORMAT (exact keys, in order; plain text only):
INFO:
- <bullet 1>
- <bullet 2>
- ... up to {info_limit} bullets

EMAIL_STATUS: SEND|SKIP
REASON: <one line>   # required if SKIP

# If SEND, provide 1 to 3 emails with numbered fields:
EMAIL_COUNT: <1-3>

EMAIL_1_GENRE: <text>
EMAIL_1_SUBJECT: <text>
EMAIL_1_BODY:
<text>

EMAIL_2_GENRE: <text>     # omit if EMAIL_COUNT < 2
EMAIL_2_SUBJECT: <text>
EMAIL_2_BODY:
<text>

EMAIL_3_GENRE: <text>     # omit if EMAIL_COUNT < 3
EMAIL_3_SUBJECT: <text>
EMAIL_3_BODY:
<text>

# Back-compat FALLBACK (must be REAL text = exact copy of EMAIL_1_*):
EMAIL_SUBJECT: <repeat EMAIL_1_SUBJECT verbatim — no references>
EMAIL_BODY:
<repeat EMAIL_1_BODY verbatim — no references>
END
""".strip()
    return f"{sys_role}\n\n{task}"

@dataclass
class LLMResult:
    info_text: str
    email_status: str
    reason: Optional[str]
    subject: Optional[str]
    body: Optional[str]

def parse_llm_output(text: str) -> LLMResult:
    m_info = re.search(r"INFO:\s*(.*?)\nEMAIL_STATUS:", text, flags=re.S)
    info_block = (m_info.group(1) if m_info else "").strip()
    m_status = re.search(r"EMAIL_STATUS:\s*(SEND|SKIP)", text)
    status = (m_status.group(1) if m_status else "SKIP").strip()
    m_reason = re.search(r"REASON:\s*(.*?)\n(?:EMAIL_SUBJECT:|EMAIL_BODY:|END|\Z)", text, flags=re.S)
    reason = (m_reason.group(1).strip() if m_reason else None)
    subject = body = None
    if status == "SEND":
        m_subj = re.search(r"EMAIL_SUBJECT:\s*(.*?)\nEMAIL_BODY:", text, flags=re.S)
        subject = (m_subj.group(1).strip() if m_subj else None)
        m_body = re.search(r"EMAIL_BODY:\s*(.*?)\nEND", text, flags=re.S)
        body = (m_body.group(1).strip() if m_body else None)
    return LLMResult(info_block, status, reason, subject, body)

def _cap_info_bullets(info_text: str, limit: int) -> str:
    """
    Keep at most `limit` bullet lines (prefix -, –, •). Do not fabricate.
    If no leading-bullet lines found, return original text.
    """
    lines = [ln.rstrip() for ln in info_text.splitlines() if ln.strip()]
    bullets = [ln for ln in lines if re.match(r"^\s*[-•–]\s+", ln)]
    if not bullets:
        # try numbered format '1) ' or '1.' if model used that style
        bullets = [ln for ln in lines if re.match(r"^\s*\d+[\)\.]\s+", ln)]
    if bullets:
        return "\n".join(bullets[:max(1, limit)])
    # Fallback: keep original but still avoid runaway length
    return "\n".join(lines[:max(1, limit)])

def main() -> int:
    cfg = load_params_file(Path("params.txt"))

    countries_file = Path(_strip_outer_quotes(cfg.get("COUNTRIES_FILE") or "countries.txt"))
    cutoff = ensure_date(_strip_outer_quotes(cfg.get("CUTOFF_DATE") or ""))
    out_root = Path(_strip_outer_quotes(cfg.get("OUT") or "outputs"))

    # LLM config
    api_key = _strip_outer_quotes(cfg.get("OPENAI_API_KEY")) or os.getenv("OPENAI_API_KEY")
    if not api_key: raise SystemExit("OPENAI_API_KEY not found in params.txt or environment.")
    model = _strip_outer_quotes(cfg.get("MODEL") or "gpt-4.1-mini")
    temperature = float(_strip_outer_quotes(cfg.get("TEMPERATURE") or "1"))
    min_items = int(_strip_outer_quotes(cfg.get("EMAIL_MIN_ITEMS") or "1"))
    words_min = int(_strip_outer_quotes(cfg.get("EMAIL_WORDS_MIN") or "80"))
    words_max = int(_strip_outer_quotes(cfg.get("EMAIL_WORDS_MAX") or "120"))
    info_limit = int(_strip_outer_quotes(cfg.get("INFO_LIMIT") or "5"))

    client = OpenAI(api_key=api_key)

    # Single run dir
    run_dir   = out_root / cutoff.replace("-", "")
    text_dir  = run_dir / "text"
    info_dir  = run_dir / "info"
    emails_dir= run_dir / "emails"
    for d in (info_dir, emails_dir): d.mkdir(parents=True, exist_ok=True)

    # Countries list
    countries = read_countries_file(countries_file)
    if not countries: raise SystemExit(f"No countries in {countries_file}")

    for country in countries:
        slug = slugify(country)
        fp = text_dir / f"{slug}.txt"
        if not fp.exists():
            print(f"[SKIP] {country}: no source text found.", file=sys.stderr)
            continue

        source_text = fp.read_text(encoding="utf-8").strip()
        prompt = build_prompt(country, cutoff, source_text, min_items, words_min, words_max, info_limit)
        try:
            resp = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                temperature=temperature,
            )
            text = (resp.choices[0].message.content or "").strip()
            parsed = parse_llm_output(text)

            # INFO (trim to INFO_LIMIT, never fabricate)
            info_out = info_dir / f"{slug}.txt"
            info_body = (parsed.info_text or "").strip()
            info_body = _cap_info_bullets(info_body, info_limit) if info_body else "(No substantive updates identified.)"
            info_out.write_text(info_body + "\n", encoding="utf-8")

            # EMAIL (optional)
            if parsed.email_status == "SEND" and parsed.subject and parsed.body:
                email_out = emails_dir / f"{slug}.txt"
                email_text = f"Subject: {parsed.subject}\n\n{parsed.body}\n"
                email_out.write_text(email_text, encoding="utf-8")
                print(f"[OK] {country}: INFO + EMAIL written.")
            else:
                reason = parsed.reason or "Insufficient country-specific substance."
                print(f"[SKIP] {country}: Email skipped — {reason}")

        except Exception as e:
            print(f"[ERROR] {country}: {e}", file=sys.stderr)

    print(f"[DONE] Wrote INFO → {info_dir} and EMAILS (when SEND) → {emails_dir}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())