import json

import pytest
from fastapi.testclient import TestClient

from bridge.app import create_app
from bridge.brokers.paper import PaperBroker
from bridge.config import Settings
from bridge.engine import Engine
from bridge.risk import split_lots, total_lots
from bridge.signals import SignalError, parse_alert

TOKEN = "test-token-1234567890"

# The alert from the user's "Jackpot Strategy" chart (XAUUSD 1H).
JACKPOT_TEXT = ("XAUUSD Sell Entry : 4180.62 Stop loss : 4215.15 "
                "TP 1 : 4146.09 TP 2 : 4111.56 TP 3 : 4077.03")


def settings(**kw) -> Settings:
    base = dict(webhook_token=TOKEN, broker="paper", dry_run=False, journal_path=":memory:",
                allowed_symbols=["XAUUSD", "EURUSD"])
    base.update(kw)
    return Settings(**base)


# ---------------------------------------------------------------- parsing
def test_parse_jackpot_text():
    s = parse_alert(JACKPOT_TEXT)
    assert (s.action, s.symbol, s.entry, s.sl) == ("sell", "XAUUSD", 4180.62, 4215.15)
    assert s.tps == [4146.09, 4111.56, 4077.03]


def test_parse_json_with_exchange_prefix():
    s = parse_alert(json.dumps({"action": "BUY", "symbol": "OANDA:XAUUSD", "entry": "2400.5",
                                "sl": 2390, "tp1": 2410, "tp2": 2420}))
    assert (s.action, s.symbol, s.entry, s.sl, s.tps) == ("buy", "XAUUSD", 2400.5, 2390.0, [2410.0, 2420.0])


def test_parse_symbol_from_url_param():
    s = parse_alert("Buy entry 1.0850 SL 1.0820 TP 1.0900", default_symbol="EURUSD")
    assert (s.symbol, s.sl, s.tps) == ("EURUSD", 1.082, [1.09])


def test_refuses_trade_without_stop_loss():
    with pytest.raises(SignalError):
        parse_alert('{"action":"buy","symbol":"XAUUSD"}')


def test_close_needs_no_stop():
    assert parse_alert("close XAUUSD").action == "close"


# ---------------------------------------------------------------- sizing
def test_lot_size_matches_risk():
    b = PaperBroker()
    b.set_price("XAUUSD", 2400.0)
    spec = b.symbol("XAUUSD")
    # $100k * 1% = $1000 risk; 10$ stop on 100oz/lot = $1000 per lot -> 1.0 lot
    assert total_lots(spec, 100_000, 1.0, 2400.0, 2390.0, None, 5.0) == 1.0
    assert total_lots(spec, 100_000, 1.0, 2400.0, 2390.0, None, 0.5) == 0.5   # MAX_LOT cap
    assert total_lots(spec, 100_000, 1.0, 2400.0, 2390.0, 0.02, 5.0) == 0.02  # FIXED_LOT


def test_split_never_exceeds_total_and_drops_tiny_parts():
    b = PaperBroker()
    b.set_price("XAUUSD", 2400.0)
    spec = b.symbol("XAUUSD")
    assert split_lots(1.0, [0.5, 0.3, 0.2], spec) == [0.5, 0.3, 0.2]
    assert split_lots(0.03, [0.5, 0.3, 0.2], spec) == [0.01, 0.01]  # 3rd part too small
    assert split_lots(0.01, [0.5, 0.3, 0.2], spec) == [0.01]
    assert split_lots(0.0, [1.0], spec) == []


# ---------------------------------------------------------------- engine
def test_jackpot_sell_opens_three_positions():
    b = PaperBroker(balance=100_000)
    r = Engine(settings(risk_percent=1.0), b).handle(parse_alert(JACKPOT_TEXT))
    assert r.status == "filled", r.reason
    pos = b.positions()
    assert [p.tp for p in pos] == [4146.09, 4111.56, 4077.03]
    assert all(p.side == "sell" and p.sl == 4215.15 for p in pos)
    # risk: $1000 / (~34.6 * 100) ~= 0.28 lots, split 50/30/20
    assert sum(p.volume for p in pos) == pytest.approx(0.27, abs=0.02)


def test_dry_run_sends_nothing():
    b = PaperBroker(balance=100_000)
    r = Engine(settings(dry_run=True), b).handle(parse_alert(JACKPOT_TEXT))
    assert r.status == "dry_run" and r.orders and b.positions() == []


