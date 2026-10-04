"""YouTube Data API v3 client (official API only).

Quota notes (default project quota 10,000 units/day):
  channels.list / playlistItems.list / videos.list = 1 unit, liveChatMessages.insert = 20,
  search.list = 100. Live detection therefore uses the channel's *uploads playlist* +
  videos.list (1-2 units) instead of search.list. search.list is an optional fallback.
"""
from __future__ import annotations

import time
from typing import Optional
from datetime import datetime

import http_util
from http_util import ApiError
from models import ChannelRef, LiveInfo

API = "https://www.googleapis.com/youtube/v3"
SCOPE = "https://www.googleapis.com/auth/youtube.force-ssl"


def fetch_identity(access_token: str) -> tuple[str, str]:
    """Return (channel_id, title) of the signed-in Google account's YouTube channel."""
    data = http_util.request("GET", API + "/channels", params={"part": "snippet", "mine": "true"},
                             headers={"Authorization": f"Bearer {access_token}"})
    items = data.get("items") or []
    if not items:
        raise ApiError("This Google account has no YouTube channel, so it cannot chat. "
                       "Create a channel first.", kind="not_found")
    return items[0]["id"], items[0].get("snippet", {}).get("title", "YouTube account")


class YouTubeAPI:
    def __init__(self, token_manager):
        self.tokens = token_manager

    def _call(self, account, method: str, path: str, params=None, json_body=None):
        for attempt in (0, 1):
            token = self.tokens.access_token(account, force_refresh=(attempt == 1))
            try:
                return http_util.request(method, API + path, params=params, json_body=json_body,
                                         headers={"Authorization": f"Bearer {token}"})
            except ApiError as e:
                if e.status == 401 and attempt == 0:
                    continue      # token rejected -> force one refresh and retry once
                raise

    # ------------------------------------------------------------ resolving
    def resolve_channel(self, account, ref: ChannelRef) -> dict:
        """Return {'channel_id', 'title', 'uploads'} for a parsed URL reference."""
        params = {"part": "snippet,contentDetails"}
        if ref.kind == "handle":
            params["forHandle"] = ref.value
        elif ref.kind == "channel_id":
            params["id"] = ref.value
        elif ref.kind == "user":
            params["forUsername"] = ref.value
        elif ref.kind == "custom":
            params["forHandle"] = "@" + ref.value   # most /c/ names equal the handle
        elif ref.kind == "video":
            v = self._call(account, "GET", "/videos", {"part": "snippet", "id": ref.value})
            items = v.get("items") or []
            if not items:
                raise ApiError("Video not found.", kind="not_found")
            params["id"] = items[0]["snippet"]["channelId"]
        else:
            raise ApiError("Unsupported YouTube URL.", kind="other")
        data = self._call(account, "GET", "/channels", params)
        items = data.get("items") or []
        if not items:
            hint = (" Use the @handle or /channel/UC... URL." if ref.kind == "custom" else "")
            raise ApiError("YouTube channel not found." + hint, kind="not_found")
        it = items[0]
        uploads = it.get("contentDetails", {}).get("relatedPlaylists", {}).get("uploads", "")
        return {"channel_id": it["id"], "title": it.get("snippet", {}).get("title", ref.value),
                "uploads": uploads}

    # ------------------------------------------------------------ live check
    def find_live(self, account, channel_id: str, uploads: str, rt, search_fallback_sec: int = 0
                  ) -> Optional[LiveInfo]:
        playlist = uploads or ("UU" + channel_id[2:])
        data = self._call(account, "GET", "/playlistItems",
                          {"part": "contentDetails", "playlistId": playlist, "maxResults": 5})
        ids = [i["contentDetails"]["videoId"] for i in data.get("items", [])
               if i.get("contentDetails", {}).get("videoId")]
        candidates = [v for v in ids if v not in rt.known_nonlive]
        info = self._live_from_videos(account, candidates, rt) if candidates else None
        if info is None and search_fallback_sec > 0 and time.time() - rt.last_search >= search_fallback_sec:
            rt.last_search = time.time()
            s = self._call(account, "GET", "/search", {"part": "id", "channelId": channel_id,
                                                        "eventType": "live", "type": "video",
                                                        "maxResults": 1})
            found = [i["id"]["videoId"] for i in s.get("items", []) if i.get("id", {}).get("videoId")]
            if found:
                info = self._live_from_videos(account, found, rt)
        return info

    def still_live(self, account, video_id: str, rt) -> Optional[LiveInfo]:
        """Cheap re-check (1 unit) of a known live video."""
        return self._live_from_videos(account, [video_id], rt)

    def _live_from_videos(self, account, video_ids: list, rt) -> Optional[LiveInfo]:
        data = self._call(account, "GET", "/videos",
                          {"part": "snippet,liveStreamingDetails", "id": ",".join(video_ids)})
        for v in data.get("items", []):
            snip = v.get("snippet", {})
            lsd = v.get("liveStreamingDetails") or {}
            state = snip.get("liveBroadcastContent", "none")
            if state == "none":
                rt.known_nonlive.add(v["id"])
                continue
            if state == "live" and lsd.get("activeLiveChatId") and lsd.get("actualStartTime") and not lsd.get("actualEndTime"):
                started = 0.0
                try: started = datetime.fromisoformat(lsd["actualStartTime"].replace("Z", "+00:00")).timestamp()
                except Exception: pass
                thumbs = snip.get("thumbnails") or {}
                thumb = ((thumbs.get("maxres") or thumbs.get("high") or thumbs.get("medium") or thumbs.get("default") or {}).get("url") or "")
                viewers = lsd.get("concurrentViewers")
                try: viewers = int(viewers) if viewers is not None else None
                except Exception: viewers = None
                return LiveInfo(session_id=v["id"], chat_id=lsd["activeLiveChatId"],
                                title=snip.get("title", ""), started_at=started,
                                channel_id=snip.get("channelId", ""), streamer=snip.get("channelTitle", ""),
                                category=snip.get("categoryId", "") or "", game=snip.get("categoryId", "") or "",
                                viewers=viewers, thumbnail=thumb,
                                url=f"https://www.youtube.com/watch?v={v['id']}")
        return None

    # --------------------------------------------------------------- chat
    def list_chat_messages(self, account, live_chat_id: str, page_token: str = "") -> dict:
        """Read live chat messages using the official Live Streaming API."""
        params = {"part": "id,snippet,authorDetails", "liveChatId": live_chat_id, "maxResults": 2000}
        if page_token:
            params["pageToken"] = page_token
        return self._call(account, "GET", "/liveChat/messages", params)

    # --------------------------------------------------------------- sending
    def send_message(self, account, live_chat_id: str, text: str) -> str:
        body = {"snippet": {"liveChatId": live_chat_id, "type": "textMessageEvent",
                            "textMessageDetails": {"messageText": text}}}
        data = self._call(account, "POST", "/liveChat/messages", {"part": "snippet"}, json_body=body)
        return data.get("id", "")
