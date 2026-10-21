"""REST + SSE API consumed by the dashboard. Every control endpoint mutates real backend state."""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta
from typing import Any, Literal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import desc, func, or_, select

from ..backtesting.engine import BacktestEngine
from ..config import ALL_CATEGORIES, NotificationPrefs, RiskLimits, RuntimeSettings
from ..core import LIVE_CONFIRM_PHRASE, ControlError, Core
from ..database.models import (
    AgentDecision,
    AppEvent,
    BacktestRun,
    ConfigHistory,
    Evidence,
    Fill,
    Market,
    MarketLink,
    ModelMetric,
    Opportunity,
    Order,
    OrderBookSnapshot,
    PriceObservation,
    ProbabilityEstimate,
    RiskAssessment,
)
from ..domain import Outcome, TradingMode, utcnow
from .auth import check_auth

router = APIRouter(prefix="/api", dependencies=[Depends(check_auth)])
public = APIRouter(prefix="/api")


def core_of(request: Request) -> Core:
    return request.app.state.core


def row(o: Any, exclude: set[str] | None = None) -> dict[str, Any]:
    d = {}
    for c in o.__table__.columns:
        if exclude and c.key in exclude:
            continue
        v = getattr(o, c.key)
        d[c.key] = v.isoformat() if isinstance(v, datetime) else v
    return d


# --------------------------------------------------------------------- health
@public.get("/health")
async def health(request: Request) -> dict[str, Any]:
    core = core_of(request)
    db_ok = True
    try:
        await core.db.ping()
    except Exception:
        db_ok = False
    sch = core.scheduler.describe()
    return {"status": "ok" if db_ok and core.started else "degraded", "db": db_ok, "started": core.started,
            "scheduler_running": sch["running"], "mode": core.state.mode.value, "time": utcnow().isoformat(), "version": "1.0.0"}


@router.get("/health/details")
async def health_details(request: Request) -> dict[str, Any]:
    core = core_of(request)
    db_ok, db_err = True, None
    try:
        await core.db.ping()
    except Exception as e:
        db_ok, db_err = False, str(e)[:200]
    async with core.db.session() as s:
        errors = (await s.execute(select(AppEvent).where(AppEvent.severity.in_(["warning", "critical"]))
                                  .order_by(desc(AppEvent.ts)).limit(30))).scalars().all()
        n_events = (await s.execute(select(func.count()).select_from(AppEvent))).scalar_one()
    providers = core.registry.describe()
    rate_warnings = [f"{p['venue']}: {p['http']['rate_limited']} rate-limited responses" for p in providers if p["http"].get("rate_limited")]
    return {
        "backend": {"status": "ok", "instance": core.instance_id, "read_only": core.read_only, "started": core.started},
        "database": {"ok": db_ok, "error": db_err, "url_kind": core.db.url.split(":")[0], "events": n_events},
        "scheduler": core.scheduler.describe(),
        "providers": providers,
        "execution": {"stats": core.execution.stats, "live": core.execution.live_status,
                      "live_trading_enabled": core.settings.live_trading_enabled},
        "agents": [a.describe() for a in (core.orchestrator, core.rigo, core.bram, core.ilsa, core.tess, core.spread)],
        "notifications": core.notifier.describe(),
        "llm": ({"enabled": True, "model": core.llm.model, "available": core.llm.available, **core.llm.usage} if core.llm else {"enabled": False}),
        "recent_errors": [row(e) for e in errors],
        "rate_limit_warnings": rate_warnings,
        "last_sync": {p["venue"]: p["last_sync"] for p in providers},
    }


