"""Position sizing. Pure functions, no broker calls."""
from __future__ import annotations

import math

from .brokers import SymbolSpec


def floor_to_step(volume: float, step: float) -> float:
    # small epsilon so 0.3/0.01 = 29.999999 still floors to 30
    return round(math.floor(volume / step + 1e-9) * step, 8)


def loss_per_lot(spec: SymbolSpec, entry: float, sl: float) -> float:
    """Money lost on 1.0 lot if price goes from entry to sl."""
    return abs(entry - sl) / spec.tick_size * spec.tick_value


def total_lots(spec: SymbolSpec, equity: float, risk_percent: float, entry: float, sl: float,
               fixed_lot: float | None, max_lot: float) -> float:
    if fixed_lot:
        lots = fixed_lot
    else:
        per_lot = loss_per_lot(spec, entry, sl)
        if per_lot <= 0:
            return 0.0
        lots = equity * risk_percent / 100.0 / per_lot
    lots = min(lots, max_lot, spec.volume_max)
    return floor_to_step(lots, spec.volume_step)


def split_lots(lots: float, weights: list[float], spec: SymbolSpec) -> list[float]:
    """Split `lots` across take-profit levels by weight.

    Each part is rounded *down* so the total never exceeds `lots` (= never more
    risk than intended). If a part would fall below the broker minimum, fewer
    TP levels are used. Returns [] when even one part is below the minimum.
    """
    for n in range(len(weights), 0, -1):
        w = weights[:n]
        total_w = sum(w)
        parts = [floor_to_step(lots * x / total_w, spec.volume_step) for x in w]
        if all(p >= spec.volume_min for p in parts):
            return parts
    return []
