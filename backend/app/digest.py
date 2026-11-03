"""Push side: windowed digest, rich cards and alerts."""
import datetime as dt
import logging
from concurrent.futures import ThreadPoolExecutor

from . import llm, sources
from .classify import CATEGORIES, CATEGORY_RANK, TIER_RANK, TOP_ROLES, classify

log = logging.getLogger("finn.digest")


def _parse_time(s: str) -> dt.datetime | None:
    try:
        return dt.datetime.strptime(s, "%Y-%m-%d %H:%M:%S").replace(tzinfo=sources.IST)
    except Exception:
        return None


def collect(symbols: list[str], hours: int) -> list[dict]:
    days = max(1, -(-hours // 24))
    with ThreadPoolExecutor(max_workers=6) as ex:
        batches = list(ex.map(lambda s: _safe_ann(s, days), symbols))
    cutoff = sources.now_ist() - dt.timedelta(hours=hours)
    seen, items = set(), []
    for rows in batches:
        for r in rows:
            t = _parse_time(r["time"])
            if not t or t < cutoff or r["id"] in seen:
                continue
            seen.add(r["id"])
            items.append({**r, **classify(r["nse_category"], r["subject"])})
    return items


def _safe_ann(symbol, days):
    try:
        return sources.announcements(symbol, days)
    except Exception as e:
        log.warning("announcements %s failed: %s", symbol, e)
        return []


def _group(items: list[dict]) -> list[dict]:
    """Bury duplicates: a results day produces 3–5 filings per company (outcome, press release,
    deck, newspaper ad). Keep the primary one and fold the rest under it."""
    out, by_key = [], {}
    for it in sorted(items, key=lambda x: (TIER_RANK[x["tier"]], x["time"]), reverse=False):
        if it["category"] == "results":
            k = (it["symbol"], "results")
            if k in by_key:
                by_key[k].setdefault("related", []).append(
                    {"subject": it["subject"], "pdf": it["pdf"], "nse_category": it["nse_category"]})
                continue
            by_key[k] = it
        out.append(it)
    return out


def build(symbols: list[str], hours: int) -> dict:
    items = _group(collect(symbols, hours))
    for it in items:
        e = _cached_enrichment(it)
        it["enrichment"] = e
        if e and it["needs_llm"]:
            it["category"] = e.get("category") or it["category"]
            it["tier"] = e.get("tier") or it["tier"]
    for it in items:
        if it["subtype"] == "results_filing":
            it["results_card"] = results_card(it["symbol"])
        # The exchange category rarely names the role; the filing does. A CEO/CFO/MD/ED change is "act".
        people = ((it.get("enrichment") or {}).get("management") or {}).get("people") or []
        if it["category"] == "management_change" and any(TOP_ROLES.search(p.get("role") or "") for p in people):
            it["tier"] = "act"
    items.sort(key=lambda x: (TIER_RANK[x["tier"]], CATEGORY_RANK.get(x["category"], 99),
                              -_ts(x["time"])))
    counts = {t: sum(1 for i in items if i["tier"] == t) for t in ("act", "know", "routine")}
    return {"hours": hours, "generated_at": sources.now_ist().isoformat(), "symbols": symbols,
            "counts": counts, "items": items,
            "pending": [i["id"] for i in items if _wants_enrichment(i) and i["enrichment"] is None]}


def _ts(s):
    t = _parse_time(s)
    return t.timestamp() if t else 0


# ---------------------------------------------------------------- enrichment (LLM, cached)

ONE_LINER_SYSTEM = (
    "You triage Indian stock-exchange filings for a busy portfolio investor. For each filing, read the "
    "excerpt and return: category (one of: " + ", ".join(CATEGORIES) + "), tier ('act' = could move the "
    "stock or changes the thesis; 'know' = worth a glance; 'routine' = compliance paperwork), and "
    "one_liner: ONE sentence, max 22 words, with the concrete fact (numbers, names, amounts, dates). "
    "No hype, no advice, no 'the company informed the exchange'. If the excerpt is boilerplate, say "
    "what it actually is. Return JSON {\"items\":[{\"id\":...,\"category\":...,\"tier\":...,\"one_liner\":...}]}."
)

BIO_SYSTEM = (
    "Extract management changes from an Indian listed-company filing. Use ONLY the filing text. "
    "Regulation 30 filings usually include a 'brief profile' of an appointee — use it for the bio. "
    "Return JSON {\"people\":[{\"name\":str,\"role\":str,\"change\":\"appointed|resigned|retired|"
    "re-appointed|ceased|redesignated\",\"effective\":str|null,\"reason\":str|null,"
    "\"bio\":[up to 4 short factual bullets: prior roles/companies, education, years of experience]}],"
    "\"headline\":\"one sentence, max 20 words\"}. If the text has no profile, bio = []."
)


def _wants_enrichment(it):
    return it["tier"] in ("act", "know") or it["needs_llm"]


def _cached_enrichment(it):
    from . import cache
    return cache.get("enrich", str(it["id"]), None)


def _excerpt(text: str, n: int = 1500) -> str:
    # Skip the address block every filing starts with.
    low = text.lower()
    for marker in ("subject", "sub:", "sub :", "dear sir", "dear madam"):
        i = low.find(marker)
        if 0 <= i < 1500:
            text = text[i:]
            break
    return text[:n]


def enrich(symbols: list[str], hours: int) -> dict:
    """Run the LLM over filings that deserve it. Cheap model, batched 6 at a time; results cached
    per filing id so each filing is read exactly once, ever."""
    from . import cache
    items = _group(collect(symbols, hours))
    todo = [i for i in items if _wants_enrichment(i) and cache.get("enrich", str(i["id"]), None) is None]
    todo = todo[:40]

    def text_for(it):
        try:
            pdf = it.get("pdf") or ""
            return _excerpt(sources.pdf_text(pdf, max_pages=4)) if pdf.startswith("http") else ""
        except Exception as e:
            log.warning("pdf %s: %s", it.get("pdf"), e)
            return ""

    with ThreadPoolExecutor(max_workers=6) as ex:
        texts = list(ex.map(text_for, todo))
    for it, tx in zip(todo, texts):
        it["_text"] = tx

    results = {}
    for b in range(0, len(todo), 6):
        batch = todo[b:b + 6]
        prompt = "\n\n".join(
            f"### id={it['id']} | {it['symbol']} | exchange category: {it['nse_category']}\n"
            f"subject: {it['subject'][:300]}\nexcerpt: {it['_text'][:1100] or '(no text)'}"
            for it in batch)
        try:
            out = llm.complete_json(ONE_LINER_SYSTEM, prompt, tier="cheap", max_tokens=900)
            for row in out.get("items", []):
                results[str(row.get("id"))] = row
        except Exception as e:
            log.warning("one-liner batch failed: %s", e)

    for it in todo:
        row = results.get(str(it["id"]), {})
        e = {"one_liner": row.get("one_liner")}
        if it["needs_llm"] and row.get("category") in CATEGORIES:
            e["category"] = row["category"]
            e["tier"] = row.get("tier") if row.get("tier") in TIER_RANK else None
        cat = e.get("category") or it["category"]
        if cat == "management_change" and it["_text"]:
            try:
                full = sources.pdf_text(it["pdf"], max_pages=6)
                e["management"] = llm.complete_json(BIO_SYSTEM, _excerpt(full, 6000), tier="cheap",
                                                     max_tokens=900)
            except Exception as ex:
                log.warning("bio %s: %s", it["id"], ex)
        if e.get("one_liner") or e.get("management"):
            cache.put("enrich", str(it["id"]), e)
        results[str(it["id"])] = e
    return {"enriched": {k: v for k, v in results.items() if isinstance(v, dict) and "one_liner" in v}}


# ---------------------------------------------------------------- results card

def results_card(symbol: str) -> dict | None:
    try:
        c = sources.company(symbol)
    except Exception as e:
        log.warning("screener %s: %s", symbol, e)
        return None
    q = c["quarters"]
    cols = q["cols"]
    if len(cols) < 2:
        return None

    def metric(name):
        r = sources.row(q, name)
        if not r:
            return None
        cur = r[-1]
        prev = r[-2] if len(r) >= 2 else None
        yago = r[-5] if len(r) >= 5 else None

        def pct(a, b):
            if a is None or b in (None, 0):
                return None
            return round((a - b) / abs(b) * 100, 1)
        return {"value": cur, "qoq": pct(cur, prev), "yoy": pct(cur, yago), "spark": r[-8:]}

    return {
        "symbol": symbol, "name": c["name"], "quarter": cols[-1], "quarter_fy": sources.fy_label(cols[-1]),
        "prev_quarter": cols[-2], "year_ago_quarter": cols[-5] if len(cols) >= 5 else None,
        "revenue": metric("revenue"), "ebitda": metric("ebitda"), "pbt": metric("pbt"), "pat": metric("pat"),
        "margin": (sources.row(q, "margin") or [None])[-1],
        "margin_prev_year": (sources.row(q, "margin") or [None] * 5)[-5] if len(cols) >= 5 else None,
        "unit": "₹ Cr", "source": c["source"] + "#quarters",
    }


# ---------------------------------------------------------------- alerts

def alerts(symbols: list[str], price_pct: float, vol_x: float, categories: list[str]) -> dict:
    out = []
    try:
        px = sources.eod(symbols)
    except Exception as e:
        log.warning("eod failed: %s", e)
        px = {}
    for s, d in px.items():
        if abs(d["pct"]) >= price_pct:
            out.append({"type": "price", "symbol": s, "severity": "act" if abs(d["pct"]) >= 2 * price_pct else "know",
                        "headline": f"{s} {'+' if d['pct'] > 0 else ''}{d['pct']}% today",
                        "detail": f"Closed ₹{d['close']:,} vs ₹{d['prev_close']:,} (threshold ±{price_pct}%)",
                        "date": d["date"], "spark": d["spark"], "pct": d["pct"]})
        if d.get("vol_ratio") and d["vol_ratio"] >= vol_x:
            out.append({"type": "volume", "symbol": s, "severity": "act" if d["vol_ratio"] >= 2 * vol_x else "know",
                        "headline": f"{s} volume {d['vol_ratio']}× its 20-day average",
                        "detail": f"{d['volume']:,} shares vs avg {d['avg20_volume']:,} (threshold {vol_x}×); "
                                  f"price {'+' if d['pct'] > 0 else ''}{d['pct']}%",
                        "date": d["date"], "spark": d["spark"], "pct": d["pct"]})
    filings = [i for i in collect(symbols, 24) if i["category"] in categories and i["tier"] == "act"]
    for f in filings:
        enr = _cached_enrichment(f) or {}
        out.append({"type": "filing", "symbol": f["symbol"], "severity": "act",
                    "headline": f"{f['symbol']}: {f['category'].replace('_', ' ')}",
                    "detail": enr.get("one_liner") or f["subject"][:180], "date": f["time"],
                    "pdf": f["pdf"], "id": f["id"]})
    out.sort(key=lambda a: (0 if a["severity"] == "act" else 1, a["symbol"]))
    return {"alerts": out, "prices": px,
            "thresholds": {"price_pct": price_pct, "vol_x": vol_x, "categories": categories}}
