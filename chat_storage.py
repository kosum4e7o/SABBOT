"""Persistent live-chat logging, search, mentions, command and !point/!time tracking."""
from __future__ import annotations
import json, sqlite3, threading, time, re
from pathlib import Path
from datetime import datetime, timezone

def _utc_iso(ts=None):
    return datetime.fromtimestamp(ts or time.time(), tz=timezone.utc).astimezone().isoformat(timespec="seconds")

class ChatStore:
    def __init__(self, base_dir: Path):
        self.base = Path(base_dir)
        self.base.mkdir(parents=True, exist_ok=True)
        self.db_path = self.base / "chat_log.db"
        self.chat_file = self.base / "chat_log.jsonl"
        self.mentions_file = self.base / "chat_mentions.jsonl"
        self.commands_file = self.base / "chat_commands.jsonl"
        self.points_file = self.base / "chat_points.jsonl"
        self.times_file = self.base / "chat_times.jsonl"
        self.lock = threading.RLock()
        self.conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self.conn.execute("""CREATE TABLE IF NOT EXISTS messages(
            id TEXT PRIMARY KEY, ts REAL NOT NULL, local_time TEXT NOT NULL,
            platform TEXT NOT NULL, channel TEXT NOT NULL, channel_id TEXT,
            username TEXT NOT NULL, user_id TEXT, text TEXT NOT NULL, kind TEXT DEFAULT 'chat')""")
        self.conn.execute("""CREATE INDEX IF NOT EXISTS idx_messages_ts ON messages(ts)""")
        self.conn.execute("""CREATE INDEX IF NOT EXISTS idx_messages_user ON messages(username)""")
        self.conn.execute("""CREATE TABLE IF NOT EXISTS command_usage(
            channel_id TEXT, username TEXT, command TEXT, count INTEGER NOT NULL DEFAULT 0,
            recorded INTEGER NOT NULL DEFAULT 0, first_ts REAL, last_ts REAL,
            PRIMARY KEY(channel_id, username, command))""")
        self.conn.execute("""CREATE TABLE IF NOT EXISTS values_seen(
            channel_id TEXT, username TEXT, value_type TEXT, value TEXT,
            ts REAL, raw_response TEXT, PRIMARY KEY(channel_id, username, value_type))""")
        self.conn.execute("""CREATE TABLE IF NOT EXISTS account_profiles(
            stream_key TEXT NOT NULL, account_id TEXT NOT NULL,
            platform TEXT, stream_name TEXT, account_name TEXT,
            points INTEGER, points_ts REAL,
            level INTEGER, watch_text TEXT, watch_seconds INTEGER, time_ts REAL,
            PRIMARY KEY(stream_key, account_id))""")
        self.conn.execute("""CREATE TABLE IF NOT EXISTS account_activity(
            id INTEGER PRIMARY KEY AUTOINCREMENT, account_id TEXT NOT NULL,
            message_id TEXT NOT NULL UNIQUE, platform TEXT NOT NULL,
            channel TEXT NOT NULL, channel_id TEXT, stream_id TEXT,
            message TEXT NOT NULL, ts REAL NOT NULL, source TEXT DEFAULT 'chat')""")
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_account_activity_account_ts ON account_activity(account_id, ts DESC)")
        # Every BOTTLY answer to !points / !time is a NEW row (history list on the dashboard).
        self.conn.execute("""CREATE TABLE IF NOT EXISTS value_history(
            id INTEGER PRIMARY KEY AUTOINCREMENT, account_id TEXT NOT NULL, account_name TEXT,
            stream_name TEXT, platform TEXT, kind TEXT NOT NULL, value_text TEXT NOT NULL, ts REAL NOT NULL)""")
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_value_history_ts ON value_history(ts DESC)")
        self.conn.execute("""CREATE TABLE IF NOT EXISTS account_timers(
            account_id TEXT PRIMARY KEY, last_activity_ts REAL, last_message_ts REAL,
            next_activity_ts REAL, enabled INTEGER NOT NULL DEFAULT 1, state TEXT NOT NULL DEFAULT 'READY')""")
        self.conn.commit()

    def _append(self, path, obj):
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(obj, ensure_ascii=False) + "\n")

    def add_message(self, *, message_id, ts, platform, channel, channel_id,
                    username, user_id, text, kind="chat"):
        ts = float(ts or time.time())
        row = (str(message_id), ts, _utc_iso(ts), platform, channel, str(channel_id or ""),
               username or "", str(user_id or ""), text or "", kind)
        with self.lock:
            cur = self.conn.execute("""INSERT OR IGNORE INTO messages
                (id,ts,local_time,platform,channel,channel_id,username,user_id,text,kind)
                VALUES (?,?,?,?,?,?,?,?,?,?)""", row)
            if cur.rowcount == 0:
                return False
            self.conn.commit()
            self._append(self.chat_file, {
                "id": row[0], "timestamp": row[2], "platform": platform, "channel": channel,
                "channel_id": str(channel_id or ""), "username": username or "",
                "user_id": str(user_id or ""), "message": text or "", "kind": kind})
            return True

    def add_special(self, filename, obj):
        with self.lock:
            self._append(self.base / filename, obj)

    def mention(self, *, ts, platform, channel, username, user_id, matched_name, text, message_id):
        obj = {"timestamp": _utc_iso(ts), "date": _utc_iso(ts)[:10], "time": _utc_iso(ts)[11:],
               "platform": platform, "channel": channel, "username": username,
               "user_id": str(user_id or ""), "matched": matched_name, "message": text,
               "message_id": message_id}
        self.add_special("chat_mentions.jsonl", obj)

    def command_seen(self, channel_id, username, command, threshold, ts=None):
        ts = float(ts or time.time())
        with self.lock:
            row = self.conn.execute("""SELECT count,recorded FROM command_usage
                    WHERE channel_id=? AND username=? AND command=?""",
                    (str(channel_id), username, command)).fetchone()
            count = (row[0] if row else 0) + 1
            recorded = row[1] if row else 0
            self.conn.execute("""INSERT INTO command_usage
                (channel_id,username,command,count,recorded,first_ts,last_ts)
                VALUES(?,?,?,?,?,?,?)
                ON CONFLICT(channel_id,username,command) DO UPDATE SET
                count=excluded.count,last_ts=excluded.last_ts""",
                (str(channel_id), username, command, count, recorded,
                 ts if row is None else None, ts))
            self.conn.commit()
            if count >= threshold and not recorded:
                self.conn.execute("""UPDATE command_usage SET recorded=1
                                     WHERE channel_id=? AND username=? AND command=?""",
                                  (str(channel_id), username, command))
                self.conn.commit()
                obj={"timestamp":_utc_iso(ts),"channel_id":str(channel_id),
                     "username":username,"command":command,"count":count}
                self.add_special("chat_commands.jsonl", obj)
                return True, count
            return False, count

    def save_value(self, channel_id, username, value_type, value, ts, raw_response):
        with self.lock:
            self.conn.execute("""INSERT INTO values_seen(channel_id,username,value_type,value,ts,raw_response)
                VALUES(?,?,?,?,?,?)
                ON CONFLICT(channel_id,username,value_type) DO UPDATE SET
                value=excluded.value,ts=excluded.ts,raw_response=excluded.raw_response""",
                (str(channel_id),username,value_type,str(value),float(ts),raw_response))
            self.conn.commit()
            obj={"timestamp":_utc_iso(ts),"channel_id":str(channel_id),"username":username,
                 "value":str(value),"response":raw_response}
            self._append(self.base / ("chat_points.jsonl" if value_type=="points" else "chat_times.jsonl"), obj)


    # ------------------------------------------------ account activity/timers
    def record_account_activity(self, *, account_id, message_id, platform, channel, channel_id,
                                stream_id, message, ts, source="chat"):
        """Persist real chat activity once. Returns True only for a new event."""
        with self.lock:
            cur=self.conn.execute("""INSERT OR IGNORE INTO account_activity
                (account_id,message_id,platform,channel,channel_id,stream_id,message,ts,source)
                VALUES(?,?,?,?,?,?,?,?,?)""",
                (str(account_id),str(message_id),str(platform),str(channel),str(channel_id or ""),
                 str(stream_id or ""),str(message or ""),float(ts),str(source or "chat")))
            self.conn.commit()
            return cur.rowcount > 0

    def account_activity(self, account_id, limit=500):
        with self.lock:
            return self.conn.execute("""SELECT ts,platform,channel,channel_id,stream_id,message,source,message_id
                FROM account_activity WHERE account_id=? ORDER BY ts DESC LIMIT ?""",
                (str(account_id),int(limit))).fetchall()

    def recent_account_activity(self, limit=100):
        with self.lock:
            return self.conn.execute("""SELECT account_id,ts,platform,channel,channel_id,stream_id,message,source,message_id
                FROM account_activity ORDER BY ts DESC LIMIT ?""",(int(limit),)).fetchall()

    def latest_account_activity(self, account_id):
        with self.lock:
            return self.conn.execute("""SELECT ts,platform,channel,channel_id,stream_id,message,source,message_id
                FROM account_activity WHERE account_id=? ORDER BY ts DESC LIMIT 1""",(str(account_id),)).fetchone()

    def get_account_timer(self, account_id):
        with self.lock:
            return self.conn.execute("""SELECT last_activity_ts,last_message_ts,next_activity_ts,enabled,state
                FROM account_timers WHERE account_id=?""",(str(account_id),)).fetchone()

    def set_account_timer(self, account_id, *, last_activity_ts=None, last_message_ts=None,
                          next_activity_ts=None, enabled=True, state="READY"):
        with self.lock:
            self.conn.execute("""INSERT INTO account_timers
                (account_id,last_activity_ts,last_message_ts,next_activity_ts,enabled,state)
                VALUES(?,?,?,?,?,?) ON CONFLICT(account_id) DO UPDATE SET
                last_activity_ts=excluded.last_activity_ts,last_message_ts=excluded.last_message_ts,
                next_activity_ts=excluded.next_activity_ts,enabled=excluded.enabled,state=excluded.state""",
                (str(account_id),last_activity_ts,last_message_ts,next_activity_ts,int(bool(enabled)),str(state)))
            self.conn.commit()

    def all_account_timers(self):
        with self.lock:
            return self.conn.execute("SELECT account_id,last_activity_ts,last_message_ts,next_activity_ts,enabled,state FROM account_timers").fetchall()

    # ------------------------------------------------ per-stream account profiles
    def ensure_profile(self, stream_key, stream_name, platform, account_id, account_name):
        """Create (or refresh the names of) the profile of one account on one stream."""
        with self.lock:
            self.conn.execute("""INSERT INTO account_profiles
                (stream_key,account_id,platform,stream_name,account_name) VALUES(?,?,?,?,?)
                ON CONFLICT(stream_key,account_id) DO UPDATE SET
                platform=excluded.platform,stream_name=excluded.stream_name,
                account_name=excluded.account_name""",
                (str(stream_key), str(account_id), platform, stream_name, account_name))
            self.conn.commit()

    def save_profile_points(self, stream_key, stream_name, platform, account_id, account_name, points, ts):
        self.ensure_profile(stream_key, stream_name, platform, account_id, account_name)
        with self.lock:
            # Older replies (e.g. YouTube chat history on connect) never overwrite newer numbers.
            self.conn.execute("""UPDATE account_profiles SET points=?,points_ts=?
                WHERE stream_key=? AND account_id=? AND (points_ts IS NULL OR points_ts<=?)""",
                              (int(points), float(ts), str(stream_key), str(account_id), float(ts)))
            self.conn.commit()

    def save_profile_time(self, stream_key, stream_name, platform, account_id, account_name,
                          level, watch_text, watch_seconds, ts):
        self.ensure_profile(stream_key, stream_name, platform, account_id, account_name)
        with self.lock:
            # A reply without a level (or without a duration) keeps the previously known value.
            self.conn.execute("""UPDATE account_profiles SET
                level=COALESCE(?,level), watch_text=CASE WHEN ? IS NULL THEN watch_text ELSE ? END,
                watch_seconds=COALESCE(?,watch_seconds), time_ts=?
                WHERE stream_key=? AND account_id=? AND (time_ts IS NULL OR time_ts<=?)""",
                (level, watch_seconds, watch_text, watch_seconds, float(ts), str(stream_key), str(account_id), float(ts)))
            self.conn.commit()

    def profiles(self, stream_key=""):
        sql = """SELECT stream_key,stream_name,platform,account_id,account_name,
                        points,points_ts,level,watch_text,watch_seconds,time_ts
                 FROM account_profiles"""
        p = []
        if stream_key:
            sql += " WHERE stream_key=?"; p.append(str(stream_key))
        sql += " ORDER BY stream_name COLLATE NOCASE, account_name COLLATE NOCASE"
        with self.lock:
            return self.conn.execute(sql, p).fetchall()

    def add_value_history(self, account_id, account_name, stream_name, platform, kind, value_text, ts):
        """Append one received !points / !time value (never updates an older row)."""
        with self.lock:
            self.conn.execute("""INSERT INTO value_history
                (account_id,account_name,stream_name,platform,kind,value_text,ts) VALUES(?,?,?,?,?,?,?)""",
                (str(account_id), str(account_name or ""), str(stream_name or ""), str(platform or ""),
                 str(kind), str(value_text), float(ts)))
            self.conn.commit()

    def value_history(self, limit=100):
        with self.lock:
            return self.conn.execute("""SELECT id,account_id,account_name,stream_name,platform,kind,value_text,ts
                FROM value_history ORDER BY ts DESC, id DESC LIMIT ?""", (int(limit),)).fetchall()

    def delete_profiles(self, account_id="", stream_key=""):
        with self.lock:
            if account_id:
                self.conn.execute("DELETE FROM value_history WHERE account_id=?", (str(account_id),))
                self.conn.execute("DELETE FROM account_profiles WHERE account_id=?", (str(account_id),))
            if stream_key:
                self.conn.execute("DELETE FROM account_profiles WHERE stream_key=?", (str(stream_key),))
            self.conn.commit()

    def query(self, search="", username="", kind="All", limit=500):
        params=[]; where=[]
        if search:
            where.append("(username LIKE ? OR text LIKE ? OR channel LIKE ?)")
            q="%"+search+"%"; params += [q,q,q]
        if username:
            where.append("username LIKE ?"); params.append("%"+username+"%")
        if kind and kind != "All":
            where.append("kind=?"); params.append(kind)
        sql="SELECT local_time,platform,channel,username,text,kind FROM messages"
        if where: sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY ts DESC LIMIT ?"; params.append(int(limit))
        with self.lock:
            return self.conn.execute(sql, params).fetchall()


    def query_special(self, kind, search="", username="", limit=500):
        files={"Mentions":"chat_mentions.jsonl","Commands":"chat_commands.jsonl",
               "Points":"chat_points.jsonl","Time":"chat_times.jsonl"}
        fn=files.get(kind)
        if not fn: return []
        path=self.base/fn
        if not path.exists(): return []
        out=[]
        with self.lock:
            try:
                with open(path,"r",encoding="utf-8") as f:
                    for line in f:
                        try:o=json.loads(line)
                        except:continue
                        if search and search.casefold() not in json.dumps(o,ensure_ascii=False).casefold(): continue
                        if username and username.casefold() not in str(o.get("username","")).casefold(): continue
                        out.append(o)
            except OSError: pass
        return out[-int(limit):][::-1]

    def latest_value(self, stream_key, username, value_type):
        """Return the latest stored bot reply for one stream/account/value type."""
        with self.lock:
            row = self.conn.execute(
                "SELECT value,ts,raw_response FROM values_seen "
                "WHERE channel_id=? AND username=? AND value_type=?",
                (str(stream_key), str(username), str(value_type))
            ).fetchone()
            return row

    def latest_values(self, value_type=None, limit=500):
        sql="SELECT username,value_type,value,ts,raw_response FROM values_seen"
        p=[]
        if value_type:
            sql+=" WHERE value_type=?";p.append(value_type)
        sql+=" ORDER BY ts DESC LIMIT ?";p.append(int(limit))
        with self.lock:return self.conn.execute(sql,p).fetchall()

    def counts(self):
        with self.lock:
            return self.conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]

    def close(self):
        with self.lock:
            self.conn.close()
