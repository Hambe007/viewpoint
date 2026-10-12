"""Broker interface shared by the MT5 and paper implementations."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass
class SymbolSpec:
    name: str
    digits: int
    point: float
    tick_size: float
    tick_value: float          # account-currency value of one tick move for 1.0 lot
    volume_min: float
    volume_max: float
    volume_step: float
    stops_level: int           # minimum SL/TP distance from price, in points
    bid: float
    ask: float

    @property
    def spread(self) -> float:
        return self.ask - self.bid


@dataclass
class Account:
    balance: float
    equity: float
    currency: str


@dataclass
class Position:
    ticket: int
    symbol: str
    side: str                  # "buy" or "sell"
    volume: float
    open_price: float
    sl: float
    tp: float
    profit: float              # floating P/L incl. swap


@dataclass
class OrderResult:
    ok: bool
    ticket: int | None = None
    price: float | None = None
    retcode: int | None = None
    message: str = ""


class Broker(Protocol):
    def connect(self) -> None: ...
    def is_connected(self) -> bool: ...
    def account(self) -> Account: ...
    def symbol(self, name: str) -> SymbolSpec: ...
    def positions(self, symbol: str | None = None) -> list[Position]: ...
    def market_order(self, symbol: str, side: str, volume: float, sl: float,
                     tp: float | None, comment: str) -> OrderResult: ...
    def close_position(self, pos: Position) -> OrderResult: ...
    def today_realized_pnl(self) -> float: ...


def make_broker(settings) -> Broker:
    if settings.broker == "paper":
        from .paper import PaperBroker
        return PaperBroker()
    if settings.broker == "mt5":
        from .mt5 import MT5Broker
        return MT5Broker(settings)
    raise ValueError(f"unknown BROKER {settings.broker!r} (use 'mt5' or 'paper')")
