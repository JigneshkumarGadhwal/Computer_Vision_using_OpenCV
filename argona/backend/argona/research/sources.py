"""Evidence sources used by BRAM. All free, keyless, official/public APIs; every item carries its
source URL, observation timestamp and a reliability grade.

* Crypto spot & realised volatility: Coinbase Exchange public API (primary), Kraken (fallback +
  cross-check). Settlement indices (e.g. CF Benchmarks, Binance candles) differ slightly from
  these spot feeds - models add a basis term for that.
* Weather: Open-Meteo multi-model daily max temperature forecast.
* Forecast file: operator-supplied forecasts (``<data_dir>/forecasts.json``) for any market,
  e.g. from a licensed data provider: {"kalshi:TICKER": {"p": 0.62, "sigma": 0.05, "source": "...", "as_of": "ISO"}}
* SIM: simulator spot path and published simulated forecasts (flagged simulated).
"""
from __future__ import annotations

import json
import logging
import math
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from ..domain import utcnow
from ..exchanges.base import HttpClient, ProviderError, parse_ts
from ..exchanges.sim import SimWorld
from ..probability.base import EvidenceItem
from ..probability.weather import Station

log = logging.getLogger("argona.research")

KRAKEN_PAIRS = {"BTC": "XBTUSD", "ETH": "ETHUSD", "SOL": "SOLUSD", "XRP": "XRPUSD", "DOGE": "XDGUSD"}


def realised_vol(closes: list[float], period_s: float) -> float | None:
    rets = [math.log(b / a) for a, b in zip(closes, closes[1:]) if a > 0 and b > 0]
    if len(rets) < 10:
        return None
    mu = sum(rets) / len(rets)
    var = sum((r - mu) ** 2 for r in rets) / (len(rets) - 1)
    return math.sqrt(var * (365.25 * 24 * 3600 / period_s))


class _TTLCache:
    def __init__(self) -> None:
        self._d: dict[str, tuple[float, Any]] = {}

    def get(self, k: str, ttl: float) -> Any:
        v = self._d.get(k)
        if v and time.monotonic() - v[0] < ttl:
            return v[1]
        return None

    def put(self, k: str, v: Any) -> None:
        self._d[k] = (time.monotonic(), v)


