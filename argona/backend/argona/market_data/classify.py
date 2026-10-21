"""Rule-based category classification (extensible: add a rule list, no engine changes needed)."""
from __future__ import annotations

import re

_RULES: list[tuple[str, list[str]]] = [
    ("weather", [r"\btemperature\b", r"\bhigh(est)? temp", r"\bKXHIGH", r"\bKXLOW", r"\brain(fall)?\b", r"\bsnow(fall)?\b",
                 r"\bhurricane\b", r"\bweather\b", r"°F", r"°C", r"\btornado", r"\bheat ?wave"]),
    ("crypto", [r"\bbitcoin\b", r"\bBTC\b", r"\bethereum\b", r"\bETH\b", r"\bsolana\b", r"\bSOL\b", r"\bXRP\b",
                r"\bdogecoin\b", r"\bDOGE\b", r"\bcrypto", r"\bKXBTC", r"\bKXETH", r"\bKXSOL", r"\bKXXRP", r"\bKXDOGE"]),
    ("economics", [r"\bfed\b", r"\bFOMC\b", r"\binterest rate", r"\brate (cut|hike)", r"\bCPI\b", r"\binflation\b",
                   r"\bGDP\b", r"\bunemployment\b", r"\bjobs report\b", r"\bnonfarm\b", r"\bpayrolls?\b", r"\brecession\b",
                   r"\bECB\b", r"\bKXFED", r"\bKXCPI", r"\bKXGDP", r"\bKXPAYROLL", r"\btreasury yield", r"\bS&P 500\b",
                   r"\bnasdaq\b", r"\bdow jones\b", r"\btariff"]),
    ("commodities", [r"\boil\b", r"\bWTI\b", r"\bbrent\b", r"\bcrude\b", r"\bgold\b", r"\bsilver\b", r"\bnatural gas\b",
                     r"\bgas prices?\b", r"\bcopper\b", r"\bwheat\b", r"\bKXWTI", r"\bKXGOLD"]),
    ("politics", [r"\belection\b", r"\bpresident", r"\bsenate\b", r"\bcongress\b", r"\bgovernor\b", r"\bparliament\b",
                  r"\bprime minister\b", r"\bnominee\b", r"\bprimary\b", r"\bimpeach", r"\bsupreme court\b", r"\bmayor\b",
                  r"\bvote\b", r"\bpoll\b", r"\bcabinet\b"]),
    ("sports", [r"\bNFL\b", r"\bNBA\b", r"\bMLB\b", r"\bNHL\b", r"\bUFC\b", r"\bF1\b", r"\bsuper bowl\b", r"\bworld cup\b",
                r"\bchampions league\b", r"\bpremier league\b", r"\bwin the\b.*\b(game|match|series|cup|title)\b",
                r"\bvs\.?\b", r"\bgrand slam\b", r"\bplayoffs?\b"]),
]
_COMPILED = [(cat, [re.compile(p, re.I) for p in pats]) for cat, pats in _RULES]

_VENUE_CATEGORY_MAP = {
    "climate and weather": "weather", "weather": "weather", "crypto": "crypto", "economics": "economics",
    "financials": "economics", "companies": "other", "politics": "politics", "elections": "politics",
    "sports": "sports", "commodities": "commodities", "world": "politics", "science and technology": "other",
}


def classify(text: str, venue_category: str | None = None) -> str:
    if venue_category:
        mapped = _VENUE_CATEGORY_MAP.get(venue_category.strip().lower())
        if mapped and mapped != "other":
            # venue taxonomy is coarse ("Financials" includes commodities) -> refine by keywords
            if mapped == "economics":
                for cat in ("commodities", "crypto"):
                    if any(p.search(text) for p in dict(_COMPILED)[cat]):
                        return cat
            return mapped
    for cat, pats in _COMPILED:
        if any(p.search(text) for p in pats):
            return cat
    return "other"
