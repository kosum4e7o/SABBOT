"""Chat ingestion and special command processing.

Besides archiving chat, mention watching and command counting, this module reads the replies of
the stream's chat bot (BOTTLY) to !points and !time, finds which of YOUR accounts the reply is for
and stores the numbers in that account's per-stream profile.

Data flow (see engine.Engine.process_chat_message for the part before this module):

    your message "!points"  -> PendingCommand(account, "!points", ...)
    BOTTLY reply            -> matched to the pending command / the named account
                            -> parse_points_response / parse_watch_time_response
                            -> profile saved -> UI refresh
                            -> on_bot_response callback (records Recent Activity, NEVER resets the timer)
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Callable, Optional

import profile_parser as pp
from kick_events import is_bot_name

DEFAULT_RESPONSE_BOTS = ("BOTTLY",)
COMMANDS = ("!points", "!time")

# A bot answer to a *bet* ("!bet c 200" -> "... нямаш достатъчно точки (200) ...") mentions points too,
# but it is NOT the viewer's balance.  Such lines are never stored as points.
_BET_REPLY_RE = re.compile(
    r"(?<!\w)(?:bet|bets|betting|залог\w*|заложи\w*|залагане)(?!\w)"
    r"|недостатъчн\w*|нямаш\s+достатъчно|не\s+достигат\w*|достатъчно\s+точки"
    r"|not\s+enough|insufficient|can'?t\s+afford|cannot\s+afford",
    re.IGNORECASE | re.UNICODE)


@dataclass
class PendingCommand:
    account: object
    command: str
    created_at: float
    username: str
    message_id: str = ""
    stream_key: str = ""


def _fmt_seconds(sec) -> str:
    sec = int(max(0, sec or 0))
    d, rem = divmod(sec, 86400)
    h, rem = divmod(rem, 3600)
    m = rem // 60
    return f"{d}d {h}h {m}m" if d else (f"{h}h {m}m" if h else f"{m}m")


class ChatProcessor:
    def __init__(self, store, config, logger=None, on_profile=None, on_bot_response=None,
                 debug=None, clock: Callable[[], float] = time.time):
        self.store = store
        self.config = config
        self.logger = logger
        self.debug = debug                  # debug trail (file log only)
        self.on_profile = on_profile        # called (no args) after a profile changed -> UI refresh
        # on_bot_response(account, kind, value, text, message_id, ts): Recent Activity hook.
        # The engine implements it WITHOUT touching the countdown timer.
        self.on_bot_response = on_bot_response
        self._now = clock
        self._unmatched_seen = set()
        # (platform, stream_key, command) -> PendingCommand
        self._pending_commands: dict[tuple, PendingCommand] = {}
        self._pending_ttl_sec = 120.0
        # Realtime de-duplication that never depends on the persistent chat-history table.
        self._realtime_seen = set()

    def _log(self, msg):
        if self.logger:
            self.logger(msg)

    def _dbg(self, msg):
        if self.debug:
            self.debug(msg)
        elif self.logger:
            self.logger(msg)

    # ----------------------------------------------------------------- helpers
    def response_bots(self) -> list[str]:
        """Nicknames whose replies are trusted. Empty setting -> the default bot (BOTTLY)."""
        names = [str(x).strip() for x in (self.config.settings.response_bot_names or []) if str(x).strip()]
        return names or list(DEFAULT_RESPONSE_BOTS)

    def _my_accounts(self, platform):
        plat = str(platform).casefold()
        return [a for a in list(self.config.accounts) if a.platform.casefold() == plat]

    # ------------------------------------------------------------------ intake
    def process(self, *, message_id, ts, platform, channel, channel_id,
                username, user_id, text, stream_key=None, stream_name=None, reply_to="",
                account_hint_id=None, is_self=None, is_bot=None, **_ignored):
        """Process one incoming chat line.

        ``is_self`` / ``account_hint_id`` come from the engine's account matching; when they are
        not supplied (old callers) the account is derived from user_id / nickname here.
        Extra keyword arguments are ignored on purpose: a new reader field must never again
        break the whole pipeline.
        """
        ts = float(ts or time.time())
        username = username or "unknown"
        text = text or ""
        stream_key = stream_key or str(channel_id)
        stream_name = stream_name or channel
        realtime_key = (str(stream_key), str(message_id))
        if realtime_key in self._realtime_seen:
            return
        self._realtime_seen.add(realtime_key)
        if len(self._realtime_seen) > 10000:
            self._realtime_seen = set(list(self._realtime_seen)[-5000:])

        if self.config.settings.chat_logging_enabled:
            # Persistent storage is best-effort and never decides whether commands/replies run.
            try:
                self.store.add_message(message_id=message_id, ts=ts, platform=platform,
                                       channel=channel, channel_id=channel_id,
                                       username=username, user_id=user_id, text=text)
            except Exception as e:
                self._log(f"Chat history save error: {e}")

        low = text.casefold()
        for name in (self.config.settings.watched_names or []):
            n = str(name).strip()
            if n and n.casefold() in low:
                self.store.mention(ts=ts, platform=platform, channel=channel, username=username,
                                   user_id=user_id, matched_name=n, text=text, message_id=message_id)

        own = self._own_account(platform, account_hint_id, user_id, username) if is_self is not False else None
        if is_self is None:
            is_self = own is not None
        if is_bot is None:
            is_bot = is_bot_name([username], self.response_bots())

        commands = re.findall(r'(?<!\w)(![\w][\w-]*)', text, flags=re.UNICODE)
        for raw_cmd in commands:
            cmd = raw_cmd.lower()
            recorded, count = self.store.command_seen(
                channel_id, username, cmd, max(1, int(self.config.settings.command_threshold)), ts)
            if recorded:
                self._log(f"Frequent command recorded: {username} -> {cmd} ({count} uses)")
            if cmd in COMMANDS and own is not None and not is_bot:
                self.register_pending(own, cmd, platform, stream_key, username, str(message_id))

        # Your own messages are never bot replies, even if you typed "@you има 5 точки".
        if own is not None or is_self:
            return
        self._read_bot_reply(platform, stream_key, stream_name, username, text, ts, reply_to,
                             message_id=str(message_id), is_bot=bool(is_bot))

    def _own_account(self, platform, account_hint_id, user_id, username):
        accounts = self._my_accounts(platform)
        if account_hint_id:
            hit = next((a for a in accounts if str(a.id) == str(account_hint_id)), None)
            if hit is not None:
                return hit
        uid = str(user_id or "").strip().lstrip("+")
        if uid:
            hit = next((a for a in accounts
                        if str(a.external_id or "").strip().lstrip("+") == uid), None)
            if hit is not None:
                return hit
        for a in accounts:
            ext = str(a.external_id or "").strip().lstrip("+")
            if uid and ext and ext != uid:
                continue            # a known, different platform user id: not you
            names = [a.display_name, getattr(a, "username", "")] + list(getattr(a, "aliases", None) or [])
            if any(pp.norm_name(username) == pp.norm_name(n) for n in names if n):
                return a
        return None

    # ------------------------------------------------------ pending commands
    def register_pending(self, account, cmd, platform, stream_key, username, message_id=""):
        pending = PendingCommand(account=account, command=cmd, created_at=self._now(),
                                 username=username, message_id=message_id, stream_key=str(stream_key))
        self._pending_commands[(str(platform).casefold(), str(stream_key), cmd, str(account.id))] = pending
        self._dbg(f"PENDING COMMAND CREATED account={account.display_name} command={cmd} "
                  f"username={username} message_id={message_id}")
        self._log(f"[COMMAND] {account.display_name}: {cmd} recorded; waiting for BOTTLY reply")
        return pending

    def pending_for(self, platform, cmd, account=None, stream_key=None) -> Optional[PendingCommand]:
        """Newest fresh pending command (optionally restricted to one account)."""
        plat = str(platform).casefold()
        now = self._now()
        best = None
        for key, p in list(self._pending_commands.items()):
            if now - p.created_at > self._pending_ttl_sec:
                self._pending_commands.pop(key, None)          # expired
                continue
            if key[0] != plat or p.command != cmd:
                continue
            if account is not None and str(p.account.id) != str(account.id):
                continue
            # Prefer the same stream; fall back to any stream of the platform (history/reconnect
            # paths can describe one stream with a slightly different key).
            rank = (1 if stream_key is not None and p.stream_key == str(stream_key) else 0, p.created_at)
            if best is None or rank > best[0]:
                best = (rank, p)
        return best[1] if best else None

    def _consume(self, platform, cmd, account):
        plat = str(platform).casefold()
        for key in list(self._pending_commands):
            if key[0] == plat and key[2] == cmd and key[3] == str(account.id):
                self._pending_commands.pop(key, None)

    def _history(self, account, stream_name, platform, kind, text, ts):
        try:
            self.store.add_value_history(account.id, account.display_name, stream_name, platform, kind, text, ts)
        except Exception as e:
            self._log(f"Value history save error: {e}")

    # ------------------------------------------------------- !points / !time
    def _read_bot_reply(self, platform, stream_key, stream_name, sender, text, ts,
                        reply_to="", message_id="", is_bot=None):
        parsed = pp.classify_reply(text)
        if parsed is None:
            return
        kind, value = parsed
        cmd = "!points" if kind == "points" else "!time"
        bots = self.response_bots()
        sender_is_bot = bool(is_bot) if is_bot is not None else is_bot_name([sender], bots)
        accounts = self._my_accounts(platform)

        # Whom is the reply for?  (reply metadata / @mention / bare nickname)
        explicit = pp.match_account(text, accounts, reply_to=reply_to, sender=sender)
        target = pp.extract_target_username(text, skip=tuple(bots) + (sender,))
        if reply_to and not explicit:
            target = target or reply_to

        pending = self.pending_for(platform, cmd, account=explicit, stream_key=stream_key) if explicit \
            else None
        account = explicit
        if account is None:
            if target:
                # The reply names somebody who is not one of our accounts: it answers another
                # viewer's command.  Never store someone else's points on our account.
                self._dbg(f"BOTTLY RESPONSE ignored (addressed to {target!r}, not one of our accounts)")
                return
            pending = self.pending_for(platform, cmd, stream_key=stream_key)
            if pending is not None:
                account = pending.account

        # Accept only: the configured bot (naming/pending one of our accounts), or - when the
        # sender name is not recognised as the bot - a reply that follows OUR pending command.
        if account is None:
            key = (str(stream_key), text.strip())
            if sender_is_bot and key not in self._unmatched_seen:
                self._unmatched_seen.add(key)
                if len(self._unmatched_seen) > 2000:
                    self._unmatched_seen = set(list(self._unmatched_seen)[-1000:])
                self._log(f"Профилът не е разпознат в отговора: {text[:180]}")
            return
        if not sender_is_bot and pending is None:
            return

        if kind == "points":
            # Points are read ONLY from the bot's answer to our own !points command: there must be a
            # fresh pending !points for exactly this account, and the line must not be a bet answer.
            own_pending = pending if (pending is not None and str(pending.account.id) == str(account.id)) \
                else self.pending_for(platform, cmd, account=account, stream_key=stream_key)
            if own_pending is None:
                self._dbg(f"POINTS ignored (no pending !points for {account.display_name}): {text[:160]!r}")
                return
            if _BET_REPLY_RE.search(text):
                self._dbg(f"POINTS ignored (looks like an answer to a bet, not to !points): {text[:160]!r}")
                return

        self._dbg(f"BOTTLY RESPONSE sender={sender} text={text[:200]!r}")
        self._consume(platform, cmd, account)
        if kind == "points":
            self._dbg(f"POINTS PARSED username={account.display_name} points={value}")
            self.store.save_profile_points(stream_key, stream_name, platform, account.id,
                                           account.display_name, value, ts)
            self.store.save_value(stream_key, account.display_name, "points", value, ts, text)
            self._log(f"Точки: {account.display_name} @ {stream_name} = {value:,}".replace(",", " "))
            self._history(account, stream_name, platform, "points", str(value), ts)
        else:
            if value.get("seconds") is not None:
                self._dbg(f"WATCH TIME PARSED username={account.display_name} watch_seconds={value['seconds']}")
            self.store.save_profile_time(stream_key, stream_name, platform, account.id,
                                         account.display_name, value["level"],
                                         value["text"] or None, value["seconds"], ts)
            self.store.save_value(stream_key, account.display_name, "time",
                                  value["text"] or f"ниво {value['level']}", ts, text)
            shown = value["text"] or (_fmt_seconds(value["seconds"]) if value.get("seconds") else "")
            if shown:       # a level-only reply is not a watch time: nothing to add to the history
                self._history(account, stream_name, platform, "time", shown, ts)
            lvl = "" if value["level"] is None else f"ниво {value['level']}, "
            self._log(f"Време: {account.display_name} @ {stream_name} = {lvl}{value['text'] or '—'}")
        if self.on_bot_response:
            try:
                self.on_bot_response(account, kind, value, text, message_id, ts)
            except Exception as e:
                self._log(f"Bot response hook error: {e}")
        if self.on_profile:
            try:
                self.on_profile()
            except Exception:
                pass
