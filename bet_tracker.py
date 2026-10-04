"""Bet tracker: detects `!bet <letter> <points>` messages in chat and keeps REAL statistics.

No Qt, no network - pure Python + sqlite3, fully unit-tested (tests/test_bet_tracker.py).

Rules
-----
* Only messages that really appeared in chat are counted (deduplicated by message id).
* Messages from response bots (BOTTLY ...) are never counted.
* Demo data is stored with source="demo" and is NEVER mixed into LIVE statistics.
* Statistics are per stream and per ROUND.  A round ends manually ("Нов рунд") or automatically
  when the stream session changes (new live session / demo reset).
"""
from __future__ import annotations

import json
import re
import sqlite3
import threading
import time
from pathlib import Path
from typing import Optional

SRC_CHAT = "chat"
SRC_MINE = "mine"      # sent by one of OUR accounts (still a real chat bet)
SRC_DEMO = "demo"

# !bet A 500   !BET a 1,000   !bet б 2k   !bet C all
_BET_RE = re.compile(r"^\s*!bet\s+([^\W\d_])\s+(all|max|\d[\d.,_]*\s?[kKmM]?)\s*$", re.IGNORECASE)
_BET_ANY_RE = re.compile(r"^\s*!bet(\s|$)", re.IGNORECASE)


def looks_like_bet(text: str) -> bool:
    """True when the message starts with !bet (even if malformed)."""
    return bool(_BET_ANY_RE.match(text or ""))


def parse_points(raw: str) -> Optional[int]:
    """'500' -> 500, '1,000' -> 1000, '2k' -> 2000, '1.5k' -> 1500, 'all' -> -1 (all-in). None = invalid."""
    s = (raw or "").strip().lower().replace("_", "").replace(" ", "")
    if s in ("all", "max"):
        return -1
    mult = 1
    if s.endswith("k"):
        mult, s = 1000, s[:-1]
    elif s.endswith("m"):
        mult, s = 1_000_000, s[:-1]
    if not s:
        return None
    if mult > 1 and re.fullmatch(r"\d+[.,]\d{1,2}", s):          # 1.5k / 2,5k
        return int(round(float(s.replace(",", ".")) * mult))
    if re.fullmatch(r"\d{1,3}([.,]\d{3})+", s):                   # 1,000 / 1.000.000
        s = re.sub(r"[.,]", "", s)
    if not s.isdigit():
        return None
    v = int(s) * mult
    return v if 0 < v <= 10**12 else None


def parse_bet(text: str) -> Optional[tuple[str, int]]:
    """Return (LETTER, points) for a valid bet command, otherwise None.  points == -1 means all-in."""
    m = _BET_RE.match(text or "")
    if not m:
        return None
    pts = parse_points(m.group(2))
    if pts is None:
        return None
    return m.group(1).upper(), pts


def format_bet(letter: str, points: int) -> str:
    letter = (letter or "").strip()
    return f"!bet {letter.upper()} {'all' if int(points) < 0 else int(points)}"


def validate_bet_input(letter: str, points: str) -> tuple[str, int]:
    """For the per-profile form. Raises ValueError with a readable (Bulgarian) message."""
    letter = (letter or "").strip()
    if len(letter) != 1 or not letter.isalpha():
        raise ValueError("Въведи точно една буква.")
    pts = parse_points(points)
    if pts is None:
        raise ValueError("Невалидни точки. Примери: 500, 1,000, 2k, all.")
    return letter.upper(), pts


