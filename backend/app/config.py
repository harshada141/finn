import os
from pathlib import Path
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")

# Model routing: cheap model for bulk extraction, strong model only where reasoning matters.
CHEAP_MODEL = os.getenv("FINN_CHEAP_MODEL", "openai/gpt-oss-20b")
STRONG_MODEL = os.getenv("FINN_STRONG_MODEL", "openai/gpt-oss-120b")
FALLBACK_MODEL = os.getenv("FINN_FALLBACK_MODEL", "gemini-3.8-flash")

# Default mock portfolio: 16 NSE names across sectors and market caps.
PORTFOLIO = [
    "RELIANCE", "HDFCBANK", "TCS", "INFY", "ITC", "BHARTIARTL", "LT", "SUNPHARMA",
    "TMPV", "ASIANPAINT", "BAJFINANCE", "ADANIPORTS", "DIXON", "POLYCAB", "ETERNAL", "IRCTC",
]
# Companies outside the portfolio used to demo on-demand research.
RESEARCH_DEMO = ["PERSISTENT", "KAYNES", "CDSL", "JUBLFOOD"]

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0 Safari/537.36"
)
