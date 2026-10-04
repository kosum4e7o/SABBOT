"""Data models and URL parsing."""
from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import parse_qs, unquote, urlparse

PLATFORM_KICK = "kick"
PLATFORM_YT = "youtube"
PLATFORMS = (PLATFORM_YT, PLATFORM_KICK)
PLATFORM_LABEL = {PLATFORM_KICK: "Kick", PLATFORM_YT: "YouTube"}

MIN_INTERVAL_SEC = 60
MAX_MESSAGE_LEN = {PLATFORM_YT: 200, PLATFORM_KICK: 500}


def new_id() -> str:
    return uuid.uuid4().hex


@dataclass
class Account:
    id: str
    platform: str
    display_name: str
    external_id: str = ""  # stable platform user/channel id
    aliases: list = field(default_factory=list)
    username: str = ""
    avatar: str = ""
    permissions: list = field(default_factory=list)
    metadata: dict = field(default_factory=dict)
    automatic_activity_enabled: bool = True

    def short_id(self) -> str:
        return self.id[:8]

    def label(self) -> str:
        return f"{self.display_name} [{self.short_id()}]"


@dataclass
class Channel:
    id: str
    platform: str
    url: str
    account_id: str = ""  # legacy/primary Account.id
    display_name: str = ""
    channel_id: str = ""  # resolved: YouTube UC... id / Kick broadcaster_user_id
    messages: list = field(default_factory=list)
    interval_minutes: float = 29
    jitter_minutes: float = 0
    enabled: bool = True
    extra: dict = field(default_factory=dict)  # e.g. {"uploads": "UU..."}
    account_ids: list = field(default_factory=list)  # all accounts connected to this channel

    def connected_account_ids(self) -> list[str]:
        """Return unique connected account IDs, keeping the legacy primary first."""
        ids = list(self.account_ids or [])
        if self.account_id and self.account_id not in ids:
            ids.insert(0, self.account_id)
        elif self.account_id and ids:
            ids = [self.account_id] + [aid for aid in ids if aid != self.account_id]
        return list(dict.fromkeys(ids))

    def copy(self) -> "Channel":
        return Channel(self.id, self.platform, self.url, self.account_id,
                       self.display_name, self.channel_id, list(self.messages),
                       self.interval_minutes, self.jitter_minutes, self.enabled,
                       dict(self.extra), list(self.account_ids))


@dataclass
class Settings:
    demo_mode: bool = False
    kick_client_id: str = ""
    kick_redirect_port: int = 8765
    google_client_json: str = ""
    live_check_kick_sec: int = 45
    live_check_youtube_sec: int = 120
    first_message_delay_sec: int = 10
    youtube_search_fallback_minutes: int = 0  # 0 = off (search.list costs 100 quota units)
    oauth_timeout_sec: int = 240
    chat_logging_enabled: bool = True
    chat_poll_sec: int = 2
    watched_names: list = field(default_factory=list)
    command_threshold: int = 3
    response_bot_names: list = field(default_factory=lambda: ["BOTTLY"])  # who answers !points / !time
    point_response_window_sec: int = 12
    account_send_gap_sec: float = 1.25
    # Automatic messages of different accounts are kept at least this far apart (random between min and max).
    account_stagger_min_minutes: float = 2.0
    account_stagger_max_minutes: float = 3.0
    duplicate_send_window_sec: int = 20
    independent_chat_processing: bool = True
    youtube_chat_poll_sec: int = 15  # liveChatMessages.list costs quota; keep this generous


@dataclass
class LiveInfo:
    session_id: str
    chat_id: str
    title: str = ""
    started_at: float = 0.0
    channel_id: str = ""
    streamer: str = ""
    category: str = ""
    game: str = ""
    viewers: Optional[int] = None
    thumbnail: str = ""
    url: str = ""


@dataclass
class Runtime:
    """Non-persistent per-channel state shared by the engine and UI."""
    live: bool = False
    session_id: str = ""
    next_send: Optional[float] = None  # legacy/channel compatibility; account timers are authoritative
    last_message: str = ""
    status: str = "Stopped"
    connection_status: str = "Offline"
    live_chat_id: str = ""
    title: str = ""
    started_at: float = 0.0
    streamer: str = ""
    category: str = ""
    game: str = ""
    viewers: Optional[int] = None
    thumbnail: str = ""
    stream_url: str = ""
    channel_id: str = ""
    simulated: bool = False
    last_error: str = ""
    send_fails: int = 0
    force_check: bool = False
    known_nonlive: set = field(default_factory=set)
    last_search: float = 0.0
    chat_page_token: str = ""
    chat_next_poll: float = 0.0
    chat_seen_ids: set = field(default_factory=set)
    pending_commands: dict = field(default_factory=dict)
    active_account_id: str = ""
    account_states: dict = field(default_factory=dict)
    account_last_send: dict = field(default_factory=dict)


