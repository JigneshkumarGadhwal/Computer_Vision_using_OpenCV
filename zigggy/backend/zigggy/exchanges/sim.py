"""SIM venue - a clearly labelled, self-contained simulated prediction market.

Purpose: exercise the complete pipeline (discovery -> evidence -> probability -> risk -> paper
fills -> settlement) without network access or money. Every market, quote and evidence item
produced here carries ``is_simulated=True`` and is rendered with a SIMULATED badge. SIM markets
can never be traded live (there is no SIM trading adapter).

World model:
  * ``SIMBTC`` spot follows a geometric Brownian motion (sigma=60%/yr). Threshold markets
    "SIMBTC above K at T" resolve on the simulated spot at expiry.
  * Forecast markets have a hidden true probability p*; a simulated forecaster publishes
    p* + noise (sigma 0.05). They resolve Bernoulli(p*).
  * Market makers quote around a noisy, mean-reverting deviation from the true value, so a
    good model sometimes has genuine edge - and sometimes the market is right.
"""
from __future__ import annotations

import json
import math
import random
from dataclasses import asdict, dataclass, field
from pathlib import Path
from datetime import datetime, timedelta
from typing import Callable

from ..domain import BookLevel, FeeModel, MarketStatus, NormalizedMarket, OrderBook, Outcome, Venue, utcnow
from .base import Capabilities, MarketDataAdapter

YEAR_S = 365.25 * 24 * 3600


