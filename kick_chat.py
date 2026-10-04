"""Read public Kick live chat through Kick's public Pusher feed.

The official Kick API is used by the app for OAuth, live detection and sending.
Kick's public chat reception feed is read from the same public Pusher channel
used by the website; no extra login is required for reading chat.
"""
from __future__ import annotations

import json
import os
import threading
import time

try:
    import websocket
except ImportError:
    websocket = None

import http_util
from kick_events import ChatMessage, is_chat_event, normalize_chat_message

# Kick's public (anonymous) Pusher app key - this is not a secret, the website ships it.
# It can be overridden with KICK_PUSHER_URL if Kick ever moves the gateway.
PUSHER_URL = os.environ.get("KICK_PUSHER_URL") or (
    "wss://ws-us2.pusher.com/app/32cbd69e4b950bf97679"
    "?protocol=7&client=js&version=8.4.0&flash=false"
)
BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

CLOUDFLARE_HELP = (
    "Kick блокира автоматичното намиране на chatroom ID (Cloudflare). "
    "Решение: отвори kick.com/{slug} в браузъра, натисни F12 -> Console и изпълни "
    "fetch('/api/v2/channels/{slug}').then(r=>r.json()).then(d=>console.log(d.chatroom.id)) "
    "- после въведи числото в Канали -> Edit -> Kick chatroom ID."
)


