"""Monitoring/sending engine: one worker thread per enabled channel.

The engine never touches Tkinter. It publishes events on a queue.Queue that the UI drains:
  ("log", "[HH:MM:SS] text")      ("channel", channel_id)      ("state",)
"""
from __future__ import annotations

import logging
import queue
import random
import threading
import time
from typing import Optional
from datetime import datetime

import accounts as acc
import kick_api
from http_util import ApiError
from models import (MIN_INTERVAL_SEC, PLATFORM_KICK, PLATFORM_LABEL, PLATFORM_YT, Channel,
                    LiveInfo, Runtime, parse_channel_url)
from oauth import AUTH_EXPIRED_MSG
from storage import app_dir
from chat_storage import ChatStore
from operational_store import OperationalStore
from chat_features import ChatProcessor
from bet_tracker import (BetTracker, JsonStore, format_bet, merge_commands, validate_bet_input)
from kick_chat import KickChatReader
from kick_events import ChatMessage, is_bot_name, match_account, normalize_chat_message

log = logging.getLogger("stream_activity_bot")


class Engine:
    def __init__(self, config, token_store, token_manager, youtube, kick, events: queue.Queue, chat_store=None):
        self.config = config
        self.token_store = token_store
        self.tm = token_manager
        self.yt = youtube
        self.kick = kick
        self.events = events
        self.running = False
        self.runtime: dict[str, Runtime] = {}
        self._workers: dict[str, tuple] = {}     # channel_id -> (thread, stop_event)
        self._lock = threading.RLock()
        self.sim_live: dict[str, bool] = {}       # demo: simulated LIVE toggle per channel
        self._sim_counter: dict[str, int] = {}
        self.chat_store = chat_store or ChatStore(app_dir())
        self.ops_store = OperationalStore(app_dir())
        self.bet_tracker = BetTracker(app_dir())
        self._profile_bets = JsonStore(app_dir() / "profile_bets.json", {})
        self._cmd_store = JsonStore(app_dir() / "saved_commands.json", [])
        self.chat_processor = ChatProcessor(self.chat_store, self.config, logger=self.log,
                                            on_profile=lambda: self.events.put(("profiles",)),
                                            on_bot_response=self.handle_bot_response,
                                            debug=self.debug)
        self._send_lock = threading.RLock()
        self._last_account_send: dict[str, float] = {}
        self._last_auto_send: dict[str, float] = {}      # account_id -> ts of its last AUTOMATIC message
        self._last_sent_message: dict[tuple[str, str], tuple[str, float]] = {}
        self._recent_outgoing_activity: dict[str, tuple[str, float]] = {}
        self.kick_chat: dict[str, KickChatReader] = {}      # stream_key -> ONE reader per stream
        self.chat_status: dict[str, str] = {}               # stream_key -> human readable chat reader state
        self._chat_logged: dict[str, str] = {}
        self.live_chat_messages: dict[str, list[dict]] = {}  # stream_key -> recent live messages
        self._yt_chat_owner: dict[str, str] = {}            # live_chat_id -> channel id that polls it
        self._account_timers: dict[str, dict] = {}
        self._timer_stop = threading.Event()
        self._timer_thread = None

    # ------------------------------------------------------------- streams
    def stream_key(self, ch: Channel) -> str:
        """Stable id of the STREAMER'S channel (not of your channel entry).

        Several entries may point at the same stream (for example one entry per account);
        they all share one profile set and one chat reader.
        """
        try:
            _, ref = parse_channel_url(ch.platform, ch.url)
            value = ref.value
        except Exception:
            value = ch.url
        return f"{ch.platform}:{str(value).strip().casefold()}"

    def stream_name(self, ch: Channel) -> str:
        return self.name_of(ch)

    def sync_profiles(self) -> None:
        """Create a profile for every logged account on every stream of the same platform.

        Sending is still controlled by Channel.connected_account_ids(), but the profile dashboard
        intentionally contains *all* logged accounts so a user can see a separate placeholder
        for each account and later populate it from a bot reply/manual command.
        """
        accounts = list(self.config.accounts)
        for ch in list(self.config.channels):
            key, name = self.stream_key(ch), self.stream_name(ch)
            for a in accounts:
                if a.platform == ch.platform:
                    self.chat_store.ensure_profile(key, name, PLATFORM_LABEL[ch.platform], a.id, a.display_name)
        self.events.put(("profiles",))

    def _stream_in_use(self, ch: Channel, exclude_id: str = "") -> bool:
        """True if another enabled channel entry on the same stream is currently live."""
        key = self.stream_key(ch)
        for other in self.config.channels:
            if other.id in (ch.id, exclude_id) or self.stream_key(other) != key:
                continue
            rt = self.runtime.get(other.id)
            if rt is not None and rt.live:
                return True
        return False

    # ------------------------------------------------------------- plumbing
    def log(self, text: str) -> None:
        log.info(text)
        self.events.put(("log", f"[{time.strftime('%H:%M:%S')}] {text}"))

    def debug(self, text: str) -> None:
        """Debug trail -> stream_activity_bot.log only (keeps the Activity page readable).

        Never pass tokens/passwords/secrets here.
        """
        log.info(text)

    def _changed(self, ch: Channel) -> None:
        self.events.put(("channel", ch.id))

    def get_runtime(self, channel_id: str) -> Runtime:
        with self._lock:
            rt = self.runtime.get(channel_id)
            if rt is None:
                rt = self.runtime[channel_id] = Runtime(status="Stopped")
            return rt

    @staticmethod
    def name_of(ch: Channel) -> str:
        return ch.display_name or ch.url

    # ---------------------------------------------------------- start / stop
    def start(self) -> None:
        with self._lock:
            if self.running:
                return
            self.running = True
            self._load_account_timers()
            for ch in list(self.config.channels):
                self._spawn(ch)
            self._timer_stop.clear()
            self._timer_thread = threading.Thread(target=self._timer_loop, daemon=True, name="account-timers")
            self._timer_thread.start()
        self.sync_profiles()
        self.events.put(("state",))
        self.log("Bot started" + (" (Demo/Test mode)" if self.config.settings.demo_mode else ""))

    def stop(self) -> None:
        with self._lock:
            was = self.running
            self.running = False
            self._timer_stop.set()
            for cid, (_, stop) in list(self._workers.items()):
                stop.set()
            for reader in self.kick_chat.values():
                reader.stop()
            self.kick_chat.clear()
            self._yt_chat_owner.clear()
            self._workers.clear()
            for cid, rt in self.runtime.items():
                rt.live, rt.next_send, rt.session_id, rt.status = False, None, "", "Stopped"
                rt.connection_status = "Offline"
        self.events.put(("state",))
        if was:
            self.log("Bot stopped")

    def _spawn(self, ch: Channel) -> None:
        self._stop_worker(ch.id)
        rt = Runtime(status="Disabled" if not ch.enabled else "Starting")
        rt.last_message = self.runtime.get(ch.id, Runtime()).last_message
        self.runtime[ch.id] = rt
        if not ch.enabled:
            self._changed(ch)
            return
        stop = threading.Event()
        t = threading.Thread(target=self._worker, args=(ch, rt, stop), daemon=True,
                             name=f"chan-{ch.id[:6]}")
        self._workers[ch.id] = (t, stop)
        t.start()

    def _stop_worker(self, channel_id: str) -> None:
        w = self._workers.pop(channel_id, None)
        if w:
            w[1].set()

    def channel_changed(self, ch: Channel) -> None:
        """Call after add/edit. Restarts that channel's worker if the bot is running."""
        with self._lock:
            if self.running:
                self._spawn(ch)
            else:
                self.runtime[ch.id] = Runtime(status="Stopped" if ch.enabled else "Disabled",
                                              last_message=self.runtime.get(ch.id, Runtime()).last_message)
        self._changed(ch)

    def channel_removed(self, channel_id: str) -> None:
        with self._lock:
            self._stop_worker(channel_id)
            gone = self.config.get_channel(channel_id)
            if gone is not None and not self._stream_in_use(gone, exclude_id=channel_id):
                reader = self.kick_chat.pop(self.stream_key(gone), None)
                if reader: reader.stop()
            self.runtime.pop(channel_id, None)
            self.sim_live.pop(channel_id, None)

    def account_changed(self, account_id: str) -> None:
        """Account added/removed/reconnected: make affected channels re-check immediately."""
        with self._lock:
            for ch in self.config.channels:
                if account_id in ch.connected_account_ids():
                    rt = self.runtime.get(ch.id)
                    if rt:
                        rt.force_check = True
                        rt.last_error = ""
        self.events.put(("state",))

    def toggle_sim_live(self, channel_id: str) -> Optional[bool]:
        with self._lock:
            new = not self.sim_live.get(channel_id, True)
            self.sim_live[channel_id] = new
            rt = self.runtime.get(channel_id)
            if rt:
                rt.force_check = True
            return new

    # --------------------------------------------------------------- worker
    def _poll_interval(self, ch: Channel) -> float:
        s = self.config.settings
        base = s.live_check_kick_sec if ch.platform == PLATFORM_KICK else s.live_check_youtube_sec
        return max(15.0, float(base)) * random.uniform(0.9, 1.1)

    def _worker(self, ch: Channel, rt: Runtime, stop: threading.Event) -> None:
        next_check = 0.0
        fails = 0
        while not stop.is_set():
            try:
                now = time.time()
                if rt.force_check or now >= next_check:
                    rt.force_check = False
                    self._poll(ch, rt, stop)
                    fails = 0
                    rt.last_error = ""
                    next_check = time.time() + self._poll_interval(ch)
                if rt.live and ch.platform == PLATFORM_YT and now >= rt.chat_next_poll:
                    self._poll_youtube_chat(ch, rt, stop)
                if stop.is_set():
                    break
            except ApiError as e:
                fails += 1
                next_check = time.time() + self._handle_poll_error(ch, rt, e, fails)
            except Exception as e:  # never let one channel kill the app
                fails += 1
                rt.status = f"Error: {type(e).__name__}"
                self._log_error_once(ch, rt, f"Unexpected error: {type(e).__name__}: {e}")
                self._changed(ch)
                next_check = time.time() + min(600, 30 * 2 ** min(fails, 5))
            stop.wait(0.5)

    def _log_error_once(self, ch: Channel, rt: Runtime, msg: str) -> None:
        if msg != rt.last_error:
            rt.last_error = msg
            self.ops_store.error(channel_id=ch.id, channel_name=self.name_of(ch),
                                 account_id=rt.active_account_id,
                                 account_name=(self.config.get_account(rt.active_account_id).display_name
                                               if rt.active_account_id and self.config.get_account(rt.active_account_id) else ""),
                                 platform=PLATFORM_LABEL[ch.platform], kind="runtime", message=msg)
            self.log(f"{PLATFORM_LABEL[ch.platform]} {self.name_of(ch)}: {msg}")

    def _handle_poll_error(self, ch: Channel, rt: Runtime, e: ApiError, fails: int) -> float:
        # Live state/timer are deliberately kept on errors so a network blip does not restart
        # the session timer; the Status column shows the problem.
        if e.kind == "auth":
            rt.status = "Auth expired"
            self._log_error_once(ch, rt, AUTH_EXPIRED_MSG)
            wait = 300
        elif e.kind == "rate_limit":
            wait = max(e.retry_after or 0, min(3600, 60 * 2 ** min(fails - 1, 6)))
            rt.status = f"Rate limited ({int(wait)}s)"
            self._log_error_once(ch, rt, f"Rate limit/quota reached - backing off {int(wait)}s ({e.message})")
        elif e.kind == "network":
            wait = min(300, 15 * 2 ** min(fails - 1, 5))
            rt.status = "Network error"
            self._log_error_once(ch, rt, e.message)
        elif e.kind == "not_found":
            wait = 300
            rt.status = "Channel not found"
            self._log_error_once(ch, rt, e.message)
        elif e.kind == "config":
            wait = 60
            rt.status = "Not configured"
            self._log_error_once(ch, rt, e.message)
        else:
            wait = min(600, 30 * 2 ** min(fails - 1, 5))
            rt.status = f"Error: {e.message}"[:90]
            self._log_error_once(ch, rt, e.message)
        self._changed(ch)
        return wait

    # ----------------------------------------------------------------- poll
    def _connected_accounts(self, ch: Channel):
        out = []
        for aid in ch.connected_account_ids():
            a = self.config.get_account(aid)
            if a is not None and a.platform == ch.platform:
                out.append(a)
        # Keep the currently working account first to avoid unnecessary API calls.
        if out and getattr(self.get_runtime(ch.id), "active_account_id", ""):
            active = self.get_runtime(ch.id).active_account_id
            out.sort(key=lambda a: 0 if a.id == active else 1)
        return out

    def _set_account_state(self, rt: Runtime, account, state: str, detail: str = "") -> None:
        rt.account_states[account.id] = {"name": account.display_name, "state": state, "detail": detail}

    def _api_ready(self, account):
        if account is None:
            return "Account missing"
        if not self.tm.has_tokens(account.id):
            return "Authorization expired"
        return acc.provider_problem(account.platform, self.config.settings, self.token_store)

    def _poll(self, ch: Channel, rt: Runtime, stop: threading.Event) -> None:
        settings = self.config.settings
        candidates = self._connected_accounts(ch)
        if not candidates:
            if settings.demo_mode:
                self._poll_simulated(ch, rt)
                return
            self._mark_offline(ch, rt, quiet=True)
            rt.status = "Account missing"
            rt.active_account_id = ""
            self._log_error_once(ch, rt, "No connected account is available for this channel.")
            self._changed(ch)
            return

        errors = []
        for account in candidates:
            if stop.is_set():
                return
            problem = self._api_ready(account)
            if problem:
                self._set_account_state(rt, account, "AUTH EXPIRED" if "expired" in problem.lower() else "NOT READY", problem)
                errors.append((account, ApiError(problem, kind="auth" if "expired" in problem.lower() else "config")))
                continue
            self._set_account_state(rt, account, "CHECKING", "")
            try:
                # Resolve the channel with the first usable account. The resolved channel
                # id is public/provider data and can then be reused by all connected accounts.
                self._ensure_resolved(ch, account)
                if stop.is_set():
                    return
                if ch.platform == PLATFORM_YT:
                    if rt.live and rt.session_id and not rt.simulated:
                        info = self.yt.still_live(account, rt.session_id, rt)
                    else:
                        info = self.yt.find_live(account, ch.channel_id, ch.extra.get("uploads", ""), rt,
                                                 settings.youtube_search_fallback_minutes * 60)
                else:
                    info = self.kick.find_live(account, ch.channel_id)
                rt.active_account_id = account.id
                rt.connection_status = "Connected"
                self._set_account_state(rt, account, "READY", "LIVE" if info else "Online")
                for other, _ in errors:
                    if other.id != account.id:
                        self._set_account_state(rt, other, rt.account_states.get(other.id, {}).get("state", "ERROR"),
                                                rt.account_states.get(other.id, {}).get("detail", ""))
                self._apply(ch, rt, info)
                return
            except ApiError as e:
                state = {"auth": "AUTH EXPIRED", "rate_limit": "RATE LIMITED",
                         "network": "NETWORK ERROR", "not_found": "NOT FOUND"}.get(e.kind, "ERROR")
                self._set_account_state(rt, account, state, e.message)
                errors.append((account, e))
            except Exception as e:
                self._set_account_state(rt, account, "ERROR", str(e))
                errors.append((account, ApiError(str(e), kind="other")))

        # A failed status request is UNKNOWN, not OFFLINE. Preserve the last known live state
        # until a successful platform response proves that the stream ended.
        rt.connection_status = "Reconnecting" if any(e.kind in ("network", "rate_limit") for _, e in errors) else "Connection failed"
        rt.active_account_id = ""
        if errors:
            preferred = next((e for _, e in errors if e.kind == "auth"), errors[0][1])
            if all(e.kind == "auth" for _, e in errors):
                rt.status = "Auth expired"
            elif any(e.kind == "rate_limit" for _, e in errors):
                rt.status = "All accounts rate limited"
            else:
                rt.status = f"No account available ({len(errors)})"
            self._log_error_once(ch, rt, f"All connected accounts failed: {preferred.message}")
        self._changed(ch)

    def _poll_simulated(self, ch: Channel, rt: Runtime) -> None:
        rt.simulated = True
        if self.sim_live.get(ch.id, True):
            n = self._sim_counter.setdefault(ch.id, 1)
            info = LiveInfo(session_id=f"demo-{n}", chat_id="demo", title="Demo stream")
        else:
            info = None
            if rt.live:
                self._sim_counter[ch.id] = self._sim_counter.get(ch.id, 1) + 1
        self._apply(ch, rt, info)
        if info:
            rt.status = "Running (demo)"
            self._simulate_demo_bets(ch, info.session_id)
            self._changed(ch)

    def _ensure_resolved(self, ch: Channel, account) -> None:
        if ch.channel_id:
            return
        _, ref = parse_channel_url(ch.platform, ch.url)
        if ch.platform == PLATFORM_YT:
            r = self.yt.resolve_channel(account, ref)
            ch.extra["uploads"] = r["uploads"]
        else:
            r = self.kick.resolve_channel(account, ref.value)
        ch.channel_id = r["channel_id"]
        if r.get("title"):
            ch.display_name = r["title"]
        if self.config.get_channel(ch.id) is ch:     # don't clobber a concurrent edit
            self.config.save()
        self.log(f"{PLATFORM_LABEL[ch.platform]} channel resolved: {self.name_of(ch)}")

    def _mark_offline(self, ch: Channel, rt: Runtime, quiet: bool = False) -> None:
        old_session = rt.session_id
        if rt.live and not quiet:
            self.log(f"Channel went OFFLINE: {PLATFORM_LABEL[ch.platform]} {self.name_of(ch)}")
        if old_session:
            self.ops_store.session_end(ch.id, old_session)
        rt.live, rt.session_id, rt.next_send, rt.live_chat_id = False, "", None, ""
        rt.started_at = 0.0
        rt.viewers = None
        rt.connection_status = "Offline"
        rt.chat_page_token, rt.chat_next_poll = "", 0.0
        for chat_id in [k for k, v in self._yt_chat_owner.items() if v == ch.id]:
            self._yt_chat_owner.pop(chat_id, None)
        if not self._stream_in_use(ch):         # keep the shared reader while a sibling entry is live
            key = self.stream_key(ch)
            reader = self.kick_chat.pop(key, None)
            if reader: reader.stop()
            self.live_chat_messages.pop(key, None)

    def _apply(self, ch: Channel, rt: Runtime, info: Optional[LiveInfo]) -> None:
        if info is None:
            if rt.live:
                self._mark_offline(ch, rt)           # timer reset: next stream starts fresh
            rt.status = "Waiting"
        else:
            if not rt.live or rt.session_id != info.session_id:
                if rt.live:
                    self.log(f"New live session on {PLATFORM_LABEL[ch.platform]} {self.name_of(ch)}")
                rt.live = True
                rt.session_id = info.session_id
                rt.live_chat_id = info.chat_id
                rt.title = info.title
                rt.started_at = info.started_at or time.time()
                rt.channel_id = info.channel_id or ch.channel_id
                rt.streamer = info.streamer or ch.display_name or ch.url
                rt.category = info.category or ""
                rt.game = info.game or rt.category
                rt.viewers = info.viewers
                rt.thumbnail = info.thumbnail or ""
                rt.stream_url = info.url or ch.url
                self.ops_store.session_start(ch.id, self.name_of(ch), PLATFORM_LABEL[ch.platform], info.session_id, ts=rt.started_at)
                rt.next_send = None
                rt.send_fails = 0
                self._ensure_live_account_timers(ch)
                self.log(f"{PLATFORM_LABEL[ch.platform]} channel detected LIVE: {self.name_of(ch)} (started {datetime.fromtimestamp(rt.started_at).strftime('%H:%M:%S')})")
            else:
                # Refresh metadata without resetting the live session/timer.
                rt.title = info.title or rt.title
                rt.started_at = info.started_at or rt.started_at
                rt.channel_id = info.channel_id or rt.channel_id or ch.channel_id
                rt.streamer = info.streamer or rt.streamer or ch.display_name or ch.url
                rt.category = info.category or rt.category
                rt.game = info.game or rt.game
                rt.viewers = info.viewers
                rt.thumbnail = info.thumbnail or rt.thumbnail
                rt.stream_url = info.url or rt.stream_url or ch.url
                rt.live_chat_id = info.chat_id
            if ch.platform == PLATFORM_KICK:
                self._start_kick_chat(ch)
            rt.status = "Running"
            rt.connection_status = "Connected"
        self._changed(ch)

    # ---------------------------------------------------------- account timers
    def _load_account_timers(self):
        self._account_timers.clear()
        rows = {str(r[0]): r for r in self.chat_store.all_account_timers()}
        for a in list(self.config.accounts):
            row = rows.get(a.id)
            if row:
                state = row[5] or "READY"
                next_activity = row[3]
                # v18.3 used AWAITING_CHAT while waiting for an echoed message.
                # v18.4 treats a successful API send as the activity itself, so recover
                # old persisted timers instead of leaving them permanently stuck.
                if state == "AWAITING_CHAT":
                    if row[2] is not None:
                        next_activity = float(row[2]) + self._interval_for_account(a.id)
                    state = "WAITING" if next_activity else "READY"
                self._account_timers[a.id] = {"lastActivityAt": row[1], "lastMessageAt": row[2],
                                              "nextActivityAt": next_activity, "enabled": bool(row[4]),
                                              "state": state}
            else:
                self._account_timers[a.id] = {"lastActivityAt": None, "lastMessageAt": None,
                                              "nextActivityAt": None, "enabled": bool(a.automatic_activity_enabled),
                                              "state": "READY"}
                self._persist_account_timer(a.id)

    def _persist_account_timer(self, account_id):
        st = self._account_timers.get(account_id)
        if not st: return
        self.chat_store.set_account_timer(account_id, last_activity_ts=st.get("lastActivityAt"),
            last_message_ts=st.get("lastMessageAt"), next_activity_ts=st.get("nextActivityAt"),
            enabled=st.get("enabled", True), state=st.get("state", "READY"))

    def set_account_auto(self, account_id, enabled: bool) -> None:
        """Stop / resume AUTOMATIC messages of ONE account (its chat tracking keeps working)."""
        account = self.config.get_account(account_id)
        if account is None:
            return
        st = self._timer_state(account)
        st["enabled"] = bool(enabled)
        account.automatic_activity_enabled = bool(enabled)
        if not enabled:
            st["nextActivityAt"] = None
            st["state"] = "DISABLED"
        else:
            now = time.time()
            last = st.get("lastActivityAt")
            base = (float(last) + self._interval_for_account(account_id)) if last else now + max(0, int(self.config.settings.first_message_delay_sec))
            st["nextActivityAt"] = self.stagger_due(account_id, max(base, now + 5))
            st["state"] = "WAITING"
        self._persist_account_timer(account_id)
        try:
            self.config.save()
        except Exception:
            pass
        self.debug(f"ACCOUNT AUTO {'ON' if enabled else 'OFF'} account={account.display_name}")
        self.events.put(("accounts",))

    def _stagger_gap_sec(self) -> float:
        """Random minimum distance (seconds) between two accounts' automatic messages."""
        s = self.config.settings
        lo = max(0.0, float(getattr(s, "account_stagger_min_minutes", 3.0))) * 60
        hi = max(lo, float(getattr(s, "account_stagger_max_minutes", 6.0)) * 60)
        return random.uniform(lo, hi) if hi > 0 else 0.0

    def stagger_due(self, account_id, due: float) -> float:
        """Move *due* later until it is at least a few minutes away from every OTHER enabled
        account's scheduled (or just sent) automatic message - accounts never write together."""
        gap = self._stagger_gap_sec()
        if gap <= 0:
            return due
        others = []
        for aid, st in list(self._account_timers.items()):
            if aid == account_id or not st.get("enabled") or self.config.get_account(aid) is None:
                continue
            if st.get("nextActivityAt") is not None:
                others.append(float(st["nextActivityAt"]))
            if self._last_auto_send.get(aid):
                others.append(float(self._last_auto_send[aid]))
        for _ in range(len(others) + 1):
            # 1e-6 tolerance: `due = clash + gap` can differ from gap by 1 ulp and re-match the same clash forever
            clash = next((o for o in sorted(others) if abs(due - o) < gap - 1e-6), None)
            if clash is None:
                break
            due = clash + gap
        return due

    def _interval_for_account(self, account_id, channel=None):
        if channel is not None:
            return self.next_interval_sec(channel)
        vals=[self.next_interval_sec(c) for c in self.config.channels if c.enabled]
        return random.choice(vals) if vals else 29*60

    def _ensure_live_account_timers(self, ch):
        now=time.time()
        for aid in ch.connected_account_ids():
            a=self.config.get_account(aid)
            if a is None or a.platform != ch.platform: continue
            st=self._account_timers.setdefault(aid,{"lastActivityAt":None,"lastMessageAt":None,
                "nextActivityAt":None,"enabled":bool(a.automatic_activity_enabled),"state":"READY"})
            if not st.get("enabled"): continue
            if st.get("lastActivityAt") is not None:
                if st.get("nextActivityAt") is None:
                    st["nextActivityAt"] = self.stagger_due(aid, float(st["lastActivityAt"])+self._interval_for_account(aid,ch))
                if st["nextActivityAt"] <= now and st.get("state") != "AWAITING_CHAT": st["state"]="READY"
            elif st.get("nextActivityAt") is None:
                st["nextActivityAt"] = self.stagger_due(aid, now + max(0,int(self.config.settings.first_message_delay_sec)))
                st["state"]="WAITING"
            self._persist_account_timer(aid)
        self.events.put(("accounts",))

    # Why a message may (or may not) restart the countdown.  This table is the SINGLE source of
    # truth for the timer rule:
    #   USER MESSAGE / USER !points / USER !time  = RESET
    #   PROGRAM AUTOMATIC MESSAGE (sent OK)       = RESET
    #   BOTTLY RESPONSE                           = NO RESET
    TIMER_RESET_REASONS = {
        "chat": "USER_MESSAGE",
        "automatic_send": "AUTOMATIC_MESSAGE",
        "manual_send": "MANUAL_MESSAGE",
    }

    def _timer_state(self, account):
        return self._account_timers.setdefault(account.id, {
            "lastActivityAt": None, "lastMessageAt": None, "nextActivityAt": None,
            "enabled": bool(account.automatic_activity_enabled), "state": "READY"})

    def reset_activity_timer(self, account, ts, reason, interval_sec=None, force=False) -> bool:
        """Restart one account's countdown.  The ONLY place that moves the timer.

        Callers: record_user_chat_activity (USER_MESSAGE) and successful sends
        (AUTOMATIC_MESSAGE / MANUAL_MESSAGE).  Any other reason - notably BOTTLY_RESPONSE - is
        refused and logged as TIMER NOT RESET.
        """
        if reason not in ("USER_MESSAGE", "AUTOMATIC_MESSAGE", "MANUAL_MESSAGE"):
            self.debug(f"TIMER NOT RESET account={account.display_name} REASON={reason}")
            return False
        st = self._timer_state(account)
        ts = float(ts)
        last = st.get("lastActivityAt")
        if not force and last is not None and ts < float(last) - 1.0:
            # An old message replayed by a reconnect/history must not move the timer backwards.
            self.debug(f"TIMER NOT RESET account={account.display_name} REASON=OLDER_THAN_LAST_ACTIVITY")
            return False
        st["lastActivityAt"] = ts
        st["lastMessageAt"] = ts
        if not st.get("enabled", True):
            # A STOPPED account only remembers its last activity; it never gets a next send time.
            st["nextActivityAt"] = None
            st["state"] = "DISABLED"
            self._persist_account_timer(account.id)
            self.debug(f"TIMER RESET account={account.display_name} REASON={reason} (account stopped: no next send)")
            self.events.put(("accounts",))
            return True
        due = ts + (float(interval_sec) if interval_sec is not None
                    else self._interval_for_account(account.id))
        st["nextActivityAt"] = self.stagger_due(account.id, due)
        st["state"] = "WAITING"
        self._persist_account_timer(account.id)
        self.debug(f"TIMER RESET account={account.display_name} REASON={reason}")
        label = {"USER_MESSAGE": "chat message", "AUTOMATIC_MESSAGE": "automatic message",
                 "MANUAL_MESSAGE": "manual message"}[reason]
        self.log(f"[ACTIVITY] {account.display_name}: {label} recorded; countdown restarted")
        self.events.put(("accounts",))
        return True

    def _record_account_activity(self, *, account, message_id, ts, platform, channel, channel_id,
                                 stream_id, message, source="chat", interval_sec=None):
        """Persist one Recent-Activity row and, ONLY for reset sources, restart the timer.

        Persisting never depends on the chat-history setting.  Returns True for a new event.
        """
        # Message IDs are usually unique per platform; namespacing prevents a collision between
        # providers/streams from silently dropping a real activity event.
        activity_id = f"{str(platform).casefold()}:{str(channel_id or channel)}:{str(message_id)}"
        is_new = self.chat_store.record_account_activity(
            account_id=account.id, message_id=activity_id, platform=platform, channel=channel,
            channel_id=channel_id, stream_id=stream_id, message=message, ts=ts, source=source)
        if not is_new:
            return False
        reason = self.TIMER_RESET_REASONS.get(source)
        if reason:
            self.reset_activity_timer(account, ts, reason, interval_sec)
        else:
            # e.g. bot_response: logged for Recent Activity, countdown deliberately untouched.
            self.debug(f"TIMER NOT RESET account={account.display_name} REASON=BOTTLY_RESPONSE")
        self.events.put(("account_activity", account.id, message_id))
        self.events.put(("accounts",))
        return True

    def record_user_chat_activity(self, account, msg: ChatMessage, *, platform, channel, channel_id,
                                  stream_id="") -> bool:
        """YOUR chat message (normal text, !points or !time): record it and restart the countdown."""
        return self._record_account_activity(
            account=account, message_id=str(msg.message_id), ts=float(msg.timestamp or time.time()),
            platform=platform, channel=channel, channel_id=channel_id,
            stream_id=stream_id or msg.stream_id, message=msg.message, source="chat")

    def handle_bot_response(self, account, kind, value, text, message_id, ts) -> bool:
        """BOTTLY's reply to !points / !time.  Shown in Recent Activity; NEVER resets the timer."""
        label = "!points" if kind == "points" else "!time"
        rt_name, channel_id = "", ""
        for ch in self.config.channels:
            if account.id in ch.connected_account_ids() and ch.platform == account.platform:
                rt_name, channel_id = self.name_of(ch), ch.channel_id
                break
        return self._record_account_activity(
            account=account, message_id=f"bot:{message_id or int(float(ts) * 1000)}", ts=float(ts),
            platform=PLATFORM_LABEL.get(account.platform, account.platform), channel=rt_name or "chat",
            channel_id=channel_id, stream_id="", message=f"BOTTLY ({label}): {text}", source="bot_response")

    def process_chat_message(self, msg: ChatMessage, *, platform, channel, channel_id, stream_key,
                             stream_name=None) -> Optional[str]:
        """THE chat pipeline.  Every platform feeds one normalized ChatMessage through here:

            CHAT RECEIVED -> bot? -> ACCOUNT MATCH -> (your message) record activity + RESET TIMER
                          -> chat_processor: commands / pending / BOTTLY response parsing

        Returns the matched Account.id (or None).  Never raises into the reader thread.
        """
        stream_name = stream_name or channel
        platform_norm = str(platform or "").strip().casefold()
        self.debug(f"CHAT RECEIVED event={msg.event_type} user_id={msg.user_id} username={msg.username} "
                   f"display_name={msg.display_name} message={msg.message!r}")
        accounts = [a for a in self.config.accounts if a.platform.casefold() == platform_norm]
        bots = [str(x) for x in (self.config.settings.response_bot_names or []) if str(x).strip()] or ["BOTTLY"]

        # 1) BOTTLY / response bots are never one of our accounts and never move the timer.
        msg.is_bot = platform_norm == "kick" and is_bot_name(msg.names(), bots)
        account, reason = (None, "bot") if msg.is_bot else match_account(msg, accounts)
        msg.is_self = account is not None
        self.debug(f"ACCOUNT MATCH matched_account={account.display_name if account else None} "
                   f"match_reason={reason if account or reason == 'bot' else 'none'}")

        # 2) In-memory live feed (Stream Chat UI) - independent of chat-history logging.
        live = self.live_chat_messages.setdefault(stream_key, [])
        live.append({"message_id": str(msg.message_id), "ts": float(msg.timestamp or time.time()),
                     "username": msg.username, "display_name": msg.display_name, "user_id": msg.user_id,
                     "text": msg.message, "reply_to": msg.reply_to, "is_self": msg.is_self,
                     "is_bot": msg.is_bot})
        if len(live) > 150:
            del live[:-150]
        self.events.put(("chat", stream_key))

        # 2b) !bet <letter> <points> -> REAL statistics (never raises into the reader thread).
        try:
            self._ingest_bet(msg, platform=platform, stream_key=stream_key)
        except Exception as e:
            self.debug(f"BET ingest error: {type(e).__name__}: {e}")

        # 3) YOUR activity (any message, including !points/!time) -> Recent Activity + timer reset.
        if account is not None:
            uid = str(msg.user_id or "").strip()
            if platform_norm == "kick" and uid and not str(account.external_id or "").strip():
                account.external_id = uid               # learn the stable id from a username match
                try:
                    self.config.save()
                except Exception:
                    pass
            echo = self._recent_outgoing_activity.get(account.id)
            if echo and str(echo[0]).strip() == msg.message.strip() and time.time() - float(echo[1]) <= 30:
                self.debug(f"CHAT ECHO of our own sent message ignored (timer already reset at send) "
                           f"account={account.display_name}")
            else:
                self.record_user_chat_activity(account, msg, platform=platform, channel=channel,
                                               channel_id=channel_id, stream_id=msg.stream_id)
        elif msg.is_bot:
            self.debug(f"BOTTLY message from {msg.username}: TIMER NOT RESET REASON=BOTTLY_RESPONSE")

        # 4) Commands, pending commands and bot-response parsing.  Errors here must never stop
        #    the reader or the activity/timer path above.
        try:
            self.chat_processor.process(
                message_id=msg.message_id, ts=msg.timestamp, platform=platform, channel=channel,
                channel_id=channel_id, username=msg.username, user_id=msg.user_id, text=msg.message,
                stream_key=stream_key, stream_name=stream_name, reply_to=msg.reply_to,
                account_hint_id=account.id if account else None, is_self=msg.is_self, is_bot=msg.is_bot)
        except Exception as e:
            self.log(f"{platform} chat processing error: {type(e).__name__}: {e}")
        return account.id if account else None

    def handle_incoming_chat_message(self, *, message_id, ts, platform, channel, channel_id,
                                    username, user_id, text, stream_id="", source="chat",
                                    stream_key="", reply_to="", **_ignored):
        """Backward-compatible wrapper (YouTube + old callers) around process_chat_message().

        Unknown keyword arguments are ignored deliberately: a new reader field must never again
        raise TypeError and silently kill activity tracking.
        """
        msg = ChatMessage(user_id=str(user_id or ""), username=str(username or ""),
                          display_name=str(username or ""), message=str(text or ""),
                          event_type="chat", stream_id=str(stream_id or ""), timestamp=float(ts or time.time()),
                          message_id=str(message_id), reply_to=str(reply_to or ""))
        if not msg.message and not msg.username:
            return None
        return self.process_chat_message(msg, platform=platform, channel=channel, channel_id=channel_id,
                                         stream_key=stream_key or str(channel_id))

    def _find_live_channel_for_account(self, account_id):
        for ch in self.config.channels:
            if not ch.enabled or account_id not in ch.connected_account_ids(): continue
            rt=self.runtime.get(ch.id)
            if rt and rt.live and rt.connection_status == "Connected": return ch,rt
        return None,None

    def send_automatic_message(self, account_id):
        """Send ONE automatic message.  The timer is reset ONLY after the send succeeded."""
        st=self._account_timers.get(account_id)
        if not st or not st.get("enabled") or st.get("state") == "AWAITING_CHAT": return
        ch,rt=self._find_live_channel_for_account(account_id)
        if ch is None: return
        account=self.config.get_account(account_id)
        msgs=[m for m in ch.messages if m and m.strip()]
        if account is None or not msgs: return
        text=random.choice(msgs)
        if ch.platform == PLATFORM_YT and not rt.live_chat_id: return
        # Guard: another account wrote automatically a moment ago -> wait a few minutes, never together.
        now_=time.time(); gap_=self._stagger_gap_sec()
        recent=[t for aid_,t in self._last_auto_send.items() if aid_!=account.id and now_-t<gap_]
        if recent:
            st["nextActivityAt"]=max(recent)+gap_+random.uniform(0,30)
            self._persist_account_timer(account.id)
            self.debug(f"STAGGER account={account.display_name} postponed to {time.strftime('%H:%M:%S',time.localtime(st['nextActivityAt']))}")
            self.events.put(("accounts",)); return
        if not self._acquire_send_slot(account.id, threading.Event()): return
        try:
            if ch.platform == PLATFORM_YT: self.yt.send_message(account,rt.live_chat_id,text)
            else: self.kick.send_message(account,ch.channel_id,text)
            sent_ts = time.time()
            self._last_auto_send[account.id] = sent_ts
            self.ops_store.send(channel_id=ch.id,channel_name=self.name_of(ch),account_id=account.id,
                account_name=account.display_name,platform=PLATFORM_LABEL[ch.platform],message=text,success=True)
            interval = self.next_interval_sec(ch)
            self._recent_outgoing_activity[account.id] = (text, sent_ts)
            self._record_account_activity(
                account=account, message_id=f"auto:{account.id}:{sent_ts:.6f}", ts=sent_ts,
                platform=PLATFORM_LABEL[ch.platform], channel=self.name_of(ch), channel_id=ch.channel_id,
                stream_id=rt.session_id, message=text, source="automatic_send", interval_sec=interval
            )
            self.log(f"[SEND] Automatic message submitted for {account.display_name}; next send in {int(interval)}s")
            self.events.put(("accounts",)); self._changed(ch)
        except ApiError as e:
            st["state"]="READY"; st["nextActivityAt"]=self.stagger_due(account.id,time.time()+min(120,self.next_interval_sec(ch)))
            self._persist_account_timer(account.id)
            self.ops_store.send(channel_id=ch.id,channel_name=self.name_of(ch),account_id=account.id,
                account_name=account.display_name,platform=PLATFORM_LABEL[ch.platform],message=text,success=False,error_kind=e.kind,error=e.message)
            self.debug(f"TIMER NOT RESET account={account.display_name} REASON=SEND_FAILED (retry scheduled)")
            self._log_error_once(ch,rt,f"Automatic send failed: {e.message}")
        except Exception as e:
            st["state"]="READY"; st["nextActivityAt"]=time.time()+60
            self._persist_account_timer(account.id)
            self.debug(f"TIMER NOT RESET account={account.display_name} REASON=SEND_FAILED (retry scheduled)")
            self._log_error_once(ch,rt,f"Automatic send failed: {e}")

    _send_automatic_for_account = send_automatic_message      # legacy name

    def _timer_loop(self):
        while self.running and not self._timer_stop.is_set():
            now=time.time()
            for aid,st in list(self._account_timers.items()):
                if not st.get("enabled") or st.get("state") == "AWAITING_CHAT": continue
                due=st.get("nextActivityAt")
                if due is not None and now >= float(due):
                    # The timer is changed only by explicit successful-send/chat
                    # handlers.  BOTTLY replies never call the reset path.
                    self.send_automatic_message(aid)
            self.events.put(("tick",))
            self._timer_stop.wait(1.0)

    # ----------------------------------------------------------------- send
    @staticmethod
    def next_interval_sec(ch: Channel) -> float:
        base = max(0.0, float(ch.interval_minutes)) * 60
        j = max(0.0, float(ch.jitter_minutes))
        if j >= 1 and float(j).is_integer():
            offset = random.randint(-int(j), int(j)) * 60      # whole-minute steps, e.g. 27..31
        else:
            offset = random.uniform(-j, j) * 60
        return max(float(MIN_INTERVAL_SEC), base + offset)

    def _acquire_send_slot(self, account_id: str, stop: threading.Event) -> bool:
        gap = max(0.25, float(self.config.settings.account_send_gap_sec))
        while not stop.is_set():
            with self._send_lock:
                wait = gap - (time.time() - self._last_account_send.get(account_id, 0.0))
                if wait <= 0:
                    self._last_account_send[account_id] = time.time()
                    return True
            stop.wait(min(wait, 0.5))
        return False

    def _send(self, ch: Channel, rt: Runtime, stop: threading.Event) -> None:
        label = PLATFORM_LABEL[ch.platform]
        msgs = [m for m in ch.messages if m and m.strip()]
        if not msgs:
            rt.status = "No messages configured"
            rt.next_send = time.time() + self.next_interval_sec(ch)
            self._changed(ch)
            return
        text = random.choice(msgs)

        account_ids = ch.connected_account_ids()
        accounts = [self.config.get_account(aid) for aid in account_ids]
        accounts = [a for a in accounts if a is not None and a.platform == ch.platform]

        if self.config.settings.demo_mode:
            self.log(f"[TEST] {label} {self.name_of(ch)} -> {text} ({len(accounts)} configured account(s))")
            rt.last_message = text
            rt.next_send = time.time() + self.next_interval_sec(ch)
            rt.status = "Running (demo)" if rt.simulated else "Running (test)"
            self._changed(ch)
            return

        if not accounts:
            rt.status = "Account missing"
            rt.next_send = time.time() + 60
            self._changed(ch)
            return

        if ch.platform == PLATFORM_YT and not rt.live_chat_id:
            self._handle_send_error(ch, rt, ApiError("No active live chat found.", kind="chat_unavailable"))
            return

        sent = 0
        failures = []
        for account in accounts:
            if stop.is_set():
                return
            key = (ch.id, account.id)
            previous = self._last_sent_message.get(key)
            window = max(0, int(self.config.settings.duplicate_send_window_sec))
            if previous and previous[0] == text and time.time() - previous[1] < window:
                self.log(f"{label} {self.name_of(ch)} [{account.display_name}]: duplicate send suppressed")
                continue
            if not self._acquire_send_slot(account.id, stop):
                return
            try:
                if ch.platform == PLATFORM_YT:
                    self.yt.send_message(account, rt.live_chat_id, text)
                else:
                    self.kick.send_message(account, ch.channel_id, text)
                sent += 1
                self._last_sent_message[key] = (text, time.time())
                self._set_account_state(rt, account, "READY", "Last send OK")
                self.ops_store.send(channel_id=ch.id, channel_name=self.name_of(ch), account_id=account.id,
                                    account_name=account.display_name, platform=label, message=text, success=True)
                sent_ts = time.time()
                self._recent_outgoing_activity[account.id] = (text, sent_ts)
                self._record_account_activity(
                    account=account, message_id=f"auto-legacy:{account.id}:{sent_ts:.6f}",
                    ts=sent_ts, platform=label, channel=self.name_of(ch), channel_id=ch.channel_id,
                    stream_id=rt.session_id, message=text, source="automatic_send",
                    interval_sec=self.next_interval_sec(ch)
                )
                self.log(f"{label} {self.name_of(ch)} [{account.display_name}] -> {text}")
            except ApiError as e:
                failures.append((account, e))
                state = {"auth": "AUTH EXPIRED", "rate_limit": "RATE LIMITED", "network": "NETWORK ERROR"}.get(e.kind, "ERROR")
                self._set_account_state(rt, account, state, e.message)
                self.ops_store.send(channel_id=ch.id, channel_name=self.name_of(ch), account_id=account.id,
                                    account_name=account.display_name, platform=label, message=text, success=False,
                                    error_kind=e.kind, error=e.message)
                self.ops_store.error(channel_id=ch.id, channel_name=self.name_of(ch), account_id=account.id,
                                     account_name=account.display_name, platform=label, kind=e.kind, message=e.message)
                self.log(f"{label} {self.name_of(ch)} [{account.display_name}]: Send failed: {e.message}")
            except Exception as e:
                failures.append((account, ApiError(str(e), kind="other")))
                self._set_account_state(rt, account, "ERROR", str(e))
                self.ops_store.send(channel_id=ch.id, channel_name=self.name_of(ch), account_id=account.id,
                                    account_name=account.display_name, platform=label, message=text, success=False,
                                    error_kind="other", error=str(e))
                self.ops_store.error(channel_id=ch.id, channel_name=self.name_of(ch), account_id=account.id,
                                     account_name=account.display_name, platform=label, kind="other", message=str(e))
                self.log(f"{label} {self.name_of(ch)} [{account.display_name}]: Send failed: {e}")

        if stop.is_set():
            return
        if sent == 0 and failures:
            self._handle_send_error(ch, rt, failures[0][1])
            return

        rt.send_fails = 0
        rt.last_message = text
        rt.next_send = time.time() + self.next_interval_sec(ch)
        if failures:
            rt.status = f"Running ({sent}/{len(accounts)} accounts sent)"
        elif sent:
            rt.status = "Running"
        else:
            rt.status = "Duplicate suppressed"
        self._changed(ch)

    def manual_send(self, channel_id: str, account_ids: list[str], text: str) -> dict:
        """Send a user-entered message immediately from one or more selected accounts.

        This deliberately bypasses the channel's automatic interval. It still uses the
        same per-account safety gap, API clients, logging and send history as automatic sends.
        The caller is expected to run this method outside the Tkinter/UI thread.
        """
        ch = self.config.get_channel(channel_id)
        if ch is None:
            return {"ok": False, "error": "Каналът не е намерен."}
        text = (text or "").strip()
        if not text:
            return {"ok": False, "error": "Напиши съобщение."}
        limit = 200 if ch.platform == PLATFORM_YT else 500
        if len(text) > limit:
            return {"ok": False, "error": f"Съобщението е {len(text)} знака, а лимитът е {limit}."}

        allowed = set(ch.connected_account_ids())
        selected = []
        for aid in account_ids or []:
            if aid in allowed and aid not in {a.id for a in selected}:
                a = self.config.get_account(aid)
                if a is not None and a.platform == ch.platform:
                    selected.append(a)
        if not selected:
            return {"ok": False, "error": "Избери поне един свързан акаунт за този стрийм."}

        label = PLATFORM_LABEL[ch.platform]
        rt = self.get_runtime(ch.id)
        if self.config.settings.demo_mode:
            return {"ok": False, "error": "Demo/test mode is disabled for production sending. Switch to LIVE mode."}

        if not rt.live:
            return {"ok": False, "error": "Този стрийм не е отбелязан като LIVE. Натисни „Старт“ и изчакай LIVE статуса."}
        if ch.platform == PLATFORM_YT and not rt.live_chat_id:
            return {"ok": False, "error": "YouTube live chat още не е готов. Изчакай да се зареди LIVE чатът."}
        if ch.platform == PLATFORM_KICK and not ch.channel_id:
            return {"ok": False, "error": "Kick каналът още не е разрешен. Изчакай да стане LIVE."}

        sent, failures = 0, []
        for account in selected:
            if not self._acquire_send_slot(account.id, threading.Event()):
                failures.append((account.display_name, "Изпращането беше прекъснато."))
                continue
            try:
                if ch.platform == PLATFORM_YT:
                    self.yt.send_message(account, rt.live_chat_id, text)
                else:
                    self.kick.send_message(account, ch.channel_id, text)
                sent += 1
                self._last_sent_message[(ch.id, account.id)] = (text, time.time())
                self._set_account_state(rt, account, "READY", "Last manual send OK")
                self.ops_store.send(channel_id=ch.id, channel_name=self.name_of(ch), account_id=account.id,
                                    account_name=account.display_name, platform=label, message=text, success=True)
                sent_ts = time.time()
                self._recent_outgoing_activity[account.id] = (text, sent_ts)
                self._record_account_activity(
                    account=account, message_id=f"manual:{account.id}:{sent_ts:.6f}",
                    ts=sent_ts, platform=label, channel=self.name_of(ch), channel_id=ch.channel_id,
                    stream_id=rt.session_id, message=text, source="manual_send",
                    interval_sec=self.next_interval_sec(ch)
                )
                # Guarantee: a successful manual send ALWAYS restarts this account's countdown,
                # even if the activity row was a duplicate and skipped the normal reset path.
                if abs(float(self._timer_state(account).get("lastActivityAt") or 0) - sent_ts) > 1.0:
                    self.reset_activity_timer(account, sent_ts, "MANUAL_MESSAGE",
                                              self.next_interval_sec(ch), force=True)
                self.log(f"{label} {self.name_of(ch)} [{account.display_name}] [РЪЧНО] -> {text}")
            except ApiError as e:
                failures.append((account.display_name, e.message))
                state = {"auth": "AUTH EXPIRED", "rate_limit": "RATE LIMITED", "network": "NETWORK ERROR"}.get(e.kind, "ERROR")
                self._set_account_state(rt, account, state, e.message)
                self.ops_store.send(channel_id=ch.id, channel_name=self.name_of(ch), account_id=account.id,
                                    account_name=account.display_name, platform=label, message=text, success=False,
                                    error_kind=e.kind, error=e.message)
                self.log(f"{label} {self.name_of(ch)} [{account.display_name}] [РЪЧНО]: Send failed: {e.message}")
            except Exception as e:
                failures.append((account.display_name, str(e)))
                self._set_account_state(rt, account, "ERROR", str(e))
                self.ops_store.send(channel_id=ch.id, channel_name=self.name_of(ch), account_id=account.id,
                                    account_name=account.display_name, platform=label, message=text, success=False,
                                    error_kind="other", error=str(e))
                self.log(f"{label} {self.name_of(ch)} [{account.display_name}] [РЪЧНО]: Send failed: {e}")

        self.events.put(("channel", ch.id))
        return {"ok": sent > 0 and not failures, "sent": sent, "failed": len(failures), "failures": failures}

    def _handle_send_error(self, ch: Channel, rt: Runtime, e: ApiError) -> None:
        rt.send_fails += 1
        interval = self.next_interval_sec(ch)
        if e.kind == "auth":
            rt.status = "Auth expired"
            msg, delay = AUTH_EXPIRED_MSG, interval
        elif e.kind == "rate_limit":
            delay = min(interval, max(e.retry_after or 0, 60 * 2 ** min(rt.send_fails - 1, 6)))
            rt.status = f"Rate limited ({int(delay)}s)"
            msg = f"Rate limit reached - retrying in {int(delay)}s ({e.message})"
        elif e.kind == "chat_unavailable":
            rt.status = "No live chat"
            rt.force_check = True            # re-verify live state right away
            msg, delay = f"Live chat unavailable: {e.message}", interval
        elif e.kind == "network":
            delay = min(interval, 60.0)
            rt.status = "Network error"
            msg = f"Send failed (will retry in {int(delay)}s): {e.message}"
        else:
            rt.status = f"Send error: {e.message}"[:90]
            msg, delay = f"Send failed: {e.message}", interval
        rt.next_send = time.time() + delay
        self.log(f"{PLATFORM_LABEL[ch.platform]} {self.name_of(ch)}: {msg}")
        self._changed(ch)

    # --------------------------------------------------------------- chat
    def _start_kick_chat(self, ch: Channel) -> None:
        key = self.stream_key(ch)
        if key in self.kick_chat:               # a sibling entry already reads this stream's chat
            return
        try:
            _, ref = parse_channel_url(ch.platform, ch.url)
            slug = ref.value
        except Exception:
            slug = ch.display_name or ch.url
        name = self.stream_name(ch)

        # Refresh the authenticated Kick identities before listening.  The public
        # /users endpoint is the authoritative source for the connected account's
        # numeric user_id.  Older saved configs can contain a stale/empty ID; that
        # would make chat messages visible but impossible to attribute to the account.
        identities_changed = False
        for aid in ch.connected_account_ids():
            account = self.config.get_account(aid)
            if account is None or account.platform != PLATFORM_KICK:
                continue
            try:
                token = self.tm.access_token(account)
                external_id, display_name = kick_api.fetch_identity(token)
                if external_id and str(account.external_id) != str(external_id):
                    account.external_id = str(external_id)
                    identities_changed = True
                if display_name and str(account.display_name) != str(display_name):
                    account.display_name = str(display_name)
                    identities_changed = True
            except Exception as e:
                # Chat itself can still work by username when the identity refresh
                # is unavailable, so do not prevent the Pusher reader from starting.
                self.log(f"Kick account identity refresh skipped for {getattr(account, 'display_name', aid)}: {e}")
        if identities_changed:
            try:
                self.config.save()
            except Exception:
                pass

        def cb(msg: ChatMessage):
            """Called by KickChatReader with ONE normalized ChatMessage (see kick_events)."""
            try:
                rt = self.runtime.get(ch.id)
                if not msg.stream_id and rt is not None:
                    msg.stream_id = rt.session_id
                self.process_chat_message(msg, platform="Kick", channel=name, channel_id=ch.channel_id,
                                          stream_key=key, stream_name=name)
            except Exception as e:      # never let a processing error kill the reader thread
                self.log(f"Kick chat processing error: {type(e).__name__}: {e}")

        def status(msg):
            # The current state is always kept (shown under the points table). Only real changes are
            # written to the Activity log, so a repeating problem does not flood it.
            if self.chat_status.get(key) != msg:
                self.chat_status[key] = msg
                self.events.put(("chat_status", key, msg))
                self.events.put(("profiles",))
            if msg not in ("resolving", "connecting") and self._chat_logged.get(key) != msg:
                self._chat_logged[key] = msg
                self.log(f"Kick чат {name}: {msg}")

        def remember_chatroom(cid):
            ch.extra["chatroom_id"] = str(cid)      # cache: no lookup next time
            if self.config.get_channel(ch.id) is ch:
                self.config.save()

        reader = KickChatReader(
            slug, cb, status,
            chatroom_id=ch.extra.get("chatroom_id"),
            on_chatroom=remember_chatroom,
            broadcaster_user_id=ch.channel_id,
            on_debug=self.debug,
        )
        self.kick_chat[key] = reader
        reader.start()

    def _poll_youtube_chat(self, ch: Channel, rt: Runtime, stop: threading.Event) -> None:
        st=self.config.settings
        if not (st.chat_logging_enabled or st.independent_chat_processing) or not rt.live_chat_id:
            return
        # Several channel entries can point at the same live chat: only one of them polls it,
        # because every poll costs YouTube quota.
        owner=self._yt_chat_owner.setdefault(rt.live_chat_id, ch.id)
        if owner != ch.id:
            owner_rt=self.runtime.get(owner)
            if owner_rt is not None and owner_rt.live:
                rt.chat_next_poll=time.time()+5
                return
            self._yt_chat_owner[rt.live_chat_id]=ch.id
        account=self.config.get_account(rt.active_account_id) if rt.active_account_id else None
        if account is None: return
        key,name=self.stream_key(ch),self.stream_name(ch)
        try:
            data=self.yt.list_chat_messages(account, rt.live_chat_id, rt.chat_page_token)
            items=data.get("items") or []
            for item in items:
                sn=item.get("snippet") or {}
                ad=item.get("authorDetails") or {}
                text=sn.get("displayMessage") or (sn.get("textMessageDetails") or {}).get("messageText") or ""
                if not text: continue
                ts=time.time()
                published=sn.get("publishedAt")
                if published:
                    try:
                        ts=__import__("datetime").datetime.fromisoformat(published.replace("Z","+00:00")).timestamp()
                    except Exception: pass
                # YouTube does not expose Kick-style reply metadata consistently,
                # but some payloads include it. Pass it through when available so a
                # bot reply can still be tied to the account that asked the command.
                reply_to = ""
                reply = sn.get("parentId") or sn.get("replyTo") or ""
                if isinstance(reply, dict):
                    reply_to = (reply.get("displayName") or reply.get("username") or
                                reply.get("channelId") or "")
                elif reply:
                    reply_to = str(reply)
                mid=item.get("id") or f"yt-{ts}-{ad.get('channelId','')}"
                uid=ad.get("channelId") or ""
                self.handle_incoming_chat_message(
                    message_id=mid, ts=ts, platform="YouTube", channel=name, channel_id=ch.channel_id,
                    username=ad.get("displayName") or "unknown", user_id=uid, text=text,
                    stream_id=rt.session_id, source="chat", stream_key=key, reply_to=reply_to)
            rt.chat_page_token=data.get("nextPageToken") or rt.chat_page_token
            poll_ms=int(data.get("pollingIntervalMillis") or max(1000,int(self.config.settings.chat_poll_sec*1000)))
            # Never poll faster than the setting: each list call costs quota units.
            rt.chat_next_poll=time.time()+max(poll_ms/1000, float(max(5,int(st.youtube_chat_poll_sec))))
        except ApiError as e:
            if e.kind in ("chat_unavailable","not_found"):
                rt.chat_next_poll=time.time()+5
            else:
                rt.chat_next_poll=time.time()+max(3,int(self.config.settings.chat_poll_sec))
            self._log_error_once(ch,rt,f"Chat reader: {e.message}")
        except Exception as e:
            rt.chat_next_poll=time.time()+max(3,int(self.config.settings.chat_poll_sec))
            self._log_error_once(ch,rt,f"Chat reader: {type(e).__name__}: {e}")

    # ------------------------------------------------------------------ bets
    def _session_for_stream(self, stream_key: str) -> str:
        for ch in self.config.channels:
            if self.stream_key(ch) == stream_key:
                rt = self.runtime.get(ch.id)
                if rt is not None and rt.live and rt.session_id:
                    return rt.session_id
        return ""

    def _ingest_bet(self, msg: ChatMessage, *, platform, stream_key) -> None:
        text = msg.message or ""
        if "!bet" not in text.casefold():
            return
        demo = bool(self.config.settings.demo_mode)
        self.bet_tracker.sync_session(stream_key, self._session_for_stream(stream_key), demo)
        got = self.bet_tracker.ingest(
            stream_key=stream_key, text=text, username=msg.display_name or msg.username, user_id=msg.user_id,
            message_id=str(msg.message_id), platform=str(platform), ts=float(msg.timestamp or time.time()),
            is_bot=bool(msg.is_bot), is_self=bool(msg.is_self), demo=demo)
        if got:
            self.debug(f"BET recorded letter={got[0]} points={got[1]} user={msg.username}")
            self.events.put(("bets", stream_key))

    def bet_stats(self, stream_key: str) -> dict:
        return self.bet_tracker.stats(stream_key, demo=bool(self.config.settings.demo_mode))

    def bet_recent(self, stream_key: str, limit: int = 30) -> list:
        return self.bet_tracker.recent(stream_key, demo=bool(self.config.settings.demo_mode), limit=limit)

    def new_bet_round(self, stream_key: str) -> int:
        rid = self.bet_tracker.new_round(stream_key, demo=bool(self.config.settings.demo_mode),
                                         session_id=self._session_for_stream(stream_key))
        self.log(f"[BET] Нов рунд #{rid} за {stream_key}")
        self.events.put(("bets", stream_key))
        return rid

    def get_profile_bet(self, account_id: str) -> dict:
        v = self._profile_bets.load().get(str(account_id))
        return v if isinstance(v, dict) else {}

    def save_profile_bet(self, account_id: str, letter: str, points: str) -> tuple[str, int]:
        """Remember the default bet (letter + points) of a profile. Raises ValueError on bad input."""
        l, p = validate_bet_input(letter, points)
        data = self._profile_bets.load()
        data[str(account_id)] = {"letter": l, "points": p}
        self._profile_bets.save(data)
        return l, p

    # ------------------------------------------------------- saved "!" commands
    def saved_commands(self) -> list:
        return list(self._cmd_store.load())

    def remember_command(self, text: str) -> list:
        """Typing a message that starts with ! adds that command to the saved list automatically."""
        cur = self._cmd_store.load()
        new = merge_commands(cur, text)
        if new != cur:
            self._cmd_store.save(new)
        return new

    def place_bet(self, channel_id: str, account_id: str, letter: str, points: str) -> dict:
        """!bet <letter> <points> from ONE profile. LIVE: really sent to chat. DEMO: simulated only."""
        try:
            l, p = self.save_profile_bet(account_id, letter, points)
        except ValueError as e:
            return {"ok": False, "error": str(e)}
        text = format_bet(l, p)
        ch = self.config.get_channel(channel_id)
        if ch is None:
            return {"ok": False, "error": "Избери стрийм."}
        if self.config.settings.demo_mode:
            a = self.config.get_account(account_id)
            key = self.stream_key(ch)
            self.bet_tracker.sync_session(key, self._session_for_stream(key), True)
            self.bet_tracker.ingest(stream_key=key, text=text, username=a.display_name if a else "me",
                                    user_id=str(account_id), message_id=f"demo-mine:{account_id}:{time.time():.6f}",
                                    platform=ch.platform, demo=True)
            self.events.put(("bets", key))
            self.log(f"[BET][ДЕМО] {a.display_name if a else account_id}: {text} (само симулация)")
            return {"ok": True, "sent": 1, "demo": True, "text": text}
        r = self.manual_send(channel_id, [account_id], text)
        r["text"] = text
        return r

    def _simulate_demo_bets(self, ch: Channel, session_id: str) -> None:
        """Demo mode only: a few fake viewers bet so the statistics panel can be tried out."""
        key = self.stream_key(ch)
        self.bet_tracker.sync_session(key, session_id, True)
        letters = "ABCD"
        weights = [5, 3, 2, 1]
        for _ in range(random.randint(3, 8)):
            n = random.randint(1, 40)
            self.bet_tracker.ingest(
                stream_key=key, text=f"!bet {random.choices(letters, weights)[0]} {random.choice([100, 250, 500, 1000, 2000])}",
                username=f"demo_viewer_{n}", user_id=f"demo-{n}", message_id=f"demo:{time.time():.6f}:{random.random():.6f}",
                platform=ch.platform, demo=True)
        self.events.put(("bets", key))

    def switch_mode(self, demo: bool) -> None:
        """DEMO <-> LIVE. Always starts clean: demo bets, simulated live flags and runtime state are reset."""
        was_running = self.running
        if was_running:
            self.stop()
        with self._lock:
            self.config.settings.demo_mode = bool(demo)
            self.sim_live.clear()
            self._sim_counter.clear()
            self.runtime.clear()
            self.live_chat_messages.clear()
            self.bet_tracker.clear_demo()
        try:
            self.config.save()
        except Exception:
            pass
        self.log("Режим: " + ("ДЕМО (чисто начало)" if demo else "LIVE (чисто начало)"))
        self.events.put(("state",))
        self.events.put(("bets", ""))
        if was_running:
            self.start()

    def close(self):
        try:
            self.bet_tracker.close()
        except Exception:
            pass
        try:
            self.ops_store.close()
        except Exception:
            pass

    # ---------------------------------------------------------------- quota
    def youtube_quota_estimate(self) -> int:
        """Rough daily quota units when all enabled YouTube channels are offline (1 unit/check)."""
        n = sum(1 for c in self.config.channels if c.platform == PLATFORM_YT and c.enabled)
        per_day = 86400 / max(15, self.config.settings.live_check_youtube_sec)
        return int(n * per_day)
