"""Venue-independent domain types shared by every subsystem.

Prices are expressed as probabilities in [0, 1] (USD per contract paying $1).
Every binary market is represented by its YES order book; the NO side is derived as the
complement (no_ask = 1 - yes_bid, no_bid = 1 - yes_ask), which is how both Kalshi
(reciprocal books) and Polymarket (complementary-token matching) operate.
"""
from __future__ import annotations

import enum
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def as_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


class Venue(str, enum.Enum):
    POLYMARKET = "polymarket"
    KALSHI = "kalshi"
    SIM = "sim"


class Outcome(str, enum.Enum):
    YES = "YES"
    NO = "NO"

    @property
    def other(self) -> "Outcome":
        return Outcome.NO if self is Outcome.YES else Outcome.YES


class Action(str, enum.Enum):
    BUY = "BUY"
    SELL = "SELL"


class TradingMode(str, enum.Enum):
    STOPPED = "STOPPED"
    PAPER = "PAPER"
    LIVE = "LIVE"


class MarketStatus(str, enum.Enum):
    OPEN = "open"
    CLOSED = "closed"  # trading halted, awaiting resolution
    RESOLVED = "resolved"
    UNKNOWN = "unknown"


class OrderStatus(str, enum.Enum):
    PENDING_SUBMIT = "PENDING_SUBMIT"  # written ahead of the network call
    OPEN = "OPEN"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELED = "CANCELED"
    EXPIRED = "EXPIRED"
    REJECTED = "REJECTED"
    UNKNOWN = "UNKNOWN"  # submission outcome uncertain -> must reconcile before any retry

    @property
    def is_terminal(self) -> bool:
        return self in (OrderStatus.FILLED, OrderStatus.CANCELED, OrderStatus.EXPIRED, OrderStatus.REJECTED)

    @property
    def is_working(self) -> bool:
        return self in (OrderStatus.PENDING_SUBMIT, OrderStatus.OPEN, OrderStatus.PARTIALLY_FILLED, OrderStatus.UNKNOWN)


@dataclass(frozen=True)
class BookLevel:
    price: float
    size: float  # contracts


@dataclass
class OrderBook:
    """YES-side book. bids sorted best (highest) first, asks best (lowest) first."""

    yes_bids: list[BookLevel]
    yes_asks: list[BookLevel]
    ts: datetime
    source: str  # e.g. "kalshi:rest", "sim"
    is_simulated: bool = False

    def __post_init__(self) -> None:
        self.yes_bids = sorted([l for l in self.yes_bids if l.size > 0 and 0 < l.price < 1], key=lambda l: -l.price)
        self.yes_asks = sorted([l for l in self.yes_asks if l.size > 0 and 0 < l.price < 1], key=lambda l: l.price)

    def bids(self, outcome: Outcome) -> list[BookLevel]:
        if outcome is Outcome.YES:
            return self.yes_bids
        return [BookLevel(round(1 - l.price, 6), l.size) for l in self.yes_asks]

    def asks(self, outcome: Outcome) -> list[BookLevel]:
        if outcome is Outcome.YES:
            return self.yes_asks
        return [BookLevel(round(1 - l.price, 6), l.size) for l in self.yes_bids]

    def best_bid(self, outcome: Outcome = Outcome.YES) -> float | None:
        b = self.bids(outcome)
        return b[0].price if b else None

    def best_ask(self, outcome: Outcome = Outcome.YES) -> float | None:
        a = self.asks(outcome)
        return a[0].price if a else None

    @property
    def mid(self) -> float | None:
        bb, ba = self.best_bid(), self.best_ask()
        if bb is None or ba is None:
            return None
        return (bb + ba) / 2

    @property
    def spread(self) -> float | None:
        bb, ba = self.best_bid(), self.best_ask()
        if bb is None or ba is None:
            return None
        return ba - bb

    def depth_usd(self, outcome: Outcome, side: str, within: float = 0.05) -> float:
        """Dollar notional available within ``within`` of the best price on one side."""
        levels = self.asks(outcome) if side == "ask" else self.bids(outcome)
        if not levels:
            return 0.0
        best = levels[0].price
        tot = 0.0
        for l in levels:
            if abs(l.price - best) > within + 1e-9:
                break
            tot += l.price * l.size
        return tot

    def age_s(self, now: datetime | None = None) -> float:
        return ((now or utcnow()) - as_utc(self.ts)).total_seconds()

    def to_json(self) -> dict[str, Any]:
        return {
            "yes_bids": [[l.price, l.size] for l in self.yes_bids],
            "yes_asks": [[l.price, l.size] for l in self.yes_asks],
            "ts": as_utc(self.ts).isoformat(),
            "source": self.source,
            "is_simulated": self.is_simulated,
        }

    @classmethod
    def from_json(cls, d: dict[str, Any]) -> "OrderBook":
        return cls(
            yes_bids=[BookLevel(float(p), float(s)) for p, s in d.get("yes_bids", [])],
            yes_asks=[BookLevel(float(p), float(s)) for p, s in d.get("yes_asks", [])],
            ts=datetime.fromisoformat(d["ts"]),
            source=d.get("source", "unknown"),
            is_simulated=bool(d.get("is_simulated", False)),
        )


