"""Central normalization of Kick chat events and account matching.

Kick delivers chat messages in several shapes.  Everything downstream of this module
works with ONE internal model, :class:`ChatMessage`:

    {user_id, username, display_name, message, event_type, is_self, stream_id, timestamp}

Supported inputs (all handled by :func:`normalize_chat_message`):

* the raw Pusher envelope ``{"event": "App\\Events\\ChatMessageEvent", "data": "<json string>",
  "channel": "chatrooms.N.v2"}`` (``data`` is double-encoded JSON);
* the already decoded ``data`` dict - flat ``{id, content, sender: {...}}``;
* the older nested ``{message: {...}, user: {...}}`` shape;
* webhook-like ``{"data": {...}}`` wrappers and ``sender`` / ``user`` / ``author`` objects.

No I/O and no Qt in this module, so it is fully unit-testable.
"""
from __future__ import annotations

import datetime
import hashlib
import json
import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Iterable, Optional

CHAT_EVENT_NAMES = (
    "App\\Events\\ChatMessageEvent",
    "App\\Events\\ChatMessageSentEvent",
    "chat.message.sent",
)


def _fold_event(name: str) -> str:
    return str(name or "").replace("\\\\", "\\").casefold()


_CHAT_EVENTS_FOLDED = {_fold_event(n) for n in CHAT_EVENT_NAMES}


def is_chat_event(name: str) -> bool:
    """True for Kick chat message events (tolerates single/double backslashes)."""
    folded = _fold_event(name)
    if folded in _CHAT_EVENTS_FOLDED:
        return True
    return folded.endswith("chatmessageevent") or folded.endswith("chatmessagesentevent")


def norm_name(name) -> str:
    """Compare-friendly nickname: case-insensitive, ignores '@', '_', '-', '.', spaces."""
    return re.sub(r"[\W_]+", "", str(name or "").casefold(), flags=re.UNICODE)


@dataclass
class ChatMessage:
    user_id: str = ""
    username: str = ""
    display_name: str = ""
    message: str = ""
    event_type: str = ""
    is_self: bool = False
    stream_id: str = ""
    timestamp: float = 0.0
    # Extra fields used by the rest of the pipeline.
    message_id: str = ""
    slug: str = ""
    reply_to: str = ""
    chatroom_id: str = ""
    is_bot: bool = False

    def names(self) -> list[str]:
        return [n for n in (self.username, self.slug, self.display_name) if n]

    def as_dict(self) -> dict:
        return {
            "user_id": self.user_id, "username": self.username, "display_name": self.display_name,
            "message": self.message, "event_type": self.event_type, "is_self": self.is_self,
            "stream_id": self.stream_id, "timestamp": self.timestamp,
        }


# ------------------------------------------------------------------ helpers
def _first_text(*values) -> str:
    for value in values:
        if value is None or isinstance(value, (dict, list)):
            continue
        text = str(value).strip()
        if text:
            return text
    return ""


def _as_dict(value) -> dict:
    return value if isinstance(value, dict) else {}


def parse_timestamp(created) -> float:
    """Kick has used Unix seconds/milliseconds and ISO-8601.  Falls back to 'now'."""
    if created is not None and created != "":
        try:
            if isinstance(created, (int, float)):
                value = float(created)
            else:
                text = str(created).strip()
                if re.fullmatch(r"\d+(\.\d+)?", text):
                    value = float(text)
                else:
                    return datetime.datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
            return value / 1000.0 if value > 1e11 else value
        except Exception:
            pass
    return time.time()


def _decode(value, depth: int = 3):
    """Decode (possibly double-encoded) JSON strings."""
    while depth > 0 and isinstance(value, (str, bytes, bytearray)):
        try:
            value = json.loads(value)
        except Exception:
            return value
        depth -= 1
    return value


def _reply_target(msg: dict, payload: dict) -> str:
    meta = _as_dict(msg.get("metadata")) or _as_dict(payload.get("metadata"))
    original = _as_dict(meta.get("original_sender"))
    name = _first_text(original.get("username"), original.get("slug"), original.get("name"))
    if name:
        return name
    # Kick's schema calls this `replies_to` (plural); older/private payloads used the others.
    replied = None
    for src in (msg, payload):
        for key in ("replies_to", "replied_to", "reply_to"):
            if src.get(key):
                replied = src.get(key)
                break
        if replied:
            break
    if isinstance(replied, dict):
        sender = _as_dict(replied.get("sender"))
        user = _as_dict(replied.get("user"))
        return _first_text(replied.get("username"), replied.get("name"), sender.get("username"),
                           sender.get("name"), user.get("username"), user.get("name"))
    return ""