class BetTracker:
    def __init__(self, directory: Path, filename: str = "bets.db"):
        self.path = Path(directory) / filename
        self._lock = threading.RLock()
        self._db = sqlite3.connect(str(self.path), check_same_thread=False)
        self._db.executescript("""
            CREATE TABLE IF NOT EXISTS bets(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts REAL NOT NULL, stream_key TEXT NOT NULL, round_id INTEGER NOT NULL,
                platform TEXT, username TEXT, user_key TEXT NOT NULL, message_id TEXT NOT NULL,
                letter TEXT NOT NULL, points INTEGER NOT NULL, source TEXT NOT NULL,
                UNIQUE(stream_key, message_id, source));
            CREATE INDEX IF NOT EXISTS idx_bets_round ON bets(stream_key, round_id, source);
            CREATE TABLE IF NOT EXISTS rounds(
                stream_key TEXT NOT NULL, source TEXT NOT NULL, round_id INTEGER NOT NULL,
                started_at REAL NOT NULL, session_id TEXT DEFAULT '',
                PRIMARY KEY(stream_key, source));
        """)
        self._db.commit()

    # ------------------------------------------------------------------ rounds
    @staticmethod
    def _scope(demo: bool) -> str:
        return SRC_DEMO if demo else "live"

    def current_round(self, stream_key: str, demo: bool = False) -> int:
        scope = self._scope(demo)
        with self._lock:
            row = self._db.execute("SELECT round_id FROM rounds WHERE stream_key=? AND source=?",
                                   (stream_key, scope)).fetchone()
            if row:
                return int(row[0])
            self._db.execute("INSERT INTO rounds VALUES(?,?,?,?,?)", (stream_key, scope, 1, time.time(), ""))
            self._db.commit()
            return 1

    def new_round(self, stream_key: str, demo: bool = False, session_id: str = "") -> int:
        scope = self._scope(demo)
        with self._lock:
            rid = self.current_round(stream_key, demo) + 1
            self._db.execute("UPDATE rounds SET round_id=?, started_at=?, session_id=? WHERE stream_key=? AND source=?",
                             (rid, time.time(), session_id, stream_key, scope))
            self._db.commit()
            return rid

    def sync_session(self, stream_key: str, session_id: str, demo: bool = False) -> bool:
        """Start a fresh round when the live session changed. Returns True if a new round started."""
        scope = self._scope(demo)
        with self._lock:
            self.current_round(stream_key, demo)
            row = self._db.execute("SELECT session_id FROM rounds WHERE stream_key=? AND source=?",
                                   (stream_key, scope)).fetchone()
            old = (row[0] if row else "") or ""
            if session_id and old and old != session_id:
                self.new_round(stream_key, demo, session_id)
                return True
            if session_id and not old:
                self._db.execute("UPDATE rounds SET session_id=? WHERE stream_key=? AND source=?",
                                 (session_id, stream_key, scope))
                self._db.commit()
            return False

    # ------------------------------------------------------------------ ingest
    def ingest(self, *, stream_key: str, text: str, username: str = "", user_id: str = "",
               message_id: str = "", platform: str = "", ts: Optional[float] = None,
               is_bot: bool = False, is_self: bool = False, demo: bool = False) -> Optional[tuple[str, int]]:
        """Record a bet found in a chat message. Returns (letter, points) if a NEW bet was stored."""
        if is_bot:
            return None
        parsed = parse_bet(text)
        if parsed is None:
            return None
        letter, points = parsed
        user_key = (str(user_id).strip() or str(username).strip().casefold() or "?")
        source = SRC_DEMO if demo else (SRC_MINE if is_self else SRC_CHAT)
        mid = str(message_id) or f"{user_key}:{int((ts or time.time()) * 1000)}:{letter}:{points}"
        with self._lock:
            rid = self.current_round(stream_key, demo)
            cur = self._db.execute(
                "INSERT OR IGNORE INTO bets(ts,stream_key,round_id,platform,username,user_key,message_id,letter,points,source)"
                " VALUES(?,?,?,?,?,?,?,?,?,?)",
                (float(ts or time.time()), stream_key, rid, platform, username, user_key, mid, letter, points, source))
            self._db.commit()
            return (letter, points) if cur.rowcount else None

    # ------------------------------------------------------------------ stats
    def stats(self, stream_key: str, demo: bool = False, round_id: Optional[int] = None) -> dict:
        """Real statistics of one round.

        letters: list sorted by number of bets (desc) -> dict(letter, bets, users, points, pct_bets, pct_points)
        users counts distinct people; all-in bets (points=-1) count as bets but add 0 to points.
        """
        scope_sources = (SRC_DEMO,) if demo else (SRC_CHAT, SRC_MINE)
        with self._lock:
            rid = round_id or self.current_round(stream_key, demo)
            q = ",".join("?" * len(scope_sources))
            rows = self._db.execute(
                f"SELECT letter, COUNT(*), COUNT(DISTINCT user_key), SUM(CASE WHEN points>0 THEN points ELSE 0 END),"
                f" SUM(CASE WHEN points<0 THEN 1 ELSE 0 END) FROM bets"
                f" WHERE stream_key=? AND round_id=? AND source IN ({q}) GROUP BY letter",
                (stream_key, rid, *scope_sources)).fetchall()
            total_users = self._db.execute(
                f"SELECT COUNT(DISTINCT user_key) FROM bets WHERE stream_key=? AND round_id=? AND source IN ({q})",
                (stream_key, rid, *scope_sources)).fetchone()[0]
        total_bets = sum(r[1] for r in rows)
        total_points = sum(int(r[3] or 0) for r in rows)
        letters = [{"letter": r[0], "bets": int(r[1]), "users": int(r[2]), "points": int(r[3] or 0),
                    "all_in": int(r[4] or 0),
                    "pct_bets": (100.0 * r[1] / total_bets) if total_bets else 0.0,
                    "pct_points": (100.0 * int(r[3] or 0) / total_points) if total_points else 0.0}
                   for r in rows]
        letters.sort(key=lambda d: (-d["bets"], -d["points"], d["letter"]))
        return {"round_id": rid, "demo": demo, "total_bets": total_bets, "total_users": int(total_users or 0),
                "total_points": total_points, "letters": letters,
                "top": letters[0]["letter"] if letters else None}

    def recent(self, stream_key: str, demo: bool = False, limit: int = 30) -> list[dict]:
        scope_sources = (SRC_DEMO,) if demo else (SRC_CHAT, SRC_MINE)
        with self._lock:
            rid = self.current_round(stream_key, demo)
            q = ",".join("?" * len(scope_sources))
            rows = self._db.execute(
                f"SELECT ts, username, letter, points, source FROM bets WHERE stream_key=? AND round_id=? "
                f"AND source IN ({q}) ORDER BY id DESC LIMIT ?", (stream_key, rid, *scope_sources, int(limit))).fetchall()
        return [{"ts": r[0], "username": r[1], "letter": r[2], "points": r[3], "source": r[4]} for r in rows]

    def clear_demo(self, stream_key: Optional[str] = None) -> None:
        """Wipe ALL demo data (used when switching Demo <-> Live)."""
        with self._lock:
            if stream_key:
                self._db.execute("DELETE FROM bets WHERE source=? AND stream_key=?", (SRC_DEMO, stream_key))
                self._db.execute("DELETE FROM rounds WHERE source=? AND stream_key=?", (SRC_DEMO, stream_key))
            else:
                self._db.execute("DELETE FROM bets WHERE source=?", (SRC_DEMO,))
                self._db.execute("DELETE FROM rounds WHERE source=?", (SRC_DEMO,))
            self._db.commit()

    def close(self) -> None:
        with self._lock:
            try:
                self._db.close()
            except sqlite3.Error:
                pass


