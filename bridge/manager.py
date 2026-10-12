"""Watches open trades: moves SL to break-even once TP1 is hit, reports closes."""
from __future__ import annotations

import threading

from .brokers import Broker
from .config import Settings
from .journal import Journal
from .notifier import Notifier


class TradeManager:
    def __init__(self, settings: Settings, broker: Broker, journal: Journal, notifier: Notifier):
        self.s, self.broker, self.journal, self.notify = settings, broker, journal, notifier
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def register(self, result) -> None:
        """Remember the positions opened by one signal so they can be managed together."""
        tickets = [o["ticket"] for o in result.orders
                   if o.get("side") and o.get("ok") and o.get("ticket")]
        if tickets:
            first = next(o for o in result.orders if o.get("ticket") == tickets[0])
            self.journal.add_group(first["symbol"], first["side"], tickets)

    def check_once(self) -> None:
        for g in self.journal.open_groups():
            open_pos = {p.ticket: p for p in self.broker.positions(g["symbol"])}
            remaining = [open_pos[t] for t in g["tickets"] if t in open_pos]

            if not remaining:
                self.journal.update_group(g["id"], closed=True)
                self.notify.send(f"🏁 {g['side'].upper()} {g['symbol']} trade fully closed "
                                 f"({len(g['tickets'])} position(s))")
                continue

            tp1_hit = g["tickets"][0] not in open_pos
            if not (self.s.breakeven_after_tp1 and tp1_hit and not g["be_done"]):
                continue

            spec = self.broker.symbol(g["symbol"])
            buffer = self.s.breakeven_buffer_points * spec.point
            min_dist = spec.stops_level * spec.point
            moved, failed = [], []
            for p in remaining:
                if p.side == "buy":
                    be = round(p.open_price + buffer, spec.digits)
                    needed = p.sl < be
                    valid = be < spec.bid - min_dist
                else:
                    be = round(p.open_price - buffer, spec.digits)
                    needed = p.sl == 0 or p.sl > be
                    valid = be > spec.ask + min_dist
                if not needed:
                    continue
                if not valid:
                    failed.append(f"#{p.ticket}: price too close to entry")
                    continue
                r = self.broker.modify_sl(p, be)
                (moved if r.ok else failed).append(f"#{p.ticket}" if r.ok else f"#{p.ticket}: {r.message}")

            if failed:
                # Try again on the next pass (price may move away from entry).
                print(f"break-even not yet possible: {failed}")
                continue
            self.journal.update_group(g["id"], be_done=True)
            if moved:
                self.notify.send(f"🛡️ TP1 hit on {g['symbol']} - SL moved to break-even on "
                                 f"{len(moved)} remaining position(s)")

    # -- background loop --------------------------------------------------
    def _run(self) -> None:
        while not self._stop.wait(self.s.monitor_seconds):
            try:
                self.check_once()
            except Exception as e:  # broker hiccup: log and keep watching
                print(f"trade manager error: {type(e).__name__}: {e}")

    def start(self) -> None:
        if self.s.dry_run:
            return     # nothing real to manage
        self._thread = threading.Thread(target=self._run, daemon=True, name="trade-manager")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
