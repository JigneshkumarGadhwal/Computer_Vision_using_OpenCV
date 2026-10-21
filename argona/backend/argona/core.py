"""Core service container: wires every subsystem, owns lifecycle, controls and background jobs."""
from __future__ import annotations

import logging
import os
import re
import socket
import uuid
from datetime import timedelta
from typing import Any

from sqlalchemy import delete, func, select

from .agents.bram import Bram
from .agents.ilsa import Ilsa
from .agents.orchestrator import Orchestrator
from .agents.rigo import Rigo
from .agents.spread import Spread
from .agents.tess import Tess
from .config import RuntimeSettings, Settings
from .database.models import (
    InstanceLock,
    Market,
    MarketLink,
    ModelMetric,
    Order,
    OrderBookSnapshot,
    PriceObservation,
    ProbabilityEstimate,
)
from .database.session import Database
from .domain import MarketStatus, NormalizedMarket, OrderStatus, TradingMode, Venue, utcnow
from .events import EventBus
from .exchanges.registry import ProviderRegistry
from .execution.service import ExecutionService
from .market_data.service import MarketService
from .notifications.service import NotificationService
from .portfolio.service import PortfolioService
from .probability.calibration import brier, calibration_table, log_loss, reliability
from .probability.engine import ProbabilityEngine
from .research.llm import LLMReviewer
from .research.sources import CryptoSource, ForecastFileSource, SimSource, WeatherSource
from .scheduler.scheduler import Job, Scheduler
from .state import StateStore

log = logging.getLogger("argona.core")

LIVE_CONFIRM_PHRASE = "ENABLE LIVE TRADING"
LOCK_STALE_S = 90


class ControlError(Exception):
    pass


