"""Kick public API client (https://docs.kick.com, base https://api.kick.com/public/v1).

Endpoints used:
  GET  /users                               (scope user:read)      - account identity
  GET  /channels?slug=...                   (scope channel:read)   - slug -> broadcaster_user_id
  GET  /livestreams?broadcaster_user_id=... (scope channel:read)   - live detection
  POST /chat {type:user, content, broadcaster_user_id} (scope chat:write)
"""
from __future__ import annotations

from typing import Optional
from datetime import datetime

import http_util
from http_util import ApiError
from models import LiveInfo

API = "https://api.kick.com/public/v1"
SCOPES = ["user:read", "channel:read", "chat:write"]


def _first(data) -> Optional[dict]:
    d = data.get("data") if isinstance(data, dict) else None
    if isinstance(d, list):
        return d[0] if d else None
    return d if isinstance(d, dict) else None


def fetch_identity(access_token: str) -> tuple[str, str]:
    """Return (user_id, name) of the authorizing Kick user."""
    data = http_util.request("GET", API + "/users", headers={"Authorization": f"Bearer {access_token}"})
    u = _first(data)
    if not u or "user_id" not in u:
        raise ApiError("Kick did not return account information.", kind="other")
    return str(u["user_id"]), str(u.get("name") or f"Kick user {u['user_id']}")


class KickAPI:
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
                    continue
                raise

    def resolve_channel(self, account, slug: str) -> dict:
        data = self._call(account, "GET", "/channels", {"slug": slug})
        items = data.get("data") if isinstance(data, dict) else None
        items = items if isinstance(items, list) else ([items] if items else [])
        for it in items:
            if str(it.get("slug", "")).lower() == slug.lower() and it.get("broadcaster_user_id"):
                return {"channel_id": str(it["broadcaster_user_id"]), "title": it.get("slug", slug)}
        raise ApiError("Kick channel not found.", kind="not_found")

    def find_live(self, account, broadcaster_user_id: str) -> Optional[LiveInfo]:
        data = self._call(account, "GET", "/livestreams",
                          {"broadcaster_user_id": broadcaster_user_id})
        items = data.get("data") if isinstance(data, dict) else None
        if isinstance(items, dict):
            items = [items]
        for it in items or []:
            if str(it.get("broadcaster_user_id")) == str(broadcaster_user_id):
                started_raw = it.get("started_at") or ""
                started = 0.0
                if started_raw:
                    try: started = datetime.fromisoformat(str(started_raw).replace("Z", "+00:00")).timestamp()
                    except Exception: pass
                category = it.get("category")
                if isinstance(category, dict): category = category.get("name") or category.get("slug") or ""
                viewers = it.get("viewer_count")
                try: viewers = int(viewers) if viewers is not None else None
                except Exception: viewers = None
                slug = str(it.get("slug") or "")
                return LiveInfo(session_id=str(started_raw or it.get("id") or "live"),
                                chat_id=str(broadcaster_user_id), title=str(it.get("stream_title", "")),
                                started_at=started, channel_id=str(broadcaster_user_id),
                                streamer=str(it.get("broadcaster_user_id") or broadcaster_user_id),
                                category=str(category or ""), game=str(category or ""), viewers=viewers,
                                thumbnail=str(it.get("thumbnail", "") or ""),
                                url=f"https://kick.com/{slug}" if slug else "")
        return None

    def send_message(self, account, broadcaster_user_id: str, text: str) -> str:
        body = {"type": "user", "content": text, "broadcaster_user_id": int(broadcaster_user_id)}
        data = self._call(account, "POST", "/chat", json_body=body)
        d = _first(data) or {}
        if d.get("is_sent") is False:
            raise ApiError("Kick did not accept the message (is_sent=false).", kind="other")
        return str(d.get("message_id", ""))
