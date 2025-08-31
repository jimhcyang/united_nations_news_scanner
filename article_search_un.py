#!/usr/bin/env python3
"""
article_search_un.py — UN Press (most recent first; no date facet)
Grabs newest results page-by-page, then drops anything older than CUTOFF_DATE.

Writes into ONE run folder (shared with Press):
  outputs/<CUTOFF_YYYYMMDD>/text/<slug>.txt   (appends a [UN] section)
  outputs/<CUTOFF_YYYYMMDD>/_index.csv

CONFIG (params.txt)
-------------------
COUNTRIES_FILE=countries.txt
CUTOFF_DATE=2025-08-25
LIMIT_UN=32
OUT=outputs
FULLTEXT=0
"""
from __future__ import annotations

import argparse, csv, html, math, os, re, sys, time, random
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Dict, Iterable, Tuple
from urllib.parse import urlencode, urlparse

import requests
from bs4 import BeautifulSoup

# ── HTTP setup ────────────────────────────────────────────────────────────────
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36")

HEADERS_PRIMARY = {
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.8",
    "Accept-Encoding": "gzip, deflate",  # avoid br
    "Referer": "https://press.un.org/en",
}
HEADERS_MINIMAL = {"User-Agent": UA, "Accept": "text/html"}

LISTING_DELAY = (0.30, 0.70)
ARTICLE_DELAY = (0.35, 0.90)

UN_BASE = "https://press.un.org/en/sitesearch"

# Internal sanity limit so we never loop forever (not exposed in params)
MAX_LIST_PAGES = 20

# ── Data model ────────────────────────────────────────────────────────────────
@dataclass
class Article:
    source: str
    title: str
    url: str
    published: Optional[str] = None  # YYYY-MM-DD
    full_text: Optional[str] = None
    def published_date(self) -> Optional[str]:
        if not self.published: return None
        m = re.match(r"^(\d{4}-\d{2}-\d{2})", self.published)
        return m.group(1) if m else None

# ── small helpers ─────────────────────────────────────────────────────────────
def _parse_bool(v: str) -> bool:
    return str(v).strip().lower() in {"1","true","yes","y","on"}

def ensure_date(d: str) -> str:
    try:
        return datetime.fromisoformat(d).date().isoformat()
    except Exception as e:
        raise SystemExit(f"Invalid CUTOFF_DATE: {d} (use YYYY-MM-DD)") from e

def read_countries(path: Path) -> List[str]:
    if not path.exists(): return []
    out = []
    for ln in path.read_text(encoding="utf-8").splitlines():
        s = ln.strip()
        if s and not s.startswith("#"): out.append(s)
    return out

def slugify(s: str) -> str:
    return re.sub(r"-+","-", re.sub(r"[^a-z0-9]+","-", s.lower())).strip("-") or "x"

def canonical_url(u: str) -> str:
    try:
        p = urlparse(u); return f"{p.scheme}://{p.netloc}{p.path}"
    except Exception:
        return u.strip()

def dedupe(items: Iterable[Article]) -> List[Article]:
    seen, out = set(), []
    for a in items:
        key = (a.title or "").strip().lower() + "||" + canonical_url(a.url)
        if key in seen: continue
        seen.add(key); out.append(a)
    return out

def load_params_file(path: Path) -> Dict[str,str]:
    cfg: Dict[str,str] = {}
    if not path.exists(): return cfg
    for raw in path.read_text(encoding="utf-8").splitlines():
        s = raw.strip()
        if not s or s.startswith("#"): continue
        if "=" in s: k,v = s.split("=",1)
        elif ":" in s: k,v = s.split(":",1)
        else:
            parts = s.split(None,1)
            if len(parts)!=2: continue
            k,v = parts[0], parts[1]
        cfg[k.strip().upper()] = v.strip()
    return cfg

# ── HTTP + parsing ───────────────────────────────────────────────────────────
def _get_html(session: requests.Session, url: str) -> BeautifulSoup:
    r = session.get(url, headers=HEADERS_PRIMARY, timeout=30, allow_redirects=True)
    if r.status_code == 429:
        time.sleep(1.0)
        r = session.get(url, headers=HEADERS_PRIMARY, timeout=30, allow_redirects=True)
    if r.status_code == 406:
        r = session.get(url, headers=HEADERS_MINIMAL, timeout=30, allow_redirects=True)
    r.raise_for_status()
    return BeautifulSoup(r.text, "html.parser")

