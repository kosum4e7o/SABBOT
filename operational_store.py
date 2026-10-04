"""Small SQLite store for operational bot history and statistics.

Chat messages remain in ChatStore. This database stores bot/runtime events only,
so the existing chat data format remains backward compatible.
"""
from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path


class OperationalStore:
    def __init__(self, base_dir: Path):
        self.base = Path(base_dir)
        self.base.mkdir(parents=True, exist_ok=True)
        self.path = self.base / "bot_runtime.db"
        self.lock = threading.RLock()
        self.conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("""CREATE TABLE IF NOT EXISTS send_history(
            id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL,
            channel_id TEXT NOT NULL, channel_name TEXT NOT NULL,
            account_id TEXT NOT NULL, account_name TEXT NOT NULL,
            platform TEXT NOT NULL, message TEXT NOT NULL,
            success INTEGER NOT NULL, error_kind TEXT DEFAULT '', error TEXT DEFAULT '')""")
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_send_ts ON send_history(ts DESC)")
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_send_channel ON send_history(channel_id, ts DESC)")
        self.conn.execute("""CREATE TABLE IF NOT EXISTS errors(
            id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL,
            channel_id TEXT, channel_name TEXT, account_id TEXT,
            account_name TEXT, platform TEXT, kind TEXT, message TEXT)""")
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_errors_ts ON errors(ts DESC)")
        self.conn.execute("""CREATE TABLE IF NOT EXISTS stream_sessions(
            id INTEGER PRIMARY KEY AUTOINCREMENT, channel_id TEXT NOT NULL,
            channel_name TEXT NOT NULL, platform TEXT NOT NULL,
            session_id TEXT NOT NULL, started_ts REAL NOT NULL,
            ended_ts REAL)""")
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_sessions_channel ON stream_sessions(channel_id, started_ts DESC)")
        self.conn.commit()

    def send(self, *, channel_id, channel_name, account_id, account_name, platform,
             message, success, error_kind="", error="", ts=None):
        with self.lock:
            self.conn.execute(
                "INSERT INTO send_history(ts,channel_id,channel_name,account_id,account_name,platform,message,success,error_kind,error) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (float(ts or time.time()), str(channel_id), str(channel_name), str(account_id),
                 str(account_name), str(platform), str(message), int(bool(success)),
                 str(error_kind or ""), str(error or "")))
            self.conn.commit()

    def error(self, *, channel_id="", channel_name="", account_id="", account_name="",
              platform="", kind="error", message="", ts=None):
        with self.lock:
            self.conn.execute(
                "INSERT INTO errors(ts,channel_id,channel_name,account_id,account_name,platform,kind,message) VALUES(?,?,?,?,?,?,?,?)",
                (float(ts or time.time()), str(channel_id), str(channel_name), str(account_id),
                 str(account_name), str(platform), str(kind), str(message)))
            self.conn.commit()

    def session_start(self, channel_id, channel_name, platform, session_id, ts=None):
        with self.lock:
            self.conn.execute(
                "INSERT INTO stream_sessions(channel_id,channel_name,platform,session_id,started_ts) VALUES(?,?,?,?,?)",
                (str(channel_id), str(channel_name), str(platform), str(session_id), float(ts or time.time())))
            self.conn.commit()

    def session_end(self, channel_id, session_id, ts=None):
        with self.lock:
            self.conn.execute(
                "UPDATE stream_sessions SET ended_ts=? WHERE channel_id=? AND session_id=? AND ended_ts IS NULL",
                (float(ts or time.time()), str(channel_id), str(session_id)))
            self.conn.commit()

    def recent_sends(self, limit=200, channel_id="", account_id=""):
        sql = "SELECT ts,platform,channel_name,account_name,message,success,error FROM send_history"
        where, params = [], []
        if channel_id:
            where.append("channel_id=?"); params.append(str(channel_id))
        if account_id:
            where.append("account_id=?"); params.append(str(account_id))
        if where: sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY ts DESC LIMIT ?"; params.append(int(limit))
        with self.lock:
            return self.conn.execute(sql, params).fetchall()

    def recent_errors(self, limit=200):
        with self.lock:
            return self.conn.execute(
                "SELECT ts,platform,channel_name,account_name,kind,message FROM errors ORDER BY ts DESC LIMIT ?",
                (int(limit),)).fetchall()

    def stats(self):
        with self.lock:
            total = self.conn.execute("SELECT COUNT(*) FROM send_history").fetchone()[0]
            ok = self.conn.execute("SELECT COUNT(*) FROM send_history WHERE success=1").fetchone()[0]
            errors = self.conn.execute("SELECT COUNT(*) FROM errors").fetchone()[0]
            sessions = self.conn.execute("SELECT COUNT(*) FROM stream_sessions").fetchone()[0]
            return {"sends": total, "successful_sends": ok, "failed_sends": total-ok,
                    "errors": errors, "sessions": sessions}

    def close(self):
        with self.lock:
            self.conn.close()
