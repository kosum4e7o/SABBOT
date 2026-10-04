"""Timer rules + full !points / !time data flow (Kick frame -> UI data)."""
import time
import unittest

from tests.helpers import EngineCase, MY_NAME
from http_util import ApiError
from models import Runtime


class TimerRuleTests(EngineCase):
    def test_normal_user_message_resets_timer(self):
        before = self.timer()["lastActivityAt"]
        self.assertEqual(self.feed("здравей"), "acc1")
        after = self.timer()
        self.assertGreater(after["lastActivityAt"], before)
        self.assertGreater(after["nextActivityAt"], time.time() + 60)
        rows = self.engine.chat_store.account_activity("acc1")
        self.assertEqual([r[5] for r in rows], ["здравей"])          # Recent Activity

    def test_points_command_resets_timer_and_is_recorded(self):
        self.feed("!points")
        self.assertGreater(self.timer()["lastActivityAt"], self.old)
        self.assertEqual(self.engine.chat_store.account_activity("acc1")[0][5], "!points")
        self.assertIsNotNone(self.engine.chat_processor.pending_for("Kick", "!points"))

    def test_time_command_resets_timer_and_is_recorded(self):
        self.feed("!time")
        self.assertGreater(self.timer()["lastActivityAt"], self.old)
        self.assertEqual(self.engine.chat_store.account_activity("acc1")[0][5], "!time")
        self.assertIsNotNone(self.engine.chat_processor.pending_for("Kick", "!time"))

    def test_bottly_response_does_not_reset_timer(self):
        self.feed("!points")
        mark = self.timer()
        time.sleep(0.01)
        self.bottly(f"@{MY_NAME} има 6826 точки!")
        self.assertEqual(self.timer(), mark)                          # NOTHING moved

    def test_bottly_time_response_does_not_reset_timer(self):
        self.feed("!time")
        mark = self.timer()
        time.sleep(0.01)
        self.bottly(f"@{MY_NAME} е гледал 2 часа и 35 минути")
        self.assertEqual(self.timer(), mark)

    def test_bottly_plain_message_does_not_reset_timer(self):
        mark = self.timer()
        self.bottly("Добре дошли в чата!")
        self.assertEqual(self.timer(), mark)

    def test_other_viewer_does_not_reset_timer(self):
        mark = self.timer()
        self.feed("hello", user_id="123", username="someone_else")
        self.assertEqual(self.timer(), mark)

    def test_old_replayed_message_does_not_move_timer_backwards(self):
        self.feed("old one", ts=self.old - 3600)
        self.assertEqual(self.timer()["lastActivityAt"], self.old)

    def _live(self):
        self.engine.runtime["ch1"] = Runtime(live=True, connection_status="Connected", session_id="s1",
                                             status="Running")

    def test_successful_automatic_message_resets_timer(self):
        self._live()
        self.engine.send_automatic_message("acc1")
        self.assertEqual(len(self.kick.sent), 1)
        self.assertGreater(self.timer()["lastActivityAt"], self.old)
        self.assertEqual(self.engine.chat_store.account_activity("acc1")[0][6], "automatic_send")

    def test_failed_automatic_message_does_not_reset_timer(self):
        self._live()
        self.kick.fail = ApiError("boom", kind="network")
        self.engine.send_automatic_message("acc1")
        self.assertEqual(self.kick.sent, [])
        self.assertEqual(self.timer()["lastActivityAt"], self.old)
        self.assertEqual(self.engine.chat_store.account_activity("acc1"), [])

    def test_echo_of_our_automatic_message_does_not_reset_twice(self):
        self._live()
        self.engine.send_automatic_message("acc1")
        first = self.timer()
        self.feed("hello chat")                                       # Kick echoes it back
        self.assertEqual(self.timer(), first)
        self.assertEqual(len(self.engine.chat_store.account_activity("acc1")), 1)

    def test_reset_refuses_bottly_reason(self):
        self.assertFalse(self.engine.reset_activity_timer(self.account, time.time(), "BOTTLY_RESPONSE"))
        self.assertEqual(self.timer()["lastActivityAt"], self.old)

    def test_legacy_handle_incoming_accepts_reply_to_kwarg(self):
        """Regression: `reply_to` used to raise TypeError and silently killed ALL activity tracking."""
        aid = self.engine.handle_incoming_chat_message(
            message_id="x1", ts=time.time(), platform="Kick", channel="s", channel_id="999",
            username=MY_NAME, user_id="555", text="hi", reply_to="", stream_key=self.key, future_field=1)
        self.assertEqual(aid, "acc1")


