#!/usr/bin/env python3
"""
article_search_press.py — Guardian (API) + Al Jazeera WHERE pages
Fetch most-recent items per source (no date params), then drop anything older than CUTOFF_DATE.
Writes into ONE run folder: outputs/<CUTOFF_YYYYMMDD>/{text,_index.csv}

CONFIG (params.txt)
-------------------
COUNTRIES_FILE=countries.txt
CUTOFF_DATE=2025-08-25
LIMIT_ALJAZEERA=5
LIMIT_GUARDIAN=5
OUT=outputs
FULLTEXT=1
"""
from __future__ import annotations

import argparse, csv, html, os, re, sys, time, random, json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Dict, Iterable, Any
from urllib.parse import quote, urljoin, urlparse

import requests
from bs4 import BeautifulSoup

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0.0.0 Safari/537.36")
HEADERS_HTML = {
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate",  # avoid 'br' to dodge decoder quirks
    "Referer": "https://www.aljazeera.com/",
}
REQ_DELAY = (0.45, 0.9)

GUARDIAN_API_URL = "https://content.guardianapis.com/search"
GUARDIAN_API_KEY = os.getenv("GUARDIAN_API_KEY", "test")

@dataclass
class Article:
    source: str
    title: str
    url: str
    published: Optional[str] = None
    full_text: Optional[str] = None
    def published_date(self) -> Optional[str]:
        if not self.published: return None
        try:
            return datetime.fromisoformat(self.published.replace("Z","")).date().isoformat()
        except Exception:
            m = re.match(r"^(\d{4}-\d{2}-\d{2})", self.published)
            return m.group(1) if m else None

def _parse_bool(v: str) -> bool:
    return str(v).strip().lower() in {"1","true","yes","y","on"}

def _strip_outer_quotes(v: Optional[str]) -> Optional[str]:
    if v is None: return None
    s = v.strip()
    if len(s)>=2 and s[0]==s[-1] and s[0] in ("'",'"'): s = s[1:-1].strip()
    return s

def ensure_date(date_str: str) -> str:
    try:
        return datetime.fromisoformat(date_str).date().isoformat()
    except Exception as e:
        raise SystemExit(f"Invalid date: {date_str}. Use YYYY-MM-DD.") from e

def read_countries_file(path: Path) -> List[str]:
    if not path.exists(): return []
    return [ln.strip() for ln in path.read_text(encoding="utf-8").splitlines()
            if ln.strip() and not ln.strip().startswith("#")]

def slugify(text: str) -> str:
    t = re.sub(r"[^a-z0-9]+","-",text.strip().lower())
    return re.sub(r"-+","-",t).strip("-") or "x"

def canonical_url(u: str) -> str:
    try:
        p = urlparse(u); return f"{p.scheme}://{p.netloc}{p.path}"
    except Exception: return u.strip()

def dedupe_articles(items: Iterable[Article]) -> List[Article]:
    seen, out = set(), []
    for a in items:
        key = canonical_url(a.url)
        if key in seen: continue
        seen.add(key); out.append(a)
    return out

def _soup(url: str, session: Optional[requests.Session] = None) -> BeautifulSoup:
    sess = session or requests
    r = sess.get(url, headers=HEADERS_HTML, timeout=30)
    if r.status_code == 429:
        time.sleep(1.2)
        r = sess.get(url, headers=HEADERS_HTML, timeout=30)
    if r.status_code == 406:
        # try a minimal header fallback
        r = sess.get(url, headers={"User-Agent": UA, "Accept": "*/*"}, timeout=30)
    r.raise_for_status()
    return BeautifulSoup(r.text, "html.parser")

# Guardian URL date helpers (supports /YYYY/mm/dd/ and /YYYY/mon/dd/)
_GUARD_MONTHS = {'jan':1,'feb':2,'mar':3,'apr':4,'may':5,'jun':6,
                 'jul':7,'aug':8,'sep':9,'oct':10,'nov':11,'dec':12}
