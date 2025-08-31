#!/usr/bin/env python3
"""
country_pipeline.py — single-run folder per cutoff, run UN/Press, write LLM, aggregate.
Adds country headers before each email block in all_emails.
"""
from __future__ import annotations
import argparse, sys, subprocess
from pathlib import Path

ALLOWED_SOURCES = {"press", "un", "both"}

def _strip_outer_quotes(v: str | None) -> str | None:
    if v is None: return None
    s = v.strip()
    if len(s) >= 2 and s[0] == s[-1] and s[0] in ("'", '"'):
        s = s[1:-1].strip()
    return s

def read_params(p: Path) -> dict:
    cfg={}
    if p.exists():
        for ln in p.read_text(encoding="utf-8").splitlines():
            s=ln.strip()
            if not s or s.startswith("#"): continue
            if "=" in s:
                k,v=s.split("=",1); cfg[k.strip().upper()]=_strip_outer_quotes(v)
    return cfg

def _slug_to_title(slug: str) -> str:
    # "united-states" -> "United States"
    return slug.replace("_", "-").replace("-", " ").title()

def cat_files(src_dir: Path, out_file: Path, kind: str = "info") -> None:
    """
    kind: 'info' or 'emails'. For 'emails', insert a country header before each email.
    """
    files = sorted([fp for fp in src_dir.glob("*.txt") if fp.is_file()])
    out_file.write_text("", encoding="utf-8")
    with out_file.open("a", encoding="utf-8") as out:
        for fp in files:
            try:
                content = fp.read_text(encoding="utf-8").rstrip()
                if kind == "emails":
                    country = _slug_to_title(fp.stem)
                    out.write(f"{country}\n")
                out.write(content + "\n\n")
            except Exception:
                pass

def aggregate_dir(run_dir: Path):
    emails_dir = run_dir / "emails"
    info_dir   = run_dir / "info"
    (run_dir / "all_emails").unlink(missing_ok=True)
    (run_dir / "all_info").unlink(missing_ok=True)
    if emails_dir.exists(): cat_files(emails_dir, run_dir / "all_emails", kind="emails")
    if info_dir.exists():   cat_files(info_dir,   run_dir / "all_info",   kind="info")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="params.txt")
    args = ap.parse_args()

    base_dir = Path(__file__).resolve().parent
    cfg_path = Path(args.config).resolve()
    cfg = read_params(cfg_path)

    sources = (cfg.get("SOURCES") or "both").strip().lower()
    if sources not in ALLOWED_SOURCES:
        print(f"[WARN] Invalid SOURCES={sources!r}; defaulting to 'both'.")
        sources = "both"

    out_root = Path(_strip_outer_quotes(cfg.get("OUT") or "outputs")).resolve()
    cutoff   = (_strip_outer_quotes(cfg.get("CUTOFF_DATE") or "") or "").strip()
    if not cutoff: sys.exit("CUTOFF_DATE is required in params.txt (YYYY-MM-DD)")

    # Single run dir (shared by UN + Press + LLM)
    run_dir = out_root / cutoff.replace("-", "")
    (run_dir / "text").mkdir(parents=True, exist_ok=True)
    (run_dir / "info").mkdir(parents=True, exist_ok=True)
    (run_dir / "emails").mkdir(parents=True, exist_ok=True)

    press_script  = (base_dir / "article_search_press.py").as_posix()
    un_script     = (base_dir / "article_search_un.py").as_posix()
    writer_script = (base_dir / "country_llm_writer.py").as_posix()

    # 1) Run searches
    if sources in ("press", "both"):
        print(f"[RUN] Press search → {press_script}")
        subprocess.run([sys.executable, press_script, "--config", str(cfg_path)], check=True)

    if sources in ("un", "both"):
        print(f"[RUN] UN search → {un_script}")
        subprocess.run([sys.executable, un_script, "--config", str(cfg_path)], check=True)

    # 2) LLM writer
    print("[RUN] country_llm_writer.py")
    subprocess.run([sys.executable, writer_script], check=True)

    # 3) Aggregate
    aggregate_dir(run_dir)
    print(f"[DONE] Pipeline complete → {run_dir}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
