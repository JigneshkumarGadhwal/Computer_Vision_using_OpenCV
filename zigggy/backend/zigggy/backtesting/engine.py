"""Event-driven historical backtest over data ZigGgy actually recorded (or imported).

Guarantees
  * Point-in-time: at simulated time t only order-book snapshots, evidence and resolutions with
    timestamps <= t are visible. Calibration tables are rebuilt only from markets resolved
    before t, using prices observed >= 1h before their resolution.
  * No survivorship filter: every recorded market in the window is replayed, including ones
    that were voided or never resolved (left open & marked to market, clearly reported).
  * Fills: taker fills against the recorded book up to the limit price, with the venue fee
    model and the configured slippage buffer - the same code paths as paper trading.
  * Never fabricates prices: if no data exists, the run reports that and produces no trades.
"""
from __future__ import annotations

import bisect
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select

from ..config import RiskLimits
from ..database.models import BacktestRun, Evidence, Market, OrderBookSnapshot
from ..database.session import Database
from ..domain import Action, OrderBook, Outcome, TradingMode, as_utc, utcnow
from ..market_data.service import row_to_market
from ..portfolio.service import PortfolioState
from ..probability.base import EvidenceItem, ModelContext
from ..probability.calibration import brier, calibration_table, log_loss
from ..probability.engine import ProbabilityEngine
from ..risk.engine import RiskContext, RiskEngine
from ..spread_engine.opportunity import evaluate_entry, evaluate_exit, rank

log = logging.getLogger("zigggy.backtest")


@dataclass
class BTPosition:
    contracts: float = 0.0
    cost: float = 0.0


@dataclass
class BTState:
    cash: float
    start: float
    positions: dict[tuple[str, str], BTPosition] = field(default_factory=dict)
    peak: float = 0.0
    day_start: float = 0.0
    day: str = ""
    fees: float = 0.0
    slippage: float = 0.0
    trades: list[dict[str, Any]] = field(default_factory=list)
    closed: list[dict[str, Any]] = field(default_factory=list)


