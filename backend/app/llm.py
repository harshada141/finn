"""LLM client with model routing, caching and provider fallback.

tier="cheap"  -> small model, bulk extraction (classification, one-liners, bios, guidance)
tier="strong" -> large model, reasoning (promised-vs-delivered verdicts, bull/bear)
Groq is primary; on rate limits or errors we fall back to Gemini. Every response is cached
by (tier, system, prompt) so a repeat view costs nothing.
"""
import json
import logging
import re
import threading
import time

import httpx

from . import cache
from .config import CHEAP_MODEL, FALLBACK_MODEL, GEMINI_API_KEY, GROQ_API_KEY, STRONG_MODEL

log = logging.getLogger("finn.llm")

STATS = {"cheap_calls": 0, "strong_calls": 0, "fallback_calls": 0, "cache_hits": 0,
         "prompt_tokens": 0, "completion_tokens": 0}
_stats_lock = threading.Lock()


def _bump(**kw):
    with _stats_lock:
        for k, v in kw.items():
            STATS[k] = STATS.get(k, 0) + v


def parse_json(text: str):
    text = re.sub(r"<think>.*?</think>", "", text or "", flags=re.DOTALL).strip()
    m = re.search(r"```(?:json)?\s*(.*?)```", text, flags=re.DOTALL)
    if m:
        text = m.group(1)
    start = min([i for i in (text.find("{"), text.find("[")) if i != -1], default=-1)
    if start == -1:
        raise ValueError("no JSON in model output")
    obj, _ = json.JSONDecoder().raw_decode(text[start:])
    return obj


def _groq(model: str, system: str, prompt: str, max_tokens: int, effort: str) -> str:
    body = {
        "model": model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
        "temperature": 0.1,
        "max_completion_tokens": max_tokens,
        "response_format": {"type": "json_object"},
        "reasoning_effort": effort,
    }
    for attempt in range(3):
        r = httpx.post("https://api.groq.com/openai/v1/chat/completions",
                       headers={"Authorization": f"Bearer {GROQ_API_KEY}"}, json=body, timeout=90)
        if r.status_code == 429 and attempt < 2:
            wait = min(float(r.headers.get("retry-after", "6") or 6), 20)
            log.warning("groq 429, sleeping %.1fs", wait)
            time.sleep(wait)
            continue
        r.raise_for_status()
        d = r.json()
        u = d.get("usage", {})
        _bump(prompt_tokens=u.get("prompt_tokens", 0), completion_tokens=u.get("completion_tokens", 0))
        return d["choices"][0]["message"]["content"]
    raise RuntimeError("groq rate limited")


def _gemini(system: str, prompt: str, max_tokens: int) -> str:
    body = {
        "systemInstruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"responseMimeType": "application/json", "maxOutputTokens": max_tokens,
                             "temperature": 0.1},
    }
    r = httpx.post(f"https://generativelanguage.googleapis.com/v1beta/models/{FALLBACK_MODEL}:generateContent",
                   headers={"x-goog-api-key": GEMINI_API_KEY}, json=body, timeout=120)
    r.raise_for_status()
    d = r.json()
    return "".join(p.get("text", "") for p in d["candidates"][0]["content"]["parts"])


def complete_json(system: str, prompt: str, tier: str = "cheap", max_tokens: int = 1500):
    key = f"{tier}|{system}|{prompt}"
    hit = cache.get("llm", key, None)
    if hit is not None:
        _bump(cache_hits=1)
        return hit

    model = STRONG_MODEL if tier == "strong" else CHEAP_MODEL
    effort = "medium" if tier == "strong" else "low"
    out = None
    if GROQ_API_KEY:
        try:
            _bump(**{f"{tier}_calls": 1})
            out = parse_json(_groq(model, system, prompt, max_tokens + 800, effort))
        except Exception as e:
            log.warning("groq %s failed: %s", model, e)
    if out is None and GEMINI_API_KEY:
        _bump(fallback_calls=1)
        out = parse_json(_gemini(system, prompt, max_tokens + 2000))
    if out is None:
        raise RuntimeError("no LLM available")
    cache.put("llm", key, out)
    return out