# ------------------------------------------------------------------- overview
@router.get("/overview")
async def overview(request: Request) -> dict[str, Any]:
    core = core_of(request)
    acct = core.state.account_name or "paper"
    pf = await core.portfolio.state(acct)
    perf = await core.portfolio.performance(acct)
    async with core.db.session() as s:
        q_count = (await s.execute(select(func.count()).select_from(Opportunity).where(
            Opportunity.status.in_(["qualified", "approved", "executed"]), Opportunity.ts >= utcnow() - timedelta(hours=24)))).scalar_one()
    st = core.markets.snapshot_stats()
    return {
        "mode": core.state.mode.value, "live_venue": core.state.live_venue, "account": acct,
        "emergency_stop": core.state.emergency_stop, "emergency_reason": core.state.emergency_reason,
        "trading_paused": core.state.runtime.trading_paused, "scanning_enabled": core.state.runtime.scanning_enabled,
        "read_only": core.read_only,
        "portfolio": pf.to_dict() if pf else None, "performance": perf,
        "scanner": {**st, "qualified_24h": q_count},
        "providers": [{"venue": p["venue"], "connected": p["connected"], "is_simulated": p["is_simulated"], "markets": p["markets"],
                       "last_sync": p["last_sync"], "last_error": p["last_error"]} for p in core.registry.describe()],
        "agents": [{"name": a.name, "status": a.status, "last_run": a.last_run.isoformat() if a.last_run else None, "errors": a.errors}
                   for a in (core.orchestrator, core.rigo, core.bram, core.ilsa, core.tess, core.spread)],
        "last_cycle": core.orchestrator.last_cycle,
        "server_time": utcnow().isoformat(),
    }


# --------------------------------------------------------------------- events
@router.get("/events")
async def events(request: Request, limit: int = Query(200, le=1000), type: str | None = None, before_id: int | None = None):
    core = core_of(request)
    async with core.db.session() as s:
        q = select(AppEvent).order_by(desc(AppEvent.id)).limit(limit)
        if type:
            q = q.where(AppEvent.type.in_(type.split(",")))
        if before_id:
            q = q.where(AppEvent.id < before_id)
        rows = (await s.execute(q)).scalars().all()
    persisted = [row(e) for e in rows]
    # include recent non-persisted (high-frequency) events from memory
    ids = {e["id"] for e in persisted}
    mem = [e for e in core.events.recent if "id" not in e or e["id"] not in ids] if not before_id and not type else []
    merged = sorted(persisted + mem, key=lambda e: e["ts"], reverse=True)[:limit]
    return merged


@router.get("/stream")
async def stream(request: Request):
    core = core_of(request)
    q = core.events.subscribe()

    async def gen():
        try:
            yield "retry: 3000\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    ev = await asyncio.wait_for(q.get(), timeout=15)
                    yield f"event: argona\ndata: {json.dumps(ev, default=str)}\n\n"
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            core.events.unsubscribe(q)

    return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# -------------------------------------------------------------------- markets
@router.get("/markets")
async def markets(request: Request, search: str | None = None, category: str | None = None, venue: str | None = None,
                  status: str | None = "open", eligible: bool | None = None, limit: int = Query(200, le=2000), offset: int = 0,
                  sort: str = "volume_24h"):
    core = core_of(request)
    async with core.db.session() as s:
        q = select(Market)
        if search:
            like = f"%{search}%"
            q = q.where(or_(Market.question.ilike(like), Market.key.ilike(like), Market.outcome_label.ilike(like)))
        if category:
            q = q.where(Market.category == category)
        if venue:
            q = q.where(Market.venue == venue)
        if status:
            q = q.where(Market.status == status)
        if eligible is not None:
            q = q.where(Market.eligible.is_(eligible))
        total = (await s.execute(select(func.count()).select_from(q.subquery()))).scalar_one()
        col = {"volume_24h": Market.volume_24h, "close_time": Market.close_time, "liquidity": Market.liquidity}.get(sort, Market.volume_24h)
        rows = (await s.execute(q.order_by(Market.last_book_at.is_(None), desc(col)).offset(offset).limit(limit))).scalars().all()
        keys = [r.key for r in rows]
        est_rows = []
        if keys:
            sub = (select(ProbabilityEstimate.market_key, func.max(ProbabilityEstimate.id).label("mid"))
                   .where(ProbabilityEstimate.market_key.in_(keys)).group_by(ProbabilityEstimate.market_key).subquery())
            est_rows = (await s.execute(select(ProbabilityEstimate).join(sub, ProbabilityEstimate.id == sub.c.mid))).scalars().all()
            sub2 = (select(Opportunity.market_key, func.max(Opportunity.ts).label("mts")).where(Opportunity.market_key.in_(keys))
                    .group_by(Opportunity.market_key).subquery())
            opp_rows = (await s.execute(select(Opportunity).join(sub2, (Opportunity.market_key == sub2.c.market_key) & (Opportunity.ts == sub2.c.mts)))).scalars().all()
        else:
            opp_rows = []
    ests = {e.market_key: e for e in est_rows}
    best_opp: dict[str, Opportunity] = {}
    for o in opp_rows:
        cur = best_opp.get(o.market_key)
        if cur is None or (o.net_ev_per_contract or -9) > (cur.net_ev_per_contract or -9):
            best_opp[o.market_key] = o
    out = []
    for r in rows:
        b = core.markets.books.get(r.key)
        e = ests.get(r.key)
        op = best_opp.get(r.key)
        bb = b.best_bid() if b else r.best_bid
        ba = b.best_ask() if b else r.best_ask
        out.append({
            "key": r.key, "venue": r.venue, "question": r.question, "outcome_label": r.outcome_label, "category": r.category,
            "status": r.status, "eligible": r.eligible, "flags": r.flags, "close_time": r.close_time.isoformat() if r.close_time else None,
            "yes_bid": bb, "yes_ask": ba, "no_bid": (1 - ba) if ba is not None else None, "no_ask": (1 - bb) if bb is not None else None,
            "spread": (ba - bb) if (ba is not None and bb is not None) else None,
            "fair_value": e.q_yes if e and not e.abstained else None, "confidence": e.confidence if e else None,
            "abstained": e.abstained if e else None, "estimate_reason": e.reason if e else None,
            "net_edge": op.net_ev_per_contract if op else None, "edge_side": op.outcome if op else None,
            "opportunity_status": op.status if op else None,
            "liquidity": (b.depth_usd(Outcome.YES, "bid") + b.depth_usd(Outcome.YES, "ask")) if b else r.liquidity,
            "volume_24h": r.volume_24h, "freshness_s": round(b.age_s(), 1) if b else None,
            "last_book_at": r.last_book_at.isoformat() if r.last_book_at else None,
            "is_simulated": r.is_simulated, "url": (r.meta or {}).get("url"),
        })
    return {"total": total, "items": out, "categories": ALL_CATEGORIES}