# ------------------------------------------------------------------ chat commands (the "!" feature)
BUILTIN_COMMANDS = ["!bet", "!points", "!time"]


def command_word(text: str) -> str:
    """'!bet A 500' -> '!bet'; '' if the text is not a command."""
    t = (text or "").lstrip()
    if not t.startswith("!") or len(t) < 2 or t[1].isspace():
        return ""
    return t.split(None, 1)[0].lower()


def merge_commands(saved: list, text: str) -> list:
    """Add the command typed in *text* to the saved list (no duplicates, newest last, max 40)."""
    word = command_word(text)
    out = [c for c in (saved or []) if isinstance(c, str) and c.startswith("!")]
    if word and word not in out and word not in BUILTIN_COMMANDS and len(word) <= 32:
        out.append(word)
    return out[-40:]


# ------------------------------------------------------------------ tiny JSON stores (independent of Config)
class JsonStore:
    """A dict persisted as JSON (atomic write). Used for per-profile default bets and saved commands."""
    def __init__(self, path: Path, default):
        self.path = Path(path)
        self.default = default
        self._lock = threading.RLock()

    def load(self):
        with self._lock:
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                return data if isinstance(data, type(self.default)) else type(self.default)()
            except (OSError, ValueError):
                return type(self.default)()

    def save(self, data) -> None:
        with self._lock:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                tmp = self.path.with_suffix(".tmp")
                tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
                tmp.replace(self.path)
            except OSError:
                pass