def build_un_list_url(country: str, page_num: int) -> str:
    """
    UN search: first page has no 'page' param; second page is ?page=1, third ?page=2, ...
    We accept page_num starting at 1 for the first page.
    """
    term = country.replace("-", " ").replace("_", " ").strip()
    q = urlencode({"search_api_fulltext": term, "sort_by": "field_dated"}, doseq=True)
    if page_num <= 1:
        return f"{UN_BASE}?{q}"
    return f"{UN_BASE}?{q}&page={page_num-1}"

def parse_listing(doc: BeautifulSoup) -> List[Tuple[str,str,Optional[str]]]:
    """
    Return list of (title, url, date_iso_or_none) for the listing page, newest first.
    """
    out: List[Tuple[str,str,Optional[str]]] = []
    blocks = doc.select("article.node--view-mode-search-results") or doc.select("article.node")
    if not blocks:
        blocks = [el for el in doc.find_all(True) if el.find("a", href=True)]
    seen = set()
    for el in blocks:
        a = el.find("a", href=True)
        if not a: continue
        url = a["href"]
        if url.startswith("/"): url = "https://press.un.org" + url
        if url in seen: continue
        seen.add(url)
        title = a.get_text(" ", strip=True)
        date_iso = None
        t = el.find("time")
        if t:
            raw = t.get("datetime") or t.get_text(" ", strip=True)
            m = re.search(r"(\d{4}-\d{2}-\d{2})", raw or "")
            if m: date_iso = m.group(1)
        else:
            # occasional "DD Month YYYY" embedded
            txt = el.get_text(" ", strip=True)
            m2 = re.search(r"(\d{1,2}\s+[A-Za-z]{3,9}\s+\d{4})", txt)
            if m2:
                try:
                    dt = datetime.strptime(m2.group(1), "%d %B %Y")
                    date_iso = dt.date().isoformat()
                except Exception:
                    pass
        out.append((title, url, date_iso))
    return out

def get_article_date(session: requests.Session, url: str) -> Optional[str]:
    """Fetch a single article page and extract a YYYY-MM-DD date."""
    try:
        time.sleep(random.uniform(*ARTICLE_DELAY))
        doc = _get_html(session, url)
    except Exception:
        return None
    t = doc.find("time", {"datetime": True})
    if t:
        raw = t.get("datetime")
        m = re.search(r"(\d{4}-\d{2}-\d{2})", raw or "")
        if m: return m.group(1)
    t2 = doc.find("time")
    if t2:
        raw = t2.get_text(" ", strip=True)
        m = re.search(r"(\d{4}-\d{2}-\d{2})", raw or "")
        if m: return m.group(1)
    for sel in [
        ('meta', {'property':'article:published_time'}),
        ('meta', {'name':'date'}),
        ('meta', {'name':'pubdate'}),
    ]:
        tag = doc.find(*sel)
        if tag:
            raw = tag.get("content") or ""
            m = re.search(r"(\d{4}-\d{2}-\d{2})", raw)
            if m: return m.group(1)
    return None

def fetch_un_article_text(session: requests.Session, url: str) -> str:
    time.sleep(random.uniform(*ARTICLE_DELAY))
    doc = _get_html(session, url)
    body = (doc.find("div", {"class": "field--name-body"})
            or doc.find("div", {"property": "content:encoded"})
            or doc.find("article") or doc)
    paras = body.find_all("p") if body else doc.find_all("p")
    lines = []
    for p in paras:
        t = p.get_text(" ", strip=True)
        if t.lower().startswith("for information media"): break
        if t: lines.append(t)
    return "\n\n".join(lines)

