"""In-memory broker for tests and for trying the bridge on any OS."""
from __future__ import annotations

import itertools

from . import Account, OrderResult, Position, SymbolSpec

# Specs modelled on Exness contracts (gold = 100 oz per lot).
_SPECS = {
    "XAUUSD": dict(digits=3, point=0.001, tick_size=0.001, tick_value=0.1, spread=0.20),
    "EURUSD": dict(digits=5, point=0.00001, tick_size=0.00001, tick_value=1.0, spread=0.00008),
    "GBPUSD": dict(digits=5, point=0.00001, tick_size=0.00001, tick_value=1.0, spread=0.00010),
}


class PaperBroker:
    def __init__(self, balance: float = 10_000.0):
        self.balance = balance
        self.realized = 0.0
        self.prices: dict[str, float] = {}       # mid prices
        self._positions: dict[int, Position] = {}
        self._tickets = itertools.count(1000)

    # paper-only helper: the engine feeds it the alert's entry price
    def set_price(self, symbol: str, mid: float) -> None:
        self.prices[self._base(symbol)] = mid

    @staticmethod
    def _base(name: str) -> str:
        return name.upper()[:6]   # "XAUUSDm" / "XAUUSD.pro" -> "XAUUSD"

    def _spec(self, name: str) -> dict:
        return _SPECS.get(self._base(name), _SPECS["EURUSD"])

    def connect(self) -> None:
        pass

    def is_connected(self) -> bool:
        return True

    def account(self) -> Account:
        floating = sum(p.profit for p in self._positions.values())
        return Account(balance=self.balance, equity=self.balance + floating, currency="USD")

    def symbol(self, name: str) -> SymbolSpec:
        s = self._spec(name)
        mid = self.prices.get(self._base(name))
        if mid is None:
            raise RuntimeError(f"paper broker has no price for {name}")
        half = s["spread"] / 2
        return SymbolSpec(
            name=name, digits=s["digits"], point=s["point"], tick_size=s["tick_size"],
            tick_value=s["tick_value"], volume_min=0.01, volume_max=200.0, volume_step=0.01,
            stops_level=0, bid=round(mid - half, s["digits"]), ask=round(mid + half, s["digits"]),
        )

    def positions(self, symbol: str | None = None) -> list[Position]:
        return [p for p in self._positions.values() if symbol is None or p.symbol == symbol]

    def market_order(self, symbol, side, volume, sl, tp, comment) -> OrderResult:
        spec = self.symbol(symbol)
        price = spec.ask if side == "buy" else spec.bid
        ticket = next(self._tickets)
        self._positions[ticket] = Position(ticket, symbol, side, volume, price, sl, tp or 0.0, 0.0)
        return OrderResult(ok=True, ticket=ticket, price=price, retcode=10009, message="paper fill")

    def close_position(self, pos: Position) -> OrderResult:
        self._positions.pop(pos.ticket, None)
        self.realized += pos.profit
        self.balance += pos.profit
        return OrderResult(ok=True, ticket=pos.ticket, message="paper close")

    def modify_sl(self, pos: Position, sl: float) -> OrderResult:
        self._positions[pos.ticket].sl = sl
        return OrderResult(ok=True, ticket=pos.ticket, message="paper modify")

    def today_realized_pnl(self) -> float:
        return self.realized
