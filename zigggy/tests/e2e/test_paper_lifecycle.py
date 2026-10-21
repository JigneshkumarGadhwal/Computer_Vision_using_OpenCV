"""End-to-end paper trading: discovery -> evidence -> estimate -> risk -> paper fill -> resolution -> settlement,
with a full audit trail, using the SIM venue and a controllable clock."""
from sqlalchemy import select

from zigggy.database.models import (AgentDecision, Evidence, Fill, Opportunity, Order, ProbabilityEstimate, RiskAssessment,
                                    Settlement)
from zigggy.domain import OrderStatus


async def test_full_paper_lifecycle(core, clock):
    await core.rigo.discover()
    orders = []
    for _ in range(12):
        res = await core.orchestrator.run_cycle()
        assert "error" not in res
        await core.execution.monitor()
        await core.snapshot_job()
        async with core.db.session() as s:
            orders = (await s.execute(select(Order))).scalars().all()
        if any(o.filled_qty > 0 for o in orders):
            break
        clock.advance(minutes=3)
        await core.rigo.discover()
    filled = [o for o in orders if o.filled_qty > 0]
    assert filled, "expected at least one simulated fill within 12 cycles"
    o = filled[0]
    assert o.is_simulated and o.account == "paper" and o.client_order_id.startswith("ZIG-")
    # audit chain: order -> opportunity -> risk assessment -> estimate (with evidence) -> fills
    async with core.db.session() as s:
        opp = await s.get(Opportunity, o.opportunity_id)
        ra = await s.get(RiskAssessment, o.risk_assessment_id)
        est = await s.get(ProbabilityEstimate, o.estimate_id)
        fills = (await s.execute(select(Fill).where(Fill.order_id == o.id))).scalars().all()
        ev = (await s.execute(select(Evidence).where(Evidence.id.in_(est.evidence_ids)))).scalars().all()
        n_dec = len((await s.execute(select(AgentDecision))).scalars().all())
    assert opp.status == "executed" and opp.net_ev_per_contract >= core.state.runtime.risk.min_net_edge
    assert ra.approved and all(c["passed"] for c in ra.checks)
    assert est.q_yes is not None and est.snapshot_id is not None and not est.abstained
    assert ev and all(e.is_simulated for e in ev)
    assert sum(f.qty for f in fills) == o.filled_qty
    assert n_dec > 5
    # advance past every SIM expiry -> resolution -> settlement
    clock.advance(hours=3)
    await core.rigo.discover()
    await core.rigo.track_resolutions()
    async with core.db.session() as s:
        sets = (await s.execute(select(Settlement))).scalars().all()
    assert sets, "positions should settle once SIM markets resolve"
    pf = await core.portfolio.state("paper")
    perf = await core.portfolio.performance("paper")
    assert perf["trades"] >= 1
    # realized P&L ledger: settlement P&L plus any early exits equals the account's realized total
    assert pf.realized_pnl == round(perf["net_realized"], 6) or abs(pf.realized_pnl - perf["net_realized"]) < 1e-6
    # cash identity: starting capital + realized + (cost basis still open is held as positions) == cash + open cost basis
    open_cost = sum(p.cost_basis for p in pf.positions)
    assert abs((10_000 + pf.realized_pnl) - (pf.cash + open_cost)) < 1e-4
    types = [e["type"] for e in core.notifier.events]
    for t in ("market_discovered", "opportunity_qualified", "risk_approved", "order_submitted", "market_resolved", "pnl_realized"):
        assert t in types, t
    # every realized P&L event is tagged as simulated
    assert all(e["data"].get("simulated") for e in core.notifier.events if e["type"] == "pnl_realized")

