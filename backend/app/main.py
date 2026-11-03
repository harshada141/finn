import logging
import re
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import digest, llm, research
from .classify import CATEGORIES
from .config import PORTFOLIO, RESEARCH_DEMO

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
app = FastAPI(title="FINN — Financial Investment Agent")
app.add_middleware(GZipMiddleware, minimum_size=1000)

FRONTEND = Path(__file__).resolve().parents[2] / "frontend"
DEFAULT_ALERT_CATS = ["results", "M&A", "management_change", "fund_raise", "pledging", "litigation",
                      "credit_rating"]


def _symbols(s: str | None) -> list[str]:
    if not s:
        return PORTFOLIO
    out = [x.strip().upper() for x in s.split(",") if re.fullmatch(r"[A-Za-z0-9&\-]{1,20}", x.strip())]
    return out[:25] or PORTFOLIO


@app.get("/api/config")
def config():
    return {"portfolio": PORTFOLIO, "research_demo": RESEARCH_DEMO, "categories": CATEGORIES,
            "default_alert_categories": DEFAULT_ALERT_CATS}


@app.get("/api/digest")
def get_digest(symbols: str | None = None, hours: int = Query(24, ge=1, le=24 * 31)):
    return digest.build(_symbols(symbols), hours)


@app.post("/api/digest/enrich")
def post_enrich(symbols: str | None = None, hours: int = Query(24, ge=1, le=24 * 31)):
    return digest.enrich(_symbols(symbols), hours)


@app.get("/api/alerts")
def get_alerts(symbols: str | None = None, price: float = Query(5.0, gt=0, le=50),
               volume: float = Query(2.0, gt=0, le=50), cats: str | None = None):
    categories = [c for c in (cats.split(",") if cats else DEFAULT_ALERT_CATS) if c in CATEGORIES]
    return digest.alerts(_symbols(symbols), price, volume, categories)


@app.get("/api/research/{symbol}")
def get_research(symbol: str):
    if not re.fullmatch(r"[A-Za-z0-9&\-]{1,20}", symbol):
        raise HTTPException(400, "bad ticker")
    try:
        return research.numbers(symbol.upper())
    except Exception as e:
        raise HTTPException(404, f"Couldn't load {symbol.upper()}: {e}")


@app.get("/api/research/{symbol}/narrative")
def get_narrative(symbol: str):
    if not re.fullmatch(r"[A-Za-z0-9&\-]{1,20}", symbol):
        raise HTTPException(400, "bad ticker")
    try:
        return research.narrative(symbol.upper())
    except Exception as e:
        raise HTTPException(502, f"Narrative failed for {symbol.upper()}: {e}")


@app.get("/api/stats")
def stats():
    return llm.STATS


@app.get("/healthz")
def health():
    return {"ok": True}


if FRONTEND.exists():
    app.mount("/static", StaticFiles(directory=FRONTEND), name="static")

    @app.get("/sw.js")
    def sw():
        return FileResponse(FRONTEND / "sw.js", media_type="application/javascript")

    @app.get("/manifest.webmanifest")
    def manifest():
        return FileResponse(FRONTEND / "manifest.webmanifest", media_type="application/manifest+json")

    @app.get("/")
    def index():
        return FileResponse(FRONTEND / "index.html")
