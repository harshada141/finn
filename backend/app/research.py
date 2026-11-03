"""Pull side: on-demand research for any NSE ticker, grounded only in primary data.

numbers()   -> snapshot, trajectory, balance sheet, cash quality. Pure arithmetic, no LLM.
narrative() -> promised-vs-delivered and bull/bear from the last 4 concall transcripts.
               cheap model extracts guidance per transcript (map), strong model judges it
               against reported numbers (reduce). Every claim must cite a retrieved chunk or a
               reported number; uncited claims are dropped by the validator.
"""
import logging
import re
from concurrent.futures import ThreadPoolExecutor

from rank_bm25 import BM25Okapi

from . import cache, llm, sources
from .digest import results_card

log = logging.getLogger("finn.research")


def _cagr(a, b, years):
    if a is None or b is None or a <= 0 or b <= 0 or years <= 0:
        return None
    return round(((b / a) ** (1 / years) - 1) * 100, 1)


def _annual_cols(table):
    """Annual tables end with a TTM column; drop it for multi-year comparisons."""
    cols = table["cols"] if table else []
    keep = [i for i, c in enumerate(cols) if c.upper() != "TTM"]
    return [cols[i] for i in keep], keep


def numbers(symbol: str) -> dict:
    c = sources.company(symbol)
    a = c["annual"]
    cols, keep = _annual_cols(a)

    def series(table, metric, idx=None):
        r = sources.row(table, metric)
        if idx is not None:
            r = [r[i] for i in idx if i < len(r)]
        return r

    rev = series(a, "revenue", keep)
    pat = series(a, "pat", keep)
    opm = series(a, "margin", keep)
    n = len(rev)

    traj = {"years": cols[-10:], "revenue": rev[-10:], "pat": pat[-10:], "margin": opm[-10:]}
    for yrs in (3, 5):
        if n > yrs:
            traj[f"revenue_cagr_{yrs}y"] = _cagr(rev[-1 - yrs], rev[-1], yrs)
            traj[f"pat_cagr_{yrs}y"] = _cagr(pat[-1 - yrs], pat[-1], yrs)
    signals = []
    if traj.get("revenue_cagr_5y") is not None:
        signals.append(("up" if traj["revenue_cagr_5y"] > 8 else "flat" if traj["revenue_cagr_5y"] > 0 else "down",
                        f"Revenue {traj['revenue_cagr_5y']:+.1f}% CAGR over 5 years"))
    if traj.get("pat_cagr_5y") is not None:
        signals.append(("up" if traj["pat_cagr_5y"] > 8 else "flat" if traj["pat_cagr_5y"] > 0 else "down",
                        f"Profit {traj['pat_cagr_5y']:+.1f}% CAGR over 5 years"))
    m = [x for x in opm[-6:] if x is not None]
    if len(m) >= 3:
        d = m[-1] - m[0]
        signals.append(("up" if d >= 2 else "down" if d <= -2 else "flat",
                        f"Operating margin {'expanded' if d >= 2 else 'compressed' if d <= -2 else 'stable'}: "
                        f"{m[0]:.0f}% → {m[-1]:.0f}% over {len(m) - 1} years"))
    traj["signals"] = [{"dir": d, "text": t} for d, t in signals]

    # Balance sheet
    b = c["balance"]
    bal = {}
    if b:
        eq = sources.row(b, "Equity Capital")
        res = sources.row(b, "Reserves")
        debt = sources.row(b, "Borrowings")
        if eq and res and debt and eq[-1] is not None and res[-1] is not None:
            networth = (eq[-1] or 0) + (res[-1] or 0)
            bal = {"as_of": b["cols"][-1], "borrowings": debt[-1], "net_worth": networth,
                   "debt_to_equity": round(debt[-1] / networth, 2) if networth and debt[-1] is not None else None,
                   "debt_series": debt[-6:], "cols": b["cols"][-6:]}
    rt = c.get("ratio_table")
    if rt:
        for k, label in (("ccc", "Cash Conversion Cycle"), ("wc_days", "Working Capital Days"),
                         ("roce", "ROCE %"), ("roe", "ROE %")):
            r = sources.row(rt, label)
            if r:
                bal[k] = r[-6:]
        bal["ratio_cols"] = rt["cols"][-6:]

    # Cash quality: does profit turn into cash?
    cf = c["cashflow"]
    cash = {}
    if cf:
        cfo = sources.row(cf, "Cash from Operating Activity")
        fcf = sources.row(cf, "Free Cash Flow")
        ccols = cf["cols"]
        pat_by_year = dict(zip(cols, pat))
        yrs = ccols[-5:]
        cfo5 = cfo[-5:]
        pat5 = [pat_by_year.get(y) for y in yrs]
        pairs = [(x, y) for x, y in zip(cfo5, pat5) if x is not None and y]
        conv = round(sum(x for x, _ in pairs) / sum(y for _, y in pairs) * 100) if pairs and sum(
            y for _, y in pairs) > 0 else None
        cash = {"years": yrs, "cfo": cfo5, "pat": pat5, "fcf": fcf[-5:] if fcf else [],
                "cfo_to_pat_5y": conv,
                "verdict": None if conv is None else (
                    "Strong — profits are converting to cash" if conv >= 90 else
                    "Moderate — some profit is tied up (receivables/inventory)" if conv >= 60 else
                    "Weak — reported profit is not turning into cash; dig into working capital")}

    return {
        "symbol": c["symbol"], "name": c["name"], "about": c["about"], "ratios": c["ratios"],
        "latest_quarter": results_card(symbol),
        "trajectory": traj, "balance_sheet": bal, "cash_quality": cash,
        "documents": {"concalls": c["concalls"][:4], "annual_reports": c["annual_reports"][:3]},
        "source": c["source"],
    }


