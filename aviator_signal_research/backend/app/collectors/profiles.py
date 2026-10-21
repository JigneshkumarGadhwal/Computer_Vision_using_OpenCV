"""Site profiles: facts about specific sites, each with the evidence it came from.

A profile is a convenience, not an authority: re-verify when the site changes.
"""
from __future__ import annotations

SPRIBE = {
    "docs": ["https://spribe.co/games/aviator"],
    # Found in spribe.co's public JS bundle (chunk 165.*.js, observed 2026-10-21):
    #   function m(i){return`https://demo.spribe.io/launch/${i}?currency=USD&lang=EN&return_url=https://spribe.co/games`}
    #   enum: aviator="aviator"
    "demo_url_template": "https://demo.spribe.io/launch/{game}?currency=USD&lang=EN&return_url=https://spribe.co/games",
    "games": {"aviator": "aviator"},
    # spribe.co shows an "I'm over 18" confirmation before opening the demo
    # (chunk 543.*.js, AgePermissionModal). Use the demo only if you are of legal age.
    "age_gate": True,
    "documented": {
        "rtp_percent": 97.0,
        "source": "https://spribe.co/games/aviator ('Return to Player (RTP)' / '97%', collected 2026-10-21)",
    },
}

PROFILES = {"spribe": SPRIBE}


def demo_url(profile: str = "spribe", game: str = "aviator") -> str:
    p = PROFILES[profile]
    return p["demo_url_template"].format(game=p["games"][game])
