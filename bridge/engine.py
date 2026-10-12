"""Turns a Signal into broker orders, after running every safety check."""
from __future__ import annotations

import threading
from dataclasses import asdict, dataclass, field

from .brokers import Broker
from .config import Settings
from .risk import split_lots, total_lots
from .signals import Signal


@dataclass
class Result:
    status: str                 # "filled", "dry_run", "partial", "closed", "rejected", "skipped", "error"
    reason: str = ""
    orders: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


class Engine:
    def __init__(self, settings: Settings, broker: Broker):
        self.s = settings
        self.broker = broker
        self.halted = not settings.trading_enabled
        self._lock = threading.Lock()   # one signal at a time

    def broker_symbol(self, tv_symbol: str) -> str:
        return self.s.symbol_map.get(tv_symbol.upper(), tv_symbol)

    def handle(self, sig: Signal) -> Result:
        with self._lock:
            if sig.symbol.upper() not in self.s.allowed_symbols:
                return Result("rejected", f"{sig.symbol} not in ALLOWED_SYMBOLS")
            if sig.action == "close":
                return self._close_all(self.broker_symbol(sig.symbol), "close signal")
            if self.halted:
                return Result("rejected", "trading is halted")
            return self._open(sig)

    # ------------------------------------------------------------------
    def _close_all(self, symbol: str, why: str) -> Result:
        positions = self.broker.positions(symbol)
        if not positions:
            return Result("skipped", f"no open positions on {symbol}")
        orders = []
        for p in positions:
            if self.s.dry_run:
                orders.append({"ticket": p.ticket, "action": "close", "ok": True, "dry_run": True})
                continue
            r = self.broker.close_position(p)
            orders.append({"ticket": p.ticket, "action": "close", "ok": r.ok,
                           "retcode": r.retcode, "message": r.message})
        ok = all(o["ok"] for o in orders)
        return Result("dry_run" if self.s.dry_run else ("closed" if ok else "partial"), why, orders)

    def _open(self, sig: Signal) -> Result:
        s, side = self.s, sig.action
        symbol = self.broker_symbol(sig.symbol)

        if hasattr(self.broker, "set_price") and sig.entry:
            self.broker.set_price(symbol, sig.entry)        # paper broker only
        spec = self.broker.symbol(symbol)
        price = spec.ask if side == "buy" else spec.bid
        sl = round(sig.sl, spec.digits)
        min_dist = spec.stops_level * spec.point

        # 1. Stop loss must be on the correct side of the current price.
        if side == "buy" and not sl < price - min_dist:
            return Result("rejected", f"buy SL {sl} is not below price {price}")
        if side == "sell" and not sl > price + min_dist:
            return Result("rejected", f"sell SL {sl} is not above price {price}")
        sl_dist = abs(price - sl)

        # 2. Spread filter (protects against news spikes / rollover).
        if spec.spread > s.max_spread_pct_of_sl * sl_dist:
            return Result("rejected", f"spread {spec.spread:.{spec.digits}f} too wide for SL distance {sl_dist:.{spec.digits}f}")

        # 3. Price must still be close to the alert's entry.
        if sig.entry:
            drift = abs(price - sig.entry)
            allowed = s.max_entry_drift_pct_of_sl * abs(sig.entry - sl)
            if drift > allowed:
                return Result("rejected", f"price {price} moved {drift:.{spec.digits}f} from entry {sig.entry} (max {allowed:.{spec.digits}f})")

        # 4. Keep only take-profits that are on the profit side of price.
        if side == "buy":
            tps = [round(t, spec.digits) for t in sig.tps if t > price + min_dist]
        else:
            tps = [round(t, spec.digits) for t in sig.tps if t < price - min_dist]

        # 5. Existing trades on this symbol.
        existing = self.broker.positions(symbol)
        if any(p.side == side for p in existing):
            return Result("skipped", f"already in a {side} trade on {symbol}")
        pre_orders: list[dict] = []
        if existing:
            if not s.close_on_opposite:
                return Result("rejected", f"opposite trade open on {symbol} and CLOSE_ON_OPPOSITE=false")
            closed = self._close_all(symbol, "opposite signal")
            pre_orders = closed.orders
            if closed.status == "partial":
                return Result("error", "could not close opposite positions", pre_orders)

        # 6. Daily loss limit.
        acct = self.broker.account()
        realized = self.broker.today_realized_pnl()
        floating = sum(p.profit for p in self.broker.positions())
        day_start = acct.balance - realized
        if realized + floating <= -day_start * s.max_daily_loss_percent / 100.0:
            return Result("rejected", f"daily loss limit hit ({realized + floating:.2f} {acct.currency})", pre_orders)

        # 7. Size the trade.
        lots = total_lots(spec, acct.equity, s.risk_percent, price, sl, s.fixed_lot, s.max_lot)
        weights = s.tp_split[:len(tps)] if tps else [1.0]
        parts = split_lots(lots, weights, spec)
        if not parts:
            return Result("rejected", f"risk {s.risk_percent}% gives {lots} lots, below broker minimum {spec.volume_min}", pre_orders)

        # positions on this symbol were opposite trades that are now closed
        open_count = len([p for p in self.broker.positions() if p.symbol != symbol])
        if open_count + len(parts) > s.max_open_positions:
            return Result("rejected", f"MAX_OPEN_POSITIONS={s.max_open_positions} would be exceeded", pre_orders)

        # 8. Send one order per take-profit level.
        orders = list(pre_orders)
        for i, vol in enumerate(parts):
            tp = tps[i] if i < len(tps) else None
            comment = f"tv {side} tp{i + 1}" if tp else f"tv {side}"
            info = {"symbol": symbol, "side": side, "volume": vol, "sl": sl, "tp": tp, "price": price}
            if s.dry_run:
                orders.append({**info, "ok": True, "dry_run": True})
                continue
            r = self.broker.market_order(symbol, side, vol, sl, tp, comment)
            orders.append({**info, "ok": r.ok, "ticket": r.ticket, "fill": r.price,
                           "retcode": r.retcode, "message": r.message})
            if not r.ok:
                break

        if s.dry_run:
            return Result("dry_run", f"would {side} {sum(parts):.2f} lots {symbol}", orders)
        new = orders[len(pre_orders):]
        if all(o["ok"] for o in new) and len(new) == len(parts):
            return Result("filled", f"{side} {sum(parts):.2f} lots {symbol}", orders)
        if any(o["ok"] for o in new):
            return Result("partial", "some orders failed", orders)
        return Result("error", new[-1].get("message", "order failed"), orders)
