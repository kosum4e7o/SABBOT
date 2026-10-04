"""Where to point the in-app player for a stream (no Qt, unit-tested in tests/test_stream_embed.py).

Kick    -> https://player.kick.com/<slug>               (official iframe player)
YouTube -> https://www.youtube.com/embed/<videoId>      (a live video)
           https://www.youtube.com/embed/live_stream?channel=UC...   (channel's current live)
If a stream cannot be embedded (e.g. only a @handle is known), `mode` is "external".
"""
from __future__ import annotations

import re
from html import escape
from typing import Optional

from models import PLATFORM_KICK, PLATFORM_YT, parse_channel_url

_VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
_UC_ID = re.compile(r"^UC[\w-]{20,}$")
EMBED_BASE_URL = "https://stream-activity-bot.local/"      # gives YouTube a Referer (avoids embed error 153)


def youtube_video_id(text: str) -> str:
    """Video id from a watch / youtu.be / live / embed URL or a bare 11-char id; '' if none."""
    t = (text or "").strip()
    if _VIDEO_ID.match(t):
        return t
    try:
        platform, ref = parse_channel_url(PLATFORM_YT, t)
    except ValueError:
        return ""
    return ref.value if ref.kind == "video" and _VIDEO_ID.match(ref.value) else ""


def build_target(platform: str, url: str, *, channel_id: str = "", stream_url: str = "",
                 session_id: str = "") -> dict:
    """Return {mode: 'url'|'html'|'external', src, html, base, external}."""
    if platform == PLATFORM_KICK:
        try:
            _p, ref = parse_channel_url(PLATFORM_KICK, url)
        except ValueError:
            return {"mode": "external", "src": "", "html": "", "base": "", "external": url}
        slug = ref.value
        return {"mode": "url", "src": f"https://player.kick.com/{slug}", "html": "", "base": "",
                "external": f"https://kick.com/{slug}"}

    # YouTube
    vid = youtube_video_id(stream_url) or youtube_video_id(session_id) or youtube_video_id(url)
    ref_kind, ref_val = "", ""
    try:
        _p, ref = parse_channel_url(PLATFORM_YT, url)
        ref_kind, ref_val = ref.kind, ref.value
    except ValueError:
        pass
    ch_id = channel_id if _UC_ID.match(channel_id or "") else (ref_val if ref_kind == "channel_id" else "")
    if vid:
        src = f"https://www.youtube.com/embed/{vid}?autoplay=1&rel=0"
        external = f"https://www.youtube.com/watch?v={vid}"
    elif ch_id:
        src = f"https://www.youtube.com/embed/live_stream?channel={ch_id}&autoplay=1&rel=0"
        external = f"https://www.youtube.com/channel/{ch_id}/live"
    else:
        handle = ref_val if ref_kind == "handle" else ""
        return {"mode": "external", "src": "", "html": "", "base": "",
                "external": f"https://www.youtube.com/{handle}/live" if handle else (url or "https://www.youtube.com")}
    page = ('<!doctype html><html><head><meta charset="utf-8"><style>html,body{margin:0;height:100%;background:#000}'
            'iframe{border:0;width:100%;height:100%}</style></head><body>'
            f'<iframe src="{escape(src, quote=True)}" allow="autoplay; encrypted-media; fullscreen; picture-in-picture" '
            'allowfullscreen referrerpolicy="strict-origin-when-cross-origin"></iframe></body></html>')
    return {"mode": "html", "src": src, "html": page, "base": EMBED_BASE_URL, "external": external}
