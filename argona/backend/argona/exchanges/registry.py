"""Provider registry: which adapters are enabled and what each can actually do."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from ..config import Settings
from ..domain import utcnow
from .base import MarketDataAdapter, TradingAdapter
from .kalshi import KalshiMarketData, KalshiSigner, KalshiTrading
from .polymarket import PolymarketMarketData, PolymarketTrading
from .sim import SimMarketData, SimWorld

log = logging.getLogger("argona.registry")


@dataclass
class ProviderStatus:
    venue: str
    enabled: bool
    is_simulated: bool
    connected: bool = False
    last_sync: str | None = None
    last_error: str | None = None
    markets: int = 0
    trading_configured: bool = False
    trading_verified: bool = False
    trading_error: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


class ProviderRegistry:
    def __init__(self, settings: Settings, *, transports: dict[str, Any] | None = None, sim_world: SimWorld | None = None) -> None:
        self.settings = settings
        self.data: dict[str, MarketDataAdapter] = {}
        self.status: dict[str, ProviderStatus] = {}
        transports = transports or {}
        for p in settings.provider_list:
            if p == "polymarket":
                a: MarketDataAdapter = PolymarketMarketData(settings.polymarket_gamma_url, settings.polymarket_clob_url,
                                                            max_markets=settings.polymarket_max_markets, rps=settings.polymarket_rps,
                                                            timeout=settings.http_timeout_s, transport=transports.get("polymarket"))
            elif p == "kalshi":
                series = [s.strip() for s in settings.kalshi_series.split(",") if s.strip()]
                a = KalshiMarketData(settings.kalshi_api_url, max_markets=settings.kalshi_max_markets, series=series,
                                     rps=settings.kalshi_rps, timeout=settings.http_timeout_s, transport=transports.get("kalshi"))
            elif p == "sim":
                state = settings.data_dir / "sim_world.json"
                if sim_world is None and state.exists():
                    try:
                        sim_world = SimWorld.load(state, seed=settings.sim_seed, n_markets=settings.sim_markets)
                    except (OSError, ValueError, KeyError, TypeError) as e:
                        log.warning("SIM state unreadable (%s); starting a fresh simulated world", e)
                self.sim_world = sim_world or SimWorld(seed=settings.sim_seed, n_markets=settings.sim_markets)
                settings.data_dir.mkdir(parents=True, exist_ok=True)
                a = SimMarketData(self.sim_world, state_path=state)
            else:
                log.warning("unknown provider %s ignored", p)
                continue
            self.data[p] = a
            self.status[p] = ProviderStatus(venue=p, enabled=True, is_simulated=a.is_simulated,
                                            trading_configured=self.trading_configured(p))
        if "sim" not in self.data:
            self.sim_world = sim_world

    def trading_configured(self, venue: str) -> bool:
        s = self.settings
        if venue == "kalshi":
            return bool(s.kalshi_key_id and s.kalshi_private_key_path and s.kalshi_private_key_path.exists())
        if venue == "polymarket":
            return bool(s.polymarket_private_key)
        return False

    def build_trading_adapter(self, venue: str) -> TradingAdapter:
        """Only called by the execution service. Credentials never leave this function's result."""
        s = self.settings
        if venue == "kalshi":
            if not self.trading_configured("kalshi"):
                raise RuntimeError("Kalshi credentials not configured (ARGONA_KALSHI_KEY_ID / ARGONA_KALSHI_PRIVATE_KEY_PATH)")
            signer = KalshiSigner.from_file(s.kalshi_key_id.get_secret_value(), s.kalshi_private_key_path)  # type: ignore[union-attr]
            return KalshiTrading(s.kalshi_api_url, signer, timeout=s.http_timeout_s)
        if venue == "polymarket":
            if not s.polymarket_private_key:
                raise RuntimeError("Polymarket credentials not configured (ARGONA_POLYMARKET_PRIVATE_KEY)")
            return PolymarketTrading(s.polymarket_private_key.get_secret_value(), s.polymarket_wallet)
        raise RuntimeError(f"venue {venue} does not support live trading")

    def mark_sync(self, venue: str, ok: bool, *, markets: int | None = None, error: str | None = None) -> bool:
        """Returns True when connectivity state flipped (for disconnect/recover events)."""
        st = self.status[venue]
        was = st.connected
        st.connected = ok
        if ok:
            st.last_sync = utcnow().isoformat()
            st.last_error = None
            if markets is not None:
                st.markets = markets
        else:
            st.last_error = (error or "error")[:300]
        return was != ok

    def describe(self) -> list[dict[str, Any]]:
        out = []
        for venue, a in self.data.items():
            st = self.status[venue]
            out.append({**st.__dict__, "capabilities": a.capabilities.to_dict(), "http": a.stats()})
        return out

    async def close(self) -> None:
        for a in self.data.values():
            await a.close()
