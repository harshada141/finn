"""Tiny disk cache.

Every external call (exchange feed, prices, screener, PDFs, LLM output) goes through here.
Writes land in cache/live. If a live fetch fails (rate limit, cloud IP blocked), we fall back
to a stale live copy, then to cache/seed — a snapshot committed to the repo so the demo
always renders.
"""
import hashlib
import json
import threading
import time
from pathlib import Path

from .config import ROOT

LIVE = ROOT / "cache" / "live"
SEED = ROOT / "cache" / "seed"
# Namespaces with a TTL that may still be served from the snapshot when there is no live copy:
# research narratives change only when a new concall is filed.
WARM_FROM_SEED = {"narrative"}
_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def _path(base: Path, ns: str, key: str) -> Path:
    h = hashlib.sha1(key.encode()).hexdigest()[:20]
    return base / ns / f"{h}.json"


def get(ns: str, key: str, ttl: float | None):
    p = _path(LIVE, ns, key)
    if p.exists() and (ttl is None or time.time() - p.stat().st_mtime < ttl):
        try:
            return json.loads(p.read_text(encoding="utf-8"))["v"]
        except Exception:
            return None
    # Immutable values (LLM outputs, per-filing summaries) are valid forever, so the committed
    # snapshot works as a warm cache: a fresh deploy doesn't re-pay for work already done.
    if ttl is None or ns in WARM_FROM_SEED:
        s = _path(SEED, ns, key)
        if s.exists():
            try:
                return json.loads(s.read_text(encoding="utf-8"))["v"]
            except Exception:
                return None
    return None


def get_stale(ns: str, key: str):
    for base in (LIVE, SEED):
        p = _path(base, ns, key)
        if p.exists():
            try:
                return json.loads(p.read_text(encoding="utf-8"))["v"]
            except Exception:
                pass
    return None


def put(ns: str, key: str, value) -> None:
    p = _path(LIVE, ns, key)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"k": key, "v": value}, ensure_ascii=False), encoding="utf-8")


def cached(ns: str, key: str, ttl: float | None, fn):
    """Return a fresh cached value, else compute; on failure serve the last known value."""
    v = get(ns, key, ttl)
    if v is not None:
        return v
    with _locks_guard:
        lock = _locks.setdefault(f"{ns}:{key}", threading.Lock())
    with lock:
        v = get(ns, key, ttl)
        if v is not None:
            return v
        try:
            v = fn()
        except Exception:
            stale = get_stale(ns, key)
            if stale is not None:
                return stale
            raise
        if v is not None:
            put(ns, key, v)
        return v