GUARD_URL_NUM = re.compile(r"/(\d{4})/(\d{2})/(\d{2})/")
GUARD_URL_TXT = re.compile(r"/(\d{4})/([a-z]{3})/(\d{1,2})/", re.I)
def guardian_date_from_url(u: str) -> Optional[str]:
    m = GUARD_URL_NUM.search(u)
    if m: y,mm,dd = m.groups(); return f"{int(y):04d}-{int(mm):02d}-{int(dd):02d}"
    m = GUARD_URL_TXT.search(u)
    if m:
        y,mon,dd = m.groups(); mm = _GUARD_MONTHS.get(mon.lower())
        if mm: return f"{int(y):04d}-{mm:02d}-{int(dd):02d}"
    return None

# Al Jazeera numeric date in URL
ALJ_DATE_IN_URL = re.compile(r"/20(\d{2})/(\d{1,2})/(\d{1,2})/")
def alj_date_from_url(u: str) -> Optional[str]:
    m = ALJ_DATE_IN_URL.search(u)
    return f"20{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}" if m else None

# ── Guardian provider ─────────────────────────────────────────────────────────
def guardian_recent(country: str, want_fulltext: bool, limit: int) -> List[Article]:
    params = {"q": country, "order-by": "newest", "page-size": min(50,max(1,limit)),
              "page": 1, "api-key": GUARDIAN_API_KEY}
    if want_fulltext:
        params["show-fields"] = "bodyText"
    r = requests.get(GUARDIAN_API_URL, params=params, timeout=30)
    if r.status_code == 429:
        time.sleep(1.0); r = requests.get(GUARDIAN_API_URL, params=params, timeout=30)
    r.raise_for_status()
    data = r.json().get("response", {})
    if data.get("status") != "ok": return []
    out: List[Article] = []
    for it in data.get("results", []):
        art = Article(
            source="The Guardian",
            title=(it.get("webTitle") or "").strip(),
            url=it.get("webUrl",""),
            published=(it.get("webPublicationDate","")[:10] or None),
        )
        if want_fulltext:
            fields = it.get("fields") or {}
            art.full_text = (fields.get("bodyText") or "").strip() or None
        out.append(art)
        if len(out) >= limit: break
    return out

# ── Al Jazeera: robust full-text extraction ───────────────────────────────────

def _jsonld_article_bodies(soup: BeautifulSoup) -> List[str]:
    """Extract articleBody/text from any JSON-LD blocks."""
    texts: List[str] = []
    for s in soup.find_all("script", {"type": "application/ld+json"}):
        raw = s.string or s.get_text()
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except Exception:
            continue
        # normalize to list
        objs: List[Any] = data if isinstance(data, list) else [data]
        for obj in objs:
            if not isinstance(obj, dict):
                continue
            if obj.get("@type") in {"NewsArticle", "Article", "Report"}:
                # Prefer articleBody, fall back to text
                body = obj.get("articleBody") or obj.get("text") or ""
                if isinstance(body, list):
                    body = "\n\n".join([str(x) for x in body if x])
                if isinstance(body, str) and body.strip():
                    texts.append(body.strip())
    return texts

_AJ_BODY_SELECTORS = [
    "article [data-component='article-body']",
    "article .wysiwyg",
    "article .article-p-wrapper",
    "article .article-body",
    "article .article__body",
    "article .longform-body",
    "article [itemprop='articleBody']",
    "article",
]

def _clean_paragraphs(nodes: List[BeautifulSoup]) -> List[str]:
    out: List[str] = []
    seen = set()
    for p in nodes:
        t = p.get_text(" ", strip=True)
        if not t: continue
        low = t.lower()
        if "sign up for" in low and "newsletter" in low: continue
        if low.startswith("follow al jazeera"): continue
        if low.startswith("recommended stories"): continue
        if low.startswith("source: al jazeera"): continue
        if t in seen: continue
        seen.add(t)
        out.append(t)
    return out

