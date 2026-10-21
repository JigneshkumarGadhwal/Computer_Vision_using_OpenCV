"""Fairness-mechanism audit using only PUBLIC, post-round metadata.

Spribe's in-game documentation (Aviator demo, "What is Provably Fair",
collected 2026-10-21) states: the operator generates a server seed whose SHA-256
hash is published before the round; when the round starts it is merged with the
client seeds of the first 3 bettors, a SHA-512 hash is computed, "and from this
hash - game result". The final step is not spelled out. It was reconstructed and
verified exactly against 16 rounds' published fairness records (see
:func:`spribe_aviator_result` and ``tests/data/spribe_fairness_samples.json``):

    H      = SHA512(server_seed + client_seed_1 + client_seed_2 + client_seed_3)
    h      = int(H[:13], 16)                    # first 52 bits
    result = max(1.00, floor(97 * 2**52 / (2**52 - h)) / 100)

With H uniformly distributed this gives P(X >= m) = 0.97 / m exactly, i.e. the
reference distribution used in the analysis, with independent rounds.
Verified for the demo server; an operator may configure a different RTP.

We also check generic commitments: whether a revealed server seed matches a
previously published hash (SHA-256 or SHA-512).

Nothing here attempts to obtain future seeds or non-public server state.
"""
from __future__ import annotations

import hashlib
import json
from typing import Callable

import pandas as pd

_DERIVATIONS: dict[str, Callable[[str, str, str | None], float]] = {}
_E52 = 2 ** 52


def spribe_hash(server_seed: str, client_seeds: list[str]) -> str:
    return hashlib.sha512((server_seed + "".join(client_seeds)).encode()).hexdigest()


def spribe_aviator_result(hash_hex: str, rtp_percent: int = 97) -> float:
    """Crash multiplier from the combined SHA-512 hex digest (integer arithmetic, no float rounding)."""
    h = int(hash_hex[:13], 16)
    return max(100, (rtp_percent * _E52) // (_E52 - h)) / 100


def verify_spribe_record(server_seed: str, client_seeds: list[str], result: float,
                         published_hash: str | None = None, rtp_percent: int = 97) -> dict:
    """Re-derive a round from its revealed seeds and compare with the published values."""
    digest = spribe_hash(server_seed, client_seeds)
    computed = spribe_aviator_result(digest, rtp_percent)
    out = {
        "computed_hash_prefix": digest[:13],
        "computed_result": computed,
        "result_matches": abs(computed - float(result)) < 1e-9,
        "hash_matches": None if published_hash is None else digest == published_hash.lower(),
        "n_client_seeds": len(client_seeds),
    }
    out["verified"] = out["result_matches"] and out["hash_matches"] is not False
    return out


def _spribe_derivation(server_seed: str, client_seeds: str, nonce: str | None) -> float:
    seeds = json.loads(client_seeds) if client_seeds and client_seeds.strip().startswith("[") else (
        [x for x in (client_seeds or "").split("|") if x])
    return spribe_aviator_result(spribe_hash(server_seed, seeds))


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


register_derivation("spribe_aviator_v1", _spribe_derivation)


def _status(has_commitments: bool, commitment_mismatches: int, derived: dict | None) -> str:
    if derived and derived.get("mismatched"):
        return "result_mismatches_found"
    if commitment_mismatches:
        return "commitment_mismatches_found"
    if derived and derived.get("matched"):
        return "results_reproduced_from_revealed_seeds" + ("_and_commitments_consistent" if has_commitments else "")
    return "commitments_consistent" if has_commitments else "no_fairness_metadata"


def audit_fairness(rounds: pd.DataFrame, derivation: str | None = "spribe_aviator_v1") -> dict:
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
    if derivation and has_seed.any():
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
        "status": _status(both.any(), mismatched, derived),
        "notes": [
            "Spribe Aviator derivation verified on published demo fairness records (see module docstring); "
            "other operators/platforms may differ.",
            "Matching commitments show the operator did not change the server seed after committing; "
            "they do not prove the RNG or payout formula is implemented as advertised.",
            "Independence of rounds is an assumption to be tested statistically (see EDA dependence tests).",
        ],
    }
