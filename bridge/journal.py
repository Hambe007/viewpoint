"""SQLite log of every alert received and what was done with it."""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Journal:
    def __init__(self, path: str):
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._lock = threading.Lock()
        self._db.execute(
            """CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                ip TEXT,
                raw TEXT,
                signal TEXT,
                status TEXT,
                detail TEXT
            )"""
        )
        self._db.execute(
            """CREATE TABLE IF NOT EXISTS trade_groups (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                symbol TEXT NOT NULL,
                side TEXT NOT NULL,
                tickets TEXT NOT NULL,      -- JSON list, TP1 position first
                be_done INTEGER NOT NULL DEFAULT 0,
                closed INTEGER NOT NULL DEFAULT 0
            )"""
        )
        self._db.commit()

    # -- positions opened together by one signal ---------------------------
    def add_group(self, symbol: str, side: str, tickets: list[int]) -> None:
        with self._lock:
            self._db.execute(
                "INSERT INTO trade_groups (ts, symbol, side, tickets) VALUES (?,?,?,?)",
                (_now(), symbol, side, json.dumps(tickets)),
            )
            self._db.commit()

    def open_groups(self) -> list[dict]:
        with self._lock:
            rows = self._db.execute(
                "SELECT id, ts, symbol, side, tickets, be_done FROM trade_groups WHERE closed = 0"
            ).fetchall()
        return [{"id": r[0], "ts": r[1], "symbol": r[2], "side": r[3],
                 "tickets": json.loads(r[4]), "be_done": bool(r[5])} for r in rows]

    def update_group(self, group_id: int, be_done: bool | None = None, closed: bool | None = None) -> None:
        with self._lock:
            if be_done is not None:
                self._db.execute("UPDATE trade_groups SET be_done = ? WHERE id = ?", (int(be_done), group_id))
            if closed is not None:
                self._db.execute("UPDATE trade_groups SET closed = ? WHERE id = ?", (int(closed), group_id))
            self._db.commit()

    # -- event log -----------------------------------------------------------
    def log(self, ip: str | None, raw: str, signal: dict | None, status: str, detail: dict) -> None:
        with self._lock:
            self._db.execute(
                "INSERT INTO events (ts, ip, raw, signal, status, detail) VALUES (?,?,?,?,?,?)",
                (_now(), ip, raw[:4000],
                 json.dumps(signal) if signal else None, status, json.dumps(detail)),
            )
            self._db.commit()

    def recent(self, limit: int = 20) -> list[dict]:
        with self._lock:
            rows = self._db.execute(
                "SELECT ts, ip, raw, signal, status, detail FROM events ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [
            {"ts": r[0], "ip": r[1], "raw": r[2],
             "signal": json.loads(r[3]) if r[3] else None,
             "status": r[4], "detail": json.loads(r[5]) if r[5] else None}
            for r in rows
        ]
