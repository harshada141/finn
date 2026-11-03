"""Pre-warm the cache for the demo and snapshot it into cache/seed (committed to the repo).

Run before deploying:  python -m app.seed
If the exchange or Yahoo block the cloud host's IP, FINN serves this snapshot instead of an error.
Raw PDF text is not snapshotted (large, and only needed to (re)generate summaries that are cached).
"""
import shutil
import time

from . import cache, digest, research
from .config import PORTFOLIO, RESEARCH_DEMO


def main():
    t0 = time.time()
    if "--fresh" in __import__("sys").argv:
        shutil.rmtree(cache.LIVE / "narrative", ignore_errors=True)  # regenerate with current prompts
    for hours in (24, 72, 168, 720):
        for _ in range(4):
            d = digest.build(PORTFOLIO, hours)
            print(f"digest {hours}h: {d['counts']} pending={len(d['pending'])}")
            if not d["pending"] or hours == 720:
                break
            digest.enrich(PORTFOLIO, hours)
    digest.alerts(PORTFOLIO, 5.0, 2.0, ["results", "M&A", "management_change", "fund_raise", "pledging",
                                         "litigation", "credit_rating"])
    for sym in RESEARCH_DEMO + ["RELIANCE", "ITC", "DIXON", "ASIANPAINT"]:
        try:
            research.numbers(sym)
            n = research.narrative(sym)
            if n.get("partial"):  # free-tier rate limit hit: wait out the per-minute window, retry once
                time.sleep(60)
                n = research.narrative(sym)
            time.sleep(15)  # stay under Groq's free-tier tokens-per-minute
            print(f"research {sym}: available={n.get('available')} guidance={len(n.get('guidance', []))} "
                  f"tally={n.get('tally')}")
        except Exception as e:
            print(f"research {sym} failed: {e}")
    shutil.rmtree(cache.SEED, ignore_errors=True)
    shutil.copytree(cache.LIVE, cache.SEED, ignore=shutil.ignore_patterns("pdf"))
    print(f"seeded in {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
