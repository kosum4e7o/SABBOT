"""Shared fixtures: an isolated Engine (temp data dir, no network, no Qt)."""
import json
import os
import queue
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from kick_events import ChatMessage, normalize_chat_message  # noqa: E402
from models import Account, Channel, Runtime  # noqa: E402

MY_ID = "555"
MY_NAME = "Desper_BG"
STREAMER = "streamer"


def kick_envelope(content, *, user_id=MY_ID, username=MY_NAME, slug=None, mid=None, created=None,
                  chatroom=123, event="App\\Events\\ChatMessageEvent", replies_to=None):
    """A realistic Pusher frame: `data` is a JSON *string* (double encoded)."""
    data = {
        "id": mid or f"m-{time.time_ns()}", "chatroom_id": chatroom, "content": content,
        "type": "message", "created_at": created or "2026-10-01T10:00:00+00:00",
        "sender": {"id": int(user_id), "username": username,
                   "slug": slug or username.lower().replace("_", "-"),
                   "identity": {"color": "#FF0000", "badges": []}},
    }
    if replies_to:
        data["metadata"] = {"original_sender": {"username": replies_to}}
    return {"event": event, "channel": f"chatrooms.{chatroom}.v2", "data": json.dumps(data)}


class EngineCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="sabot-test-")
        self._old_home = os.environ.get("STREAM_BOT_HOME")
        os.environ["STREAM_BOT_HOME"] = self._tmp
        from engine import Engine
        from storage import Config
        self.cfg = Config()
        self.cfg.settings.account_send_gap_sec = 0.25
        self.account = Account(id="acc1", platform="kick", display_name=MY_NAME, external_id=MY_ID)
        self.cfg.accounts.append(self.account)
        self.channel = Channel(id="ch1", platform="kick", url=f"https://kick.com/{STREAMER}",
                               account_id="acc1", account_ids=["acc1"], channel_id="999",
                               messages=["hello chat"], interval_minutes=29)
        self.cfg.channels.append(self.channel)
        self.kick = FakeKick()
        self.events = queue.Queue()
        self.engine = Engine(self.cfg, None, None, None, self.kick, self.events)
        self.engine._load_account_timers()
        self.key = self.engine.stream_key(self.channel)
        # A known "old" activity so a reset is observable.
        self.old = time.time() - 600
        st = self.engine._account_timers["acc1"]
        st.update(lastActivityAt=self.old, lastMessageAt=self.old, nextActivityAt=self.old + 1740, state="WAITING")

    def tearDown(self):
        try:
            self.engine.close()
            self.engine.chat_store.close()
        finally:
            if self._old_home is None:
                os.environ.pop("STREAM_BOT_HOME", None)
            else:
                os.environ["STREAM_BOT_HOME"] = self._old_home
            shutil.rmtree(self._tmp, ignore_errors=True)

    # -- helpers
    def feed(self, content, **kw):
        """Run a Kick frame through normalize_chat_message -> Engine.process_chat_message."""
        kw.setdefault("created", None)
        env = kick_envelope(content, **{k: v for k, v in kw.items() if k != "ts"})
        msg = normalize_chat_message(env)
        msg.timestamp = kw.get("ts", time.time())
        return self.engine.process_chat_message(msg, platform="Kick", channel=STREAMER,
                                                channel_id=self.channel.channel_id,
                                                stream_key=self.key, stream_name=STREAMER)

    def timer(self):
        return dict(self.engine._account_timers["acc1"])

    def bottly(self, content, **kw):
        return self.feed(content, user_id="777", username="BOTTLY", slug="bottly", **kw)


class FakeKick:
    def __init__(self):
        self.sent = []
        self.fail = None

    def send_message(self, account, broadcaster_user_id, text):
        if self.fail is not None:
            raise self.fail
        self.sent.append((account.id, broadcaster_user_id, text))
        return "msg-id"