# ── core search ───────────────────────────────────────────────────────────────
def search_un(country: str, cutoff: str, limit: int) -> List[Article]:
    """
    Walk listing pages sorted by date, collect up to LIMIT items on/after cutoff.
    To resolve missing dates, open at most ceil(log2(limit)) article pages.
    """
    cutoff_iso = ensure_date(cutoff)
    candidates: List[Article] = []
    date_fetch_budget = max(1, math.ceil(math.log2(max(2, limit))))
    older_seen = False

    with requests.Session() as s:
        page = 1
        while len(candidates) < limit and page <= MAX_LIST_PAGES and not older_seen:
            url = build_un_list_url(country, page)
            try:
                doc = _get_html(s, url)
            except Exception:
                break
            rows = parse_listing(doc)
            if not rows:
                break

            for (title, url, date_iso) in rows:
                # If listing has no date and budget remains, fetch one
                if not date_iso and date_fetch_budget > 0:
                    d = get_article_date(s, url)
                    if d:
                        date_iso = d
                    date_fetch_budget -= 1

                # Early stop if we hit a definitely-older item (list is newest→older)
                if date_iso and date_iso < cutoff_iso:
                    older_seen = True
                    break

                candidates.append(Article(source="UN Press", title=title, url=url, published=date_iso))
                if len(candidates) >= limit:
                    break

            page += 1
            time.sleep(random.uniform(*LISTING_DELAY))

    # Filter by cutoff when we know the date; keep unknown-date items only if needed to fill to LIMIT
    known_fresh = [a for a in candidates if (a.published_date() or "") >= cutoff_iso]
    unknowns = [a for a in candidates if not a.published_date()]

    kept = dedupe(known_fresh)
    kept.sort(key=lambda x: x.published_date() or "", reverse=True)

    if len(kept) < limit and unknowns:
        # Append unknown-date items in listing order to fill to LIMIT (best-effort)
        for a in unknowns:
            kept.append(a)
            if len(kept) >= limit:
                break

    return kept[:limit]

# ── output writers ───────────────────────────────────────────────────────────
def write_country_txt(text_dir: Path, country: str, cutoff: str, items: List[Article], fulltext: bool) -> Path:
    text_dir.mkdir(parents=True, exist_ok=True)
    fp = text_dir / f"{slugify(country)}.txt"
    new_file = not fp.exists()
    with fp.open("a", encoding="utf-8") as f:
        if new_file:
            f.write(f"Country: {country} | Cutoff: {cutoff}\n\n")
        f.write(("[UN]\n") if new_file else ("\n[UN]\n"))
        if not items:
            f.write("No UN results found.\n")
        else:
            # Optionally fetch full text (polite)
            if fulltext:
                with requests.Session() as s:
                    for a in items:
                        if a.full_text: continue
                        try:
                            a.full_text = fetch_un_article_text(s, a.url)
                        except Exception:
                            pass
            for i, a in enumerate(items, 1):
                pub = a.published_date() or (a.published or "")
                f.write(f"{i}) {html.unescape(a.title or '').strip()} — {a.source}{f' ({pub})' if pub else ''}\n")
                f.write(f"   URL: {a.url}\n")
                if fulltext and a.full_text:
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

# ── main ─────────────────────────────────────────────────────────────────────
def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="UN Press (newest first, no facet); filter by CUTOFF_DATE.")
    ap.add_argument("--config", default=None)
    ap.add_argument("--countries", default=None)
    ap.add_argument("--cutoff", default=None)
    ap.add_argument("--limit-un", type=int, default=None)
    ap.add_argument("--out", default=None)
    ap.add_argument("--fulltext", action="store_true")
    args = ap.parse_args(argv)

    cfg_path = Path(args.config) if args.config else Path("params.txt")
    cfg = load_params_file(cfg_path) if cfg_path.exists() else {}

    countries_path = args.countries or cfg.get("COUNTRIES_FILE")
    cutoff = ensure_date(args.cutoff or cfg.get("CUTOFF_DATE") or "")
    limit_un = args.limit_un or int(cfg.get("LIMIT_UN","32"))
    out_root = Path(args.out or cfg.get("OUT","outputs"))
    include_fulltext = args.fulltext or _parse_bool(cfg.get("FULLTEXT","0"))

    if not countries_path:
        raise SystemExit("Missing COUNTRIES_FILE/--countries")

    run_dir  = out_root / cutoff.replace("-", "")
    text_dir = run_dir / "text"
    run_dir.mkdir(parents=True, exist_ok=True)
    text_dir.mkdir(parents=True, exist_ok=True)
    index_csv = run_dir / "_index.csv"

    countries = read_countries(Path(countries_path))
    total = 0
    for ctry in countries:
        print(f"[INFO] UN: {ctry!r} — limit={limit_un} fulltext={include_fulltext}")
        items = search_un(ctry, cutoff=cutoff, limit=limit_un)
        write_country_txt(text_dir, ctry, cutoff, items, fulltext=include_fulltext)
        append_index_csv(index_csv, ctry, items, cutoff)
        total += len(items)

    print(f"[DONE] UN written → {run_dir} (total items: {total})")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
