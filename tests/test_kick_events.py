"""normalize_chat_message + account matching + Pusher frame handling."""
import json
import unittest

from tests.helpers import MY_ID, MY_NAME, kick_envelope, EngineCase  # noqa: F401  (sets sys.path)
from kick_events import ChatMessage, is_chat_event, match_account, normalize_chat_message
from models import Account


class NormalizeTests(unittest.TestCase):
    def test_envelope_with_double_encoded_data(self):
        m = normalize_chat_message(kick_envelope("здравей"))
        self.assertEqual((m.user_id, m.username, m.message), (MY_ID, MY_NAME, "здравей"))
        self.assertEqual(m.event_type, "App\\Events\\ChatMessageEvent")
        self.assertEqual(m.chatroom_id, "123")
        self.assertEqual(m.display_name, MY_NAME)
        self.assertEqual(m.slug, "desper-bg")

    def test_flat_payload(self):
        m = normalize_chat_message({"id": "1", "content": "hi", "sender": {"id": 9, "username": "Bob"}})
        self.assertEqual((m.user_id, m.username, m.message, m.message_id), ("9", "Bob", "hi", "1"))

    def test_nested_old_payload(self):
        m = normalize_chat_message({"message": {"id": "a1", "message": "yo", "created_at": 1700000000},
                                    "user": {"user_id": 42, "username": "Zed", "displayname": "ZED"}},
                                   event_type="App\\Events\\ChatMessageSentEvent")
        self.assertEqual((m.user_id, m.username, m.display_name, m.message), ("42", "Zed", "ZED", "yo"))
        self.assertEqual(m.timestamp, 1700000000.0)

    def test_webhook_like_wrapper_and_sent_event(self):
        m = normalize_chat_message({"event": "App\\Events\\ChatMessageSentEvent",
                                    "data": {"data": {"message_id": "w1", "content": "x",
                                                      "sender": {"user_id": 5, "username": "w", "channel_slug": "w-s"}}}})
        self.assertEqual((m.user_id, m.username, m.slug, m.message_id), ("5", "w", "w-s", "w1"))

    def test_millisecond_timestamp_and_iso(self):
        a = normalize_chat_message({"id": "1", "content": "a", "created_at": 1700000000123, "sender": {"id": 1, "username": "a"}})
        self.assertAlmostEqual(a.timestamp, 1700000000.123, places=2)
        b = normalize_chat_message(kick_envelope("b", created="2026-10-01T10:00:00Z"))
        self.assertGreater(b.timestamp, 1.7e9)

    def test_no_text_returns_none(self):
        self.assertIsNone(normalize_chat_message({"id": "1", "sender": {"id": 1, "username": "a"}}))
        self.assertIsNone(normalize_chat_message("not json"))

    def test_reply_target(self):
        m = normalize_chat_message(kick_envelope("hi", user_id="777", username="BOTTLY", replies_to="Desper_BG"))
        self.assertEqual(m.reply_to, "Desper_BG")

    def test_event_names(self):
        for name in ("App\\Events\\ChatMessageEvent", "App\\\\Events\\\\ChatMessageSentEvent", "chat.message.sent"):
            self.assertTrue(is_chat_event(name), name)
        self.assertFalse(is_chat_event("pusher:ping"))
        self.assertFalse(is_chat_event("App\\Events\\MessageDeletedEvent"))


class AccountMatchTests(unittest.TestCase):
    def setUp(self):
        self.a = Account(id="a", platform="kick", display_name="Desper_BG", external_id="555",
                         aliases=["desperbg2"], metadata={"slug": "desper-bg"})
        self.b = Account(id="b", platform="kick", display_name="Other", external_id="666")

    def msg(self, uid, name, slug="", disp=""):
        return ChatMessage(user_id=uid, username=name, slug=slug, display_name=disp or name, message="x")

    def test_user_id(self):
        acc, why = match_account(self.msg("666", "totally-different"), [self.a, self.b])
        self.assertEqual((acc.id, why), ("b", "user_id"))

    def test_username_case_insensitive(self):
        acc, why = match_account(self.msg("", "DESPER_bg"), [self.a, self.b])
        self.assertEqual((acc.id, why), ("a", "username"))
        acc, _ = match_account(self.msg("555", "desper-bg"), [self.a, self.b])
        self.assertEqual(acc.id, "a")

    def test_slug_and_alias(self):
        acc, why = match_account(self.msg("", "whatever", slug="Desper-BG"), [self.b, self.a])
        self.assertEqual((acc.id, why), ("a", "slug"))
        acc, why = match_account(self.msg("", "DesperBG2"), [self.a, self.b])
        self.assertEqual((acc.id, why), ("a", "alias"))

    def test_known_different_user_id_blocks_name_match(self):
        acc, why = match_account(self.msg("999", "Desper_BG"), [self.a, self.b])
        self.assertEqual((acc, why), (None, "none"))

    def test_no_match(self):
        self.assertEqual(match_account(self.msg("1", "nobody"), [self.a, self.b]), (None, "none"))


class ReaderFrameTests(unittest.TestCase):
    def test_reader_emits_one_normalized_message_and_dedupes(self):
        import kick_chat
        got, dbg = [], []
        r = kick_chat.KickChatReader("streamer", got.append, chatroom_id=123, on_debug=dbg.append)
        raw = json.dumps(kick_envelope("здравей", mid="same"))
        r._handle_raw(None, raw, 123)
        r._handle_raw(None, raw, 123)                         # duplicate id -> ignored
        self.assertEqual(len(got), 1)
        self.assertIsInstance(got[0], ChatMessage)
        self.assertEqual(got[0].message, "здравей")
        self.assertTrue(any("CHAT EVENT PARSED" in d for d in dbg))

    def test_reader_ignores_other_events_and_logs_subscription(self):
        import kick_chat
        got, dbg = [], []
        r = kick_chat.KickChatReader("streamer", got.append, chatroom_id=123, on_debug=dbg.append)
        r._handle_raw(None, json.dumps({"event": "pusher_internal:subscription_succeeded",
                                        "channel": "chatrooms.123.v2", "data": "{}"}), 123)
        r._handle_raw(None, json.dumps({"event": "App\\Events\\MessageDeletedEvent", "data": "{}"}), 123)
        self.assertEqual(got, [])
        self.assertTrue(any("SUBSCRIPTION SUCCEEDED" in d for d in dbg))


if __name__ == "__main__":
    unittest.main()
