"""MetaTrader 5 broker (Exness or any MT5 broker).

Needs Windows, a running MT5 terminal, and `pip install MetaTrader5`.
"""
from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone

from . import Account, OrderResult, Position, SymbolSpec

try:
    import MetaTrader5 as mt5
except ImportError:  # non-Windows or package missing
    mt5 = None


class MT5Broker:
    def __init__(self, settings):
        if mt5 is None:
            raise RuntimeError(
                "MetaTrader5 package not available. It only runs on Windows: "
                "pip install MetaTrader5 (or set BROKER=paper to test)."
            )
        self.s = settings
        # The MT5 Python API is not safe to call from several threads at once.
        self._lock = threading.RLock()

    # -- connection -------------------------------------------------------
    def connect(self) -> None:
        with self._lock:
            kwargs = {"timeout": 60_000}
            if self.s.mt5_path:
                kwargs["path"] = self.s.mt5_path
            if self.s.mt5_login:
                kwargs.update(login=self.s.mt5_login, password=self.s.mt5_password,
                              server=self.s.mt5_server)
            if not mt5.initialize(**kwargs):
                raise RuntimeError(f"MT5 initialize failed: {mt5.last_error()}")
            info = mt5.terminal_info()
            if info is not None and not info.trade_allowed:
                # Not fatal in dry-run, but live orders will be rejected.
                print("WARNING: 'Algo Trading' is disabled in the MT5 terminal - enable it.")

    def is_connected(self) -> bool:
        with self._lock:
            return mt5.terminal_info() is not None and mt5.account_info() is not None

    def _ensure(self) -> None:
        if not self.is_connected():
            self.connect()

    # -- reads ------------------------------------------------------------
    def account(self) -> Account:
        with self._lock:
            self._ensure()
            a = mt5.account_info()
            return Account(balance=a.balance, equity=a.equity, currency=a.currency)

    def symbol(self, name: str) -> SymbolSpec:
        with self._lock:
            self._ensure()
            info = mt5.symbol_info(name)
            if info is None:
                raise RuntimeError(f"symbol {name!r} not found at broker (check SYMBOL_MAP)")
            if not info.visible:
                mt5.symbol_select(name, True)
            tick = mt5.symbol_info_tick(name)
            if tick is None or tick.bid <= 0:
                raise RuntimeError(f"no live price for {name} (market closed?)")
            return SymbolSpec(
                name=name, digits=info.digits, point=info.point,
                tick_size=info.trade_tick_size,
                tick_value=info.trade_tick_value_loss or info.trade_tick_value,
                volume_min=info.volume_min, volume_max=info.volume_max,
                volume_step=info.volume_step, stops_level=info.trade_stops_level,
                bid=tick.bid, ask=tick.ask,
            )

    def positions(self, symbol: str | None = None) -> list[Position]:
        with self._lock:
            self._ensure()
            raw = mt5.positions_get(symbol=symbol) if symbol else mt5.positions_get()
            if raw is None:   # error, not "no positions" (that is an empty tuple)
                raise RuntimeError(f"positions_get failed: {mt5.last_error()}")
            out = []
            for p in raw:
                if p.magic != self.s.magic:
                    continue  # never touch manual trades or other EAs
                out.append(Position(
                    ticket=p.ticket, symbol=p.symbol,
                    side="buy" if p.type == mt5.POSITION_TYPE_BUY else "sell",
                    volume=p.volume, open_price=p.price_open, sl=p.sl, tp=p.tp,
                    profit=p.profit + p.swap,
                ))
            return out

    def today_realized_pnl(self) -> float:
        with self._lock:
            self._ensure()
            now = datetime.now(timezone.utc)
            start = now.replace(hour=0, minute=0, second=0, microsecond=0)
            deals = mt5.history_deals_get(start, now + timedelta(days=1)) or ()
            return sum(
                d.profit + d.commission + d.swap + getattr(d, "fee", 0.0)
                for d in deals if d.magic == self.s.magic
            )

    # -- trading ----------------------------------------------------------
    @staticmethod
    def _filling(name: str) -> int:
        mode = mt5.symbol_info(name).filling_mode
        if mode & 1:
            return mt5.ORDER_FILLING_FOK
        if mode & 2:
            return mt5.ORDER_FILLING_IOC
        return mt5.ORDER_FILLING_RETURN

    def _send(self, request: dict) -> OrderResult:
        r = mt5.order_send(request)
        if r is None:
            return OrderResult(ok=False, message=f"order_send failed: {mt5.last_error()}")
        ok = r.retcode == mt5.TRADE_RETCODE_DONE
        return OrderResult(ok=ok, ticket=r.order or None, price=r.price or None,
                           retcode=r.retcode, message=r.comment)

    def market_order(self, symbol, side, volume, sl, tp, comment) -> OrderResult:
        with self._lock:
            self._ensure()
            tick = mt5.symbol_info_tick(symbol)
            req = {
                "action": mt5.TRADE_ACTION_DEAL,
                "symbol": symbol,
                "volume": float(volume),
                "type": mt5.ORDER_TYPE_BUY if side == "buy" else mt5.ORDER_TYPE_SELL,
                "price": tick.ask if side == "buy" else tick.bid,
                "sl": float(sl),
                "tp": float(tp or 0.0),
                "deviation": self.s.deviation_points,
                "magic": self.s.magic,
                "comment": comment[:31],
                "type_time": mt5.ORDER_TIME_GTC,
                "type_filling": self._filling(symbol),
            }
            return self._send(req)

    def close_position(self, pos: Position) -> OrderResult:
        with self._lock:
            self._ensure()
            tick = mt5.symbol_info_tick(pos.symbol)
            req = {
                "action": mt5.TRADE_ACTION_DEAL,
                "symbol": pos.symbol,
                "volume": float(pos.volume),
                "type": mt5.ORDER_TYPE_SELL if pos.side == "buy" else mt5.ORDER_TYPE_BUY,
                "position": pos.ticket,
                "price": tick.bid if pos.side == "buy" else tick.ask,
                "deviation": self.s.deviation_points,
                "magic": self.s.magic,
                "comment": "tv-bridge close",
                "type_time": mt5.ORDER_TIME_GTC,
                "type_filling": self._filling(pos.symbol),
            }
            return self._send(req)

    def modify_sl(self, pos: Position, sl: float) -> OrderResult:
        with self._lock:
            self._ensure()
            return self._send({
                "action": mt5.TRADE_ACTION_SLTP,
                "symbol": pos.symbol,
                "position": pos.ticket,
                "sl": float(sl),
                "tp": float(pos.tp),
                "magic": self.s.magic,
            })