def test_rejects_sl_on_wrong_side():
    b = PaperBroker()
    r = Engine(settings(), b).handle(parse_alert('{"action":"buy","symbol":"XAUUSD","entry":2400,"sl":2410}'))
    assert r.status == "rejected" and "SL" in r.reason


def test_rejects_when_price_ran_away_from_entry():
    b = PaperBroker(balance=100_000)
    b.set_price("XAUUSD", 4150.0)
    b.set_price = lambda *a: None   # keep market at 4150: 30 from entry, max allowed ~12
    r = Engine(settings(), b).handle(parse_alert(JACKPOT_TEXT))
    assert r.status == "rejected" and "moved" in r.reason


def test_same_direction_signal_skipped_opposite_closes():
    b = PaperBroker(balance=100_000)
    e = Engine(settings(), b)
    assert e.handle(parse_alert(JACKPOT_TEXT)).status == "filled"
    assert e.handle(parse_alert(JACKPOT_TEXT)).status == "skipped"
    r = e.handle(parse_alert('{"action":"buy","symbol":"XAUUSD","entry":4180,"sl":4150,"tp1":4250}'))
    assert r.status == "filled"
    assert {p.side for p in b.positions()} == {"buy"}


def test_daily_loss_limit_blocks_new_trades():
    b = PaperBroker(balance=10_000)
    b.realized, b.balance = -400.0, 9_600.0   # lost 4% today
    r = Engine(settings(max_daily_loss_percent=3), b).handle(parse_alert(JACKPOT_TEXT))
    assert r.status == "rejected" and "daily loss" in r.reason


def test_risk_too_small_for_min_lot_is_rejected_not_oversized():
    b = PaperBroker(balance=100)    # 0.5% of $100 = $0.50, far below 0.01 lot risk
    r = Engine(settings(), b).handle(parse_alert(JACKPOT_TEXT))
    assert r.status == "rejected" and "minimum" in r.reason


def test_symbol_not_allowed():
    r = Engine(settings(allowed_symbols=["EURUSD"]), PaperBroker()).handle(parse_alert(JACKPOT_TEXT))
    assert r.status == "rejected"


def test_halted_blocks_opening_but_close_still_works():
    b = PaperBroker(balance=100_000)
    e = Engine(settings(), b)
    e.handle(parse_alert(JACKPOT_TEXT))
    e.halted = True
    assert e.handle(parse_alert(JACKPOT_TEXT)).reason == "trading is halted"
    assert e.handle(parse_alert("close XAUUSD")).status == "closed"
    assert b.positions() == []


def test_symbol_map_used():
    b = PaperBroker(balance=100_000)
    Engine(settings(symbol_map={"XAUUSD": "XAUUSDm"}), b).handle(parse_alert(JACKPOT_TEXT))
    assert {p.symbol for p in b.positions()} == {"XAUUSDm"}


# ---------------------------------------------------------------- http
@pytest.fixture
def client():
    s = settings()
    with TestClient(create_app(s, PaperBroker(balance=100_000))) as c:
        yield c


def test_webhook_requires_token(client):
    assert client.post("/webhook", content=JACKPOT_TEXT).status_code == 401
    assert client.post("/webhook?token=wrong", content=JACKPOT_TEXT).status_code == 401


def test_webhook_text_alert_trades_and_is_journaled(client):
    r = client.post(f"/webhook?token={TOKEN}", content=JACKPOT_TEXT)
    assert r.status_code == 200 and r.json()["status"] == "filled"
    log = client.get(f"/journal?token={TOKEN}").json()
    assert log[0]["status"] == "filled"


def test_webhook_token_in_json_body_is_redacted(client):
    body = json.dumps({"secret": TOKEN, "action": "close", "symbol": "XAUUSD"})
    assert client.post("/webhook", content=body).status_code == 200
    assert TOKEN not in json.dumps(client.get(f"/journal?token={TOKEN}").json())


def test_duplicate_alert_ignored(client):
    client.post(f"/webhook?token={TOKEN}", content=JACKPOT_TEXT)
    r = client.post(f"/webhook?token={TOKEN}", content=JACKPOT_TEXT)
    assert r.json()["reason"] == "duplicate alert"


def test_bad_alert_returns_400(client):
    assert client.post(f"/webhook?token={TOKEN}", content="hello").status_code == 400


def test_halt_endpoint(client):
    client.post(f"/webhook?token={TOKEN}", content=JACKPOT_TEXT)
    r = client.post(f"/halt?token={TOKEN}&close_all=true").json()
    assert r["halted"] and r["closed"][0]["status"] == "closed"
    assert client.get("/health").json()["halted"] is True
