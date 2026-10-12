"""HTTP server that TradingView alerts post to.

Run:  python -m bridge            (see README.md)
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from .brokers import make_broker
from .config import Settings
from .engine import Engine, Result
from .journal import Journal
from .signals import SignalError, parse_alert

# Addresses TradingView sends webhooks from (https://www.tradingview.com/support/solutions/43000529348).
TRADINGVIEW_IPS = ["52.89.214.238", "34.212.75.30", "54.218.53.128", "52.32.178.7"]


def create_app(settings: Settings, broker=None) -> FastAPI:
    broker = broker or make_broker(settings)
    engine = Engine(settings, broker)
    journal = Journal(settings.journal_path)
    seen: dict[str, float] = {}
    allow = [ip for ip in settings.ip_allowlist if ip.lower() != "tradingview"]
    if any(ip.lower() == "tradingview" for ip in settings.ip_allowlist):
        allow += TRADINGVIEW_IPS

    @asynccontextmanager
    async def lifespan(_app):
        broker.connect()
        mode = "DRY RUN (no real orders)" if settings.dry_run else "LIVE TRADING"
        print(f"Bridge ready: broker={settings.broker} mode={mode} symbols={settings.allowed_symbols}")
        yield

    app = FastAPI(title="TradingView -> MT5 bridge", lifespan=lifespan)
    app.state.engine, app.state.journal = engine, journal

    def _auth(token: str | None) -> None:
        if not token or not hmac.compare_digest(token, settings.webhook_token):
            raise HTTPException(status_code=401, detail="bad token")

    def _client_ip(request: Request) -> str:
        # Behind Cloudflare Tunnel / ngrok the real address is in a header.
        for h in ("cf-connecting-ip", "x-forwarded-for"):
            if request.headers.get(h):
                return request.headers[h].split(",")[0].strip()
        return request.client.host if request.client else ""

    @app.post("/webhook")
    async def webhook(request: Request, token: str | None = None, symbol: str | None = None):
        raw = (await request.body()).decode("utf-8", errors="replace")
        logged = raw.replace(settings.webhook_token, "***")   # never store the secret
        ip = _client_ip(request)

        if allow and ip not in allow:
            journal.log(ip, logged, None, "forbidden", {"reason": "ip not allowed"})
            raise HTTPException(status_code=403, detail="ip not allowed")

        # Token may be in the URL (?token=) or in a JSON body field "token"/"secret".
        body_token = None
        if raw.lstrip().startswith("{"):
            try:
                data = json.loads(raw)
                body_token = data.get("token") or data.get("secret")
            except (json.JSONDecodeError, AttributeError):
                pass
        try:
            _auth(token or body_token)
        except HTTPException:
            journal.log(ip, "<redacted>", None, "unauthorized", {})
            raise

        # Drop duplicates (TradingView can re-send, or an alert can fire twice).
        now = time.time()
        key = hashlib.sha256(raw.encode()).hexdigest()
        for k, t in list(seen.items()):
            if now - t > settings.dedup_seconds:
                del seen[k]
        if key in seen:
            res = Result("skipped", "duplicate alert")
            journal.log(ip, logged, None, res.status, res.to_dict())
            return res.to_dict()
        seen[key] = now

        try:
            sig = parse_alert(raw, default_symbol=symbol)
        except SignalError as e:
            res = Result("rejected", f"could not parse alert: {e}")
            journal.log(ip, logged, None, res.status, res.to_dict())
            return JSONResponse(res.to_dict(), status_code=400)

        try:
            res = engine.handle(sig)
        except Exception as e:  # broker/network failure - log it, don't crash
            res = Result("error", f"{type(e).__name__}: {e}")
        journal.log(ip, logged, sig.__dict__, res.status, res.to_dict())
        print(f"[{res.status}] {sig.action} {sig.symbol}: {res.reason}")
        return res.to_dict()

    @app.get("/health")
    def health():
        out = {"ok": True, "dry_run": settings.dry_run, "halted": engine.halted,
               "broker": settings.broker}
        try:
            a = broker.account()
            out.update(connected=broker.is_connected(), balance=a.balance,
                       equity=a.equity, currency=a.currency)
        except Exception as e:
            out.update(ok=False, connected=False, error=str(e))
        return out

    @app.get("/journal")
    def recent(token: str, limit: int = 20):
        _auth(token)
        return journal.recent(min(limit, 200))

    @app.post("/halt")
    def halt(token: str, close_all: bool = False):
        """Kill switch: stop opening new trades (and optionally close everything)."""
        _auth(token)
        engine.halted = True
        closed = []
        if close_all:
            for sym in {p.symbol for p in broker.positions()}:
                closed.append(engine._close_all(sym, "halt").to_dict())
        journal.log(None, "", None, "halted", {"close_all": close_all, "closed": closed})
        return {"halted": True, "closed": closed}

    @app.post("/resume")
    def resume(token: str):
        _auth(token)
        engine.halted = False
        journal.log(None, "", None, "resumed", {})
        return {"halted": False}

    return app