class BacktestEngine:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def run(self, params: dict[str, Any]) -> int:
        async with self.db.tx() as s:
            run = BacktestRun(params=params, status="running")
            s.add(run)
            await s.flush()
            run_id = run.id
        try:
            metrics, trades, curve, notes = await self._simulate(params)
            status = "completed"
        except Exception as e:  # report failures on the run record
            log.exception("backtest failed")
            metrics, trades, curve, notes, status = {"error": str(e)}, [], [], [f"failed: {e}"], "failed"
        async with self.db.tx() as s:
            r = await s.get(BacktestRun, run_id)
            r.status, r.metrics, r.trades, r.equity_curve, r.notes, r.finished_at = status, metrics, trades[:2000], curve, notes, utcnow()
        return run_id

    async def _simulate(self, p: dict[str, Any]) -> tuple[dict, list, list, list]:
        limits = RiskLimits.model_validate(p.get("risk") or {})
        capital = float(p.get("starting_capital", 10_000))
        step = timedelta(seconds=int(p.get("step_s", 300)))
        cats = set(p.get("categories") or [])
        include_sim = bool(p.get("include_simulated", True))
        notes: list[str] = []
        async with self.db.session() as s:
            q = select(OrderBookSnapshot).order_by(OrderBookSnapshot.ts)
            if p.get("start"):
                q = q.where(OrderBookSnapshot.ts >= datetime.fromisoformat(p["start"]))
            if p.get("end"):
                q = q.where(OrderBookSnapshot.ts <= datetime.fromisoformat(p["end"]))
            if not include_sim:
                q = q.where(OrderBookSnapshot.is_simulated.is_(False))
            snaps = (await s.execute(q)).scalars().all()
            keys = {x.market_key for x in snaps}
            mrows = {m.key: m for m in (await s.execute(select(Market).where(Market.key.in_(list(keys))))).scalars()} if keys else {}
            if snaps:
                ev_rows = (await s.execute(select(Evidence).where(Evidence.observed_at <= snaps[-1].ts,
                                                                  Evidence.observed_at >= snaps[0].ts - timedelta(days=2))
                                           .order_by(Evidence.observed_at))).scalars().all()
            else:
                ev_rows = []
        if cats:
            snaps = [x for x in snaps if x.market_key in mrows and mrows[x.market_key].category in cats]
        if not snaps:
            notes.append("No recorded order-book data in the selected window. Run ZigGgy (paper mode) to record data, or import "
                         "history with `zigggy import-history`. No results were fabricated.")
            return {"trades": 0}, [], [], notes
        sim_share = sum(1 for x in snaps if x.is_simulated) / len(snaps)
        if sim_share > 0:
            notes.append(f"{sim_share:.0%} of replayed snapshots come from the SIM venue (simulated data).")
        price_only = sum(1 for x in snaps if "price-only" in (x.source or ""))
        if price_only:
            notes.append(f"{price_only} snapshots are imported price-only history with an assumed spread/depth (see importer settings).")
        markets = {k: row_to_market(r) for k, r in mrows.items()}
        by_market: dict[str, list[tuple[datetime, OrderBook]]] = defaultdict(list)
        for x in snaps:
            by_market[x.market_key].append((as_utc(x.ts), OrderBook.from_json(x.book)))
        ts_index = {k: [t for t, _ in v] for k, v in by_market.items()}
        evidence = [EvidenceItem(e.kind, e.subject, e.source, e.source_url, as_utc(e.observed_at), e.reliability, e.payload,
                                 e.summary, e.is_simulated, e.id) for e in ev_rows]
        ev_times = [e.observed_at for e in evidence]
        resolutions = sorted([(as_utc(r.resolved_at or r.close_time), k) for k, r in mrows.items()
                              if r.result in ("YES", "NO") and (r.resolved_at or r.close_time)])
        engine = ProbabilityEngine()
        risk = RiskEngine(limits)
        st = BTState(cash=capital, start=capital, peak=capital, day_start=capital)
        t = as_utc(snaps[0].ts)
        t_end = as_utc(snaps[-1].ts)
        curve: list[dict[str, Any]] = []
        decisions: list[tuple[float, int, str, float | None]] = []  # (q, y, category, mid) for calibration
        resolved_seen: set[str] = set()
        calib_pairs: dict[str, list[tuple[float, int]]] = defaultdict(list)
        ri = 0
        last_entry: dict[str, datetime] = {}
        while t <= t_end + step:
            # 1) resolutions visible at t
            while ri < len(resolutions) and resolutions[ri][0] <= t:
                _, k = resolutions[ri]
                ri += 1
                if k in resolved_seen:
                    continue
                resolved_seen.add(k)
                m = mrows[k]
                y = 1 if m.result == "YES" else 0
                # calibration sample: last price >= 1h before resolution
                times = ts_index.get(k, [])
                j = bisect.bisect_right(times, resolutions[ri - 1][0] - timedelta(hours=1)) - 1
                if j >= 0 and by_market[k][j][1].mid is not None:
                    calib_pairs[m.category].append((by_market[k][j][1].mid, y))
                for o in (Outcome.YES, Outcome.NO):
                    pos = st.positions.pop((k, o.value), None)
                    if pos and pos.contracts > 0:
                        payout = pos.contracts * (y if o is Outcome.YES else 1 - y)
                        st.cash += payout
                        pnl = payout - pos.cost
                        st.closed.append({"market": k, "outcome": o.value, "pnl": pnl, "cost": pos.cost, "ts": t.isoformat(),
                                          "category": m.category, "how": "settled"})
            # 2) current books (latest snapshot <= t, fresh enough)
            books: dict[str, OrderBook] = {}
            for k, times in ts_index.items():
                j = bisect.bisect_right(times, t) - 1
                if j >= 0 and (t - times[j]).total_seconds() <= limits.max_staleness_s and k not in resolved_seen:
                    books[k] = by_market[k][j][1]
            # 3) evidence visible at t (latest per kind/subject within 2 days)
            e_hi = bisect.bisect_right(ev_times, t)
            latest: dict[tuple[str, str], EvidenceItem] = {}
            for e in evidence[:e_hi]:
                if (t - e.observed_at).total_seconds() <= 2 * 86400:
                    latest[(e.kind, e.subject)] = e
            by_kind: dict[str, list[EvidenceItem]] = defaultdict(list)
            for (kind, _), e in latest.items():
                by_kind[kind].append(e)
            calib = {c: calibration_table(v) for c, v in calib_pairs.items() if len(v) >= 40}
            pf = self._state(st, books, markets, limits)
            held = {(k, o): pos.contracts for (k, o), pos in st.positions.items()}
            cands = []
            for k, b in books.items():
                m = markets.get(k)
                if not m or (m.close_time and m.close_time <= t):
                    continue
                ctx = ModelContext(now=t, evidence=by_kind, linked=[], calibration=calib, prior_sigma_floor=limits.market_prior_sigma)
                est = engine.estimate(m, b, ctx)
                if not est.abstained and est.q is not None:
                    decisions.append((est.q, -1, m.category, b.mid, k))  # type: ignore[arg-type]
                for o in (Outcome.YES, Outcome.NO):
                    h = held.get((k, o.value), 0)
                    if h > 0 and limits.exit_on_edge_reversal:
                        c = evaluate_exit(m, b, est, o, h, limits, t)
                        if c:
                            cands.append(c)
                cands += evaluate_entry(m, b, est, limits, t)
            for c in rank(cands):
                if not c.qualified:
                    continue
                k = c.market.key
                recent = {kk for kk, ts_ in last_entry.items() if (t - ts_).total_seconds() < limits.reentry_cooldown_s}
                ctx = RiskContext(mode=TradingMode.PAPER, trading_allowed=True, trading_block_reason="ok", working_order_markets=set(),
                                  held=held, venue_tradable=True, recent_entries=recent)
                d = risk.assess(c, _AgedBook(books[k], t), pf, ctx)
                if not d.approved:
                    continue
                self._fill(st, c, d, books[k], t)
                if c.action is Action.BUY:
                    last_entry[k] = t
                pf = self._state(st, books, markets, limits)
                held = {(kk, o): pos.contracts for (kk, o), pos in st.positions.items()}
            eq = pf.equity
            st.peak = max(st.peak, eq)
            day = t.date().isoformat()
            if day != st.day:
                st.day, st.day_start = day, eq
            curve.append({"ts": t.isoformat(), "equity": round(eq, 4), "cash": round(st.cash, 4)})
            t += step
        # calibration of decisions on markets that resolved, using estimates made >= 1h before resolution
        res_map = {k: (ts_, 1 if mrows[k].result == "YES" else 0) for ts_, k in resolutions}
        model_pairs, mkt_pairs = [], []
        by_cat_pairs: dict[str, list] = defaultdict(list)
        last_by_market: dict[str, tuple[float, float | None]] = {}
        for q_, _, cat, mid, k in decisions:  # type: ignore[misc]
            last_by_market[k] = (q_, mid)
        for k, (q_, mid) in last_by_market.items():
            if k in res_map:
                y = res_map[k][1]
                model_pairs.append((q_, y))
                by_cat_pairs[markets[k].category].append((q_, y))
                if mid is not None:
                    mkt_pairs.append((mid, y))
        open_value = sum(pos.contracts * (self._mark(books_last(by_market, k), Outcome(o)) or 0) for (k, o), pos in st.positions.items())
        final_eq = st.cash + open_value
        pnls = [c["pnl"] for c in st.closed]
        wins = [x for x in pnls if x > 0]
        losses = [x for x in pnls if x < 0]
        mdd, peak = 0.0, None
        for pt in curve:
            peak = pt["equity"] if peak is None or pt["equity"] > peak else peak
            mdd = max(mdd, (peak - pt["equity"]) / peak if peak else 0)
        by_cat: dict[str, dict[str, float]] = defaultdict(lambda: {"trades": 0, "pnl": 0.0})
        for c in st.closed:
            by_cat[c["category"]]["trades"] += 1
            by_cat[c["category"]]["pnl"] += c["pnl"]
        if st.positions:
            notes.append(f"{len(st.positions)} position(s) still open at window end are marked to the last recorded bid (unrealized).")
        if not resolutions:
            notes.append("No market in the window has a recorded resolution: realized P&L and calibration are unavailable.")
        notes.append("Hypothetical results from historical observations with simulated fills; not a guarantee of future performance.")
        metrics = {
            "starting_capital": capital, "final_equity": round(final_eq, 4), "total_return": round(final_eq / capital - 1, 6),
            "net_pnl": round(final_eq - capital, 4), "realized_pnl": round(sum(pnls), 4), "unrealized_pnl": round(open_value - sum(p.cost for p in st.positions.values()), 4),
            "max_drawdown": round(mdd, 6), "trades": len(st.trades), "round_trips": len(pnls),
            "win_rate": (len(wins) / len(pnls)) if pnls else None,
            "profit_factor": (sum(wins) / -sum(losses)) if losses else None,
            "expectancy": (sum(pnls) / len(pnls)) if pnls else None,
            "fees": round(st.fees, 4), "slippage": round(st.slippage, 4),
            "brier_model": brier(model_pairs), "brier_market": brier(mkt_pairs), "log_loss_model": log_loss(model_pairs),
            "calibration_samples": len(model_pairs), "by_category": {k: dict(v) for k, v in by_cat.items()},
            "calibration_by_category": {k: {"n": len(v), "brier": brier(v)} for k, v in by_cat_pairs.items()},
            "snapshots": len(snaps), "markets": len(by_market), "window": [snaps[0].ts.isoformat(), snaps[-1].ts.isoformat()],
        }
        return metrics, st.trades, curve[:: max(1, len(curve) // 1500)], notes

    @staticmethod
    def _mark(book: OrderBook | None, o: Outcome) -> float | None:
        if not book:
            return None
        b = book.best_bid(o)
        if b is not None:
            return b
        return (book.mid if o is Outcome.YES else 1 - book.mid) if book.mid is not None else None

    def _state(self, st: BTState, books: dict[str, OrderBook], markets, limits: RiskLimits) -> PortfolioState:
        pv = 0.0
        by_m, by_e, by_c = {}, {}, {}
        exposure = 0.0
        for (k, o), pos in st.positions.items():
            mk = self._mark(books.get(k), Outcome(o))
            pv += pos.contracts * mk if mk is not None else pos.cost
            m = markets.get(k)
            by_m[k] = by_m.get(k, 0) + pos.cost
            ev = (m.event_id if m and m.event_id else k)
            by_e[ev] = by_e.get(ev, 0) + pos.cost
            cat = m.category if m else "other"
            by_c[cat] = by_c.get(cat, 0) + pos.cost
            exposure += pos.cost
        eq = st.cash + pv
        peak = max(st.peak, eq)
        return PortfolioState("backtest", "PAPER", st.start, st.cash, 0.0, st.cash, pv, eq, 0.0, 0.0, st.fees, exposure, peak,
                              (peak - eq) / peak if peak else 0.0, eq - (st.day_start or eq), 0, [], by_m, by_e, by_c)

    def _fill(self, st: BTState, c, d, book: OrderBook, t: datetime) -> None:
        k, o = c.market.key, c.outcome
        levels = book.asks(o) if c.action is Action.BUY else book.bids(o)
        remaining = d.contracts
        best = levels[0].price if levels else None
        for lvl in levels:
            if remaining <= 0:
                break
            if (c.action is Action.BUY and lvl.price > d.limit_price + 1e-9) or (c.action is Action.SELL and lvl.price < d.limit_price - 1e-9):
                break
            q = min(remaining, lvl.size)
            fee = c.market.fee_model.order_fee(lvl.price, q)
            pos = st.positions.setdefault((k, o.value), BTPosition())
            if c.action is Action.BUY:
                if st.cash < q * lvl.price + fee:
                    break
                st.cash -= q * lvl.price + fee
                pos.contracts += q
                pos.cost += q * lvl.price + fee
            else:
                avg = pos.cost / pos.contracts if pos.contracts else 0
                st.cash += q * lvl.price - fee
                pnl = q * lvl.price - fee - avg * q
                pos.contracts -= q
                pos.cost -= avg * q
                st.closed.append({"market": k, "outcome": o.value, "pnl": pnl, "cost": avg * q, "ts": t.isoformat(),
                                  "category": c.market.category, "how": "exit"})
                if pos.contracts <= 1e-9:
                    st.positions.pop((k, o.value), None)
            st.fees += fee
            st.slippage += abs(lvl.price - (best or lvl.price)) * q
            st.trades.append({"ts": t.isoformat(), "market": k, "question": c.market.question[:120], "outcome": o.value,
                              "action": c.action.value, "qty": q, "price": lvl.price, "fee": round(fee, 6), "fair": round(c.win_prob, 4),
                              "net_ev": round(c.net_ev, 4), "simulated_fill": True})
            remaining -= q


class _AgedBook:
    """Wrap a historical book so freshness is judged against simulated time, not wall-clock."""

    def __init__(self, book: OrderBook, now: datetime) -> None:
        self._b = book
        self._now = now

    def __getattr__(self, item):
        return getattr(self._b, item)

    def age_s(self, now: datetime | None = None) -> float:
        return self._b.age_s(self._now)


def books_last(by_market: dict[str, list[tuple[datetime, OrderBook]]], k: str) -> OrderBook | None:
    v = by_market.get(k)
    return v[-1][1] if v else None
