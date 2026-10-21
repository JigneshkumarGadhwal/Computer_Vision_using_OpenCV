"""Configuration.

Two layers:

* ``Settings`` - static, environment-driven configuration (secrets, hosts, providers).
  Loaded once at startup from environment variables / ``.env``. Never persisted, never logged.
* ``RuntimeSettings`` - operator-tunable trading policy (risk limits, thresholds, categories,
  notification preferences). Defaults come from ``Settings``; changes made from the dashboard
  are persisted in the database (``settings_kv``) with an audit trail (``config_history``).
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ALL_CATEGORIES = ["crypto", "weather", "economics", "commodities", "politics", "sports", "other"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="ZIGGGY_", env_file=".env", env_file_encoding="utf-8", extra="ignore", env_ignore_empty=True
    )

    # --- application ---
    host: str = "127.0.0.1"
    port: int = 8000
    log_level: str = "INFO"
    log_json: bool = True
    data_dir: Path = Path("./data")
    database_url: str = ""  # default: sqlite+aiosqlite:///<data_dir>/zigggy.db
    frontend_dist: Path = Path(__file__).resolve().parents[2] / "frontend" / "dist"
    api_token: SecretStr | None = None
    allow_insecure_no_auth: bool = False
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    # --- providers ---
    providers: str = "polymarket,kalshi"  # comma separated: polymarket,kalshi,sim
    http_timeout_s: float = 15.0
    polymarket_gamma_url: str = "https://gamma-api.polymarket.com"
    polymarket_clob_url: str = "https://clob.polymarket.com"
    polymarket_max_markets: int = 1500
    polymarket_rps: float = 8.0
    kalshi_api_url: str = "https://api.elections.kalshi.com/trade-api/v2"
    kalshi_max_markets: int = 3000
    kalshi_series: str = ""  # optional comma list of series tickers to restrict discovery
    kalshi_rps: float = 8.0
    sim_seed: int = 7
    sim_markets: int = 24

    # --- credentials (LIVE only; never required for paper trading) ---
    kalshi_key_id: SecretStr | None = None
    kalshi_private_key_path: Path | None = None
    polymarket_private_key: SecretStr | None = None
    polymarket_wallet: str | None = None

    # --- live trading master switch (server side) ---
    live_trading_enabled: bool = False
    live_resume_on_restart: bool = False

    # --- external evidence sources (free, keyless) ---
    coinbase_url: str = "https://api.exchange.coinbase.com"
    kraken_url: str = "https://api.kraken.com"
    open_meteo_url: str = "https://api.open-meteo.com"

    # --- optional LLM research (BRAM) ---
    llm_provider: Literal["none", "anthropic"] = "none"
    anthropic_api_key: SecretStr | None = None
    llm_model: str = "claude-opus-5-5"
    llm_max_reviews_per_hour: int = 20

    # --- notifications ---
    telegram_bot_token: SecretStr | None = None
    telegram_chat_id: str | None = None
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_user: str | None = None
    smtp_password: SecretStr | None = None
    smtp_from: str | None = None
    smtp_to: str | None = None
    smtp_starttls: bool = True

    # --- scheduler (seconds) ---
    discovery_interval_s: int = 600
    scan_interval_s: int = 45
    order_monitor_interval_s: int = 10
    reconcile_interval_s: int = 60
    settlement_interval_s: int = 180
    snapshot_interval_s: int = 60
    health_interval_s: int = 30
    metrics_interval_s: int = 3600
    maintenance_interval_s: int = 21600
    max_books_per_cycle: int = 60

    # --- defaults for runtime settings ---
    paper_starting_capital: float = 10_000.0
    start_mode: Literal["STOPPED", "PAPER"] = "PAPER"

    @property
    def provider_list(self) -> list[str]:
        return [p.strip().lower() for p in self.providers.split(",") if p.strip()]

    @property
    def resolved_database_url(self) -> str:
        if self.database_url:
            return self.database_url
        self.data_dir.mkdir(parents=True, exist_ok=True)
        return f"sqlite+aiosqlite:///{(self.data_dir / 'zigggy.db').resolve()}"

    @property
    def is_loopback(self) -> bool:
        return self.host in ("127.0.0.1", "localhost", "::1")


class RiskLimits(BaseModel):
    """Hard limits enforced by TESS on every proposed order (paper and live alike)."""

    min_net_edge: float = Field(0.03, ge=0.0, le=0.5, description="Min execution-adjusted EV per $1 contract")
    min_confidence: float = Field(0.35, ge=0.0, le=1.0)
    kelly_fraction: float = Field(0.25, gt=0.0, le=1.0)
    max_position_usd: float = Field(250.0, gt=0)
    max_market_exposure_pct: float = Field(0.05, gt=0, le=1)
    max_event_exposure_pct: float = Field(0.08, gt=0, le=1)
    max_category_exposure_pct: float = Field(0.25, gt=0, le=1)
    max_total_exposure_pct: float = Field(0.60, gt=0, le=1)
    max_daily_loss_usd: float = Field(300.0, gt=0)
    max_drawdown_pct: float = Field(0.15, gt=0, le=1)
    max_open_orders: int = Field(20, ge=1)
    min_liquidity_usd: float = Field(150.0, ge=0)
    max_spread: float = Field(0.10, gt=0, le=1)
    max_staleness_s: int = Field(180, ge=5)
    min_hours_to_resolution: float = Field(0.25, ge=0)
    max_days_to_resolution: float = Field(45.0, gt=0)
    min_price: float = Field(0.03, ge=0.0, lt=1)
    max_price: float = Field(0.97, gt=0, le=1)
    slippage_buffer: float = Field(0.005, ge=0, le=0.1, description="Extra per-contract execution cost assumption")
    order_ttl_s: int = Field(300, ge=10)
    reentry_cooldown_s: int = Field(600, ge=0, description="No new entry in a market within this time of the last one")
    allow_market_orders: bool = False
    market_prior_sigma: float = Field(0.06, ge=0.01, le=0.5,
                                      description="Uncertainty assigned to the market-implied prior (lower = defer more to the market)")
    exit_on_edge_reversal: bool = True
    min_exit_edge: float = Field(0.02, ge=0)


class NotificationPrefs(BaseModel):
    enabled: bool = True
    min_severity: Literal["info", "warning", "critical"] = "warning"
    notify_opportunities: bool = False
    notify_orders: bool = True
    notify_fills: bool = True
    notify_risk: bool = True
    notify_provider: bool = True


class RuntimeSettings(BaseModel):
    categories: list[str] = Field(default_factory=lambda: list(ALL_CATEGORIES))
    scanning_enabled: bool = True
    trading_paused: bool = False
    risk: RiskLimits = Field(default_factory=RiskLimits)
    notifications: NotificationPrefs = Field(default_factory=NotificationPrefs)
    emergency_cancel_orders: bool = True

    @field_validator("categories")
    @classmethod
    def _valid_categories(cls, v: list[str]) -> list[str]:
        bad = [c for c in v if c not in ALL_CATEGORIES]
        if bad:
            raise ValueError(f"unknown categories: {bad}")
        return v


@lru_cache
def get_settings() -> Settings:
    return Settings()