# ---------------------------------------------------------------- narrative (LLM)

GUIDANCE_QUERY = ("guidance expect target outlook going forward next year fy margin ebitda growth "
                  "revenue capex volume double digit percent aspire plan by end of guide visibility "
                  "will achieve should be range sustain")

EXTRACT_SYSTEM = (
    "You read an earnings-call transcript excerpt from an Indian listed company. Extract forward-looking "
    "statements by MANAGEMENT that contain a number, a range or a date: growth targets, margin bands, capex, "
    "capacity, store/plant counts, debt, launch timelines, aspirations ('we aim for 16-17%'). Put operating "
    "outcomes (revenue/volume growth, EBITDA or operating margin, profit) FIRST, then everything else. "
    "Ignore analyst questions, past results and vague optimism without numbers. Each item must quote "
    "management verbatim (max 30 words) and name the chunk id it came from. "
    "Return JSON {\"guidance\":[{\"metric\":\"revenue growth|EBITDA margin|capex|...\","
    "\"statement\":\"plain-English paraphrase, max 18 words\",\"target\":\"e.g. 15-17% / ₹5,000 Cr / "
    "by FY27\",\"timeframe\":\"e.g. FY27, next 2 quarters\",\"quote\":\"verbatim\",\"chunk\":\"T1-3\"}]} "
    "with at most 5 items, most material first. Return {\"guidance\":[]} if none."
)

JUDGE_SYSTEM = (
    "You are a buy-side analyst checking management credibility for an Indian listed company. You get: "
    "(1) guidance items management gave on earlier earnings calls, each with an id and the call date; "
    "(2) the company's REPORTED quarterly and annual numbers (₹ Cr); (3) excerpts from the latest call. "
    "Use ONLY this data — no outside knowledge, no news. Tasks:\n"
    "A) For each guidance id, give verdict: delivered | partial | missed | pending (period not over yet) | "
    "unverifiable (the numbers given can't test it). evidence: one sentence quoting the actual numbers "
    "(e.g. 'Q2 FY27 OPM 17% vs 18-19% guided'). cites: list of ids used, e.g. [\"G3\",\"Q:Sep 2026\"].\n"
    "B) credibility: one sentence overall read on promise vs delivery.\n"
    "C) bull and bear: 3 points each, max 22 words each, each grounded in the data with cites "
    "(chunk ids like T1-4, or Q:<quarter>, or A:<year> for reported numbers).\n"
    "Return JSON {\"verdicts\":[{\"id\":\"G1\",\"verdict\":...,\"evidence\":...,\"cites\":[...]}],"
    "\"credibility\":str,\"bull\":[{\"point\":str,\"cites\":[...]}],\"bear\":[{\"point\":str,\"cites\":[...]}]}"
)


def _chunks(text: str, tid: str, size: int = 1200, overlap: int = 150) -> list[dict]:
    text = re.sub(r"\s+", " ", text)
    out, i, n = [], 0, 0
    while i < len(text):
        out.append({"id": f"{tid}-{n}", "text": text[i:i + size]})
        i += size - overlap
        n += 1
    return out