@dataclass
class ChannelRef:
    kind: str   # handle | channel_id | user | custom | video | slug
    value: str


_KICK_RESERVED = {"", "video", "videos", "categories", "category", "browse", "search",
                  "dashboard", "following", "clips", "terms-of-service", "privacy-policy"}


def detect_platform(url: str) -> Optional[str]:
    u = url.strip()
    if re.match(r"^(@[\w.\-]+|UC[\w\-]{20,})$", u):
        return PLATFORM_YT
    host = urlparse(u if "//" in u else "https://" + u).netloc.lower()
    host = host.split(":")[0]
    if host.startswith("www."):
        host = host[4:]
    if host in ("youtube.com", "m.youtube.com", "youtu.be", "music.youtube.com"):
        return PLATFORM_YT
    if host in ("kick.com", "m.kick.com"):
        return PLATFORM_KICK
    return None


def normalize_kick_slug(slug: str) -> str:
    return slug.strip().lower().replace("_", "-")


def parse_channel_url(platform_hint: Optional[str], url: str) -> tuple[str, ChannelRef]:
    """Return (platform, ChannelRef). Raises ValueError with a readable message."""
    raw = (url or "").strip()
    if not raw:
        raise ValueError("Channel URL is empty.")
    detected = detect_platform(raw)
    if platform_hint and detected and detected != platform_hint:
        raise ValueError(f"This URL looks like a {PLATFORM_LABEL[detected]} URL, "
                         f"but the platform is set to {PLATFORM_LABEL[platform_hint]}.")
    platform = platform_hint or detected
    if platform is None:
        raise ValueError("Unsupported URL. Use a youtube.com or kick.com channel URL.")

    if platform == PLATFORM_YT:
        if re.match(r"^@[\w.\-]+$", raw):
            return platform, ChannelRef("handle", raw)
        if re.match(r"^UC[\w\-]{20,}$", raw):
            return platform, ChannelRef("channel_id", raw)
        p = urlparse(raw if "//" in raw else "https://" + raw)
        host = p.netloc.lower().replace("www.", "")
        segs = [unquote(s) for s in p.path.split("/") if s]
        if host == "youtu.be" and segs:
            return platform, ChannelRef("video", segs[0])
        if segs:
            if segs[0].startswith("@"):
                return platform, ChannelRef("handle", segs[0])
            if segs[0] == "channel" and len(segs) > 1:
                return platform, ChannelRef("channel_id", segs[1])
            if segs[0] == "user" and len(segs) > 1:
                return platform, ChannelRef("user", segs[1])
            if segs[0] == "c" and len(segs) > 1:
                return platform, ChannelRef("custom", segs[1])
            if segs[0] in ("live", "shorts", "embed") and len(segs) > 1:
                return platform, ChannelRef("video", segs[1])
            if segs[0] == "watch":
                v = parse_qs(p.query).get("v", [""])[0]
                if v:
                    return platform, ChannelRef("video", v)
        raise ValueError("Unrecognized YouTube URL. Use https://www.youtube.com/@handle "
                         "or https://www.youtube.com/channel/UC...")

    # Kick
    if "/" not in raw and "." not in raw:
        slug = raw
    else:
        p = urlparse(raw if "//" in raw else "https://" + raw)
        segs = [unquote(s) for s in p.path.split("/") if s]
        slug = segs[0] if segs else ""
    slug = normalize_kick_slug(slug)
    if slug in _KICK_RESERVED or not re.match(r"^[a-z0-9][a-z0-9\-]{0,63}$", slug):
        raise ValueError("Unrecognized Kick URL. Use https://kick.com/channelname")
    return platform, ChannelRef("slug", slug)


def default_display_name(ref: ChannelRef) -> str:
    return ref.value


def validate_messages(platform: str, messages: list) -> list:
    msgs = [m.strip() for m in messages if m and m.strip()]
    if not msgs:
        raise ValueError("Add at least one message.")
    limit = MAX_MESSAGE_LEN[platform]
    for m in msgs:
        if len(m) > limit:
            raise ValueError(f"A {PLATFORM_LABEL[platform]} message can be at most {limit} characters.")
    return msgs
