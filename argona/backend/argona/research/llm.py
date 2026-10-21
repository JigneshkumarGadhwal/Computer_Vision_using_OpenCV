"""Optional, bounded LLM review of market resolution criteria (BRAM).

The LLM never produces prices or probabilities used for trading. It only reads the market's own
rules text and returns structured flags (ambiguity, mismatched resolution source, early-close
conditions). Flags make markets ineligible or lower confidence; they can never make a trade happen.
Rate-limited per hour and cached per rules-hash. Disabled unless ARGONA_LLM_PROVIDER=anthropic.
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
from typing import Any

log = logging.getLogger("argona.llm")

REVIEW_SCHEMA = {
    "type": "object",
    "properties": {
        "ambiguous": {"type": "boolean"},
        "flags": {"type": "array", "items": {"type": "string", "enum": [
            "ambiguous_wording", "discretionary_resolution", "unclear_resolution_source", "unclear_timing",
            "early_close_possible", "multi_condition", "definition_mismatch_with_title"]}},
        "resolution_source": {"type": "string"},
        "notes": {"type": "string"},
    },
    "required": ["ambiguous", "flags", "resolution_source", "notes"],
    "additionalProperties": False,
}

SYSTEM = ("You review prediction-market contract rules for a risk desk. Judge only the text provided. "
          "Flag wording that leaves the outcome open to interpretation, discretionary resolution, unclear "
          "sources or timing, and titles that do not match the rules. Be terse. Do not estimate probabilities.")


class LLMReviewer:
    def __init__(self, api_key: str | None, model: str, max_per_hour: int) -> None:
        self.model = model
        self.max_per_hour = max_per_hour
        self._calls: list[float] = []
        self._cache: dict[str, dict[str, Any]] = {}
        self.usage = {"calls": 0, "errors": 0, "input_tokens": 0, "output_tokens": 0, "skipped_rate_limit": 0}
        self.client = None
        if api_key:
            try:
                import anthropic

                self.client = anthropic.AsyncAnthropic(api_key=api_key, max_retries=2, timeout=60.0)
            except ImportError:  # pragma: no cover
                log.warning("anthropic package not installed; LLM review disabled (pip install 'argona[llm]')")

    @property
    def available(self) -> bool:
        return self.client is not None

    async def review(self, question: str, rules: str, source: str) -> dict[str, Any] | None:
        if not self.client:
            return None
        key = hashlib.sha256(f"{question}\n{rules}\n{source}".encode()).hexdigest()
        if key in self._cache:
            return self._cache[key]
        now = time.time()
        self._calls = [t for t in self._calls if now - t < 3600]
        if len(self._calls) >= self.max_per_hour:
            self.usage["skipped_rate_limit"] += 1
            return None
        self._calls.append(now)
        prompt = f"Market title: {question}\nStated resolution source: {source or '(none)'}\n\nRules:\n{rules[:6000]}"
        try:
            resp = await self.client.beta.messages.create(
                model=self.model,
                max_tokens=2000,
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
                system=SYSTEM,
                output_config={"effort": "low", "format": {"type": "json_schema", "schema": REVIEW_SCHEMA}},
                messages=[{"role": "user", "content": prompt}],
            )
        except Exception as e:  # network/API errors must never break the trading loop
            self.usage["errors"] += 1
            log.warning("LLM review failed: %s", type(e).__name__)
            return None
        self.usage["calls"] += 1
        u = getattr(resp, "usage", None)
        if u:
            self.usage["input_tokens"] += getattr(u, "input_tokens", 0) or 0
            self.usage["output_tokens"] += getattr(u, "output_tokens", 0) or 0
        if resp.stop_reason == "refusal":
            return None
        text = next((b.text for b in resp.content if b.type == "text"), None)
        if not text:
            return None
        try:
            data = json.loads(text)
        except ValueError:
            return None
        self._cache[key] = data
        return data