def _top(chunks, query, k):
    if not chunks:
        return []
    tok = lambda s: re.findall(r"[a-z0-9%]+", s.lower())
    bm = BM25Okapi([tok(c["text"]) for c in chunks])
    scores = bm.get_scores(tok(query))
    # skip the first ~2 chunks of a transcript (operator intro, safe-harbour boilerplate)
    ranked = sorted(range(len(chunks)), key=lambda i: scores[i] * (0.3 if i < 2 else 1), reverse=True)
    return [chunks[i] for i in sorted(ranked[:k])]


def _norm(s):
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def _quote_ok(quote, chunk_text):
    """Citation validator: the quote must actually appear in the cited chunk (fuzzy)."""
    q, c = _norm(quote), _norm(chunk_text)
    if not q:
        return False
    if q[:60] in c:
        return True
    qw = q.split()
    return len(qw) >= 4 and sum(w in c.split() for w in qw) / len(qw) >= 0.75


def _transcripts_from_exchange(symbol: str) -> list[dict]:
    """Fallback when screener has no transcript links: find them in the company's own NSE filings."""
    try:
        rows = sources.announcements(symbol, 400)
    except Exception as e:
        log.warning("exchange transcripts %s: %s", symbol, e)
        return []
    out, seen_months = [], set()
    for r in sorted(rows, key=lambda x: x["time"], reverse=True):
        if "transcript" in r["subject"].lower() and (r.get("pdf") or "").startswith("http"):
            month = r["time"][:7]
            if month in seen_months:
                continue
            seen_months.add(month)
            import datetime as dt
            label = dt.datetime.strptime(month, "%Y-%m").strftime("%b %Y")
            out.append({"date": label, "url": r["pdf"]})
        if len(out) == 4:
            break
    return out


def narrative(symbol: str) -> dict:
    """Cached for a day — but only complete runs. If any model call failed (free-tier rate limits),
    the result is returned to the user marked partial and recomputed next time."""
    symbol = symbol.upper()
    hit = cache.get("narrative", symbol, 24 * 3600)
    if hit is not None:
        return hit
    try:
        out = _narrative(symbol)
    except Exception:
        stale = cache.get_stale("narrative", symbol)
        if stale is not None:
            return stale
        raise
    if out.get("partial"):
        stale = cache.get_stale("narrative", symbol)
        if stale is not None and len(stale.get("guidance", [])) >= len(out.get("guidance", [])):
            return stale
    else:
        cache.put("narrative", symbol, out)
    return out