@router.get("/markets/{key:path}/detail")
async def market_detail(request: Request, key: str):
    core = core_of(request)
    async with core.db.session() as s:
        m = await s.get(Market, key)
        if not m:
            raise HTTPException(404, "market not found")
        ests = (await s.execute(select(ProbabilityEstimate).where(ProbabilityEstimate.market_key == key)
                                .order_by(desc(ProbabilityEstimate.id)).limit(50))).scalars().all()
        obs = (await s.execute(select(PriceObservation).where(PriceObservation.market_key == key)
                               .order_by(desc(PriceObservation.ts)).limit(500))).scalars().all()
        opps = (await s.execute(select(Opportunity).where(Opportunity.market_key == key).order_by(desc(Opportunity.ts)).limit(30))).scalars().all()
        ev_ids = {i for e in ests[:5] for i in (e.evidence_ids or [])}
        evid = (await s.execute(select(Evidence).where(Evidence.id.in_(list(ev_ids))))).scalars().all() if ev_ids else []
        links = (await s.execute(select(MarketLink).where(or_(MarketLink.market_a == key, MarketLink.market_b == key)))).scalars().all()
        last_snap = (await s.execute(select(OrderBookSnapshot).where(OrderBookSnapshot.market_key == key)
                                     .order_by(desc(OrderBookSnapshot.ts)).limit(1))).scalar_one_or_none()
    b = core.markets.books.get(key)
    book = b.to_json() if b else (last_snap.book if last_snap else None)
    return {"market": row(m), "book": book, "book_age_s": round(b.age_s(), 1) if b else None,
            "estimates": [row(e) for e in ests], "history": [row(o) for o in reversed(obs)],
            "opportunities": [row(o) for o in opps], "evidence": [row(e) for e in evid], "links": [row(l) for l in links]}


# --------------------------------------------------------------- opportunities
@router.get("/opportunities")
async def opportunities(request: Request, status: str | None = None, limit: int = Query(200, le=1000), hours: float = 24):
    core = core_of(request)
    async with core.db.session() as s:
        q = select(Opportunity, Market.question).join(Market, Market.key == Opportunity.market_key, isouter=True).where(
            Opportunity.ts >= utcnow() - timedelta(hours=hours))
        if status:
            q = q.where(Opportunity.status.in_(status.split(",")))
        rows = (await s.execute(q.order_by(desc(Opportunity.ts)).limit(limit))).all()
        ids = [o.id for o, _ in rows]
        ras = {r.opportunity_id: r for r in (await s.execute(select(RiskAssessment).where(RiskAssessment.opportunity_id.in_(ids)))).scalars()} if ids else {}
    return [{**row(o), "question": q, "risk": row(ras[o.id]) if o.id in ras else None} for o, q in rows]