class KickChatReader:
    def __init__(self, slug, on_message, on_status=None, chatroom_id=None, on_chatroom=None,
                 broadcaster_user_id=None, on_debug=None):
        self.slug = slug
        # on_message receives ONE normalized kick_events.ChatMessage.
        self.on_message = on_message
        self.on_debug = on_debug
        self.on_status = on_status
        self.on_chatroom = on_chatroom
        self.chatroom_id = chatroom_id
        self.broadcaster_user_id = str(broadcaster_user_id or "")
        self.history_channel_id = self.broadcaster_user_id
        self.stop_event = threading.Event()
        self.thread = None
        self.history_thread = None
        self._ws = None
        self._seen_ids = set()
        self._seen_lock = threading.RLock()

    # ------------------------------------------------------------ lifecycle
    def start(self):
        if websocket is None:
            self._status(
                "error: липсва websocket-client. Инсталирай requirements.txt и стартирай отново."
            )
            return
        if self.thread and self.thread.is_alive():
            return
        self.stop_event.clear()
        self.thread = threading.Thread(target=self._run, daemon=True, name="kick-chat")
        self.thread.start()
        if self.broadcaster_user_id:
            self.history_thread = threading.Thread(
                target=self._history_loop, daemon=True, name="kick-chat-history"
            )
            self.history_thread.start()

    def stop(self):
        self.stop_event.set()
        ws = self._ws
        if ws is not None:
            try:
                ws.close()
            except Exception:
                pass

    def _debug(self, text: str):
        """Debug trail (never contains tokens/secrets - the Pusher app key is public)."""
        if self.on_debug:
            try:
                self.on_debug(text)
            except Exception:
                pass

    def _status(self, value: str):
        if self.on_status:
            try:
                self.on_status(value)
            except Exception:
                pass

    # ------------------------------------------------------ chatroom lookup
    @staticmethod
    def _find_chatroom_id(data):
        if not isinstance(data, dict):
            return None
        room = data.get("chatroom")
        if isinstance(room, dict) and room.get("id"):
            return int(room["id"])
        if data.get("chatroom_id"):
            return int(data["chatroom_id"])
        if data.get("id") and ("chat_mode" in data or "slow_mode" in data):
            return int(data["id"])
        return None

    def _resolve_chatroom(self):
        headers = {
            "User-Agent": BROWSER_UA,
            "Referer": f"https://kick.com/{self.slug}",
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9",
        }
        blocked = False
        last = None
        urls = (
            f"https://kick.com/api/v2/channels/{self.slug}",
            f"https://kick.com/api/v2/channels/{self.slug}/chatroom",
            f"https://kick.com/api/v1/channels/{self.slug}",
        )
        for url in urls:
            try:
                data = http_util.request("GET", url, headers=headers, timeout=15)
                if isinstance(data, dict):
                    # Website channel responses normally expose their own numeric
                    # channel id at the top level. Prefer it for the history API;
                    # broadcaster_user_id and chatroom_id are different identifiers.
                    website_id = data.get("id") or data.get("channel_id")
                    if website_id:
                        self.history_channel_id = str(website_id)
                cid = self._find_chatroom_id(data)
                if cid:
                    return cid
            except http_util.ApiError as e:
                last = e
                if e.status in (403, 429, 503):
                    blocked = True
            except Exception as e:
                last = e
        if blocked:
            raise RuntimeError(CLOUDFLARE_HELP.format(slug=self.slug))
        raise RuntimeError(
            f"Не може да се намери chatroom ID за '{self.slug}': "
            f"{last or 'непознат отговор'}"
        )

    # ------------------------------------------------------------ message
    def _emit(self, msg: ChatMessage, source: str = "pusher") -> None:
        """De-duplicate by message id and hand ONE normalized message to the engine."""
        mid = str(msg.message_id)
        with self._seen_lock:
            if mid in self._seen_ids:
                return
            self._seen_ids.add(mid)
            if len(self._seen_ids) > 10000:
                self._seen_ids = set(list(self._seen_ids)[-5000:])
        self._debug(f"CHAT EVENT PARSED source={source} event={msg.event_type} message_id={mid}")
        self.on_message(msg)
        self._status("chat receiving")

    def _handle_raw(self, ws, raw, chatroom):
        """Decode one Pusher frame (including nested/double-encoded JSON)."""
        outer = json.loads(raw)
        event = str(outer.get("event") or "")

        if event == "pusher:ping":
            if ws is not None:
                ws.send(json.dumps({"event": "pusher:pong", "data": {}}))
            return

        if event == "pusher:connection_established":
            self._debug("PUSHER HANDSHAKE received (pusher:connection_established)")
            return

        if event == "pusher:error":
            self._debug(f"PUSHER ERROR data={outer.get('data')}")
            self._status(f"error: Pusher error: {outer.get('data')}")
            return

        if event == "pusher_internal:subscription_succeeded":
            self._debug(f"SUBSCRIPTION SUCCEEDED channel={outer.get('channel')}")
            self._status(f"connected (chatroom {chatroom})")
            return

        if event == "pusher:subscription_error":
            self._debug(f"SUBSCRIPTION ERROR channel={outer.get('channel')} data={outer.get('data')}")
            self._status(f"error: chat subscription rejected: {outer.get('data')}")
            # A cached room ID can become stale. Force a fresh lookup and a new connection.
            self.chatroom_id = None
            if ws is not None:
                try:
                    ws.close()
                except Exception:
                    pass
            return

        if not is_chat_event(event):
            if not event.startswith("pusher"):
                self._debug(f"EVENT IGNORED name={event}")
            return

        msg = normalize_chat_message(outer, chatroom_id=chatroom)
        if msg is None:
            keys = sorted(outer.keys())
            self._debug(f"CHAT EVENT NOT PARSED event={event} envelope_keys={keys} (no message text)")
            return
        self._emit(msg)

    # ---------------------------------------------------------- history fallback
    def _history_once(self):
        """Best-effort recent chat fallback (fills gaps after a reconnect).

        Realtime delivery is Pusher; this website endpoint is optional and frequently
        blocked by Cloudflare, so it never changes the connection status.
        """
        if not self.history_channel_id:
            return
        headers = {
            "User-Agent": BROWSER_UA,
            "Referer": f"https://kick.com/{self.slug}",
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9",
        }
        urls = (
            f"https://kick.com/api/v2/channels/{self.history_channel_id}/messages",
            f"https://web.kick.com/api/v1/chat/{self.history_channel_id}/history",
        )
        for url in urls:
            try:
                data = http_util.request("GET", url, headers=headers, timeout=10)
                block = data.get("data") if isinstance(data, dict) else None
                messages = []
                if isinstance(block, dict):
                    messages = block.get("messages") or []
                elif isinstance(block, list):
                    messages = block
                if not messages and isinstance(data, dict):
                    messages = data.get("messages") or []
                if not isinstance(messages, list):
                    messages = []
                normalized = []
                for m in messages:
                    if isinstance(m, dict):
                        n = normalize_chat_message(m, event_type="history",
                                                   chatroom_id=self.chatroom_id or "")
                        if n is not None:
                            normalized.append(n)
                # Oldest first so command -> bot reply ordering is preserved.
                for n in sorted(normalized, key=lambda x: x.timestamp):
                    self._emit(n, source="history")
                return
            except Exception:
                continue

    def _history_loop(self):
        while not self.stop_event.is_set():
            try:
                self._history_once()
            except Exception:
                pass
            self.stop_event.wait(4.0)

    # ---------------------------------------------------------------- loop
    def _run(self):
        backoff = 2
        while not self.stop_event.is_set():
            connected = {"ok": False}
            try:
                self._status("resolving")
                chatroom = int(self.chatroom_id) if self.chatroom_id else self._resolve_chatroom()
                if not self.chatroom_id and self.on_chatroom:
                    self.on_chatroom(chatroom)
                self.chatroom_id = chatroom
                self._status("connecting")
                channel_name = f"chatrooms.{chatroom}.v2"

                def subscribe(ws):
                    ws.send(json.dumps({
                        "event": "pusher:subscribe",
                        "data": {"auth": "", "channel": channel_name},
                    }))
                    self._debug(f"SUBSCRIPTION SENT channel={channel_name}")
                    self._status(f"subscribing (chatroom {chatroom})")

                def on_open(ws):
                    # Subscribe ONLY after pusher:connection_established (not here).
                    self._debug("WEBSOCKET CONNECTED")
                    self._status("socket connected")

                def on_message(ws, raw):
                    try:
                        outer = json.loads(raw)
                        if outer.get("event") == "pusher:connection_established":
                            self._debug("PUSHER HANDSHAKE received (pusher:connection_established)")
                            subscribe(ws)
                            return
                        self._handle_raw(ws, raw, chatroom)
                    except Exception as e:
                        self._debug(f"MESSAGE HANDLING ERROR {type(e).__name__}: {e}")
                        self._status(f"error: message parse error: {e}")

                def on_error(ws, err):
                    self._debug(f"SOCKET ERROR {err}")
                    self._status(f"error: socket error: {err}")

                def on_close(ws, *args):
                    self._debug("WEBSOCKET CLOSED")
                    self._status("disconnected")

                self._ws = websocket.WebSocketApp(
                    PUSHER_URL,
                    header=[f"User-Agent: {BROWSER_UA}"],
                    on_open=on_open,
                    on_message=on_message,
                    on_error=on_error,
                    on_close=on_close,
                )
                self._ws.run_forever(
                    ping_interval=25,
                    ping_timeout=10,
                    origin="https://kick.com",
                )
                connected["ok"] = True
                if self.stop_event.is_set():
                    break
            except Exception as e:
                self._status(f"error: {e}")
            finally:
                self._ws = None

            backoff = 2 if connected["ok"] else min(30, backoff * 2)
            self.stop_event.wait(backoff)