def _ncdf(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


@dataclass
class _SimMarket:
    mid: str
    kind: str  # "crypto" | "forecast"
    question: str
    category: str
    created: datetime
    expiry: datetime
    strike: float | None = None
    true_p: float | None = None
    forecast_p: float | None = None
    deviation: float = 0.0
    spread: float = 0.03
    depth_scale: float = 200.0
    result: Outcome | None = None
    resolved_at: datetime | None = None
    event_id: str | None = None
    label: str | None = None


@dataclass
class SimWorld:
    seed: int = 7
    n_markets: int = 24
    clock: Callable[[], datetime] = utcnow
    spot: float = 100_000.0
    vol: float = 0.60
    history: list[tuple[datetime, float]] = field(default_factory=list)
    markets: dict[str, _SimMarket] = field(default_factory=dict)
    _last: datetime | None = None
    _counter: int = 0

    def __post_init__(self) -> None:
        self.rng = random.Random(self.seed)
        now = self.clock()
        # 6h of 1-minute history so volatility estimators have data from the start
        t = now - timedelta(hours=6)
        p = self.spot
        while t < now:
            p = self._gbm_step(p, 60.0)
            self.history.append((t, p))
            t += timedelta(minutes=1)
        self.spot = p
        self._last = now
        for _ in range(self.n_markets):
            self._spawn(now)

    # ---------- persistence (so SIM positions survive a restart) ----------
    def save(self, path: Path) -> None:
        def ser(m: _SimMarket) -> dict:
            d = asdict(m)
            for k in ("created", "expiry", "resolved_at"):
                d[k] = d[k].isoformat() if d[k] else None
            d["result"] = m.result.value if m.result else None
            return d

        state = {"spot": self.spot, "counter": self._counter, "last": self._last.isoformat() if self._last else None,
                 "history": [(t.isoformat(), p) for t, p in self.history[-720:]], "markets": [ser(m) for m in self.markets.values()],
                 "rng": self.rng.getstate()}
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, default=list))
        tmp.replace(path)

    @classmethod
    def load(cls, path: Path, *, seed: int, n_markets: int, clock: Callable[[], datetime] = utcnow) -> "SimWorld":
        from datetime import datetime as _dt

        st = json.loads(path.read_text())
        w = cls.__new__(cls)
        w.seed, w.n_markets, w.clock, w.vol = seed, n_markets, clock, 0.60
        w.rng = random.Random(seed)
        rs = st.get("rng")
        if rs:
            w.rng.setstate((rs[0], tuple(rs[1]), rs[2]))
        w.spot = float(st["spot"])
        w._counter = int(st["counter"])
        w._last = _dt.fromisoformat(st["last"]) if st.get("last") else clock()
        w.history = [(_dt.fromisoformat(t), float(p)) for t, p in st["history"]]
        w.markets = {}
        for d in st["markets"]:
            for k in ("created", "expiry", "resolved_at"):
                d[k] = _dt.fromisoformat(d[k]) if d[k] else None
            d["result"] = Outcome(d["result"]) if d["result"] else None
            m = _SimMarket(**d)
            w.markets[m.mid] = m
        return w

    # ---------- dynamics ----------
    def _gbm_step(self, p: float, dt_s: float) -> float:
        dt = dt_s / YEAR_S
        return p * math.exp(-0.5 * self.vol ** 2 * dt + self.vol * math.sqrt(dt) * self.rng.gauss(0, 1))

    def advance(self) -> None:
        now = self.clock()
        if self._last is None:
            self._last = now
        while self._last < now:
            step = min(60.0, (now - self._last).total_seconds())
            if step <= 0:
                break
            self.spot = self._gbm_step(self.spot, step)
            self._last += timedelta(seconds=step)
            if not self.history or (self._last - self.history[-1][0]).total_seconds() >= 60:
                self.history.append((self._last, self.spot))
            for m in self.markets.values():  # deviation: Ornstein-Uhlenbeck, half-life ~10 min
                if m.result is None:
                    k = 1 - math.exp(-step / 900.0)
                    m.deviation += -k * m.deviation + 0.07 * math.sqrt(2 * k) * self.rng.gauss(0, 1)
        self.history = self.history[-720:]
        for m in list(self.markets.values()):
            if m.result is None and now >= m.expiry:
                self._resolve(m, now)
        open_n = sum(1 for m in self.markets.values() if m.result is None)
        for _ in range(self.n_markets - open_n):
            self._spawn(now)
        for k in [k for k, m in self.markets.items() if m.resolved_at and now - m.resolved_at > timedelta(hours=6)]:
            del self.markets[k]

    def _resolve(self, m: _SimMarket, now: datetime) -> None:
        if m.kind == "crypto":
            m.result = Outcome.YES if self.spot > (m.strike or 0) else Outcome.NO
        else:
            m.result = Outcome.YES if self.rng.random() < (m.true_p or 0.5) else Outcome.NO
        m.resolved_at = now

    def _spawn(self, now: datetime) -> None:
        self._counter += 1
        idx = self._counter
        minutes = self.rng.choice([15, 20, 30, 45, 60, 90])
        expiry = now + timedelta(minutes=minutes)
        if self.rng.random() < 0.5:
            sigma_t = self.vol * math.sqrt(minutes * 60 / YEAR_S)
            strike = round(self.spot * math.exp(self.rng.uniform(-1.2, 1.2) * sigma_t), -1)
            m = _SimMarket(mid=f"SIMBTC-{idx}", kind="crypto", category="crypto", created=now, expiry=expiry, strike=strike,
                           question=f"[SIM] Will SIMBTC be above ${strike:,.0f} at {expiry:%H:%M} UTC?",
                           event_id=f"SIMBTC-{expiry:%H%M}", label=f">{strike:,.0f}")
        else:
            p = min(0.95, max(0.05, self.rng.betavariate(2, 2)))
            cat = self.rng.choice(["economics", "weather", "commodities", "politics"])
            m = _SimMarket(mid=f"SIMFC-{idx}", kind="forecast", category=cat, created=now, expiry=expiry, true_p=p,
                           forecast_p=min(0.99, max(0.01, p + self.rng.gauss(0, 0.05))),
                           question=f"[SIM] Synthetic {cat} event #{idx} resolves YES by {expiry:%H:%M} UTC?")
        m.deviation = self.rng.gauss(0, 0.07)
        m.spread = self.rng.choice([0.01, 0.02, 0.03, 0.04, 0.06])
        m.depth_scale = self.rng.choice([40, 80, 150, 300, 600])
        self.markets[m.mid] = m

    # ---------- observables ----------
    def true_value(self, m: _SimMarket, now: datetime) -> float:
        if m.kind == "crypto":
            t = max((m.expiry - now).total_seconds(), 1.0) / YEAR_S
            d2 = (math.log(self.spot / (m.strike or self.spot)) - 0.5 * self.vol ** 2 * t) / (self.vol * math.sqrt(t))
            return _ncdf(d2)
        return m.true_p or 0.5

    def quote_mid(self, m: _SimMarket, now: datetime) -> float:
        return min(0.98, max(0.02, self.true_value(m, now) + m.deviation))

    def book(self, m: _SimMarket, now: datetime) -> OrderBook:
        mid = self.quote_mid(m, now)
        half = m.spread / 2
        bids, asks = [], []
        for i in range(5):
            b = round(mid - half - 0.01 * i, 2)
            a = round(mid + half + 0.01 * i, 2)
            size = round(m.depth_scale * (1 + i) * self.rng.uniform(0.6, 1.4))
            if 0.01 <= b <= 0.99:
                bids.append(BookLevel(b, size))
            if 0.01 <= a <= 0.99:
                asks.append(BookLevel(a, size))
        return OrderBook(yes_bids=bids, yes_asks=asks, ts=now, source="sim", is_simulated=True)

    def forecast(self, mid: str) -> tuple[float, float] | None:
        m = self.markets.get(mid)
        if not m or m.kind != "forecast":
            return None
        return m.forecast_p or 0.5, 0.06

    def candles(self) -> list[tuple[datetime, float]]:
        return list(self.history)

    def normalized(self, m: _SimMarket) -> NormalizedMarket:
        now = self.clock()
        status = MarketStatus.RESOLVED if m.result else MarketStatus.OPEN
        bk = self.book(m, now) if not m.result else None
        rules = (f"Resolves YES if the simulated SIMBTC spot at {m.expiry.isoformat()} is strictly above {m.strike}."
                 if m.kind == "crypto" else
                 f"Synthetic event; resolves by a seeded Bernoulli draw at {m.expiry.isoformat()}. Simulated forecaster published.")
        return NormalizedMarket(
            venue=Venue.SIM, venue_market_id=m.mid, question=m.question, category=m.category, status=status,
            close_time=m.expiry, rules=rules, resolution_source="ZigGgy simulator", event_id=m.event_id,
            outcome_label=m.label, result=m.result, settlement_value=(1.0 if m.result is Outcome.YES else 0.0) if m.result else None,
            tick_size=0.01, min_order_size=1, volume=m.depth_scale * 50, volume_24h=m.depth_scale * 20,
            liquidity=m.depth_scale * 10, best_bid=bk.best_bid() if bk else None, best_ask=bk.best_ask() if bk else None,
            fee_model=FeeModel(kind="quadratic", rate=0.07, round_up_cents=True),
            strike={"strike_type": "greater", "floor_strike": m.strike, "underlying": "SIMBTC"} if m.kind == "crypto" else {},
            is_simulated=True,
            meta={"sim_kind": m.kind, "expiry": m.expiry.isoformat()},
        )


