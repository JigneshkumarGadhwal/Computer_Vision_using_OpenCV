"""Fairness-mechanism audit using only PUBLIC, post-round metadata.

What is (secondhand-)documented for Spribe's Aviator: a commit-reveal scheme
in which a hash of the server seed is shown before the round, client seeds come
from the first bettors, and the result is derived from a SHA-512 hash of the
combined seeds. The exact multiplier formula and the RTP are NOT confirmed
from primary documentation in this project, so:

* We verify only what is generic and checkable: whether a revealed server
  seed matches its pre-round commitment (SHA-256 or SHA-512).
* Deriving the multiplier from seeds requires a platform-specific function.
  None is shipped by default; register one with :func:`register_derivation`
  once you have verified it against the operator's documentation.

Nothing here attempts to obtain future seeds or non-public server state.
"""
from __future__ import annotations

import hashlib
from typing import Callable

import pandas as pd

_DERIVATIONS: dict[str, Callable[[str, str, str | None], float]] = {}


def register_derivation(name: str, fn: Callable[[str, str, str | None], float]) -> None:
    """Register a verified seed -> multiplier derivation for a platform."""
    _DERIVATIONS[name] = fn


def commitment_matches(server_seed: str, server_seed_hash: str) -> str | None:
    """Return the matching hash algorithm, or None if neither matches."""
    h = server_seed_hash.strip().lower()
    for algo in ("sha256", "sha512"):
        if hashlib.new(algo, server_seed.encode()).hexdigest() == h:
            return algo
    return None


def audit_fairness(rounds: pd.DataFrame, derivation: str | None = None) -> dict:
    n = len(rounds)
    has_hash = rounds["server_seed_hash"].notna() if "server_seed_hash" in rounds else pd.Series(False, index=rounds.index)
    has_seed = rounds["server_seed"].notna() if "server_seed" in rounds else pd.Series(False, index=rounds.index)
    both = has_hash & has_seed
    matched, mismatched, algos = 0, 0, {}
    mismatched_ids = []
    for _, r in rounds[both].iterrows():
        algo = commitment_matches(r["server_seed"], r["server_seed_hash"])
        if algo:
            matched += 1
            algos[algo] = algos.get(algo, 0) + 1
        else:
            mismatched += 1
            if len(mismatched_ids) < 20:
                mismatched_ids.append(r["round_id"])
    derived = None
    if derivation:
        fn = _DERIVATIONS.get(derivation)
        if fn is None:
            derived = {"error": f"no derivation registered under '{derivation}'"}
        else:
            ok = bad = 0
            for _, r in rounds[has_seed].iterrows():
                try:
                    val = fn(r["server_seed"], r.get("client_seeds") or "", r.get("nonce"))
                    ok += abs(val - r["multiplier"]) < 0.005
                    bad += abs(val - r["multiplier"]) >= 0.005
                except Exception:
                    bad += 1
            derived = {"matched": ok, "mismatched": bad}
    return {
        "n_rounds": n,
        "rounds_with_commitment": int(has_hash.sum()),
        "rounds_with_revealed_seed": int(has_seed.sum()),
        "commitment_checks": int(both.sum()),
        "commitment_matched": matched,
        "commitment_mismatched": mismatched,
        "mismatched_round_ids": mismatched_ids,
        "hash_algorithms": algos,
        "multiplier_derivation": derived,
        "registered_derivations": sorted(_DERIVATIONS),
        "status": (
            "no_fairness_metadata"
            if not both.any()
            else ("commitments_consistent" if mismatched == 0 else "commitment_mismatches_found")
        ),
        "notes": [
            "Documentation of the generation mechanism is platform-specific and was not verified from primary sources.",
            "Matching commitments show the operator did not change the server seed after committing; "
            "they do not prove the RNG or payout formula is implemented as advertised.",
            "Independence of rounds is an assumption to be tested statistically (see EDA dependence tests).",
        ],
    }
