"""Persistent schema.

Monetary amounts and contract quantities use the ``Money`` type: stored as integer
micro-units (1e-6) so values at rest are exact and identical on SQLite and PostgreSQL.
All timestamps are timezone-aware UTC.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    TypeDecorator,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

MICRO = 1_000_000


class Money(TypeDecorator):
    impl = BigInteger
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        return int(round(float(value) * MICRO))

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        return value / MICRO


class UTCDateTime(TypeDecorator):
    """DateTime that always round-trips as aware UTC (SQLite drops tzinfo)."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    type_annotation_map = {dict: JSON, list: JSON}


class Market(Base):
    __tablename__ = "markets"
    key: Mapped[str] = mapped_column(String(160), primary_key=True)
    venue: Mapped[str] = mapped_column(String(24), index=True)
    venue_market_id: Mapped[str] = mapped_column(String(160))
    event_id: Mapped[str | None] = mapped_column(String(160), index=True)
    question: Mapped[str] = mapped_column(Text)
    outcome_label: Mapped[str | None] = mapped_column(String(200))
    category: Mapped[str] = mapped_column(String(32), index=True)
    rules: Mapped[str] = mapped_column(Text, default="")
    resolution_source: Mapped[str] = mapped_column(Text, default="")
    close_time: Mapped[datetime | None] = mapped_column(UTCDateTime)
    status: Mapped[str] = mapped_column(String(16), index=True)
    result: Mapped[str | None] = mapped_column(String(8))
    settlement_value: Mapped[float | None] = mapped_column(Float)
    yes_token_id: Mapped[str | None] = mapped_column(String(100))
    no_token_id: Mapped[str | None] = mapped_column(String(100))
    tick_size: Mapped[float] = mapped_column(Float, default=0.01)
    min_order_size: Mapped[float] = mapped_column(Float, default=1.0)
    volume: Mapped[float] = mapped_column(Float, default=0.0)
    volume_24h: Mapped[float] = mapped_column(Float, default=0.0)
    liquidity: Mapped[float] = mapped_column(Float, default=0.0)
    best_bid: Mapped[float | None] = mapped_column(Float)
    best_ask: Mapped[float | None] = mapped_column(Float)
    last_price: Mapped[float | None] = mapped_column(Float)
    fee_model: Mapped[dict] = mapped_column(JSON, default=dict)
    strike: Mapped[dict] = mapped_column(JSON, default=dict)
    meta: Mapped[dict] = mapped_column(JSON, default=dict)
    flags: Mapped[list] = mapped_column(JSON, default=list)
    mutually_exclusive: Mapped[bool] = mapped_column(Boolean, default=False)
    is_simulated: Mapped[bool] = mapped_column(Boolean, default=False)
    eligible: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    first_seen: Mapped[datetime] = mapped_column(UTCDateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=_now)
    last_book_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    resolved_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


class PriceObservation(Base):
    __tablename__ = "price_observations"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    market_key: Mapped[str] = mapped_column(String(160), index=True)
    ts: Mapped[datetime] = mapped_column(UTCDateTime, index=True)
    best_bid: Mapped[float | None] = mapped_column(Float)
    best_ask: Mapped[float | None] = mapped_column(Float)
    mid: Mapped[float | None] = mapped_column(Float)
    spread: Mapped[float | None] = mapped_column(Float)
    bid_depth_usd: Mapped[float] = mapped_column(Float, default=0.0)
    ask_depth_usd: Mapped[float] = mapped_column(Float, default=0.0)
    source: Mapped[str] = mapped_column(String(40))
    is_simulated: Mapped[bool] = mapped_column(Boolean, default=False)


class OrderBookSnapshot(Base):
    __tablename__ = "orderbook_snapshots"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    market_key: Mapped[str] = mapped_column(String(160), index=True)
    ts: Mapped[datetime] = mapped_column(UTCDateTime, index=True)
    book: Mapped[dict] = mapped_column(JSON)
    source: Mapped[str] = mapped_column(String(40))
    is_simulated: Mapped[bool] = mapped_column(Boolean, default=False)