def alj_fetch_fulltext(url: str, session: Optional[requests.Session] = None) -> str:
    """Best-effort extraction for Al Jazeera article bodies."""
    time.sleep(random.uniform(*REQ_DELAY))
    s = _soup(url, session=session)

    # 1) JSON-LD (most reliable when present)
    bodies = _jsonld_article_bodies(s)
    if bodies:
        # Keep the longest JSON-LD body (some pages include short 'text' alongside full body)
        best = max(bodies, key=len)
        if len(best.split()) > 40:  # guard against ultra-short blurbs
            return best

    # 2) Known body containers
    for sel in _AJ_BODY_SELECTORS:
        conts = s.select(sel)
        if not conts: 
            continue
        paras: List[str] = []
        for c in conts:
            paras.extend(c.find_all("p"))
        cleaned = _clean_paragraphs(paras)
        if cleaned:
            return "\n\n".join(cleaned)

    # 3) Fallback: any <p> on page (last resort)
    cleaned = _clean_paragraphs(s.find_all("p"))
    return "\n\n".join(cleaned)

def alj_where_recent(country: str, want_fulltext: bool, limit: int) -> List[Article]:
    url = f"https://www.aljazeera.com/where/{quote(country)}/"
    out: List[Article] = []
    with requests.Session() as sess:
        s = _soup(url, session=sess)
        for a in s.select("a.u-clickable-card__link"):
            href = a.get("href") or ""
            if not href: continue
            u = href if href.startswith("http") else urljoin("https://www.aljazeera.com", href)
            if "/news/" not in u: continue
            title = a.get_text(" ", strip=True)
            art = Article(source="Al Jazeera", title=title, url=u, published=alj_date_from_url(u))
            if want_fulltext:
                try:
                    art.full_text = alj_fetch_fulltext(u, session=sess)
                except Exception:
                    art.full_text = None
            out.append(art)
            if len(out) >= limit: break
    return out

# ── Outputs ───────────────────────────────────────────────────────────────────
def write_country_txt(text_dir: Path, country: str, cutoff: str, items: List[Article], include_fulltext: bool) -> Path:
    text_dir.mkdir(parents=True, exist_ok=True)
    fp = text_dir / f"{slugify(country)}.txt"
    new_file = not fp.exists()
    with fp.open("a", encoding="utf-8") as f:
        if new_file:
            f.write(f"Country: {country} | Cutoff: {cutoff}\n\n")
        f.write(("[PRESS]\n") if new_file else ("\n[PRESS]\n"))
        if not items:
            f.write("No Press results found.\n")
        else:
            for i, a in enumerate(items, 1):
                title = html.unescape(a.title or "").strip()
                pub = a.published_date() or (a.published or "").strip()
                f.write(f"{i}) {title} — {a.source}{f' ({pub})' if pub else ''}\n")
                f.write(f"   URL: {a.url}\n")
                if include_fulltext and a.full_text:
                    f.write("   Text:\n")
                    for para in a.full_text.splitlines():
                        if para.strip(): f.write(f"     {para.strip()}\n")
                f.write("\n")
    return fp

