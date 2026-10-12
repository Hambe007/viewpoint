# viewpoint: TradingView → Exness MT5 bridge

This bridge receives TradingView alerts (for example from **Jackpot Strategy** on XAUUSD), runs
safety checks and places the trades on your **Exness MetaTrader 5** account.

```
TradingView alert ──HTTPS webhook──▶ bridge (Python, on a Windows PC/VPS) ──▶ MT5 terminal ──▶ Exness
```

> **Risk warning.** This software executes your TradingView signals; it does not make them
> profitable. Leveraged gold/forex trading loses money for most retail traders. Run it in
> `DRY_RUN=true` and then on an **Exness demo account** for several weeks before risking real money,
> and only ever risk money you can afford to lose.

## What it does with each alert

1. Checks the secret token (and optionally that the request came from TradingView's IPs).
2. Ignores duplicates (same alert within 60 s).
3. Parses **Entry / Stop loss / TP1 / TP2 / TP3**. It accepts plain text such as
   `XAUUSD Sell Entry : 4180.62 Stop loss : 4215.15 TP 1 : 4146.09 TP 2 : 4111.56 TP 3 : 4077.03`
   or JSON. **Alerts without a stop loss are refused.**
4. Rejects the trade if:
   - the symbol isn't allowed,
   - the SL is on the wrong side of the price,
   - the spread is too wide,
   - the price has moved too far from the alert's entry,
   - you're already in a trade in the same direction,
   - today's loss limit has been hit,
   - the bridge is halted.
5. Sizes the position so hitting the SL loses `RISK_PERCENT` of equity. The size is always rounded
   **down**. If even the broker's minimum lot would risk more than that, the trade is skipped
   rather than oversized.
6. Splits the size across TP1/TP2/TP3 (50/30/20 by default): one MT5 position per TP, all sharing
   the same SL.
7. An opposite signal closes the open trades first. A `close` alert closes everything on that
   symbol.
8. Logs everything to `journal.db`. You can view it at `/journal?token=...`.

The bridge only touches positions carrying its own magic number. **Manual trades are never
modified.**

## Setup (Windows PC or Windows VPS)

The `MetaTrader5` Python package only works on Windows, next to a running MT5 terminal. A cheap
Windows VPS (for example a Forex VPS near Exness's servers) keeps it running 24/5. Your phone
can't run it.

1. **Install the Exness MT5 terminal** and log in to your **demo** account first.
   - Go to *Tools → Options → Expert Advisors* and tick **Allow algorithmic trading**.
   - Click the **Algo Trading** button on the toolbar so it turns green.
   - In *Market Watch*, note the exact gold symbol name. Standard accounts use `XAUUSDm`;
     Raw Spread, Zero and Pro accounts usually use `XAUUSD`.
2. **Install Python 3.11+** from python.org and tick "Add to PATH".
3. Download this repository and double-click `scripts\start.bat`. The first run creates `.env`
   and opens it in Notepad.
4. **Edit `.env`:**
   - `WEBHOOK_TOKEN`: a long random string. Generate one with
     `python -c "import secrets; print(secrets.token_urlsafe(24))"`.
   - `MT5_LOGIN`, `MT5_PASSWORD`, `MT5_SERVER`: these are shown in the MT5 login window. You can
     leave them blank to use the account already logged in.
   - `SYMBOL_MAP`: must match your Market Watch names (`XAUUSD:XAUUSDm` or `XAUUSD:XAUUSD`).
   - `RISK_PERCENT`: start small (0.25–0.5).
   - Leave **`DRY_RUN=true`** for now.
5. Run `scripts\start.bat` again. You should see
   `Bridge ready: broker=mt5 mode=DRY RUN`. Check it at <http://127.0.0.1:8000/health>.

### Give it a public HTTPS address

TradingView webhooks only go to ports **80/443**, so put a tunnel in front of port 8000. The
simplest free options:

- **Cloudflare Tunnel:** install `cloudflared`, then run
  `cloudflared tunnel --url http://localhost:8000`. It prints a `https://….trycloudflare.com` URL.
  Use a named tunnel with your own domain for a URL that doesn't change.
- **ngrok:** run `ngrok http 8000`.

Your webhook URL is then:

```
https://YOUR-TUNNEL-URL/webhook?token=YOUR_WEBHOOK_TOKEN
```

Once it works, you can set `IP_ALLOWLIST=tradingview` so that only TradingView's servers are
accepted.

## TradingView alert setup

Webhooks need a paid TradingView plan.

1. Open XAUUSD 1H with Jackpot Strategy. Go to **Alerts → Create alert**.
2. **Condition:** *Jackpot Strategy* → **Any alert() function call** (or its Buy/Sell alert
   conditions).
3. **Notifications:** tick **Webhook URL** and paste the URL above.
4. **Message:**
   - If the indicator writes its own message (Entry / Stop loss / TP …), leave it. The bridge
     reads that text.
   - If you can edit the message, JSON is the most reliable format:
     ```json
     {"action": "sell", "symbol": "{{ticker}}", "entry": {{close}}, "sl": 4215.15, "tp1": 4146.09, "tp2": 4111.56, "tp3": 4077.03}
     ```
     Replace the numbers with the indicator's plot placeholders, for example
     `{{plot("Stop loss")}}`, if it exposes them.
   - If the message doesn't contain the symbol, add `&symbol=XAUUSD` to the webhook URL.
5. To close trades from an alert, send any message containing `close`, for example
   `close XAUUSD`.

After the first alert fires, open `/journal?token=…` and check that it was parsed correctly. If
the indicator's message looks different, adjust the alert (or the parser in `bridge/signals.py`).

## Going live checklist

- [ ] Several weeks of dry-run, then demo, with results you're happy with. Compare every journal
      entry with what the chart showed.
- [ ] `RISK_PERCENT` ≤ 1, a sensible `MAX_LOT`, and `MAX_DAILY_LOSS_PERCENT` set.
- [ ] VPS set to restart the bridge and MT5 after a reboot (Task Scheduler → "At startup").
- [ ] You know the kill switch (below).
- [ ] Then set `DRY_RUN=false`, point MT5 to the live account, and restart.

## Kill switch & monitoring

| Action | Request |
|---|---|
| Status / balance | `GET /health` |
| Last alerts & results | `GET /journal?token=TOKEN&limit=50` |
| Stop opening trades | `POST /halt?token=TOKEN` |
| Stop **and close all bridge trades** | `POST /halt?token=TOKEN&close_all=true` |
| Resume | `POST /resume?token=TOKEN` |

Halt doesn't survive a restart. For a permanent stop, set `TRADING_ENABLED=false` in `.env`.

## Settings

All settings live in `.env`; `.env.example` documents every one. The key ones:

| Setting | Default | Meaning |
|---|---|---|
| `DRY_RUN` | `true` | Log orders, send nothing |
| `RISK_PERCENT` | `0.5` | % of equity lost if SL is hit |
| `FIXED_LOT` | – | Use a fixed size instead |
| `MAX_LOT` | `1.0` | Cap on total lots per signal |
| `TP_SPLIT` | `0.5,0.3,0.2` | Size share for TP1/TP2/TP3 |
| `MAX_DAILY_LOSS_PERCENT` | `3` | Stop for the day after this loss |
| `MAX_SPREAD_PCT_OF_SL` | `0.15` | Skip if spread > 15% of the stop distance |
| `MAX_ENTRY_DRIFT_PCT_OF_SL` | `0.35` | Skip if price ran away from the alert entry |
| `CLOSE_ON_OPPOSITE` | `true` | Opposite signal closes open trades |

**About sizing on gold:** 1 lot = 100 oz, so a $34 stop (like the 4180 → 4215 example) costs
about $3,460 per lot. At 0.5% risk on a $10,000 account ($50), that is only 0.01 lot: one position
with TP1 only. All three TPs need at least 0.05 lots in total.

## Development

```bash
pip install fastapi uvicorn python-dotenv pytest httpx
pytest -q
BROKER=paper python -m bridge   # runs anywhere, with a fake broker
```