@router.get("/spread")
async def spread(request: Request):
    core = core_of(request)
    rep = core.spread.last_report
    stats = []
    if rep:
        for k, st in rep.stats.items():
            m = core.markets.markets.get(k)
            stats.append({"key": k, "question": m.question if m else k, "venue": m.venue.value if m else None,
                          "is_simulated": m.is_simulated if m else False, **st.to_dict(),
                          "history": core.markets.spread_history.get(k, [])[-60:]})
    stats.sort(key=lambda x: (x["spread"] is None, -(x["spread"] or 0)))
    async with core.db.session() as s:
        links = (await s.execute(select(MarketLink).order_by(desc(MarketLink.score)).limit(200))).scalars().all()
    comparisons = []
    for l in links:
        a, b = core.markets.books.get(l.market_a), core.markets.books.get(l.market_b)
        comparisons.append({**row(l), "a_mid": a.mid if a else None, "b_mid": b.mid if b else None,
                            "a_question": core.markets.markets[l.market_a].question if l.market_a in core.markets.markets else l.market_a,
                            "b_question": core.markets.markets[l.market_b].question if l.market_b in core.markets.markets else l.market_b})
    return {"stats": stats, "arbitrage": [a.to_dict() for a in (rep.arbitrage if rep else [])], "comparisons": comparisons}


class LinkAction(BaseModel):
    status: Literal["confirmed", "rejected", "proposed"]
    relation: Literal["equivalent", "complement"] = "equivalent"


@router.post("/links/{link_id}")
async def update_link(request: Request, link_id: int, body: LinkAction):
    core = core_of(request)
    async with core.db.tx() as s:
        l = await s.get(MarketLink, link_id)
        if not l:
            raise HTTPException(404)
        l.status, l.relation, l.updated_at = body.status, body.relation, utcnow()
    await core.load_links()
    await core.events.emit("settings_changed", f"market link {link_id} {body.status} ({body.relation})")
    return {"ok": True}


class NewLink(BaseModel):
    market_a: str
    market_b: str
    relation: Literal["equivalent", "complement"] = "equivalent"


@router.post("/links")
async def create_link(request: Request, body: NewLink):
    core = core_of(request)
    a, b = sorted((body.market_a, body.market_b))
    async with core.db.tx() as s:
        for k in (a, b):
            if not await s.get(Market, k):
                raise HTTPException(404, f"unknown market {k}")
        s.add(MarketLink(market_a=a, market_b=b, relation=body.relation, status="confirmed", score=1.0, reason="operator"))
    await core.load_links()
    return {"ok": True}


# --------------------------------------------------------------------- agents
@router.get("/agents")
async def agents(request: Request):
    core = core_of(request)
    async with core.db.session() as s:
        recent = (await s.execute(select(AgentDecision).order_by(desc(AgentDecision.id)).limit(120))).scalars().all()
    by_agent: dict[str, list] = {}
    for d in recent:
        by_agent.setdefault(d.agent, []).append(row(d))
    return [{**a.describe(), "recent_decisions": by_agent.get(a.name, [])[:15]}
            for a in (core.orchestrator, core.rigo, core.bram, core.ilsa, core.tess, core.spread)]


# ------------------------------------------------------------------ portfolio
@router.get("/portfolio")
async def portfolio(request: Request, account: str | None = None):
    core = core_of(request)
    acct = account or core.state.account_name or "paper"
    pf = await core.portfolio.state(acct)
    if not pf:
        raise HTTPException(404, f"account {acct} not found")
    windows = {w: await core.portfolio.performance(acct, d) for w, d in (("24h", 1), ("7d", 7), ("30d", 30), ("all", None))}
    return {"state": pf.to_dict(), "performance": windows}


@router.get("/portfolio/equity")
async def equity(request: Request, account: str | None = None, hours: float | None = None):
    core = core_of(request)
    acct = account or core.state.account_name or "paper"
    since = utcnow() - timedelta(hours=hours) if hours else None
    return await core.portfolio.equity_curve(acct, since)


@router.get("/portfolio/ledger")
async def ledger(request: Request, account: str | None = None, limit: int = Query(300, le=2000)):
    core = core_of(request)
    return await core.portfolio.ledger(account or core.state.account_name or "paper", limit)


