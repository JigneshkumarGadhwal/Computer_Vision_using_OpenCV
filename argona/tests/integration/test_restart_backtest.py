"""Restart recovery, persistence, instance lock, backtesting."""
from datetime import timedelta

from sqlalchemy import select

from argona.backtesting.engine import BacktestEngine
from argona.database.models import BacktestRun, Order
from argona.domain import OrderStatus, TradingMode, utcnow
from argona.exchanges.sim import SimWorld
from tests.conftest import build_core


async def test_live_never_resumed_and_pending_orders_recovered(settings, clock):
    core = await build_core(settings, SimWorld(seed=5, n_markets=8, clock=clock))
    await core.rigo.discover()
    await core.orchestrator.run_cycle()
    core.state.mode, core.state.live_venue = TradingMode.LIVE, "kalshi"
    await core.state.save_system()
    async with core.db.tx() as s:
        s.add(Order(client_order_id="ARG-crash-live", account="live:kalshi", mode="LIVE", venue="kalshi", market_key="kalshi:X",
                    outcome="YES", action="BUY", limit_price=0.5, quantity=10, status=OrderStatus.PENDING_SUBMIT.value, is_simulated=False))
        s.add(Order(client_order_id="ARG-crash-paper", account="paper", mode="PAPER", venue="sim", market_key="sim:X",
                    outcome="YES", action="BUY", limit_price=0.5, quantity=10, status=OrderStatus.PENDING_SUBMIT.value, is_simulated=True))
    pf_before = await core.portfolio.state("paper")
    await core.stop()

    core2 = await build_core(settings, SimWorld(seed=5, n_markets=8, clock=clock))
    try:
        assert core2.state.mode is TradingMode.STOPPED  # LIVE not silently resumed
        async with core2.db.session() as s:
            orders = {o.client_order_id: o for o in (await s.execute(select(Order))).scalars()}
        assert orders["ARG-crash-live"].status == OrderStatus.UNKNOWN.value  # must reconcile before anything else
        assert orders["ARG-crash-paper"].status == OrderStatus.OPEN.value
        pf_after = await core2.portfolio.state("paper")
        assert pf_after.cash == pf_before.cash and len(pf_after.positions) == len(pf_before.positions)
        assert any(e["type"] == "mode_changed" and "NOT resumed" in e["message"] for e in core2.notifier.events)
    finally:
        await core2.stop()


async def test_paper_mode_and_settings_persist(settings, clock):
    core = await build_core(settings, SimWorld(seed=5, n_markets=4, clock=clock))
    rt = core.state.runtime.model_copy(deep=True)
    rt.risk.kelly_fraction = 0.1
    await core.update_runtime(rt)
    await core.stop()
    core2 = await build_core(settings, SimWorld(seed=5, n_markets=4, clock=clock))
    try:
        assert core2.state.mode is TradingMode.PAPER and core2.state.runtime.risk.kelly_fraction == 0.1
    finally:
        await core2.stop()


async def test_second_instance_is_read_only(settings, clock):
    a = await build_core(settings, SimWorld(seed=1, n_markets=2, clock=clock))
    b = await build_core(settings, SimWorld(seed=1, n_markets=2, clock=clock))
    try:
        assert not a.read_only and b.read_only
        from argona.core import ControlError
        import pytest

        with pytest.raises(ControlError):
            await b.set_mode(TradingMode.PAPER)
    finally:
        await b.stop()
        await a.stop()


async def test_backtest_without_data_reports_and_fabricates_nothing(core):
    rid = await BacktestEngine(core.db).run({"step_s": 60})
    async with core.db.session() as s:
        r = await s.get(BacktestRun, rid)
    assert r.status == "completed" and r.metrics == {"trades": 0} and "No results were fabricated" in r.notes[0]


async def test_backtest_replays_recorded_sim_data(core, clock):
    await core.rigo.discover()
    for _ in range(6):
        await core.orchestrator.run_cycle()
        await core.execution.monitor()
        clock.advance(minutes=5)
        await core.rigo.discover()
    clock.advance(hours=2)
    await core.rigo.discover()  # markets resolve in the simulator -> resolutions recorded
    rid = await BacktestEngine(core.db).run({"step_s": 300, "risk": core.state.runtime.risk.model_dump(), "include_simulated": True})
    async with core.db.session() as s:
        r = await s.get(BacktestRun, rid)
    assert r.status == "completed", r.notes
    assert r.metrics["snapshots"] > 0 and r.metrics["markets"] > 0
    assert any("SIM venue" in n for n in r.notes)
    assert all(t["simulated_fill"] for t in r.trades)


async def test_sim_world_survives_restart_and_positions_settle(settings):
    from argona.exchanges.registry import ProviderRegistry

    reg = ProviderRegistry(settings)
    w = reg.sim_world
    ms = await reg.data["sim"].discover()
    await reg.close()
    reg2 = ProviderRegistry(settings)
    assert set(reg2.sim_world.markets) == set(w.markets)
    assert reg2.sim_world.spot == w.spot
    await reg2.close()
