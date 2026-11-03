# FINN — Financial Investment Agent
### Product Requirements Document (v1, submission build)

**Author:** Harshada Rajhans · **Date:** 3 Nov 2026 · **Status:** Built — see "As built" notes below

---

## 1. Problem

An investor tracking 15–20 stocks gets 30–80 exchange filings a week, plus price moves, results and concalls. Most of it is routine paperwork (ESOP allotments, compliance certificates, newspaper ad copies). Most investors don't lack information. They lack **triage**: someone to say *these 3 things matter today, and here's why*.

## 2. Product thesis (our point of view)

> **FINN is an inbox you can finish.**

Most finance apps are dashboards: you go looking. FINN works like a triaged inbox. Every morning it hands you a short, ranked stack of cards. You clear it in about 2 minutes, and anything that sparks a question opens a grounded research view **in context** ("Ask FINN about this").

Three design principles:
1. **Earn the attention.** Each item is placed in one of three tiers: 🔴 *Act / read now*, 🟡 *Worth knowing*, ⚪ *Routine*. The Routine tier is collapsed by default. The tier comes from rules first, and an LLM only resolves the ambiguous cases.
2. **Show the number, not the adjective.** A results card shows Revenue / EBITDA / PBT / PAT with QoQ and YoY deltas. A management-change card shows who the person is. An alert says "Volume 3.4× 20-day avg", not "unusual activity".
3. **Every claim has a receipt.** On the pull side, every sentence links back to the annual report or concall chunk it came from. If the system can't cite something, it doesn't say it.

## 3. Users & core jobs

| Job | When | FINN surface |
|---|---|---|
| "What happened in my portfolio since I last looked?" | Morning, 2 min | **Brief** (windowed digest) |
| "Tell me the moment something unusual happens" | Through the day | **Alerts** (configurable thresholds) |
| "Is this company actually delivering what it promised?" | Ad hoc | **Research** (pull, any NSE ticker) |

## 4. Scope — what we build (thin but working)

### 4.1 PUSH — Brief (windowed digest)
- Window selector: **24h · 3d · 7d · 30d** (24h by default).
- Pulls every corporate announcement for the portfolio from the exchange feed.
- **Classifier** maps each filing to the 17 reference categories (results, M&A, management_change, fund_raise, litigation, pledging, …, unclassified).
  - Step 1: keyword/regex rules on the subject line, at zero cost. These handle most filings.
  - Step 2: only ambiguous filings go to a **small, cheap model** (Groq Llama-3.1-8B).
- **Priority = category weight × company context** (for example, results ranks higher in results season and pledging ranks higher for small caps). This produces the 3 tiers.
- 🔴/🟡 items get a **one-line "why it matters" summary**. ⚪ items are listed as plain filing links, collapsed.

### 4.2 PUSH — Rich cards (the two examples ENAM called out)
- **Quarterly results card:** Revenue, EBITDA, PBT, PAT for this quarter, with QoQ and YoY % change and margin, plus a sparkline of the last 8 quarters and a link to the actual filing.
  *Source:* the standardised quarterly table on screener.in, cross-linked to the exchange filing. (Parsing the results PDF itself is in §8 as next step.)
- **Management change card:** who joined or left, their role, and a short **profile**. SEBI's Reg 30 rules require appointment filings to include a *"brief profile"* of the appointee, so FINN extracts the bio **from the filing itself**. That makes it primary-sourced, with no LinkedIn scraping.

### 4.3 PUSH — Alerts
- End-of-day price and volume from **yfinance** (`.NS`).
- User-configurable rules, stored per device:
  - Price move beyond **±X%** in a day (default 5%)
  - Volume above **N×** its 20-day average (default 2×)
  - A material filing in categories I choose (default: results, M&A, management_change, fund_raise, pledging, litigation, credit_rating)
- Fired alerts appear as cards at the top of the Brief and get a badge on the tab.

### 4.4 PULL — Research (works on *any* NSE ticker)
Enter any ticker. FINN builds a research view **only from primary data** (financials, annual reports, concall transcripts linked on screener.in). From the 8-item menu, we build these 5:

| Section | How | Model cost |
|---|---|---|
| **Financial snapshot** | Computed directly from tables (revenue, margins, PAT, ROE/ROCE, D/E) | **No LLM** |
| **Trajectory** | 5-yr CAGR, margin trend, and what's growing vs. compressing, computed in code | **No LLM** |
| **Cash quality** | Cash from operations vs. PAT, plus FCF, computed in code | **No LLM** |
| **Promised vs. Delivered** ⭐ | From the last 4 concall transcripts: extract guidance statements (cheap model), match each to the actual reported numbers, give a verdict (✅ delivered / ⚠️ partial / ❌ missed), cited | Cheap model to extract, strong model to judge |
| **Bull vs. Bear** | 3 points each, every point cited to a source chunk | Strong model, once, cached |

Every LLM-generated sentence carries a citation. A **citation validator** (reused from my Enterprise RAG project) drops any claim whose cited chunk wasn't actually retrieved.

### 4.5 Interaction model
- Mobile-first PWA with 3 tabs: **Brief · Alerts · Research**, plus a **Settings** sheet (portfolio and thresholds).
- Card-stack UI: tap to expand, swipe or tap ✓ to mark seen, and a progress ring that shows "you're done for today".
- **"Ask FINN about this"** on any card opens Research for that company, scrolled to the relevant section. This connects push to pull.
- It can be installed to the home screen and works one-handed.

## 5. Portfolio & demo set

**Portfolio (16 stocks, chosen to cover sectors and sizes):**
RELIANCE, HDFCBANK, TCS, INFY, ITC, BHARTIARTL, LT, SUNPHARMA, TMPV (Tata Motors PV), ASIANPAINT, BAJFINANCE, ADANIPORTS, DIXON, POLYCAB, ETERNAL (Zomato), IRCTC

