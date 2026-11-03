"""Rules-first announcement classifier.

Maps the exchange's own filing category + subject line to the 17 reference categories and a
priority tier. No model is used when a rule is confident; ambiguous filings are flagged
`needs_llm` and resolved by the cheap model in a batch (see digest.py).

Tiers:  "act"  -> read now (results, M&A, top-management change, fund raise, pledge, serious litigation)
        "know" -> worth knowing (orders, capex, dividends, ratings, director changes)
        "routine" -> collapsed (ESOP allotments, newspaper ads, compliance certificates, ...)
"""
import re

CATEGORIES = [
    "results", "M&A", "fund_raise", "management_change", "litigation", "insider_trading",
    "earnings_call", "investor_meetings", "credit_rating", "dividend", "bonus_split", "capex",
    "press_release", "postal_ballot", "agm_egm", "pledging", "unclassified",
]

DEFAULT_TIER = {
    "results": "act", "M&A": "act", "fund_raise": "act", "management_change": "know",
    "litigation": "know", "pledging": "act", "insider_trading": "know", "credit_rating": "know",
    "dividend": "know", "bonus_split": "know", "capex": "know", "press_release": "know",
    "earnings_call": "routine", "investor_meetings": "routine", "postal_ballot": "routine",
    "agm_egm": "routine", "unclassified": "routine",
}

# (regex on NSE category, our category). First match wins.
NSE_RULES = [
    (r"financial result|integrated filing- financial", "results"),
    (r"acquisition|amalgamation|merger|demerger|scheme of arrangement|joint venture|"
     r"sale or disposal|divestment|restructuring", "M&A"),
    (r"qualified institutional|rights issue|preferential|buy ?back|commercial paper|"
     r"issue of securities|non-convertible|debenture|private placement", "fund_raise"),
    (r"appointment|resignation|change in management|change in director|cessation|retirement|"
     r"change in company secretary|change in key managerial|re-appointment", "management_change"),
    (r"litigation|action\(s\)|insolvency|penalt|fraud|default|order passed|show cause|"
     r"search|investigation", "litigation"),
    (r"pledge|encumbrance|invocation", "pledging"),
    (r"insider trading|sast|takeover|acquisition of shares|disclosure of shareholding", "insider_trading"),
    (r"con\. call|analysts|institutional investor|investor meet", "investor_meetings"),
    (r"investor presentation", "results"),
    (r"credit rating", "credit_rating"),
    (r"dividend", "dividend"),
    (r"bonus|split|sub-division", "bonus_split"),
    (r"capacity addition|commencement of commercial production|capital expenditure|expansion", "capex"),
    (r"bagging|orders/contracts|press release|agreement|monthly business|business update|"
     r"launch|award", "press_release"),
    (r"postal ballot", "postal_ballot"),
    (r"shareholders meeting|annual general meeting|agm|egm|e-voting", "agm_egm"),
    (r"esop|esos|esps|newspaper|trading window|certificate under|monitoring agency|"
     r"statement of deviation|loss of share|duplicate|record date|book closure|"
     r"compliance certificate|regulation 74|reg\. 74|regulation 7\(3\)|esg|brsr|sustainability", "unclassified"),
]

# Exchange categories that are too generic to trust a rule on.
AMBIGUOUS = re.compile(r"^(general updates|updates|outcome of board meeting|press release|"
                       r"clarification.*|other)$", re.I)

TOP_ROLES = re.compile(r"\b(ceo|cfo|chief executive|chief financial|managing director|\bmd\b|"
                       r"whole[- ]time director|executive director|chairman|chairperson|"
                       r"chief operating|coo|key managerial|kmp)\b", re.I)


def classify(nse_category: str, subject: str) -> dict:
    cat_text = (nse_category or "").lower()
    subj = (subject or "").lower()
    both = f"{cat_text} || {subj}"

    category, needs_llm = None, False
    if AMBIGUOUS.match(cat_text.strip()):
        # Board outcomes and generic updates: look at the subject line first.
        if re.search(r"financial results?|unaudited|audited results", subj):
            category = "results"
        elif re.search(r"dividend", subj):
            category = "dividend"
        else:
            needs_llm = True
    if category is None and not needs_llm:
        for pat, cat in NSE_RULES:
            if re.search(pat, cat_text):
                category = cat
                break
        if category is None:
            needs_llm = True

    category = category or "unclassified"
    tier = DEFAULT_TIER[category]
    subtype = None

    # --- refinements that change priority within a category
    if category == "results":
        if re.search(r"newspaper|advertisement|publication", both):
            tier, subtype = "routine", "results_ad"
        elif re.search(r"investor presentation|presentation", both):
            tier, subtype = "know", "results_deck"
        else:
            subtype = "results_filing"
    elif category == "investor_meetings" and re.search(r"transcript|audio|recording|conference call|earnings call", both):
        category, tier = "earnings_call", "routine"
        if "transcript" in both:
            tier, subtype = "know", "transcript"
    elif category == "management_change":
        if re.search(r"company secretary|compliance officer", both):
            tier = "routine"
        elif TOP_ROLES.search(both):
            tier = "act"
    elif category == "credit_rating":
        if re.search(r"downgrad|negative|watch", both):
            tier = "act"
        elif re.search(r"reaffirm|re-affirm", both):
            tier = "routine"
    elif category == "litigation":
        if re.search(r"insolvency|fraud|default|sebi|search|cbi|enforcement", both):
            tier = "act"
    elif category == "pledging" and re.search(r"release|revocation", both):
        tier = "know"
    elif category == "fund_raise" and re.search(r"esop|esos|employee", both):
        category, tier = "unclassified", "routine"

    return {"category": category, "tier": tier, "subtype": subtype, "needs_llm": needs_llm}


TIER_RANK = {"act": 0, "know": 1, "routine": 2}
CATEGORY_RANK = {c: i for i, c in enumerate([
    "results", "management_change", "M&A", "fund_raise", "pledging", "litigation", "credit_rating",
    "capex", "press_release", "dividend", "bonus_split", "insider_trading", "earnings_call",
    "investor_meetings", "agm_egm", "postal_ballot", "unclassified"])}