class CryptoSource:
    def __init__(self, coinbase_url: str, kraken_url: str, *, timeout: float = 10.0,
                 transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.cb = HttpClient(coinbase_url, rps=5, timeout=timeout, transport=transport)
        self.kr = HttpClient(kraken_url, rps=1, timeout=timeout, transport=transport)
        self.cache = _TTLCache()

    async def spot(self, asset: str) -> list[EvidenceItem]:
        cached = self.cache.get(f"spot:{asset}", 30)
        if cached:
            return cached
        items: list[EvidenceItem] = []
        try:
            d = await self.cb.request("GET", f"/products/{asset}-USD/ticker")
            items.append(EvidenceItem("crypto_spot", asset, "coinbase", f"{self.cb.base_url}/products/{asset}-USD/ticker",
                                      parse_ts(d.get("time")) or utcnow(), "high", {"price": float(d["price"])},
                                      f"Coinbase {asset}-USD last {float(d['price']):,.2f}"))
        except (ProviderError, KeyError, ValueError, TypeError) as e:
            log.info("coinbase spot %s failed: %s", asset, e)
        need_cross = not items or not self.cache.get(f"xcheck:{asset}", 300)
        if need_cross and asset in KRAKEN_PAIRS:
            try:
                d = await self.kr.request("GET", "/0/public/Ticker", params={"pair": KRAKEN_PAIRS[asset]})
                res = next(iter((d.get("result") or {}).values()))
                px = float(res["c"][0])
                items.append(EvidenceItem("crypto_spot", asset, "kraken", f"{self.kr.base_url}/0/public/Ticker?pair={KRAKEN_PAIRS[asset]}",
                                          utcnow(), "high", {"price": px}, f"Kraken {asset}/USD last {px:,.2f}"))
                self.cache.put(f"xcheck:{asset}", True)
            except (ProviderError, KeyError, ValueError, TypeError, StopIteration) as e:
                log.info("kraken spot %s failed: %s", asset, e)
        if items:
            self.cache.put(f"spot:{asset}", items)
        return items

    async def vol(self, asset: str) -> list[EvidenceItem]:
        cached = self.cache.get(f"vol:{asset}", 900)
        if cached:
            return cached
        items: list[EvidenceItem] = []
        try:
            rows = await self.cb.request("GET", f"/products/{asset}-USD/candles", params={"granularity": 3600})
            rows = sorted(rows or [], key=lambda r: r[0])[-168:]
            closes = [float(r[4]) for r in rows]
            s = realised_vol(closes, 3600)
            if s:
                items.append(EvidenceItem("crypto_vol", asset, "coinbase", f"{self.cb.base_url}/products/{asset}-USD/candles?granularity=3600",
                                          datetime.fromtimestamp(rows[-1][0], tz=timezone.utc), "medium",
                                          {"sigma_annual": s, "n": len(closes), "method": "hourly log-return stdev, 7d"},
                                          f"{asset} realised vol {s:.0%} (7d hourly)"))
        except (ProviderError, ValueError, TypeError, IndexError) as e:
            log.info("coinbase candles %s failed: %s", asset, e)
        if not items and asset in KRAKEN_PAIRS:
            try:
                d = await self.kr.request("GET", "/0/public/OHLC", params={"pair": KRAKEN_PAIRS[asset], "interval": 60})
                res = [v for k, v in (d.get("result") or {}).items() if k != "last"][0][-168:]
                closes = [float(r[4]) for r in res]
                s = realised_vol(closes, 3600)
                if s:
                    items.append(EvidenceItem("crypto_vol", asset, "kraken", f"{self.kr.base_url}/0/public/OHLC", utcnow(), "medium",
                                              {"sigma_annual": s, "n": len(closes), "method": "hourly log-return stdev, 7d"},
                                              f"{asset} realised vol {s:.0%} (Kraken 7d hourly)"))
            except (ProviderError, ValueError, TypeError, IndexError, KeyError) as e:
                log.info("kraken ohlc %s failed: %s", asset, e)
        if items:
            self.cache.put(f"vol:{asset}", items)
        return items

    async def close(self) -> None:
        await self.cb.close()
        await self.kr.close()


class WeatherSource:
    MODELS = "best_match,gfs_seamless,ecmwf_ifs025,icon_seamless"

    def __init__(self, base_url: str, *, timeout: float = 10.0, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.http = HttpClient(base_url, rps=2, timeout=timeout, transport=transport)
        self.cache = _TTLCache()

    async def forecast(self, st: Station) -> list[EvidenceItem]:
        subject = f"{st.lat:.4f},{st.lon:.4f}"
        cached = self.cache.get(subject, 3600)
        if cached:
            return cached
        params = {"latitude": st.lat, "longitude": st.lon, "daily": "temperature_2m_max", "temperature_unit": st.unit,
                  "timezone": st.tz, "forecast_days": 7, "models": self.MODELS}
        try:
            d = await self.http.request("GET", "/v1/forecast", params=params)
        except ProviderError as e:
            log.info("open-meteo %s failed: %s", st.name, e)
            return []
        daily = (d or {}).get("daily") or {}
        days = daily.get("time") or []
        per_day: dict[str, dict[str, float | None]] = {day: {} for day in days}
        for k, vals in daily.items():
            if not k.startswith("temperature_2m_max"):
                continue
            model = k[len("temperature_2m_max"):].lstrip("_") or "default"
            for day, v in zip(days, vals or []):
                per_day[day][model] = v
        url = str(httpx.URL(f"{self.http.base_url}/v1/forecast", params=params))
        item = EvidenceItem("weather_forecast", subject, "open-meteo", url, utcnow(), "medium",
                            {"station": st.name, "unit": st.unit, "daily_max": per_day},
                            f"Open-Meteo daily max for {st.name}: " + ", ".join(
                                f"{day}: {sum(v for v in m.values() if v is not None) / max(1, sum(1 for v in m.values() if v is not None)):.1f}"
                                for day, m in list(per_day.items())[:3] if m))
        self.cache.put(subject, [item])
        return [item]

    async def close(self) -> None:
        await self.http.close()


class SimSource:
    """Simulator feeds - always flagged simulated."""

    def __init__(self, world: SimWorld) -> None:
        self.world = world

    def spot(self) -> list[EvidenceItem]:
        self.world.advance()
        return [EvidenceItem("crypto_spot", "SIMBTC", "argona-sim", "sim://SIMBTC/spot", self.world.clock(), "high",
                             {"price": self.world.spot}, f"SIMBTC {self.world.spot:,.2f} (simulated)", is_simulated=True)]

    def vol(self) -> list[EvidenceItem]:
        hist = self.world.candles()
        s = realised_vol([p for _, p in hist], 60.0)
        if not s:
            return []
        return [EvidenceItem("crypto_vol", "SIMBTC", "argona-sim", "sim://SIMBTC/candles", hist[-1][0], "high",
                             {"sigma_annual": s, "n": len(hist), "method": "1-min log-return stdev"},
                             f"SIMBTC realised vol {s:.0%} (simulated)", is_simulated=True)]

    def forecast(self, market_key: str, mid: str) -> list[EvidenceItem]:
        f = self.world.forecast(mid)
        if not f:
            return []
        return [EvidenceItem("forecast", market_key, "argona-sim-forecaster", f"sim://forecast/{mid}", self.world.clock(), "medium",
                             {"p": f[0], "sigma": f[1]}, f"simulated forecaster: {f[0]:.1%} ± {f[1]:.0%}", is_simulated=True)]


class ForecastFileSource:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._mtime = 0.0
        self._data: dict[str, Any] = {}

    def _load(self) -> None:
        try:
            m = self.path.stat().st_mtime
        except FileNotFoundError:
            self._data = {}
            return
        if m != self._mtime:
            try:
                self._data = json.loads(self.path.read_text())
                self._mtime = m
            except (OSError, ValueError) as e:
                log.warning("forecast file unreadable: %s", e)

    def forecast(self, market_key: str) -> list[EvidenceItem]:
        self._load()
        f = self._data.get(market_key)
        if not isinstance(f, dict) or "p" not in f:
            return []
        as_of = parse_ts(f.get("as_of")) or utcnow()
        if (utcnow() - as_of).total_seconds() > float(f.get("max_age_s", 86400)):
            return []
        return [EvidenceItem("forecast", market_key, str(f.get("source", "operator-file")), str(f.get("url", f"file://{self.path}")),
                             as_of, str(f.get("reliability", "medium")), {"p": float(f["p"]), "sigma": float(f.get("sigma", 0.08))},
                             f"operator forecast {float(f['p']):.1%} from {f.get('source', 'file')}")]
