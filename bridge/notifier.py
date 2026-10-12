"""Telegram notifications, sent from a background thread so alerts are never delayed."""
from __future__ import annotations

import json
import queue
import threading
import urllib.parse
import urllib.request


class Notifier:
    def __init__(self, bot_token: str | None, chat_id: str | None):
        self.enabled = bool(bot_token and chat_id)
        self._url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        self._chat_id = chat_id
        self._q: queue.Queue[str | None] = queue.Queue(maxsize=200)
        self.sent: list[str] = []          # kept for tests / debugging
        if self.enabled:
            threading.Thread(target=self._run, daemon=True, name="telegram").start()

    def send(self, text: str) -> None:
        self.sent.append(text)
        del self.sent[:-50]
        if not self.enabled:
            return
        try:
            self._q.put_nowait(text)
        except queue.Full:
            pass   # Telegram down for a long time - drop rather than block trading

    def _run(self) -> None:
        while True:
            text = self._q.get()
            data = urllib.parse.urlencode({
                "chat_id": self._chat_id, "text": text[:4000], "disable_web_page_preview": "true",
            }).encode()
            try:
                with urllib.request.urlopen(self._url, data=data, timeout=10) as r:
                    json.load(r)
            except Exception as e:  # never let notifications crash the bridge
                print(f"telegram send failed: {e}")


def format_result(sig, res) -> str:
    icon = {"filled": "✅", "dry_run": "🧪", "partial": "⚠️", "closed": "🔒",
            "rejected": "⛔", "error": "❌", "skipped": "↪️"}.get(res.status, "•")
    lines = [f"{icon} {res.status.upper()}: {sig.action.upper()} {sig.symbol}", res.reason]
    opened = [o for o in res.orders if o.get("side") and o.get("ok")]
    if opened:
        o = opened[0]
        lines.append(f"Entry ~{o.get('fill') or o['price']}  SL {o['sl']}")
        lines.append("TPs: " + ", ".join(f"{x['volume']} lot @ {x['tp'] or '-'}" for x in opened))
    return "\n".join(x for x in lines if x)