**Ad-hoc research demo (outside the portfolio):** PERSISTENT, KAYNES, CDSL, JUBLFOOD (TRENT dropped: it holds no earnings calls, which FINN now reports gracefully)

(The final list may change slightly depending on which tickers have concall transcripts available.)

## 6. Architecture

```
            ┌──────────────── Mobile PWA (vanilla JS + SVG, no build step) ────────────────┐
            │   Brief   ·   Alerts   ·   Research   ·   Settings (localStorage)    │
            └──────────────────────────────┬────────────────────────────────────────┘
                                           │ JSON
            ┌──────────────────────────────▼────────────────────────────────────────┐
            │                     FastAPI (single service)                           │
            │  /digest  /alerts  /research/{ticker}  /portfolio                       │
            ├───────────────┬────────────────┬──────────────────┬────────────────────┤
            │ Announcements │  Price/Volume  │  Fundamentals    │  LLM Router        │
            │ BSE/NSE feed  │  yfinance      │  screener.in     │  rules → 8B → 70B  │
            │ + classifier  │  + rule engine │  + AR/concall    │  + disk cache      │
            │               │                │  chunk + BM25    │  + cite validator  │
            └───────────────┴────────────────┴──────────────────┴────────────────────┘
                         SQLite / JSON cache (every external call cached by key+date)
```

**Reused from my Enterprise RAG Financial Assistant:** the chunking pipeline, the retrieval + citation-validator pattern, the LLM-with-fallback client (Groq → Gemini), and the structured-JSON extraction with tolerant parsing.

**Intentionally dropped:** Neo4j and FAISS. A graph database adds nothing to these two workflows, and it can't run on a free host. Each research run covers about 4 transcripts and 1 annual report per company, so in-memory BM25 is enough and needs no embedding model in RAM.

### Model routing (cost discipline, which ENAM gives bonus points for)
| Task | Model | Why |
|---|---|---|
| Announcement category | **Rules (no model)** → Llama-3.1-8B only if no rule matches | Most filings have formulaic subjects |
| One-line "why it matters" | Llama-3.1-8B | Short, low-stakes |
| Bio extraction from filing | Llama-3.1-8B, JSON mode | Simple extraction |
| Guidance extraction per transcript chunk | Llama-3.1-8B | Bulk, parallel |
| Promised-vs-delivered verdict, Bull/Bear | gpt-oss-120b / Llama-3.3-70B | Real reasoning, ~2 calls per company |
| Snapshot, trajectory, cash quality | **No LLM** | It's arithmetic |

All LLM outputs are cached by `(ticker, doc hash, task)`, so a repeat view costs nothing. Everything runs on free tiers.

## 7. Deliverables (submission checklist)
1. **Live mobile URL**: FastAPI and the built frontend in one service on Render (free tier).
2. **GitHub repo**: `finn` (public), with a README covering setup, architecture and decisions.
3. **AI build history**: this Claude Code session exported as a transcript, plus a link to the earlier RAG project's history if available.
4. **Covering email**: a short writeup covering what we built, key decisions and trade-offs, what we left out, and next steps. Sent to assessment@enam.com with subject **"ENAM - IIT screening - 2027"**.

## 8. Deliberately out of scope (named in the writeup)
- Parsing results numbers **directly from the exchange PDF** (v1 uses screener's standardised table and links the filing).
- Real push notifications via service worker or WhatsApp (v1 shows in-app alerts on open).
- Intraday data (the brief specifies end-of-day).
- Balance sheet deep-dive, narrative-vs-numbers on the annual report, and business snapshot (3 of the 8 menu items).
- User accounts (portfolio and thresholds live in localStorage).
- Voice interface.

## 9. Risks & mitigations
| Risk | Mitigation |
|---|---|
| NSE blocks requests from cloud servers | NSE API is primary (BSE's API rejected scripted requests in testing). A **snapshot cache** committed to the repo means the demo always renders. |
| screener.in rate limits | Cache aggressively, add a polite delay, and pre-warm the demo tickers |
| Free LLM tier rate limits | Rules-first classification, caching, and Groq → Gemini fallback |
| Render free tier sleeps (cold start ~30–50s) | A loading screen that says so, and a pre-warm ping before sharing the link |
| Time (single day) | Build order follows priority. Brief and Results card ship first, then Research, then Alerts settings, then polish. |

## 10. Success criteria for this submission
- The link opens on a phone and the Brief renders real filings in under 3s (when warm).
- At least one results card and one management-change card show real data.
- Research works on a ticker **not** in the demo set (live test).
- Promised-vs-Delivered shows at least 3 guidance items with verdicts and working citations.
- The repo README and email clearly state the trade-offs.

---

## As built: deviations from this plan
- **Frontend:** vanilla JS + hand-drawn SVG charts instead of React/Vite. No build step means one Python service deploys everything, which removes a whole class of deploy failures.
- **Models:** Groq's current free models are `gpt-oss-20b` (cheap) and `gpt-oss-120b` (strong); Llama 3.1 8B is no longer offered. Gemini Flash is the fallback.
- **Announcement feed:** NSE's corporate-announcements API is primary. BSE's API rejected scripted requests.
- **Rules share:** measured at 69% of filings (not the 80%+ assumed), because "Outcome of Board Meeting" and "General Updates" are too generic to classify by rule.
- **Transcript fallback:** if screener.in has no transcript links, FINN searches the company's own NSE filings.
- **Management tier upgrade:** the role only appears inside the filing, so CEO/CFO/MD/ED changes are promoted to *Act now* after the bio extraction.