class Core:
    def __init__(self, settings: Settings, *, registry: ProviderRegistry | None = None, notifier: NotificationService | None = None,
                 crypto_source: CryptoSource | None = None, weather_source: WeatherSource | None = None) -> None:
        self.settings = settings
        self.db = Database(settings.resolved_database_url)
        self.events = EventBus(self.db)
        self.state = StateStore(self.db, settings)
        self.events.mode_provider = lambda: self.state.mode.value
        self.registry = registry or ProviderRegistry(settings)
        self.markets = MarketService(self.db)
        self.portfolio = PortfolioService(self.db, self.markets.mark)
        self.execution = ExecutionService(self.db, self.events, self.portfolio, self.markets, self.registry)
        self.prob_engine = ProbabilityEngine()
        self.crypto_source = crypto_source if crypto_source is not None else CryptoSource(settings.coinbase_url, settings.kraken_url,
                                                                                         timeout=settings.http_timeout_s)
        self.weather_source = weather_source if weather_source is not None else WeatherSource(settings.open_meteo_url,
                                                                                              timeout=settings.http_timeout_s)
        world = getattr(self.registry, "sim_world", None)
        self.sim_source = SimSource(world) if world else None
        self.forecast_file = ForecastFileSource(settings.data_dir / "forecasts.json")
        key = settings.anthropic_api_key.get_secret_value() if settings.anthropic_api_key else None
        self.llm = LLMReviewer(key, settings.llm_model, settings.llm_max_reviews_per_hour) if settings.llm_provider == "anthropic" else None
        self.notifier = notifier or NotificationService(settings, lambda: self.state.runtime.notifications)
        self.events.add_hook(self.notifier.on_event)
        self.rigo, self.bram, self.ilsa = Rigo(self), Bram(self), Ilsa(self)
        self.tess, self.spread, self.orchestrator = Tess(self), Spread(self), Orchestrator(self)
        self.scheduler = Scheduler(on_error=self._job_error)
        self.links_by_market: dict[str, list[tuple[str, str]]] = {}
        self.calibration: dict[str, Any] = {}
        self.risk_alerts: dict[str, bool] = {}
        self.instance_id = f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:6]}"
        self.read_only = False
        self.started = False

    # ---------------------------------------------------------------- lifecycle
    async def start(self, *, run_scheduler: bool = True) -> None:
        await self.state.load()
        await self._acquire_lock()
        await self.portfolio.ensure_account("paper", "PAPER", self.settings.paper_starting_capital)
        await self.markets.load_cache()
        await self.load_links()
        await self.recover()
        if run_scheduler:
            self._register_jobs()
            self.scheduler.start()
        self.started = True
        await self.events.emit("system", f"ARGONA started (mode {self.state.mode.value}, providers {', '.join(self.registry.data)}"
                               f"{', READ-ONLY: another instance holds the lock' if self.read_only else ''})",
                               severity="warning" if self.read_only else "info")

    async def stop(self) -> None:
        await self.events.emit("system", "ARGONA shutting down (safe stop: scheduler drained, state persisted)")
        await self.scheduler.stop()
        await self._release_lock()
        await self.execution.close()
        await self.registry.close()
        for src in (self.crypto_source, self.weather_source):
            if src:
                await src.close()
        await self.db.dispose()

    async def _acquire_lock(self) -> None:
        now = utcnow()
        async with self.db.tx() as s:
            lock = await s.get(InstanceLock, 1)
            if lock and lock.owner != self.instance_id and (now - lock.heartbeat_at).total_seconds() < LOCK_STALE_S:
                self.read_only = True
                log.error("another ARGONA instance (%s) is active; starting READ-ONLY to prevent duplicate orders", lock.owner)
                return
            if lock:
                lock.owner, lock.heartbeat_at = self.instance_id, now
            else:
                s.add(InstanceLock(id=1, owner=self.instance_id, heartbeat_at=now))

    async def _heartbeat(self) -> None:
        if self.read_only:
            await self._acquire_lock()  # take over once the other instance's heartbeat goes stale
            if not self.read_only:
                await self.events.emit("system", "instance lock acquired; leaving read-only mode")
            return
        async with self.db.tx() as s:
            lock = await s.get(InstanceLock, 1)
            if lock and lock.owner == self.instance_id:
                lock.heartbeat_at = utcnow()

    async def _release_lock(self) -> None:
        if self.read_only:
            return
        async with self.db.tx() as s:
            lock = await s.get(InstanceLock, 1)
            if lock and lock.owner == self.instance_id:
                await s.delete(lock)

    async def recover(self) -> None:
        """Restart recovery: never silently resume live trading; reconcile uncertain orders first."""
        async with self.db.tx() as s:
            pend = (await s.execute(select(Order).where(Order.status == OrderStatus.PENDING_SUBMIT.value))).scalars().all()
            for o in pend:
                # paper: the simulator is local, so a pending order is simply re-matched; live: outcome unknown
                o.status = OrderStatus.OPEN.value if o.is_simulated else OrderStatus.UNKNOWN.value
        if pend:
            await self.events.emit("reconciliation", f"recovered {len(pend)} order(s) left PENDING_SUBMIT by a previous run "
                                   "(live ones marked UNKNOWN and will be looked up at the venue before anything else)",
                                   severity="warning")
        if self.state.mode is TradingMode.LIVE:
            venue = self.state.live_venue
            if not self.settings.live_resume_on_restart or not self.settings.live_trading_enabled or not venue:
                self.state.mode = TradingMode.STOPPED
                await self.state.save_system(actor="restart")
                await self.events.emit("mode_changed", "restart: previous LIVE session NOT resumed automatically; mode set to STOPPED. "
                                       "Re-activate LIVE explicitly from Settings.", severity="warning")
            else:
                st = await self.execution.verify_live(venue)
                rep = await self.execution.reconcile_live(venue) if st.get("verified") else {"ok": False, "issues": [st.get("error")]}
                if not rep.get("ok"):
                    self.state.mode = TradingMode.STOPPED
                    await self.state.save_system(actor="restart")
                    await self.events.emit("reconciliation_mismatch", f"restart: live reconciliation failed ({'; '.join(map(str, rep.get('issues', [])))[:200]}); "
                                           "mode set to STOPPED", severity="critical")
                else:
                    await self.events.emit("reconciliation", f"restart: live state reconciled with {venue}; resuming LIVE", severity="warning")
        if self.state.emergency_stop:
            await self.events.emit("emergency_stop", f"emergency stop still active from previous run: {self.state.emergency_reason}",
                                   severity="critical")

    # -------------------------------------------------------------------- jobs
    def _register_jobs(self) -> None:
        s, rt = self.settings, lambda: self.state.runtime
        trading_ok = lambda: not self.read_only  # noqa: E731
        sch = self.scheduler
        sch.add("discovery", s.discovery_interval_s, self.rigo.discover, initial_delay_s=0.5)
        # scanning also runs while STOPPED (evaluation without execution); the orchestrator skips TESS/SPREAD then
        sch.add("scan_cycle", s.scan_interval_s, self.orchestrator.run_cycle, initial_delay_s=4,
                enabled=lambda: rt().scanning_enabled and trading_ok())
        sch.add("order_monitor", s.order_monitor_interval_s, self.execution.monitor, initial_delay_s=6, enabled=trading_ok)
        sch.add("settlement", s.settlement_interval_s, self.rigo.track_resolutions, initial_delay_s=15, enabled=trading_ok)
        sch.add("reconcile_live", s.reconcile_interval_s, self.reconcile_job, initial_delay_s=20,
                enabled=lambda: self.state.mode is TradingMode.LIVE and trading_ok())
        sch.add("portfolio_snapshot", s.snapshot_interval_s, self.snapshot_job, initial_delay_s=10)
        sch.add("health", s.health_interval_s, self.health_job, initial_delay_s=2)
        sch.add("model_metrics", s.metrics_interval_s, self.metrics_job, initial_delay_s=30)
        sch.add("maintenance", s.maintenance_interval_s, self.maintenance_job, initial_delay_s=120)

    async def _job_error(self, job: Job, e: Exception) -> None:
        if job.consecutive_failures in (1, 5, 20):
            await self.events.emit("scheduler_error", f"job {job.name} failed ({job.consecutive_failures}x): {type(e).__name__}: {str(e)[:160]}",
                                   severity="warning" if job.consecutive_failures < 5 else "critical")

    async def reconcile_job(self) -> None:
        venue = self.state.live_venue
        if not venue:
            return
        rep = await self.execution.reconcile_live(venue)
        if not rep["ok"]:
            if not self.state.runtime.trading_paused:
                rt = self.state.runtime.model_copy(deep=True)
                rt.trading_paused = True
                await self.state.save_runtime(rt, actor="reconciliation")
            await self.events.emit("reconciliation_mismatch", f"{venue}: {'; '.join(rep['issues'])[:300]} — new trades paused",
                                   severity="critical", data=rep)

    async def snapshot_job(self) -> None:
        await self.portfolio.snapshot("paper")
        if self.state.live_venue:
            await self.portfolio.snapshot(f"live:{self.state.live_venue}")

    async def health_job(self) -> None:
        await self._heartbeat()
        now = utcnow()
        for venue, st in self.registry.status.items():
            if st.last_sync:
                from datetime import datetime

                age = (now - datetime.fromisoformat(st.last_sync)).total_seconds()
                limit = max(3 * self.settings.discovery_interval_s, 3 * self.settings.scan_interval_s)
                if age > limit and st.connected:
                    st.connected = False
                    await self.events.emit("provider_disconnected", f"{venue}: no successful sync for {age:.0f}s", severity="warning")

    async def maintenance_job(self) -> None:
        cutoff = utcnow() - timedelta(days=60)
        async with self.db.tx() as s:
            await s.execute(delete(PriceObservation).where(PriceObservation.ts < cutoff))
        async with self.db.tx() as s:
            await s.execute(delete(OrderBookSnapshot).where(OrderBookSnapshot.ts < cutoff))
        await self.bram.prune()
        await self.propose_links()

    # ----------------------------------------------------------- calibration
    async def metrics_job(self) -> dict[str, Any]:
        """Point-in-time calibration data: price/estimate observed >= 1h before resolution vs. outcome."""
        async with self.db.session() as s:
            resolved = (await s.execute(select(Market).where(Market.status == MarketStatus.RESOLVED.value, Market.result.in_(["YES", "NO"]))
                                        .order_by(Market.resolved_at.desc()).limit(3000))).scalars().all()
            market_pairs: dict[str, list[tuple[float, int]]] = {}
            model_pairs: dict[str, list[tuple[float, int]]] = {}
            mkt_on_model: dict[str, list[tuple[float, int]]] = {}
            for m in resolved:
                ref = m.close_time if m.close_time and (not m.resolved_at or m.close_time < m.resolved_at) else m.resolved_at
                if not ref:
                    continue
                cutoff = ref - timedelta(hours=1)
                y = 1 if m.result == "YES" else 0
                mid = (await s.execute(select(PriceObservation.mid).where(PriceObservation.market_key == m.key, PriceObservation.ts <= cutoff,
                                                                          PriceObservation.mid.is_not(None))
                                       .order_by(PriceObservation.ts.desc()).limit(1))).scalar_one_or_none()
                if mid is not None:
                    market_pairs.setdefault(m.category, []).append((mid, y))
                est = (await s.execute(select(ProbabilityEstimate).where(ProbabilityEstimate.market_key == m.key, ProbabilityEstimate.ts <= cutoff,
                                                                         ProbabilityEstimate.abstained.is_(False))
                                       .order_by(ProbabilityEstimate.ts.desc()).limit(1))).scalar_one_or_none()
                if est and est.q_yes is not None:
                    model_pairs.setdefault(m.category, []).append((est.q_yes, y))
                    if est.market_mid is not None:
                        mkt_on_model.setdefault(m.category, []).append((est.market_mid, y))
        self.calibration = {cat: calibration_table(p) for cat, p in market_pairs.items() if len(p) >= 40}
        rows = []
        for cat, pairs in model_pairs.items():
            rows.append(ModelMetric(model="argona-ensemble", category=cat, n=len(pairs), brier=brier(pairs), log_loss=log_loss(pairs),
                                    market_brier=brier(mkt_on_model.get(cat, [])), reliability=reliability(pairs)))
        allp = [p for v in model_pairs.values() for p in v]
        if allp:
            allm = [p for v in mkt_on_model.values() for p in v]
            rows.append(ModelMetric(model="argona-ensemble", category="all", n=len(allp), brier=brier(allp), log_loss=log_loss(allp),
                                    market_brier=brier(allm), reliability=reliability(allp)))
        for cat, pairs in market_pairs.items():
            rows.append(ModelMetric(model="market_implied", category=cat, n=len(pairs), brier=brier(pairs), log_loss=log_loss(pairs),
                                    market_brier=brier(pairs), reliability=reliability(pairs)))
        if rows:
            async with self.db.tx() as s:
                s.add_all(rows)
        return {"categories_calibrated": list(self.calibration), "model_samples": len(allp)}

    # ------------------------------------------------------------------ links
    async def load_links(self) -> None:
        async with self.db.session() as s:
            rows = (await s.execute(select(MarketLink).where(MarketLink.status == "confirmed"))).scalars().all()
        by: dict[str, list[tuple[str, str]]] = {}
        for r in rows:
            by.setdefault(r.market_a, []).append((r.market_b, r.relation))
            by.setdefault(r.market_b, []).append((r.market_a, r.relation))
        self.links_by_market = by

    @staticmethod
    def _tokens(q: str) -> set[str]:
        stop = {"will", "the", "be", "on", "in", "of", "a", "by", "to", "at", "for", "and", "or", "than", "is", "?"}
        return {t for t in re.findall(r"[a-z0-9]+", q.lower()) if t not in stop}

    async def propose_links(self, max_new: int = 50) -> int:
        """Suggest cross-venue equivalents (never auto-confirmed: rules may differ)."""
        open_ms = [m for m in self.markets.markets.values() if not m.is_simulated and m.close_time]
        by_venue: dict[str, list[NormalizedMarket]] = {}
        for m in open_ms:
            by_venue.setdefault(m.venue.value, []).append(m)
        if len(by_venue) < 2:
            return 0
        venues = list(by_venue)
        new = 0
        async with self.db.tx() as s:
            existing = {(a, b) for a, b in (await s.execute(select(MarketLink.market_a, MarketLink.market_b))).all()}
            for a in by_venue[venues[0]]:
                ta = self._tokens(a.question + " " + (a.outcome_label or ""))
                na = set(re.findall(r"\d[\d,.]*", a.question + " " + (a.outcome_label or "")))
                for b in by_venue[venues[1]]:
                    if a.category != b.category or abs((a.close_time - b.close_time).total_seconds()) > 36 * 3600:
                        continue
                    tb = self._tokens(b.question + " " + (b.outcome_label or ""))
                    nb = set(re.findall(r"\d[\d,.]*", b.question + " " + (b.outcome_label or "")))
                    if na != nb:
                        continue
                    j = len(ta & tb) / max(1, len(ta | tb))
                    if j < 0.5:
                        continue
                    k1, k2 = sorted((a.key, b.key))
                    if (k1, k2) in existing:
                        continue
                    s.add(MarketLink(market_a=k1, market_b=k2, relation="equivalent", score=round(j, 3), status="proposed",
                                     reason=f"title similarity {j:.2f}, same numbers, close times within 36h"))
                    existing.add((k1, k2))
                    new += 1
                    if new >= max_new:
                        break
                if new >= max_new:
                    break
        return new

    # ---------------------------------------------------------------- helpers
    def forecast_file_has(self, key: str) -> bool:
        return bool(self.forecast_file.forecast(key))

    def venue_tradable(self, market: NormalizedMarket, mode: TradingMode) -> tuple[bool, str]:
        if mode is TradingMode.PAPER:
            return True, "paper (simulated execution)"
        if mode is TradingMode.LIVE:
            if market.is_simulated or market.venue is Venue.SIM:
                return False, "SIM markets are never traded live"
            if market.venue.value != self.state.live_venue:
                return False, f"live trading is active on {self.state.live_venue} only"
            st = self.execution.live_status.get(market.venue.value, {})
            if not st.get("verified"):
                return False, "live connectivity not verified"
            return True, "live"
        return False, "trading stopped"

    async def settle(self, key: str) -> None:
        async with self.db.tx() as s:
            m = await s.get(Market, key)
            if not m:
                return
            settlements = await self.portfolio.settle_market(s, m)
            q, res = m.question, m.result or f"{m.settlement_value}"
        await self.events.emit("market_resolved", f"resolved {res}: {q[:90]}", agent="RIGO", market_key=key, data={"result": res})
        for st in settlements:
            tag = "PAPER" if st.account == "paper" else "LIVE"
            await self.events.emit("pnl_realized", f"[{tag}] settled {st.contracts:.0f} {st.outcome} → payout ${st.payout:.2f}, "
                                   f"realized P&L {st.realized_pnl:+.2f}", agent="TESS", market_key=key,
                                   severity="info", data={"account": st.account, "pnl": st.realized_pnl, "simulated": tag == "PAPER"})

    # --------------------------------------------------------------- controls
    async def update_runtime(self, new: RuntimeSettings, actor: str = "api") -> RuntimeSettings:
        old = self.state.runtime
        await self.state.save_runtime(new, actor=actor)
        changed = [k for k in new.model_dump() if new.model_dump()[k] != old.model_dump()[k]]
        await self.events.emit("settings_changed", f"settings updated by {actor}: {', '.join(changed) or 'no changes'}",
                               data={"changed": changed})
        if "categories" in changed or "risk" in changed:
            await self.markets.reevaluate(new)
        return new

    async def set_paused(self, paused: bool, actor: str = "api") -> None:
        rt = self.state.runtime.model_copy(deep=True)
        rt.trading_paused = paused
        await self.state.save_runtime(rt, actor=actor)
        await self.events.emit("trading_paused" if paused else "trading_resumed",
                               "new trades paused by operator (exits and monitoring continue)" if paused else "new trades resumed by operator",
                               severity="warning" if paused else "info")

    async def set_scanning(self, enabled: bool, actor: str = "api") -> None:
        rt = self.state.runtime.model_copy(deep=True)
        rt.scanning_enabled = enabled
        await self.state.save_runtime(rt, actor=actor)
        await self.events.emit("settings_changed", f"market scanning {'started' if enabled else 'stopped'} by {actor}")
        if enabled:
            self.scheduler.poke("scan_cycle")

    async def emergency_stop(self, reason: str, actor: str = "api") -> dict[str, Any]:
        self.state.emergency_stop = True
        self.state.emergency_reason = f"{reason} (by {actor} at {utcnow().isoformat()})"
        await self.state.save_system(actor=actor)
        canceled = 0
        if self.state.runtime.emergency_cancel_orders:
            canceled = await self.execution.cancel_all("emergency stop")
        await self.events.emit("emergency_stop", f"EMERGENCY STOP: {reason}. New orders blocked; {canceled} working order(s) canceled.",
                               severity="critical", data={"canceled": canceled})
        return {"emergency_stop": True, "canceled_orders": canceled}

    async def emergency_reset(self, actor: str = "api") -> None:
        self.state.emergency_stop = False
        self.state.emergency_reason = None
        prev = self.state.mode
        self.state.mode = TradingMode.STOPPED
        await self.state.save_system(actor=actor)
        await self.events.emit("mode_changed", f"emergency stop cleared by {actor}; mode set to STOPPED (was {prev.value}). "
                               "Select PAPER or LIVE deliberately to resume.", severity="warning")

    async def set_mode(self, target: TradingMode, *, confirm: str | None = None, venue: str | None = None, actor: str = "api") -> dict[str, Any]:
        cur = self.state.mode
        if self.read_only:
            raise ControlError("instance is read-only (another ARGONA instance holds the lock)")
        if target is not TradingMode.STOPPED and self.state.emergency_stop:
            raise ControlError("emergency stop active: reset it first")
        if target is TradingMode.LIVE:
            if not self.settings.live_trading_enabled:
                raise ControlError("live trading is disabled server-side (set ARGONA_LIVE_TRADING_ENABLED=true and restart)")
            if confirm != LIVE_CONFIRM_PHRASE:
                raise ControlError(f'confirmation phrase required: "{LIVE_CONFIRM_PHRASE}"')
            if venue not in ("kalshi", "polymarket"):
                raise ControlError("venue must be kalshi or polymarket")
            if venue not in self.registry.data:
                raise ControlError(f"{venue} market data provider is not enabled (ARGONA_PROVIDERS)")
            if not self.registry.trading_configured(venue):
                raise ControlError(f"{venue} trading credentials are not configured")
            L = self.state.runtime.risk
            if L.max_daily_loss_usd <= 0 or L.max_position_usd <= 0:
                raise ControlError("risk limits must be configured")
            st = await self.execution.verify_live(venue)
            if not st.get("verified"):
                raise ControlError(f"live connectivity check failed: {st.get('error')}")
            rep = await self.execution.reconcile_live(venue)
            if not rep.get("ok") and any("failed" in i for i in rep.get("issues", [])):
                raise ControlError(f"reconciliation failed: {rep['issues']}")
            self.state.live_venue = venue
        if cur is not target and cur is not TradingMode.STOPPED:
            acct = self.state.account_name
            n = 0
            for o in await self.execution.working_orders(acct):
                n += int(await self.execution.cancel(o.id, f"mode change {cur.value}->{target.value}"))
            if n:
                await self.events.emit("order_canceled", f"{n} working order(s) canceled due to mode change", severity="warning")
        self.state.mode = target
        if target is not TradingMode.LIVE:
            self.state.live_venue = None
        await self.state.save_system(actor=actor)
        if target is TradingMode.PAPER:
            await self.portfolio.ensure_account("paper", "PAPER", self.settings.paper_starting_capital)
        await self.events.emit("mode_changed", f"trading mode {cur.value} → {target.value}" + (f" on {venue} (REAL MONEY)" if target is TradingMode.LIVE else ""),
                               severity="critical" if target is TradingMode.LIVE else "warning")
        return {"mode": target.value, "live_venue": self.state.live_venue}

    async def counts(self) -> dict[str, int]:
        async with self.db.session() as s:
            n = (await s.execute(select(func.count()).select_from(Market))).scalar_one()
        return {"markets": n}