class Evidence(Base):
    __tablename__ = "evidence"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    market_key: Mapped[str | None] = mapped_column(String(160), index=True)
    kind: Mapped[str] = mapped_column(String(40), index=True)  # crypto_spot, weather_forecast, llm_review...
    subject: Mapped[str] = mapped_column(String(120), index=True)  # e.g. BTC-USD, 40.78,-73.97:2026-10-21
    source: Mapped[str] = mapped_column(String(80))
    source_url: Mapped[str] = mapped_column(Text, default="")
    observed_at: Mapped[datetime] = mapped_column(UTCDateTime, index=True)
    fetched_at: Mapped[datetime] = mapped_column(UTCDateTime, default=_now)
    reliability: Mapped[str] = mapped_column(String(16), default="medium")
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    summary: Mapped[str] = mapped_column(Text, default="")
    is_simulated: Mapped[bool] = mapped_column(Boolean, default=False)


class ProbabilityEstimate(Base):
    __tablename__ = "probability_estimates"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    market_key: Mapped[str] = mapped_column(String(160), index=True)
    ts: Mapped[datetime] = mapped_column(UTCDateTime, index=True, default=_now)
    model: Mapped[str] = mapped_column(String(60))
    model_version: Mapped[str] = mapped_column(String(20))
    q_yes: Mapped[float | None] = mapped_column(Float)
    sigma: Mapped[float | None] = mapped_column(Float)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    market_mid: Mapped[float | None] = mapped_column(Float)
    abstained: Mapped[bool] = mapped_column(Boolean, default=False)
    reason: Mapped[str] = mapped_column(Text, default="")
    components: Mapped[list] = mapped_column(JSON, default=list)
    evidence_ids: Mapped[list] = mapped_column(JSON, default=list)
    supporting: Mapped[list] = mapped_column(JSON, default=list)
    conflicting: Mapped[list] = mapped_column(JSON, default=list)
    snapshot_id: Mapped[int | None] = mapped_column(Integer)


class Opportunity(Base):
    __tablename__ = "opportunities"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    cycle_id: Mapped[str] = mapped_column(String(40), index=True)
    ts: Mapped[datetime] = mapped_column(UTCDateTime, index=True, default=_now)
    market_key: Mapped[str] = mapped_column(String(160), index=True)
    venue: Mapped[str] = mapped_column(String(24))
    category: Mapped[str] = mapped_column(String(32))
    kind: Mapped[str] = mapped_column(String(24), default="directional")  # directional | exit | structural_arb | cross_venue
    outcome: Mapped[str] = mapped_column(String(4))
    action: Mapped[str] = mapped_column(String(4))
    fair_value: Mapped[float | None] = mapped_column(Float)
    best_price: Mapped[float | None] = mapped_column(Float)
    entry_price: Mapped[float | None] = mapped_column(Float)  # executable VWAP for proposed size
    exit_value: Mapped[float | None] = mapped_column(Float)  # expected settlement value per contract
    gross_edge: Mapped[float | None] = mapped_column(Float)
    fee_per_contract: Mapped[float | None] = mapped_column(Float)
    slippage_per_contract: Mapped[float | None] = mapped_column(Float)
    net_ev_per_contract: Mapped[float | None] = mapped_column(Float)
    est_return: Mapped[float | None] = mapped_column(Float)
    confidence: Mapped[float | None] = mapped_column(Float)
    evidence_quality: Mapped[str] = mapped_column(String(16), default="none")
    liquidity_usd: Mapped[float | None] = mapped_column(Float)
    max_contracts: Mapped[float | None] = mapped_column(Float)
    score: Mapped[float | None] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(16), index=True)  # qualified | rejected | approved | vetoed | executed | expired
    reasons: Mapped[list] = mapped_column(JSON, default=list)
    risk_flags: Mapped[list] = mapped_column(JSON, default=list)
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    estimate_id: Mapped[int | None] = mapped_column(Integer)
    snapshot_id: Mapped[int | None] = mapped_column(Integer)
    expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    mode: Mapped[str] = mapped_column(String(8), default="PAPER")


class AgentDecision(Base):
    __tablename__ = "agent_decisions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(UTCDateTime, index=True, default=_now)
    agent: Mapped[str] = mapped_column(String(16), index=True)
    cycle_id: Mapped[str | None] = mapped_column(String(40), index=True)
    market_key: Mapped[str | None] = mapped_column(String(160))
    decision: Mapped[str] = mapped_column(String(40))
    details: Mapped[dict] = mapped_column(JSON, default=dict)


