"""Data plumbing: NSE announcements, filing PDFs, EOD prices (yfinance), fundamentals (screener.in)."""
import datetime as dt
import io
import logging
import re
import threading

import httpx
from bs4 import BeautifulSoup

from . import cache
from .config import USER_AGENT

log = logging.getLogger("finn.sources")
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))


def now_ist() -> dt.datetime:
    return dt.datetime.now(IST)


# ---------------------------------------------------------------- NSE announcements

class _NSE:
    """NSE's JSON API needs cookies from a homepage visit first."""

    def __init__(self):
        self._client = None
        self._lock = threading.Lock()

    def _session(self, fresh=False) -> httpx.Client:
        with self._lock:
            if self._client is None or fresh:
                c = httpx.Client(headers={"User-Agent": USER_AGENT, "Accept": "application/json,*/*",
                                          "Accept-Language": "en-US,en;q=0.9",
                                          "Referer": "https://www.nseindia.com/"},
                                 timeout=30, follow_redirects=True)
                c.get("https://www.nseindia.com/")
                self._client = c
            return self._client

    def get(self, url: str) -> httpx.Response:
        r = self._session().get(url)
        if r.status_code in (401, 403):
            r = self._session(fresh=True).get(url)
        r.raise_for_status()
        return r


nse = _NSE()


def announcements(symbol: str, days: int) -> list[dict]:
    to = now_ist().date()
    frm = to - dt.timedelta(days=days)
    url = (f"https://www.nseindia.com/api/corporate-announcements?index=equities&symbol={symbol}"
           f"&from_date={frm:%d-%m-%Y}&to_date={to:%d-%m-%Y}")

    def fetch():
        rows = nse.get(url).json()
        return [{
            "id": r.get("seq_id"),
            "symbol": r.get("symbol"),
            "company": r.get("sm_name"),
            "nse_category": r.get("desc") or "",
            "subject": (r.get("attchmntText") or "").strip(),
            "pdf": r.get("attchmntFile"),
            "time": r.get("sort_date"),  # 'YYYY-MM-DD HH:MM:SS' IST
        } for r in rows if isinstance(r, dict)]

    # The exchange feed is the "push" source: short TTL. Keyed per day so the cache key is stable.
    return cache.cached("ann", f"{symbol}|{days}|{to}", 15 * 60, fetch)


# ---------------------------------------------------------------- PDFs (filings, transcripts)

def pdf_text(url: str, max_pages: int = 40, max_chars: int = 120_000) -> str:
    def fetch():
        from pypdf import PdfReader
        if "nseindia.com" in url:
            content = nse.get(url).content
        else:
            ref = "https://www.bseindia.com/" if "bseindia" in url else url
            r = httpx.get(url, headers={"User-Agent": USER_AGENT, "Referer": ref}, timeout=60,
                          follow_redirects=True)
            r.raise_for_status()
            content = r.content
        reader = PdfReader(io.BytesIO(content))
        parts = []
        for page in reader.pages[:max_pages]:
            try:
                parts.append(page.extract_text() or "")
            except Exception:
                continue
            if sum(map(len, parts)) > max_chars:
                break
        text = "\n".join(parts)
        text = re.sub(r"[ \t ]+", " ", text)
        return re.sub(r"\n\s*\n+", "\n", text).strip()[:max_chars]

    # Filings never change once published: cache forever.
    return cache.cached("pdf", f"{url}|{max_pages}", None, fetch)


# ---------------------------------------------------------------- EOD prices

def eod(symbols: list[str]) -> dict:
    key = ",".join(sorted(symbols)) + f"|{now_ist().date()}"

    def fetch():
        import yfinance as yf
        tickers = [f"{s}.NS" for s in symbols]
        df = yf.download(tickers, period="3mo", interval="1d", group_by="ticker",
                         auto_adjust=False, progress=False, threads=True)
        out = {}
        for s, t in zip(symbols, tickers):
            try:
                sub = df[t] if len(tickers) > 1 else df
                sub = sub.dropna(subset=["Close"])
                if len(sub) < 3:
                    continue
                closes = sub["Close"].tolist()
                vols = sub["Volume"].tolist()
                hist_vol = vols[-21:-1] or vols[:-1]
                avg20 = sum(hist_vol) / max(len(hist_vol), 1)
                out[s] = {
                    "date": sub.index[-1].strftime("%Y-%m-%d"),
                    "close": round(closes[-1], 2),
                    "prev_close": round(closes[-2], 2),
                    "pct": round((closes[-1] / closes[-2] - 1) * 100, 2),
                    "volume": int(vols[-1]),
                    "avg20_volume": int(avg20),
                    "vol_ratio": round(vols[-1] / avg20, 2) if avg20 else None,
                    "spark": [round(c, 2) for c in closes[-30:]],
                }
            except Exception as e:
                log.warning("eod parse %s: %s", s, e)
        if not out:
            raise RuntimeError("no price data")
        return out

    return cache.cached("eod", key, 30 * 60, fetch)