@router.get("/orders")
async def orders(request: Request, account: str | None = None, status: str | None = None, limit: int = Query(200, le=2000)):
    core = core_of(request)
    async with core.db.session() as s:
        q = select(Order, Market.question).join(Market, Market.key == Order.market_key, isouter=True)
        if account:
            q = q.where(Order.account == account)
        if status:
            q = q.where(Order.status.in_(status.split(",")))
        rows = (await s.execute(q.order_by(desc(Order.id)).limit(limit))).all()
    return [{**row(o, {"raw"}), "question": q} for o, q in rows]


@router.get("/orders/{order_id}/trace")
async def order_trace(request: Request, order_id: int):
    """Full audit chain: snapshot -> estimate (model/version/evidence) -> opportunity -> risk -> order -> fills -> result."""
    core = core_of(request)
    async with core.db.session() as s:
        o = await s.get(Order, order_id)
        if not o:
            raise HTTPException(404)
        opp = await s.get(Opportunity, o.opportunity_id) if o.opportunity_id else None
        ra = await s.get(RiskAssessment, o.risk_assessment_id) if o.risk_assessment_id else None
        est = await s.get(ProbabilityEstimate, o.estimate_id or (opp.estimate_id if opp else None) or -1)
        snap = await s.get(OrderBookSnapshot, est.snapshot_id) if est and est.snapshot_id else None
        ev = (await s.execute(select(Evidence).where(Evidence.id.in_(est.evidence_ids or [])))).scalars().all() if est else []
        fills = (await s.execute(select(Fill).where(Fill.order_id == o.id))).scalars().all()
        m = await s.get(Market, o.market_key)
    return {"order": row(o), "market": row(m) if m else None, "opportunity": row(opp) if opp else None, "risk": row(ra) if ra else None,
            "estimate": row(est) if est else None, "snapshot": row(snap) if snap else None, "evidence": [row(e) for e in ev],
            "fills": [row(f) for f in fills]}


@router.post("/orders/{order_id}/cancel")
async def cancel_order(request: Request, order_id: int):
    ok = await core_of(request).execution.cancel(order_id, "operator")
    if not ok:
        raise HTTPException(409, "order not cancellable")
    return {"ok": True}


# ------------------------------------------------------------------- settings
class SettingsBody(BaseModel):
    categories: list[str] | None = None
    risk: RiskLimits | None = None
    notifications: NotificationPrefs | None = None
    emergency_cancel_orders: bool | None = None


@router.get("/settings")
async def get_settings_ep(request: Request):
    core = core_of(request)
    s = core.settings
    return {
        "runtime": core.state.runtime.model_dump(), "system": core.state.system_dict(), "categories": ALL_CATEGORIES,
        "live_confirm_phrase": LIVE_CONFIRM_PHRASE,
        "static": {"providers": s.provider_list, "live_trading_enabled": s.live_trading_enabled, "host": s.host, "port": s.port,
                   "database": core.db.url.split(":")[0], "scan_interval_s": s.scan_interval_s, "discovery_interval_s": s.discovery_interval_s,
                   "paper_starting_capital": s.paper_starting_capital, "llm_provider": s.llm_provider,
                   "auth_enabled": bool(s.api_token)},
        "providers": [{"venue": v, "trading_configured": core.registry.trading_configured(v),
                       "trading_verified": core.execution.live_status.get(v, {}).get("verified", False),
                       "capabilities": a.capabilities.to_dict(), "is_simulated": a.is_simulated} for v, a in core.registry.data.items()],
        "notifications": core.notifier.describe(),
    }


@router.put("/settings")
async def put_settings(request: Request, body: SettingsBody):
    core = core_of(request)
    cur = core.state.runtime.model_dump()
    upd = body.model_dump(exclude_none=True)
    try:
        new = RuntimeSettings.model_validate({**cur, **upd})
    except Exception as e:
        raise HTTPException(422, str(e)) from e
    await core.update_runtime(new)
    return new.model_dump()


@router.get("/settings/history")
async def settings_history(request: Request, limit: int = 100):
    core = core_of(request)
    async with core.db.session() as s:
        rows = (await s.execute(select(ConfigHistory).order_by(desc(ConfigHistory.id)).limit(limit))).scalars().all()
    return [row(r) for r in rows]


# ------------------------------------------------------------------- controls
class Toggle(BaseModel):
    enabled: bool


