"""Settings, loaded from environment variables (or a .env file)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field

from dotenv import load_dotenv


def _bool(name: str, default: bool) -> bool:
    v = os.getenv(name)
    if v is None or v.strip() == "":
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


def _float(name: str, default: float | None) -> float | None:
    v = os.getenv(name)
    if v is None or v.strip() == "":
        return default
    return float(v)


def _list(name: str, default: str) -> list[str]:
    return [x.strip() for x in os.getenv(name, default).split(",") if x.strip()]


def _map(name: str) -> dict[str, str]:
    """Parse "XAUUSD:XAUUSDm,EURUSD:EURUSDm" into a dict."""
    out: dict[str, str] = {}
    for pair in _list(name, ""):
        tv, _, broker = pair.partition(":")
        if tv and broker:
            out[tv.strip().upper()] = broker.strip()
    return out


@dataclass
class Settings:
    webhook_token: str
    broker: str = "mt5"                       # "mt5" or "paper"
    dry_run: bool = True                      # True = log orders, never send them
    trading_enabled: bool = True              # master switch (also /halt endpoint)

    mt5_login: int | None = None
    mt5_password: str | None = None
    mt5_server: str | None = None
    mt5_path: str | None = None

    allowed_symbols: list[str] = field(default_factory=lambda: ["XAUUSD"])
    symbol_map: dict[str, str] = field(default_factory=dict)

    risk_percent: float = 0.5                 # % of equity risked per signal
    fixed_lot: float | None = None            # if set, overrides risk sizing
    max_lot: float = 1.0                      # hard cap on total lots per signal
    tp_split: list[float] = field(default_factory=lambda: [0.5, 0.3, 0.2])

    max_daily_loss_percent: float = 3.0
    max_open_positions: int = 6
    close_on_opposite: bool = True            # opposite signal closes existing trades
    max_spread_pct_of_sl: float = 0.15        # spread must be <= 15% of SL distance
    max_entry_drift_pct_of_sl: float = 0.35   # price may be <= 35% of SL distance from alert entry

    breakeven_after_tp1: bool = True          # move SL to entry once TP1 is hit
    breakeven_buffer_points: int = 0          # lock in this many points beyond entry
    monitor_seconds: float = 5.0

    telegram_bot_token: str | None = None
    telegram_chat_id: str | None = None
    notify_rejected: bool = True

    magic: int = 20261012
    deviation_points: int = 50
    dedup_seconds: int = 60
    journal_path: str = "journal.db"
    ip_allowlist: list[str] = field(default_factory=list)

    @classmethod
    def from_env(cls, env_file: str | None = ".env") -> "Settings":
        if env_file:
            load_dotenv(env_file)
        token = os.getenv("WEBHOOK_TOKEN", "")
        if len(token) < 16:
            raise ValueError("WEBHOOK_TOKEN must be set and at least 16 characters long")
        login = os.getenv("MT5_LOGIN")
        split = [float(x) for x in _list("TP_SPLIT", "0.5,0.3,0.2")]
        return cls(
            webhook_token=token,
            broker=os.getenv("BROKER", "mt5").lower(),
            dry_run=_bool("DRY_RUN", True),
            trading_enabled=_bool("TRADING_ENABLED", True),
            mt5_login=int(login) if login else None,
            mt5_password=os.getenv("MT5_PASSWORD") or None,
            mt5_server=os.getenv("MT5_SERVER") or None,
            mt5_path=os.getenv("MT5_PATH") or None,
            allowed_symbols=[s.upper() for s in _list("ALLOWED_SYMBOLS", "XAUUSD")],
            symbol_map=_map("SYMBOL_MAP"),
            risk_percent=_float("RISK_PERCENT", 0.5),
            fixed_lot=_float("FIXED_LOT", None),
            max_lot=_float("MAX_LOT", 1.0),
            tp_split=split,
            max_daily_loss_percent=_float("MAX_DAILY_LOSS_PERCENT", 3.0),
            max_open_positions=int(os.getenv("MAX_OPEN_POSITIONS", "6")),
            close_on_opposite=_bool("CLOSE_ON_OPPOSITE", True),
            max_spread_pct_of_sl=_float("MAX_SPREAD_PCT_OF_SL", 0.15),
            max_entry_drift_pct_of_sl=_float("MAX_ENTRY_DRIFT_PCT_OF_SL", 0.35),
            breakeven_after_tp1=_bool("BREAKEVEN_AFTER_TP1", True),
            breakeven_buffer_points=int(os.getenv("BREAKEVEN_BUFFER_POINTS", "0")),
            monitor_seconds=_float("MONITOR_SECONDS", 5.0),
            telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN") or None,
            telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID") or None,
            notify_rejected=_bool("NOTIFY_REJECTED", True),
            magic=int(os.getenv("MAGIC", "20261012")),
            deviation_points=int(os.getenv("DEVIATION_POINTS", "50")),
            dedup_seconds=int(os.getenv("DEDUP_SECONDS", "60")),
            journal_path=os.getenv("JOURNAL_PATH", "journal.db"),
            ip_allowlist=_list("IP_ALLOWLIST", ""),
        )