class DataFlowTests(EngineCase):
    def profile(self):
        rows = self.engine.chat_store.profiles()
        return rows[0] if rows else None

    def test_points_full_flow(self):
        self.feed("!points")
        self.bottly(f"@{MY_NAME} има 6826 точки!")
        p = self.profile()
        self.assertEqual(p[3], "acc1")
        self.assertEqual(p[5], 6826)                                   # points saved
        acts = self.engine.chat_store.account_activity("acc1")
        self.assertEqual({r[6] for r in acts}, {"chat", "bot_response"})
        self.assertIsNone(self.engine.chat_processor.pending_for("Kick", "!points"))   # consumed
        feed = self.engine.live_chat_messages[self.key]
        self.assertEqual([(m["username"], m["is_self"], m["is_bot"]) for m in feed],
                         [(MY_NAME, True, False), ("BOTTLY", False, True)])

    def test_time_full_flow(self):
        self.feed("!time")
        self.bottly(f"@{MY_NAME} е гледал 2 часа и 35 минути")
        p = self.profile()
        self.assertEqual(p[9], 9300)                                   # watch_seconds
        self.assertEqual(p[8], "2h 35m")

    def test_every_points_and_time_reply_is_a_new_history_row(self):
        self.feed("!points"); self.bottly(f"@{MY_NAME} има 100 точки!")
        self.feed("!points"); self.bottly(f"@{MY_NAME} има 150 точки!")
        self.feed("!time");   self.bottly(f"@{MY_NAME} е гледал 2 часа и 35 минути")
        rows = self.engine.chat_store.value_history()
        self.assertEqual([(r[5], r[6]) for r in rows], [("time", "2h 35m"), ("points", "150"), ("points", "100")])
        self.assertEqual(rows[0][2], MY_NAME)

    def test_reply_without_name_uses_pending_command(self):
        self.feed("!points")
        self.bottly("Имаш 17 точки!")
        self.assertEqual(self.profile()[5], 17)

    def test_reply_for_another_viewer_never_touches_our_account(self):
        self.feed("!points")
        self.bottly("@someone_else има 99999 точки!")
        p = self.profile()
        self.assertTrue(p is None or p[5] is None)

    def test_random_viewer_cannot_set_our_points(self):
        self.feed(f"@{MY_NAME} има 5 точки", user_id="123", username="troll")
        p = self.profile()
        self.assertTrue(p is None or p[5] is None)

    def test_our_own_fake_reply_is_not_treated_as_bot(self):
        self.feed("!points")
        self.feed(f"@{MY_NAME} има 123 точки")                        # typed by us
        p = self.profile()
        self.assertTrue(p is None or p[5] is None)

    def test_bet_answer_never_overwrites_points(self):
        """Bug: `!bet c 200` -> bot says we lack points (mentions 200) -> 200 was stored as our points."""
        self.feed("!points"); self.bottly(f"@{MY_NAME} има 150 точки!")
        self.assertEqual(self.profile()[5], 150)
        self.feed("!bet c 200")
        self.bottly(f"@{MY_NAME} нямаш достатъчно точки за залог от 200 точки!")
        self.bottly(f"@{MY_NAME} не може да заложиш 200 точки. Имаш само 150 точки.")
        self.bottly(f"@{MY_NAME} you don't have enough points to bet 200 points")
        self.assertEqual(self.profile()[5], 150)                       # untouched
        self.assertEqual([r[6] for r in self.engine.chat_store.value_history() if r[5] == "points"], ["150"])

    def test_points_need_our_own_pending_command(self):
        """A bot line that merely mentions our name + 'точки' is not a !points answer."""
        self.bottly(f"@{MY_NAME} спечели 300 точки от залога!")
        p = self.profile()
        self.assertTrue(p is None or p[5] is None)

    def test_bet_reply_does_not_consume_a_real_pending_points_answer(self):
        self.feed("!points")
        self.feed("!bet c 200")
        self.bottly(f"@{MY_NAME} нямаш достатъчно точки (200)")       # answer to the bet: ignored
        p = self.profile()
        self.assertTrue(p is None or p[5] is None)
        self.bottly(f"@{MY_NAME} има 150 точки!")                      # the real !points answer
        self.assertEqual(self.profile()[5], 150)

    def test_no_values_before_first_response(self):
        self.feed("!points")
        p = self.profile()
        self.assertTrue(p is None or (p[5] is None and p[9] is None))

    def test_activity_recorded_even_with_chat_logging_disabled(self):
        self.cfg.settings.chat_logging_enabled = False
        self.feed("здравей")
        self.assertEqual(len(self.engine.chat_store.account_activity("acc1")), 1)
        self.assertGreater(self.timer()["lastActivityAt"], self.old)

    def test_debug_trail(self):
        import logging
        lines = []

        class H(logging.Handler):
            def emit(self, rec): lines.append(rec.getMessage())
        h = H(); lg = logging.getLogger("stream_activity_bot"); lg.addHandler(h); lg.setLevel(logging.INFO)
        try:
            self.feed("!points")
            self.bottly(f"@{MY_NAME} има 6826 точки!")
        finally:
            lg.removeHandler(h)
        text = "\n".join(lines)
        for needle in ("CHAT RECEIVED", "ACCOUNT MATCH", "match_reason=user_id", "PENDING COMMAND CREATED",
                       "TIMER RESET", "REASON=USER_MESSAGE", "BOTTLY RESPONSE", "POINTS PARSED",
                       "TIMER NOT RESET", "REASON=BOTTLY_RESPONSE"):
            self.assertIn(needle, text)


if __name__ == "__main__":
    unittest.main()