class ModeBody(BaseModel):
    mode: TradingMode
    confirm: str | None = None
    venue: str | None = None


class EmergencyBody(BaseModel):
    reason: str = Field("operator emergency stop", max_length=300)


@router.post("/control/scanning")
async def control_scanning(request: Request, body: Toggle):
    await core_of(request).set_scanning(body.enabled)
    return {"scanning_enabled": body.enabled}


@router.post("/control/pause")
async def control_pause(request: Request, body: Toggle):
    await core_of(request).set_paused(body.enabled)
    return {"trading_paused": body.enabled}


@router.post("/control/mode")
async def control_mode(request: Request, body: ModeBody):
    try:
        return await core_of(request).set_mode(body.mode, confirm=body.confirm, venue=body.venue)
    except ControlError as e:
        raise HTTPException(409, str(e)) from e


@router.post("/control/emergency-stop")
async def control_estop(request: Request, body: EmergencyBody):
    return await core_of(request).emergency_stop(body.reason)


@router.post("/control/emergency-reset")
async def control_ereset(request: Request):
    await core_of(request).emergency_reset()
    return {"emergency_stop": False, "mode": "STOPPED"}


@router.post("/control/run-cycle")
async def control_run_cycle(request: Request):
    core = core_of(request)
    if core.read_only:
        raise HTTPException(409, "read-only instance")
    return await core.orchestrator.run_cycle()


@router.post("/control/run-job/{name}")
async def control_run_job(request: Request, name: str):
    core = core_of(request)
    if name not in core.scheduler.jobs:
        raise HTTPException(404, "unknown job")
    if core.read_only and name in ("scan_cycle", "order_monitor", "settlement", "reconcile_live"):
        raise HTTPException(409, "read-only instance")
    res = await core.scheduler.run_now(name)
    return {"job": name, "result": res if isinstance(res, (dict, list, int, float, str, type(None))) else str(res)}


@router.post("/control/verify/{venue}")
async def control_verify(request: Request, venue: str):
    core = core_of(request)
    if venue not in ("kalshi", "polymarket"):
        raise HTTPException(400, "venue must be kalshi or polymarket")
    return await core.execution.verify_live(venue)


@router.post("/notifications/test")
async def notify_test(request: Request):
    core = core_of(request)
    if not core.notifier.channels:
        raise HTTPException(409, "no notification channel configured (Telegram or SMTP)")
    return await core.notifier.send(f"[SYSTEM] ARGONA test notification at {utcnow().isoformat()}")


# ------------------------------------------------------------------- research
@router.get("/models/metrics")
async def model_metrics(request: Request):
    core = core_of(request)
    async with core.db.session() as s:
        rows = (await s.execute(select(ModelMetric).order_by(desc(ModelMetric.id)).limit(60))).scalars().all()
    latest: dict[tuple[str, str], Any] = {}
    for r in rows:
        latest.setdefault((r.model, r.category), row(r))
    return {"metrics": list(latest.values()), "calibrated_categories": list(core.calibration)}


class BacktestBody(BaseModel):
    start: str | None = None
    end: str | None = None
    categories: list[str] = []
    starting_capital: float = 10_000
    step_s: int = Field(300, ge=30, le=86400)
    include_simulated: bool = True
    risk: RiskLimits | None = None


@router.post("/backtests")
async def run_backtest(request: Request, body: BacktestBody, bg: BackgroundTasks):
    core = core_of(request)
    params = body.model_dump()
    params["risk"] = (body.risk or core.state.runtime.risk).model_dump()

    async def _go():
        rid = await BacktestEngine(core.db).run(params)
        await core.events.emit("backtest", f"backtest #{rid} finished", data={"id": rid})

    bg.add_task(_go)
    return {"accepted": True}


@router.get("/backtests")
async def list_backtests(request: Request):
    core = core_of(request)
    async with core.db.session() as s:
        rows = (await s.execute(select(BacktestRun).order_by(desc(BacktestRun.id)).limit(50))).scalars().all()
    return [{k: v for k, v in row(r).items() if k not in ("trades", "equity_curve")} for r in rows]


@router.get("/backtests/{run_id}")
async def get_backtest(request: Request, run_id: int):
    core = core_of(request)
    async with core.db.session() as s:
        r = await s.get(BacktestRun, run_id)
    if not r:
        raise HTTPException(404)
    return row(r)


@router.get("/providers")
async def providers(request: Request):
    return core_of(request).registry.describe()