class RiskAssessment(Base):
    __tablename__ = "risk_assessments"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(UTCDateTime, default=_now)
    opportunity_id: Mapped[str] = mapped_column(String(40), index=True)
    account: Mapped[str] = mapped_column(String(40))
    approved: Mapped[bool] = mapped_column(Boolean)
    contracts: Mapped[float] = mapped_column(Money, default=0)
    stake_usd: Mapped[float] = mapped_column(Money, default=0)
    kelly_raw: Mapped[float | None] = mapped_column(Float)
    kelly_applied: Mapped[float | None] = mapped_column(Float)
    checks: Mapped[list] = mapped_column(JSON, default=list)
    reasons: Mapped[list] = mapped_column(JSON, default=list)
    limits: Mapped[dict] = mapped_column(JSON, default=dict)


class Account(Base):
    __tablename__ = "accounts"
    name: Mapped[str] = mapped_column(String(40), primary_key=True)  # "paper" | "live:kalshi" | "live:polymarket"
    mode: Mapped[str] = mapped_column(String(8))
    starting_capital: Mapped[float] = mapped_column(Money)
    cash: Mapped[float] = mapped_column(Money)
    peak_equity: Mapped[float] = mapped_column(Money)
    day_start_equity: Mapped[float] = mapped_column(Money)
    day_start_date: Mapped[str] = mapped_column(String(10))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=_now)
    last_reconciled_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


class Order(Base):
    __tablename__ = "orders"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    client_order_id: Mapped[str] = mapped_column(String(64), unique=True)
    account: Mapped[str] = mapped_column(String(40), index=True)
    mode: Mapped[str] = mapped_column(String(8))
    venue: Mapped[str] = mapped_column(String(24))
    market_key: Mapped[str] = mapped_column(String(160), index=True)
    outcome: Mapped[str] = mapped_column(String(4))
    action: Mapped[str] = mapped_column(String(4))
    order_type: Mapped[str] = mapped_column(String(12), default="limit")
    limit_price: Mapped[float] = mapped_column(Float)
    quantity: Mapped[float] = mapped_column(Money)
    filled_qty: Mapped[float] = mapped_column(Money, default=0)
    avg_fill_price: Mapped[float | None] = mapped_column(Float)
    fees: Mapped[float] = mapped_column(Money, default=0)
    reserved: Mapped[float] = mapped_column(Money, default=0)
    status: Mapped[str] = mapped_column(String(20), index=True)
    venue_order_id: Mapped[str | None] = mapped_column(String(120), index=True)
    opportunity_id: Mapped[str | None] = mapped_column(String(40))
    risk_assessment_id: Mapped[int | None] = mapped_column(Integer)
    estimate_id: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=_now)
    submitted_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    submit_attempts: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text)
    raw: Mapped[dict] = mapped_column(JSON, default=dict)
    is_simulated: Mapped[bool] = mapped_column(Boolean, default=True)