class SimMarketData(MarketDataAdapter):
    venue = Venue.SIM.value
    is_simulated = True
    capabilities = Capabilities(discover_markets=True, market_metadata=True, orderbook=True, batch_orderbook=True,
                                notes=["SIMULATED venue for paper trading and testing. Never traded live."])

    def __init__(self, world: SimWorld, state_path: Path | None = None) -> None:
        super().__init__()
        self.world = world
        self.state_path = state_path
        self.requests = 0

    def persist(self) -> None:
        if self.state_path:
            try:
                self.world.save(self.state_path)
            except OSError:
                pass

    async def discover(self) -> list[NormalizedMarket]:
        self.requests += 1
        self.world.advance()
        self.persist()
        return [self.world.normalized(m) for m in self.world.markets.values()]

    async def close(self) -> None:
        self.persist()

    async def fetch_books(self, markets: list[NormalizedMarket]) -> dict[str, OrderBook]:
        self.requests += 1
        self.world.advance()
        now = self.world.clock()
        out = {}
        for nm in markets:
            m = self.world.markets.get(nm.venue_market_id)
            if m and m.result is None:
                out[nm.key] = self.world.book(m, now)
        return out

    async def refresh_market(self, market: NormalizedMarket) -> NormalizedMarket | None:
        self.requests += 1
        self.world.advance()
        m = self.world.markets.get(market.venue_market_id)
        return self.world.normalized(m) if m else None

    def stats(self) -> dict:
        return {"requests": self.requests, "errors": 0, "rate_limited": 0, "last_ok": utcnow().isoformat(),
                "last_error": None, "last_error_at": None, "consecutive_failures": 0}
