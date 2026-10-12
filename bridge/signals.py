"""Parse TradingView alert messages into a Signal.

Two formats are accepted:

1. JSON (recommended):
   {"action": "sell", "symbol": "{{ticker}}", "entry": 4180.62,
    "sl": 4215.15, "tp1": 4146.09, "tp2": 4111.56, "tp3": 4077.03}

2. Plain text, as printed by indicators such as "Jackpot Strategy":
   "XAUUSD Sell Entry : 4180.62 Stop loss : 4215.15 TP 1 : 4146.09 TP 2 : 4111.56 TP 3 : 4077.03"
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field


class SignalError(ValueError):
    pass


@dataclass
class Signal:
    action: str                 # "buy", "sell" or "close"
    symbol: str                 # TradingView symbol, e.g. "XAUUSD"
    entry: float | None = None
    sl: float | None = None
    tps: list[float] = field(default_factory=list)

    @property
    def side(self) -> str:
        return self.action


_NUM = r"([0-9]+(?:[.,][0-9]+)?)"
_SEP = r"\s*[:=@]?\s*"
_ACTIONS = {
    "buy": "buy", "long": "buy",
    "sell": "sell", "short": "sell",
    "close": "close", "exit": "close", "flat": "close",
}


def _num(v) -> float | None:
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        return float(v)
    return float(str(v).replace(",", "").strip())


def clean_symbol(raw: str) -> str:
    """'OANDA:XAUUSD' -> 'XAUUSD', 'xauusd' -> 'XAUUSD'."""
    s = raw.strip().upper()
    if ":" in s:
        s = s.split(":", 1)[1]
    return s


def _from_json(data: dict, default_symbol: str | None) -> Signal:
    d = {str(k).lower(): v for k, v in data.items()}
    action = _ACTIONS.get(str(d.get("action") or d.get("side") or "").strip().lower())
    if not action:
        raise SignalError(f"unknown action: {d.get('action')!r}")
    symbol = d.get("symbol") or d.get("ticker") or default_symbol
    if not symbol:
        raise SignalError("symbol missing")
    tps: list[float] = []
    if isinstance(d.get("tp"), list):
        tps = [_num(x) for x in d["tp"] if _num(x) is not None]
    else:
        if d.get("tp") is not None:
            tps.append(_num(d["tp"]))
        i = 1
        while f"tp{i}" in d:
            v = _num(d[f"tp{i}"])
            if v is not None:
                tps.append(v)
            i += 1
    return Signal(
        action=action,
        symbol=clean_symbol(str(symbol)),
        entry=_num(d.get("entry") or d.get("price")),
        sl=_num(d.get("sl") or d.get("stop_loss") or d.get("stoploss")),
        tps=tps,
    )


def _from_text(text: str, default_symbol: str | None) -> Signal:
    low = text.lower()
    m = re.search(r"\b(buy|long|sell|short|close|exit|flat)\b", low)
    if not m:
        raise SignalError("no buy/sell/close keyword in alert text")
    action = _ACTIONS[m.group(1)]

    sym = re.search(r"\b(?:[A-Z]+:)?([A-Z]{6}|XAUUSD|XAGUSD|US30|NAS100|US500|BTCUSD)\b", text.upper())
    symbol = sym.group(1) if sym else default_symbol
    if not symbol:
        raise SignalError("symbol missing (put it in the alert text or the webhook URL ?symbol=)")

    def find(pattern: str) -> float | None:
        mm = re.search(pattern + _SEP + _NUM, low)
        return _num(mm.group(1)) if mm else None

    entry = find(r"entry")
    sl = find(r"(?:stop\s*loss|stoploss|\bsl\b)")
    tps = []
    for _, val in sorted(
        (int(n), _num(v)) for n, v in re.findall(r"\btp\s*([1-9])" + _SEP + _NUM, low)
    ):
        tps.append(val)
    if not tps:
        single = find(r"(?:take\s*profit|\btp\b)")
        if single is not None:
            tps.append(single)
    return Signal(action=action, symbol=clean_symbol(symbol), entry=entry, sl=sl, tps=tps)


def parse_alert(body: str, default_symbol: str | None = None) -> Signal:
    body = (body or "").strip()
    if not body:
        raise SignalError("empty alert body")
    if body.startswith("{"):
        try:
            data = json.loads(body)
        except json.JSONDecodeError as e:
            raise SignalError(f"invalid JSON: {e}") from e
        sig = _from_json(data, default_symbol)
    else:
        sig = _from_text(body, default_symbol)

    if sig.action in ("buy", "sell") and sig.sl is None:
        raise SignalError("refusing to trade without a stop loss")
    return sig
