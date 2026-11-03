# FINN: Financial Investment Agent

**A triaged investor inbox you can finish in two minutes, plus research grounded only in what companies filed.**

Built for the ENAM Holdings AI Engineer Build Challenge. Mobile-first web app (installable PWA).

---

## The point of view

Most investor tools are dashboards you dig through. FINN works more like an inbox: each morning it hands you a short, ranked stack of cards, you clear them, and you're done. Anything that raises a question opens research on that company **in context** ("Ask FINN →").

Three rules shape every screen:

1. **Earn the attention.** Each filing is ranked 🔴 *Act now* / 🟡 *Worth knowing* / ⚪ *Routine*. Routine stays collapsed. Duplicates (a results day produces 3–5 filings per company) fold into one card.
2. **Show the number, not the adjective.** Results cards show Revenue / EBITDA / PBT / PAT with QoQ and YoY. Alerts say "volume 2.5× its 20-day average", not "unusual activity".
3. **Every claim has a receipt.** Each research sentence cites the transcript chunk or reported number it rests on. A validator drops any claim whose citation doesn't check out.

## What it does

### Push
| Feature | How |
|---|---|
| **Windowed brief** (24h · 72h · 7d · 30d) | All NSE announcements for the portfolio, classified into the 17 reference categories and ranked by tier. Routine items collapsed, duplicates folded. |
| **One-line "what happened"** | A small model reads the filing PDF and writes one concrete sentence (numbers, names, dates). Batched 6 filings per call, cached per filing forever. |
| **Quarterly results card** | Revenue, EBITDA, PBT, PAT, with QoQ and YoY, margin vs a year ago, 8-quarter sparklines, and a link to the filing. |
| **Management change card** | SEBI Reg 30 requires appointment filings to include a *brief profile* of the appointee. FINN extracts the person's bio **from the filing itself** (primary source, no scraping), and upgrades CEO/CFO/MD/ED changes to *Act now*. |
| **Alerts** | Thresholds you set: price ±X%, volume N× 20-day average (EOD from yfinance), plus material filings in the categories you choose. |

### Pull (any NSE ticker)
| Section | How | Model |
|---|---|---|
| Snapshot, trajectory, cash quality, balance sheet | Computed from screener.in's standardised financials: CAGRs, margin trend, CFO÷PAT, D/E, cash conversion cycle | **None (arithmetic)** |
| **Promised vs delivered** ★ | Last 4 concall transcripts → BM25 picks guidance-heavy chunks → small model extracts quantified management promises with verbatim quotes → quote is verified against the chunk → large model judges each promise against reported quarterly/annual numbers: *delivered / partial / missed / pending / unverifiable* | small (map) + large (reduce) |
| **Bull vs bear** | 3 points each. A point without a valid citation is dropped. | large (same call) |

If screener.in has no transcript links for a company, FINN looks for transcripts in that company's own NSE filings. If the company doesn't hold calls at all (e.g. Trent), it says so instead of guessing.

## Architecture

```
 Mobile PWA (vanilla JS + SVG charts, no build step)
   Brief · Alerts · Research · Settings (localStorage)
                │ JSON
 FastAPI (one service, also serves the frontend)
   /api/digest  /api/digest/enrich  /api/alerts  /api/research/{t}  /api/research/{t}/narrative
   ├─ sources.py   NSE announcements + filing PDFs · yfinance EOD · screener.in fundamentals
   ├─ classify.py  rules-first 17-category classifier + tiering (no model)
   ├─ digest.py    windowing, grouping, enrichment (small model), results/management cards, alerts
   ├─ research.py  numbers (no model) · promised-vs-delivered + bull/bear (small→large, cited)
   ├─ llm.py       model routing, Groq → Gemini fallback, response cache, usage stats
   └─ cache.py     disk cache: live → stale → committed seed snapshot
```

### Model routing and cost
| Task | Model | Why |
|---|---|---|
| Filing category | **Rules**, measured at 69% of filings (146 of 212 portfolio filings over 30 days); small model only for vague exchange categories like "General Updates" or "Outcome of Board Meeting" | Most exchange categories are formulaic |
| One-liners, bios, guidance extraction | `gpt-oss-20b` (Groq) | Bulk, simple extraction |
| Promise verdicts + bull/bear | `gpt-oss-120b` (Groq), **one call per company** | Real reasoning over numbers |
| Snapshot / trajectory / cash / balance sheet | none | It's arithmetic |
| Fallback | Gemini Flash | Groq rate limits on the free tier |

Every LLM output is cached by (tier, prompt), and every filing is read exactly once. The Settings sheet shows live call counts, cache hits and tokens. Everything runs on free tiers.

### Reused from my earlier project
Pattern-level reuse from my Enterprise RAG Financial Assistant: chunk → retrieve → generate with citations → **validate citations**, plus the tolerant JSON parsing and provider fallback. I deliberately dropped Neo4j and FAISS. A graph database adds nothing to these workflows and can't run on a free host, and in-memory BM25 over about 4 transcripts per company needs no embedding model in RAM.

## Run locally

```bash
cd backend
python -m venv .venv && .venv/Scripts/activate      # Windows (macOS/Linux: source .venv/bin/activate)
pip install -r requirements.txt
cp .env.example .env                                 # add GROQ_API_KEY and GEMINI_API_KEY
uvicorn app.main:app --port 8000
```
Open http://localhost:8000 (use your browser's device toolbar for the phone view).

`python -m app.seed` pre-warms the cache and snapshots it into `backend/cache/seed/` so the deployed demo never shows an empty screen if a data source blocks the host.

## Deploy (Render, free)
`render.yaml` is a Render Blueprint. Create a New → Blueprint from this repo and paste the two API keys when asked.

## What I deliberately left out (and next steps)
- **Parsing results from the exchange PDF itself.** v1 uses screener's standardised quarterly table and links the filing. Next: parse the XBRL attached to NSE results filings (`hasXbrl` is already in the feed) for same-hour numbers.
- **True push** (web push or WhatsApp). v1 shows alerts and badges when you open the app. A cron job plus web push is the next step.
- **3 of the 8 research items:** business snapshot / segment mix, narrative-vs-numbers on the annual report, and guidance tracking across more than 4 calls.
- **Accounts:** the portfolio and thresholds live on the device.
- **Intraday data:** the brief specifies end-of-day.

## Data sources
NSE corporate announcements API and filing PDFs · Yahoo Finance via `yfinance` (EOD) · screener.in (standardised financials and transcript links). The research side uses only primary company data, with no news, broker notes or model memory.
