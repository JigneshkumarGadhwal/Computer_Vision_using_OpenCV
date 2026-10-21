"""Daily-high-temperature model.

Forecast: multi-model daily maximum from Open-Meteo (free, keyless). The settlement value is the
official station reading reported as a whole degree, so with mean mu and error sigma:

  between [a, b] (inclusive integers):  P = N((b + 0.5 - mu)/s) - N((a - 0.5 - mu)/s)
  greater than f  (i.e. >= f+1):         P = 1 - N((f + 0.5 - mu)/s)
  less than c     (i.e. <= c-1):         P = N((c - 0.5 - mu)/s)

s^2 = sigma_lead^2 + model_spread^2, sigma_lead from typical day-ahead max-temperature errors.
Station mapping is approximate (Open-Meteo grid point vs. the exact ASOS station), so quality is
"medium" and the venue's own resolution source always governs.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import date, datetime
from zoneinfo import ZoneInfo

from ..domain import NormalizedMarket, OrderBook, Venue
from .base import Abstention, ModelContext, ModelEstimate, clamp, ncdf


@dataclass(frozen=True)
class Station:
    name: str
    lat: float
    lon: float
    tz: str
    unit: str = "fahrenheit"


KALSHI_SERIES = {
    "KXHIGHNY": Station("New York Central Park", 40.7790, -73.9692, "America/New_York"),
    "KXHIGHCHI": Station("Chicago Midway", 41.7868, -87.7522, "America/Chicago"),
    "KXHIGHMIA": Station("Miami Intl", 25.7959, -80.2870, "America/New_York"),
    "KXHIGHAUS": Station("Austin Bergstrom", 30.1945, -97.6699, "America/Chicago"),
    "KXHIGHDEN": Station("Denver Intl", 39.8561, -104.6737, "America/Denver"),
    "KXHIGHLAX": Station("Los Angeles Intl", 33.9416, -118.4085, "America/Los_Angeles"),
    "KXHIGHPHIL": Station("Philadelphia Intl", 39.8744, -75.2424, "America/New_York"),
}
CITY_STATIONS = {
    "new york": Station("New York LaGuardia", 40.7769, -73.8740, "America/New_York"),
    "nyc": Station("New York LaGuardia", 40.7769, -73.8740, "America/New_York"),
    "chicago": Station("Chicago O'Hare", 41.9742, -87.9073, "America/Chicago"),
    "miami": Station("Miami Intl", 25.7959, -80.2870, "America/New_York"),
    "atlanta": Station("Atlanta Hartsfield", 33.6407, -84.4277, "America/New_York"),
    "dallas": Station("Dallas Love Field", 32.8471, -96.8518, "America/Chicago"),
    "seattle": Station("Seattle-Tacoma", 47.4502, -122.3088, "America/Los_Angeles"),
    "denver": Station("Denver Intl", 39.8561, -104.6737, "America/Denver"),
    "austin": Station("Austin Bergstrom", 30.1945, -97.6699, "America/Chicago"),
    "los angeles": Station("Los Angeles Intl", 33.9416, -118.4085, "America/Los_Angeles"),
    "london": Station("London City", 51.5048, 0.0495, "Europe/London", "celsius"),
    "paris": Station("Paris CDG", 49.0097, 2.5479, "Europe/Paris", "celsius"),
    "seoul": Station("Seoul Incheon", 37.4602, 126.4407, "Asia/Seoul", "celsius"),
    "toronto": Station("Toronto Pearson", 43.6777, -79.6248, "America/Toronto", "celsius"),
    "berlin": Station("Berlin Brandenburg", 52.3667, 13.5033, "Europe/Berlin", "celsius"),
}
SIGMA_LEAD_F = {0: 1.6, 1: 2.4, 2: 3.0, 3: 3.6, 4: 4.2}
_MONTHS = "january|february|march|april|may|june|july|august|september|october|november|december"


@dataclass
class TempSpec:
    station: Station
    day: date
    kind: str  # between | greater | less
    lo: float | None
    hi: float | None


def station_for(m: NormalizedMarket) -> Station | None:
    series = (m.meta.get("series_ticker") or m.venue_market_id.split("-")[0]).upper()
    if series in KALSHI_SERIES:
        return KALSHI_SERIES[series]
    text = f"{m.question} {m.meta.get('event_title') or ''}".lower()
    for city, st in CITY_STATIONS.items():
        if re.search(rf"\b{re.escape(city)}\b", text):
            return st
    return None


def _target_day(m: NormalizedMarket, st: Station) -> date | None:
    ev = m.event_id or ""
    mm = re.search(r"-(\d{2})([A-Z]{3})(\d{2})", ev) or re.search(r"-(\d{2})([A-Z]{3})(\d{2})", m.venue_market_id)
    if mm:
        try:
            return datetime.strptime(f"{mm.group(1)}{mm.group(2)}{mm.group(3)}", "%y%b%d").date()
        except ValueError:
            pass
    q = f"{m.question} {m.meta.get('event_title') or ''}"
    dm = re.search(rf"\b({_MONTHS})\s+(\d{{1,2}})\b", q, re.I)
    if dm and m.close_time:
        year = m.close_time.astimezone(ZoneInfo(st.tz)).year
        try:
            return datetime.strptime(f"{dm.group(1)} {dm.group(2)} {year}", "%B %d %Y").date()
        except ValueError:
            return None
    return None


def parse_spec(m: NormalizedMarket) -> TempSpec | None:
    text = f"{m.question} {m.meta.get('event_title') or ''}".lower()
    if not ("high" in text or "max" in text or "highest" in text or (m.meta.get("series_ticker") or "").upper().startswith("KXHIGH")):
        return None  # only daily-maximum markets are modelled
    st = station_for(m)
    if not st:
        return None
    day = _target_day(m, st)
    if not day:
        return None
    s = m.strike or {}
    stype = s.get("strike_type")
    if stype:
        lo, hi = s.get("floor_strike"), s.get("cap_strike")
        if stype == "between" and lo is not None and hi is not None:
            return TempSpec(st, day, "between", float(lo), float(hi))
        if stype in ("greater", "greater_or_equal") and lo is not None:
            return TempSpec(st, day, "greater", float(lo) - (1 if stype == "greater_or_equal" else 0), None)
        if stype in ("less", "less_or_equal") and hi is not None:
            return TempSpec(st, day, "less", None, float(hi) + (1 if stype == "less_or_equal" else 0))
        return None
    label = (m.outcome_label or m.question).replace("º", "°")
    rng = re.search(r"(-?\d+)\s*(?:-|–|to)\s*(-?\d+)\s*°", label)
    if rng:
        return TempSpec(st, day, "between", float(rng.group(1)), float(rng.group(2)))
    ge = re.search(r"(?:≥|>=)\s*(-?\d+)|(-?\d+)\s*°[FC]?\s*(?:or (?:higher|above|more))", label, re.I)
    if ge:
        v = float(ge.group(1) or ge.group(2))
        return TempSpec(st, day, "greater", v - 1, None)
    le = re.search(r"(?:≤|<=)\s*(-?\d+)|(-?\d+)\s*°[FC]?\s*(?:or (?:lower|below|less))", label, re.I)
    if le:
        v = float(le.group(1) or le.group(2))
        return TempSpec(st, day, "less", None, v + 1)
    single = re.search(r"\b(-?\d+)\s*°[FC]\b", label)
    if single:
        v = float(single.group(1))
        return TempSpec(st, day, "between", v, v)
    return None


def bucket_prob(spec: TempSpec, mu: float, s: float) -> float:
    if spec.kind == "between":
        return max(0.0, ncdf((spec.hi + 0.5 - mu) / s) - ncdf((spec.lo - 0.5 - mu) / s))  # type: ignore[operator]
    if spec.kind == "greater":
        return 1.0 - ncdf((spec.lo + 0.5 - mu) / s)  # type: ignore[operator]
    return ncdf((spec.hi - 0.5 - mu) / s)  # type: ignore[operator]


class WeatherHighTempModel:
    name = "weather_daily_high"
    version = "1.0"

    def applies(self, market: NormalizedMarket) -> bool:
        return market.category == "weather" and not market.is_simulated

    def estimate(self, market: NormalizedMarket, book: OrderBook | None, ctx: ModelContext) -> ModelEstimate | Abstention:
        spec = parse_spec(market)
        if not spec:
            return Abstention(self.name, "not a parseable daily-high temperature market (station/date/bucket)")
        subject = f"{spec.station.lat:.4f},{spec.station.lon:.4f}"
        fcs = [e for e in ctx.evidence.get("weather_forecast", []) if e.subject == subject]
        if not fcs:
            return Abstention(self.name, f"no forecast evidence for {spec.station.name}")
        ev = max(fcs, key=lambda e: e.observed_at)
        if (ctx.now - ev.observed_at).total_seconds() > 6 * 3600:
            return Abstention(self.name, "forecast older than 6h")
        per_model = ev.payload.get("daily_max", {}).get(spec.day.isoformat())
        if not per_model:
            return Abstention(self.name, f"forecast does not cover {spec.day}")
        vals = [float(v) for v in per_model.values() if v is not None]
        if not vals:
            return Abstention(self.name, "forecast values missing")
        unit = ev.payload.get("unit", "fahrenheit")
        mu = sum(vals) / len(vals)
        spread = (sum((v - mu) ** 2 for v in vals) / max(1, len(vals) - 1)) ** 0.5 if len(vals) > 1 else 1.0
        local_today = ctx.now.astimezone(ZoneInfo(spec.station.tz)).date()
        lead = (spec.day - local_today).days
        if lead < 0:
            return Abstention(self.name, "target day already passed (awaiting official report)")
        sig_lead = SIGMA_LEAD_F.get(min(lead, 4), 4.2)
        if unit == "celsius":
            sig_lead /= 1.8
        s = math.sqrt(sig_lead ** 2 + spread ** 2)
        q = bucket_prob(spec, mu, s)
        q_a = bucket_prob(spec, mu + 0.5 * spread, s)
        q_b = bucket_prob(spec, mu - 0.5 * spread, s)
        se = abs(q_a - q_b) / 2 + 0.03
        conflicting = []
        if spread > 2.5 * (1 if unit == "fahrenheit" else 0.55):
            conflicting.append(f"weather models disagree (spread {spread:.1f}°)")
        return ModelEstimate(
            model=self.name, version=self.version, q=clamp(q, 0.002, 0.998), sigma=clamp(se, 0.02, 0.5), quality="medium",
            evidence=[ev],
            inputs={"station": spec.station.name, "day": spec.day.isoformat(), "kind": spec.kind, "lo": spec.lo, "hi": spec.hi,
                    "mu": round(mu, 2), "model_spread": round(spread, 2), "sigma_total": round(s, 2), "lead_days": lead,
                    "unit": unit, "models": per_model},
            supporting=[f"forecast max {mu:.1f}° (σ {s:.1f}°, {len(vals)} models) for {spec.station.name} on {spec.day}"],
            conflicting=conflicting + ([] if market.venue is Venue.KALSHI else ["station mapping approximate"]),
        )