def append_index_csv(index_path: Path, country: str, items: List[Article], cutoff: str) -> None:
    exists = index_path.exists()
    with index_path.open("a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if not exists:
            w.writerow(["country","source","title","url","published","cutoff"])
        for a in items:
            w.writerow([country, a.source, a.title, a.url, a.published_date() or (a.published or ""), cutoff])

def guardian_recent_wrapped(country: str, fulltext: bool, lim_g: int) -> List[Article]:
    try:
        return guardian_recent(country, want_fulltext=fulltext, limit=lim_g)
    except Exception as e:
        print(f"[WARN] Guardian failed for {country}: {e}", file=sys.stderr)
        return []

def alj_where_recent_wrapped(country: str, fulltext: bool, lim_alj: int) -> List[Article]:
    try:
        return alj_where_recent(country, want_fulltext=fulltext, limit=lim_alj)
    except Exception as e:
        print(f"[WARN] Al Jazeera failed for {country}: {e}", file=sys.stderr)
        return []

def collect_for_country(country: str, cutoff: str, lim_alj: int, lim_g: int, fulltext: bool) -> List[Article]:
    g_hits = guardian_recent_wrapped(country, fulltext, lim_g)
    a_hits = alj_where_recent_wrapped(country, fulltext, lim_alj)
    for a in g_hits:
        if not a.published_date():
            a.published = guardian_date_from_url(a.url) or a.published
    c = ensure_date(cutoff)
    kept = []
    for a in (g_hits + a_hits):
        d = a.published_date()
        if d and d < c: continue
        kept.append(a)
    kept = dedupe_articles(kept)
    kept.sort(key=lambda a: a.published_date() or "", reverse=True)
    return kept

def load_params_file(path: Path) -> Dict[str,str]:
    cfg: Dict[str,str] = {}
    if not path.exists(): return cfg
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"): continue
        if "=" in line: k,v = line.split("=",1)
        elif ":" in line: k,v = line.split(":",1)
        else:
            parts = line.split(None,1)
            if len(parts)!=2: continue
            k,v = parts[0].lstrip("-"), parts[1]
        cfg[k.strip().upper()] = _strip_outer_quotes(v)
    return cfg

def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Fetch most-recent Guardian + Al Jazeera (/where/<country>/), then filter by CUTOFF_DATE.")
    ap.add_argument("--config", default=None)
    ap.add_argument("--countries", default=None, help="TXT with one country per line.")
    ap.add_argument("--cutoff",  default=None, help="YYYY-MM-DD, keep items on/after this date.")
    ap.add_argument("--limit-alj", type=int, default=None)
    ap.add_argument("--limit-guardian", type=int, default=None)
    ap.add_argument("--out", default=None)
    ap.add_argument("--fulltext", action="store_true")
    args = ap.parse_args(argv)

    cfg_path = Path(args.config) if args.config else Path("params.txt")
    cfg = load_params_file(cfg_path) if cfg_path.exists() else {}

    countries_path = _strip_outer_quotes(args.countries or cfg.get("COUNTRIES_FILE"))
    cutoff = ensure_date(_strip_outer_quotes(args.cutoff or cfg.get("CUTOFF_DATE") or ""))
    lim_alj = args.limit_alj or int(cfg.get("LIMIT_ALJAZEERA", "5"))
    lim_g   = args.limit_guardian or int(cfg.get("LIMIT_GUARDIAN", "5"))
    out_root = Path(args.out or cfg.get("OUT", "outputs"))
    include_fulltext = args.fulltext or _parse_bool(cfg.get("FULLTEXT", "0"))

    if not countries_path:
        raise SystemExit("Missing COUNTRIES_FILE/--countries")

    run_dir = out_root / cutoff.replace("-","")
    text_dir = run_dir / "text"
    run_dir.mkdir(parents=True, exist_ok=True)
    text_dir.mkdir(parents=True, exist_ok=True)
    index_csv = run_dir / "_index.csv"

    countries = read_countries_file(Path(countries_path))
    if not countries:
        print(f"[WARN] No countries in {countries_path}", file=sys.stderr)

    total = 0
    for ctry in countries:
        print(f"[INFO] {ctry!r} — ALJ={lim_alj} Guardian={lim_g} fulltext={include_fulltext}")
        items = collect_for_country(ctry, cutoff=cutoff, lim_alj=lim_alj, lim_g=lim_g, fulltext=include_fulltext)
        write_country_txt(text_dir, ctry, cutoff, items, include_fulltext=include_fulltext)
        append_index_csv(index_csv, ctry, items, cutoff)
        total += len(items)

    print(f"[DONE] Press written → {run_dir} (total items: {total})")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
