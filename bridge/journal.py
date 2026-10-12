"""SQLite log of every alert received and what was done with it."""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone


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
        self._db.commit()

    def log(self, ip: str | None, raw: str, signal: dict | None, status: str, detail: dict) -> None:
        with self._lock:
            self._db.execute(
                "INSERT INTO events (ts, ip, raw, signal, status, detail) VALUES (?,?,?,?,?,?)",
                (datetime.now(timezone.utc).isoformat(timespec="seconds"), ip, raw[:4000],
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