@dataclass(frozen=True)
class FeeModel:
    """Per-contract trading fee.

    kinds:
      * ``none``            - no fee
      * ``quadratic``       - fee = rate * p * (1 - p)   (Kalshi taker schedule, rate ~0.07)
      * ``polymarket``      - fee = rate * (p * (1 - p)) ** exponent (Polymarket feeSchedule)
      * ``flat``            - fee = rate (USD per contract)
    Rounding of the order total (e.g. Kalshi rounds up to the cent) is applied by ``order_fee``.
    """

    kind: str = "none"
    rate: float = 0.0
    exponent: float = 1.0
    round_up_cents: bool = False

    def per_contract(self, price: float) -> float:
        p = min(max(price, 0.0), 1.0)
        if self.kind == "quadratic":
            return self.rate * p * (1 - p)
        if self.kind == "polymarket":
            return self.rate * (p * (1 - p)) ** self.exponent
        if self.kind == "flat":
            return self.rate
        return 0.0

    def order_fee(self, price: float, contracts: float) -> float:
        raw = self.per_contract(price) * contracts
        if self.round_up_cents and raw > 0:
            import math

            return math.ceil(round(raw * 100, 6)) / 100
        return raw

    def to_json(self) -> dict[str, Any]:
        return {"kind": self.kind, "rate": self.rate, "exponent": self.exponent, "round_up_cents": self.round_up_cents}

    @classmethod
    def from_json(cls, d: dict[str, Any] | None) -> "FeeModel":
        if not d:
            return cls()
        return cls(kind=d.get("kind", "none"), rate=float(d.get("rate", 0)), exponent=float(d.get("exponent", 1)),
                   round_up_cents=bool(d.get("round_up_cents", False)))


@dataclass
class NormalizedMarket:
    venue: Venue
    venue_market_id: str
    question: str
    category: str
    status: MarketStatus
    close_time: datetime | None
    rules: str = ""
    resolution_source: str = ""
    event_id: str | None = None
    outcome_label: str | None = None  # bucket label within a multi-outcome event
    result: Outcome | None = None
    settlement_value: float | None = None  # payout per YES contract when resolved
    yes_token_id: str | None = None
    no_token_id: str | None = None
    tick_size: float = 0.01
    min_order_size: float = 1.0
    volume: float = 0.0
    volume_24h: float = 0.0
    liquidity: float = 0.0
    best_bid: float | None = None
    best_ask: float | None = None
    last_price: float | None = None
    fee_model: FeeModel = field(default_factory=FeeModel)
    strike: dict[str, Any] = field(default_factory=dict)
    mutually_exclusive: bool = False
    is_simulated: bool = False
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def key(self) -> str:
        return f"{self.venue.value}:{self.venue_market_id}"


@dataclass
class VenueOrderAck:
    venue_order_id: str | None
    status: OrderStatus
    filled_qty: float = 0.0
    avg_fill_price: float | None = None
    fees: float = 0.0
    raw: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


@dataclass
class VenueFill:
    venue_fill_id: str
    venue_order_id: str | None
    client_order_id: str | None
    market_id: str
    outcome: Outcome
    action: Action
    qty: float
    price: float
    fee: float
    ts: datetime


@dataclass
class VenuePosition:
    market_id: str
    outcome: Outcome
    contracts: float
    cost_basis: float | None = None