# ------------------------------------------------------------- normalization
def normalize_chat_message(payload, *, event_type: str = "", chatroom_id="",
                           default_stream_id: str = "") -> Optional[ChatMessage]:
    """Turn any supported Kick chat payload into a :class:`ChatMessage`.

    Returns ``None`` when the payload carries no message text.  ``is_self`` is NOT decided
    here - use :func:`match_account` (it needs the configured accounts).
    """
    payload = _decode(payload)
    if not isinstance(payload, dict):
        return None
    channel = ""
    # Pusher envelope: {"event": ..., "data": "<json>", "channel": "chatrooms.N.v2"}
    if "event" in payload and "data" in payload and "content" not in payload and "sender" not in payload:
        event_type = event_type or str(payload.get("event") or "")
        channel = str(payload.get("channel") or "")
        payload = _decode(payload.get("data"))
        if not isinstance(payload, dict):
            return None
    # Webhook-like wrapper: {"data": {...sender/content...}}
    inner = _decode(payload.get("data"))
    if isinstance(inner, dict) and not any(k in payload for k in ("content", "message", "sender", "user")):
        payload = inner

    raw_message = payload.get("message")
    msg = raw_message if isinstance(raw_message, dict) else payload

    user = {}
    for candidate in (payload.get("sender"), payload.get("user"), payload.get("author"),
                      msg.get("sender"), msg.get("user"), msg.get("author")):
        if isinstance(candidate, dict) and candidate:
            user = candidate
            break

    text = _first_text(
        msg.get("content"), msg.get("message"), msg.get("text"),
        payload.get("content"), payload.get("text"),
        raw_message if isinstance(raw_message, str) else None,
    )
    if not text:
        return None

    username = _first_text(
        user.get("username"), user.get("name"), msg.get("sender_username"), msg.get("username"),
        payload.get("username"), user.get("slug"), user.get("channel_slug"), msg.get("slug"),
        payload.get("slug"),
    )
    slug = _first_text(user.get("slug"), user.get("channel_slug"), msg.get("slug"), payload.get("slug"))
    display_name = _first_text(
        user.get("display_name"), user.get("displayname"), user.get("displayName"),
        msg.get("display_name"), msg.get("displayname"), payload.get("display_name"),
        payload.get("displayname"), username,
    )
    # NOTE: channel_id is deliberately NOT used as user_id - it is a different identifier.
    user_id = _first_text(
        user.get("user_id"), user.get("id"), msg.get("user_id"), msg.get("sender_id"),
        payload.get("user_id"), payload.get("sender_id"),
    )
    created = _first_text(msg.get("created_at"), msg.get("createdAt"), msg.get("timestamp"),
                          payload.get("created_at"), payload.get("createdAt"), payload.get("timestamp"))
    room = _first_text(chatroom_id, msg.get("chatroom_id"), payload.get("chatroom_id"),
                       channel.split(".")[1] if channel.startswith("chatrooms.") and "." in channel else "")
    message_id = _first_text(msg.get("id"), msg.get("message_id"), payload.get("id"), payload.get("message_id"))
    if not message_id:
        if created:
            seed = f"{room}|{user_id or username}|{text}|{created}"
            message_id = "h" + hashlib.sha1(seed.encode("utf-8", "replace")).hexdigest()[:24]
        else:
            message_id = f"{room or 'kick'}-{uuid.uuid4().hex}"
    stream_id = _first_text(payload.get("stream_id"), msg.get("stream_id"), payload.get("livestream_id"),
                            default_stream_id)
    event = _first_text(event_type, payload.get("event_type"), payload.get("event"), payload.get("type"))

    return ChatMessage(
        user_id=user_id, username=username or "unknown", display_name=display_name or username or "unknown",
        message=text, event_type=event, is_self=False, stream_id=stream_id,
        timestamp=parse_timestamp(created), message_id=message_id, slug=slug,
        reply_to=_reply_target(msg, payload), chatroom_id=str(room or ""),
    )


# ------------------------------------------------------------ account match
def account_slug(account) -> str:
    meta = getattr(account, "metadata", None)
    if isinstance(meta, dict):
        return _first_text(meta.get("slug"), meta.get("channel_slug"))
    return ""


def match_account(msg: ChatMessage, accounts: Iterable) -> tuple[Optional[object], str]:
    """Return ``(account, reason)``; reason is user_id / username / slug / alias / none.

    A known platform user_id is authoritative: an account whose external_id is set and
    differs from the message's user_id is never matched by name (avoids look-alike nicknames).
    """
    accounts = list(accounts)
    uid = str(msg.user_id or "").strip().lstrip("+")
    if uid:
        for a in accounts:
            if str(getattr(a, "external_id", "") or "").strip().lstrip("+") == uid:
                return a, "user_id"

    def allowed(a) -> bool:
        ext = str(getattr(a, "external_id", "") or "").strip().lstrip("+")
        return not (uid and ext and ext != uid)

    candidates = [a for a in accounts if allowed(a)]
    uname, slug, disp = norm_name(msg.username), norm_name(msg.slug), norm_name(msg.display_name)

    def primary(a):
        return {norm_name(getattr(a, "username", "")), norm_name(getattr(a, "display_name", ""))} - {""}

    for a in candidates:
        if uname and uname in primary(a):
            return a, "username"
    for a in candidates:
        if slug and (slug in primary(a) or slug == norm_name(account_slug(a))):
            return a, "slug"
    for a in candidates:
        if uname and uname == norm_name(account_slug(a)):
            return a, "slug"
    for a in candidates:
        if disp and disp in primary(a):
            return a, "username"
    for a in candidates:
        aliases = {norm_name(x) for x in (getattr(a, "aliases", None) or [])} - {""}
        if aliases and (uname in aliases or slug in aliases or disp in aliases):
            return a, "alias"
    return None, "none"


def is_bot_name(names: Iterable[str], bot_names: Iterable[str]) -> bool:
    """True if any of *names* is one of the configured response bots (BOTTLY, ...)."""
    bots = [norm_name(b) for b in bot_names if norm_name(b)]
    for n in names:
        key = norm_name(n)
        if not key:
            continue
        for b in bots:
            if key == b or (len(b) >= 4 and b in key):
                return True
    return False