def _narrative(symbol: str) -> dict:
    c = sources.company(symbol)
    calls = c["concalls"][:4]
    if len(calls) < 2:
        calls = _transcripts_from_exchange(symbol) or calls
    if not calls:
        return {"available": False, "reason": "No concall transcripts found on screener.in or in NSE filings."}

    def load(ix_call):
        ix, call = ix_call
        tid = f"T{ix + 1}"
        try:
            txt = sources.pdf_text(call["url"], max_pages=40)
        except Exception as e:
            log.warning("transcript %s: %s", call["url"], e)
            return None
        if len(txt) < 2000:
            return None
        return {"tid": tid, "date": call["date"], "url": call["url"], "chunks": _chunks(txt, tid)}

    with ThreadPoolExecutor(max_workers=4) as ex:
        transcripts = [t for t in ex.map(load, enumerate(calls)) if t]
    if not transcripts:
        return {"available": False, "reason": "Transcripts could not be downloaded right now."}

    chunk_index = {ch["id"]: {**ch, "date": t["date"], "url": t["url"]} for t in transcripts for ch in t["chunks"]}

    # MAP: cheap model extracts guidance per transcript, from the BM25 top chunks only.
    guidance, failures = [], 0
    for t in transcripts:
        top = _top(t["chunks"], GUIDANCE_QUERY, 6)
        prompt = (f"Company: {c['name']} ({symbol}). Call date: {t['date']}.\n\n" +
                  "\n\n".join(f"[{ch['id']}] {ch['text']}" for ch in top))
        try:
            out = llm.complete_json(EXTRACT_SYSTEM, prompt, tier="cheap", max_tokens=1200)
        except Exception as e:
            log.warning("extract %s: %s", t["tid"], e)
            failures += 1
            continue
        for g in out.get("guidance", [])[:5]:
            ch = chunk_index.get(str(g.get("chunk", "")).strip())
            if not ch or not _quote_ok(g.get("quote"), ch["text"]):
                # try to locate the quote in any of the retrieved chunks before dropping it
                ch = next((x for x in top if _quote_ok(g.get("quote"), x["text"])), None)
                if not ch:
                    continue
                g["chunk"] = ch["id"]
            g["call_date"] = t["date"]
            g["source_url"] = chunk_index[g["chunk"]]["url"]
            guidance.append(g)
    for i, g in enumerate(guidance):
        g["id"] = f"G{i + 1}"

    # REDUCE: strong model judges promises against reported numbers, once per company.
    q = c["quarters"]
    qcols = q["cols"][-8:]
    lines = ["QUARTERLY (₹ Cr): " + " | ".join(f"{x} ({sources.fy_label(x)})" for x in qcols)]
    for mname in ("revenue", "ebitda", "margin", "pat"):
        r = sources.row(q, mname)[-8:]
        if r:
            lines.append(f"{mname}: " + " | ".join("-" if v is None else f"{v:g}" for v in r))
    a = c["annual"]
    acols, keep = _annual_cols(a)
    lines.append("ANNUAL (₹ Cr): " + " | ".join(f"{x} ({sources.fy_label(x, annual=True)})" for x in acols[-5:]))
    for mname in ("revenue", "margin", "pat"):
        r = [sources.row(a, mname)[i] for i in keep if i < len(sources.row(a, mname))][-5:]
        if r:
            lines.append(f"{mname}: " + " | ".join("-" if v is None else f"{v:g}" for v in r))
    latest = transcripts[0]
    latest_top = _top(latest["chunks"], "risk challenge headwind pressure demand outlook competition "
                                        "margin guidance growth opportunity", 4)
    prompt = (f"Company: {c['name']} ({symbol})\n\nGUIDANCE GIVEN:\n" +
              ("\n".join(f"{g['id']} [{g['call_date']} call] {g.get('metric')}: {g.get('statement')} "
                         f"(target: {g.get('target')}, timeframe: {g.get('timeframe')})" for g in guidance)
               or "(none extracted)") +
              "\n\nREPORTED NUMBERS:\n" + "\n".join(lines) +
              f"\n\nLATEST CALL EXCERPTS ({latest['date']}):\n" +
              "\n\n".join(f"[{ch['id']}] {ch['text']}" for ch in latest_top))
    try:
        judged = llm.complete_json(JUDGE_SYSTEM, prompt, tier="strong", max_tokens=1800)
    except Exception as e:
        log.warning("judge %s: %s", symbol, e)
        judged = {}
        failures += 1

    valid_ids = set(chunk_index) | {g["id"] for g in guidance} | {f"Q:{x}" for x in q["cols"]} | {
        f"A:{x}" for x in acols}

    def clean_cites(cites):
        return [x for x in (cites or []) if isinstance(x, str) and x in valid_ids]

    verdicts = {v.get("id"): v for v in judged.get("verdicts", []) if isinstance(v, dict)}
    for g in guidance:
        v = verdicts.get(g["id"], {})
        g["verdict"] = v.get("verdict", "unverifiable")
        g["evidence"] = v.get("evidence")
        g["cites"] = clean_cites(v.get("cites"))

    def points(lst):
        out = []
        for p in lst or []:
            if not isinstance(p, dict) or not p.get("point"):
                continue
            cites = clean_cites(p.get("cites"))
            if cites:  # no receipt, no claim
                out.append({"point": p["point"], "cites": cites})
        return out[:3]

    tally = {k: sum(1 for g in guidance if g["verdict"] == k)
             for k in ("delivered", "partial", "missed", "pending", "unverifiable")}
    return {
        "available": True, "partial": failures > 0,
        "transcripts": [{"tid": t["tid"], "date": t["date"], "url": t["url"]} for t in transcripts],
        "guidance": guidance, "tally": tally,
        "credibility": judged.get("credibility"),
        "bull": points(judged.get("bull")), "bear": points(judged.get("bear")),
        "chunks": {cid: {"text": chunk_index[cid]["text"], "date": chunk_index[cid]["date"],
                         "url": chunk_index[cid]["url"]}
                   for cid in {x for g in guidance for x in [g["chunk"]] + g["cites"]} |
                   {x for p in (judged.get("bull") or []) + (judged.get("bear") or []) if isinstance(p, dict)
                    for x in clean_cites(p.get("cites"))}
                   if cid in chunk_index},
    }