# ---------------------------------------------------------------- screener.in fundamentals

def _num(s: str):
    s = (s or "").replace(",", "").replace("%", "").replace("₹", "").strip()
    if s in ("", "-", "--"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _label(td) -> str:
    btn = td.find("button")
    txt = btn.get_text(" ", strip=True) if btn else td.get_text(" ", strip=True)
    return re.sub(r"\s*\+$", "", txt).strip()


def _table(soup, section: str) -> dict | None:
    t = soup.select_one(f"#{section} table")
    if not t:
        return None
    cols = [th.get_text(strip=True) for th in t.select("thead th")][1:]
    rows = {}
    for tr in t.select("tbody tr"):
        tds = tr.select("td")
        if not tds:
            continue
        lab = _label(tds[0])
        if lab:
            rows[lab] = [_num(td.get_text(strip=True)) for td in tds[1:]]
    return {"cols": cols, "rows": rows}


def company(symbol: str) -> dict:
    symbol = symbol.upper().strip()

    def fetch():
        last_err = None
        for path in (f"/company/{symbol}/consolidated/", f"/company/{symbol}/"):
            r = httpx.get("https://www.screener.in" + path, headers={"User-Agent": USER_AGENT},
                          timeout=30, follow_redirects=True)
            if r.status_code == 404:
                last_err = ValueError(f"{symbol} not found on screener.in")
                continue
            r.raise_for_status()
            s = BeautifulSoup(r.text, "lxml")
            q = _table(s, "quarters")
            if not q or not q["rows"] or all(v is None for v in next(iter(q["rows"].values()))):
                last_err = ValueError("empty financials")
                continue
            ratios = {}
            for li in s.select("#top-ratios li"):
                name = li.select_one(".name")
                val = li.select_one(".value")
                if name and val:
                    ratios[name.get_text(strip=True)] = " ".join(val.get_text(" ", strip=True).split())
            about = s.select_one(".company-profile .about") or s.select_one(".about")
            concalls = []
            for li in s.select("#documents .concalls li"):
                when = li.select_one("div")
                tr = li.find("a", title="Raw Transcript") or next(
                    (a for a in li.select("a") if a.get_text(strip=True) == "Transcript"), None)
                if tr and when:
                    concalls.append({"date": when.get_text(strip=True), "url": tr.get("href")})
            ars = []
            for a in s.select("#documents .annual-reports a"):
                ars.append({"title": " ".join(a.get_text(" ", strip=True).split()), "url": a.get("href")})
            h1 = s.select_one("h1")
            return {
                "symbol": symbol,
                "name": h1.get_text(strip=True) if h1 else symbol,
                "about": " ".join(about.get_text(" ", strip=True).split()) if about else "",
                "ratios": ratios,
                "quarters": q,
                "annual": _table(s, "profit-loss"),
                "balance": _table(s, "balance-sheet"),
                "cashflow": _table(s, "cash-flow"),
                "ratio_table": _table(s, "ratios"),
                "concalls": concalls,
                "annual_reports": ars,
                "source": "https://www.screener.in" + path,
            }
        raise last_err or ValueError(f"{symbol}: no data")

    return cache.cached("screener", symbol, 6 * 3600, fetch)


def fy_label(col: str, annual: bool = False) -> str:
    """'Sep 2026' -> 'Q2 FY27' (Indian fiscal year runs Apr–Mar); annual 'Mar 2026' -> 'FY26'."""
    try:
        d = dt.datetime.strptime(col.strip(), "%b %Y")
    except ValueError:
        return col
    fy = d.year + 1 if d.month >= 4 else d.year
    if annual:
        return f"FY{fy % 100:02d}"
    q = (d.month - 4) % 12 // 3 + 1
    return f"Q{q} FY{fy % 100:02d}"


ALIASES = {
    "revenue": ["Sales", "Revenue"],
    "ebitda": ["Operating Profit", "Financing Profit"],
    "margin": ["OPM %", "Financing Margin %"],
    "pbt": ["Profit before tax"],
    "pat": ["Net Profit"],
    "eps": ["EPS in Rs"],
}


def row(table: dict | None, metric: str) -> list:
    if not table:
        return []
    for name in ALIASES.get(metric, [metric]):
        for k, v in table["rows"].items():
            if k.lower().startswith(name.lower()):
                return v
    return []