class Fill(Base):
    __tablename__ = "fills"
    __table_args__ = (UniqueConstraint("account", "venue_fill_id", name="uq_fill_account_venue_id"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"), index=True)
    account: Mapped[str] = mapped_column(String(40), index=True)
    market_key: Mapped[str] = mapped_column(String(160), index=True)
    outcome: Mapped[str] = mapped_column(String(4))
    action: Mapped[str] = mapped_column(String(4))
    qty: Mapped[float] = mapped_column(Money)
    price: Mapped[float] = mapped_column(Float)
    fee: Mapped[float] = mapped_column(Money, default=0)
    slippage: Mapped[float] = mapped_column(Money, default=0)  # vs best price at decision time
    ts: Mapped[datetime] = mapped_column(UTCDateTime, default=_now, index=True)
    venue_fill_id: Mapped[str] = mapped_column(String(120))
    is_simulated: Mapped[bool] = mapped_column(Boolean, default=True)


class Position(Base):
    __tablename__ = "positions"
    __table_args__ = (Index("ix_pos_account_market_outcome", "account", "market_key", "outcome"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    account: Mapped[str] = mapped_column(String(40), index=True)
    market_key: Mapped[str] = mapped_column(String(160))
    outcome: Mapped[str] = mapped_column(String(4))
    contracts: Mapped[float] = mapped_column(Money, default=0)
    cost_basis: Mapped[float] = mapped_column(Money, default=0)  # includes entry fees
    realized_pnl: Mapped[float] = mapped_column(Money, default=0)
    fees_paid: Mapped[float] = mapped_column(Money, default=0)
    status: Mapped[str] = mapped_column(String(10), default="open", index=True)  # open | closed | settled
    opened_at: Mapped[datetime] = mapped_column(UTCDateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=_now)
    closed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


class Settlement(Base):
    __tablename__ = "settlements"
    __table_args__ = (UniqueConstraint("account", "position_id", name="uq_settlement_position"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    account: Mapped[str] = mapped_column(String(40), index=True)
    position_id: Mapped[int] = mapped_column(ForeignKey("positions.id"))
    market_key: Mapped[str] = mapped_column(String(160), index=True)
    outcome: Mapped[str] = mapped_column(String(4))
    result: Mapped[str] = mapped_column(String(8))
    contracts: Mapped[float] = mapped_column(Money)
    payout_per_contract: Mapped[float] = mapped_column(Float)
    payout: Mapped[float] = mapped_column(Money)
    cost_basis: Mapped[float] = mapped_column(Money)
    realized_pnl: Mapped[float] = mapped_column(Money)
    ts: Mapped[datetime] = mapped_column(UTCDateTime, default=_now)


class AccountSnapshot(Base):
    __tablename__ = "account_snapshots"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    account: Mapped[str] = mapped_column(String(40), index=True)
    ts: Mapped[datetime] = mapped_column(UTCDateTime, index=True, default=_now)
    cash: Mapped[float] = mapped_column(Money)
    reserved: Mapped[float] = mapped_column(Money)
    positions_value: Mapped[float] = mapped_column(Money)
    equity: Mapped[float] = mapped_column(Money)
    realized_pnl: Mapped[float] = mapped_column(Money)
    unrealized_pnl: Mapped[float] = mapped_column(Money)
    fees_paid: Mapped[float] = mapped_column(Money, default=0)
    exposure: Mapped[float] = mapped_column(Money)
    peak_equity: Mapped[float] = mapped_column(Money)
    drawdown: Mapped[float] = mapped_column(Float)


class AppEvent(Base):
    __tablename__ = "events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(UTCDateTime, index=True, default=_now)
    type: Mapped[str] = mapped_column(String(40), index=True)
    severity: Mapped[str] = mapped_column(String(10), default="info")
    agent: Mapped[str | None] = mapped_column(String(16))
    market_key: Mapped[str | None] = mapped_column(String(160))
    mode: Mapped[str | None] = mapped_column(String(8))
    message: Mapped[str] = mapped_column(Text)
    data: Mapped[dict] = mapped_column(JSON, default=dict)


class SettingKV(Base):
    __tablename__ = "settings_kv"
    key: Mapped[str] = mapped_column(String(60), primary_key=True)
    value: Mapped[dict] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=_now)


class ConfigHistory(Base):
    __tablename__ = "config_history"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(UTCDateTime, default=_now)
    key: Mapped[str] = mapped_column(String(60))
    old_value: Mapped[dict | None] = mapped_column(JSON)
    new_value: Mapped[dict | None] = mapped_column(JSON)
    actor: Mapped[str] = mapped_column(String(40), default="api")


class BacktestRun(Base):
    __tablename__ = "backtest_runs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=_now)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    status: Mapped[str] = mapped_column(String(12), default="running")
    params: Mapped[dict] = mapped_column(JSON, default=dict)
    metrics: Mapped[dict] = mapped_column(JSON, default=dict)
    trades: Mapped[list] = mapped_column(JSON, default=list)
    equity_curve: Mapped[list] = mapped_column(JSON, default=list)
    notes: Mapped[list] = mapped_column(JSON, default=list)


class ModelMetric(Base):
    __tablename__ = "model_metrics"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(UTCDateTime, default=_now)
    model: Mapped[str] = mapped_column(String(60))
    category: Mapped[str] = mapped_column(String(32))
    n: Mapped[int] = mapped_column(Integer)
    brier: Mapped[float | None] = mapped_column(Float)
    log_loss: Mapped[float | None] = mapped_column(Float)
    market_brier: Mapped[float | None] = mapped_column(Float)
    reliability: Mapped[list] = mapped_column(JSON, default=list)


class MarketLink(Base):
    __tablename__ = "market_links"
    __table_args__ = (UniqueConstraint("market_a", "market_b", name="uq_market_link"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    market_a: Mapped[str] = mapped_column(String(160), index=True)
    market_b: Mapped[str] = mapped_column(String(160), index=True)
    relation: Mapped[str] = mapped_column(String(16), default="equivalent")
    score: Mapped[float] = mapped_column(Float, default=0.0)
    status: Mapped[str] = mapped_column(String(12), default="proposed")  # proposed | confirmed | rejected
    reason: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=_now)


class InstanceLock(Base):
    __tablename__ = "instance_lock"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner: Mapped[str] = mapped_column(String(80))
    heartbeat_at: Mapped[datetime] = mapped_column(UTCDateTime)
