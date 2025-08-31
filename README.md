# News Scanner

Country-focused news collection + drafting pipeline for UN/press signals.  
It pulls recent items from **The Guardian** (API) and **Al Jazeera** (web), optionally **UN Press**, filters by cutoff date, then asks an LLM to produce:
- an **INFO** digest (up to `INFO_LIMIT` bullets per country), and
- one or more **EMAIL** drafts when there’s sufficient country-relevant substance.

All outputs for a run live in a **single folder**: `outputs/<CUTOFF_YYYYMMDD>/`.

---

## Contents

- `article_search_press.py` – Guardian API + Al Jazeera recent articles, per country, optional full text.
- `article_search_un.py` – UN Press site search, newest pages first; trims by `CUTOFF_DATE`.
- `country_llm_writer.py` – builds the INFO bullets + outreach email(s) from collected text.
- `country_pipeline.py` – orchestration: runs press/un searches → LLM writer → aggregates.
- `params.txt` – run configuration (see below).
- `countries.txt` – one country per line (names/slugs like `united-states`, `france`, etc.).

> Note: All three scripts write into the same run directory: `outputs/<CUTOFF_YYYYMMDD>/`.

---

## Requirements

- Python **3.10+** (3.11 recommended)
- A virtual environment (recommended)
- Dependencies in `requirements.txt`
- (Optional) **Guardian** Content API key
- (Required for LLM) **OpenAI API key**

---

## Quick start

```bash
# from the repo root
python -V                          # ensure 3.10+
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
````

### Configure `params.txt`

Example:

```ini
COUNTRIES_FILE=countries.txt
CUTOFF_DATE=2025-08-25

# caps per provider (press/un)
LIMIT_UN=32
LIMIT_ALJAZEERA=32
LIMIT_GUARDIAN=64

# un | press | both
SOURCES=both
OUT=outputs
FULLTEXT=1
INFO_LIMIT=9

# Guardian API (demo "test" is limited; use a real key if possible)
GUARDIAN_API_KEY=test

# LLM
OPENAI_API_KEY=sk-...
MODEL=gpt-4.1-mini
TEMPERATURE=0.45
EMAIL_MIN_ITEMS=1
EMAIL_WORDS_MIN=80
EMAIL_WORDS_MAX=320
```

Put your OpenAI key here **or** set it in the shell:

```bash
export OPENAI_API_KEY=sk-...
```

### Countries file

`countries.txt` (one per line). Use common names or slugs (hyphenated):

```
united-states
china
germany
france
```

---

## Run the full pipeline

```bash
python country_pipeline.py --config params.txt
```

What happens:

1. **Press search** (Guardian + Al Jazeera) writes into `outputs/<CUTOFF>/text/<country>.txt` (adds a `[PRESS]` section).
2. **UN Press** search (newest pages first) writes into the same files (adds a `[UN]` section).
3. **LLM writer** reads those country text files and writes:

   * `outputs/<CUTOFF>/info/<country>.txt` (INFO bullets)
   * `outputs/<CUTOFF>/emails/<country>.txt` (one or more emails, when warranted)
   * Aggregations: `outputs/<CUTOFF>/all_info` and `outputs/<CUTOFF>/all_emails`

     * `all_emails` groups emails **by country header**, then prints each email’s Subject + Body.

Folder layout example:

```
outputs/20250825/
  _index.csv
  text/
    united-states.txt      # contains [PRESS] and [UN] sections
    france.txt
  info/
    united-states.txt
    france.txt
  emails/
    united-states.txt
    france.txt
  all_info
  all_emails
```

---

## Script usage (advanced)

### Press only

```bash
python article_search_press.py --config params.txt
```

* Respects: `COUNTRIES_FILE`, `CUTOFF_DATE`, `LIMIT_ALJAZEERA`, `LIMIT_GUARDIAN`, `FULLTEXT`.

### UN Press only

```bash
python article_search_un.py --config params.txt
```

* Fetches newest listing pages and trims by `CUTOFF_DATE`.
* If listing lacks dates, it opens a few articles to infer dates; then filters.

### LLM writer only

```bash
python country_llm_writer.py
```

* Reads `OUT/<CUTOFF>/text/<country>.txt` files already produced.
* Writes INFO & EMAILS to the same `OUT/<CUTOFF>/` tree.

---

## Environment variables (optional)

* `GUARDIAN_API_KEY` – overrides params file.
* `OPENAI_API_KEY`   – overrides params file.
* Rate limits exist inside the scripts; if you hit throttling, reduce `LIMIT_*` or set `FULLTEXT=0`.

---

## Troubleshooting

* **UN Press returns 0 items**: check `CUTOFF_DATE` isn’t ahead of recent items; ensure country tokens (e.g., `"united-states"` not `"usa"`). Network hiccups can cause empty pages; rerun.
* **Guardian full text empty**: demo API key `"test"` often suppresses `show-fields=bodyText`. Use a real key or set `FULLTEXT=0`.
* **LLM errors**: ensure `OPENAI_API_KEY` is set and the `MODEL` exists on your account. Lower `TEMPERATURE` for more neutral tone.

---

## Notes & limits

* HTML scraping (AJ/UN) can change; heuristics aim to be resilient but not perfect.
* `FULLTEXT=1` is slower and may trigger site throttling; adjust limits if needed.
* Emails use a structured prompt to: (a) prune irrelevant items; (b) cap bullets at `INFO_LIMIT`; and (c) generate up to four concise, theme-specific emails per country **only when** there are ≥3 strong items.

---

## License

Private / internal use.