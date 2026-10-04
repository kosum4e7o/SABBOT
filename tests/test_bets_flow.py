"""!bet flow with the REAL engine: Kick frame -> statistics, per-profile bet, demo/live switch."""
import unittest

from tests.helpers import EngineCase, MY_ID, MY_NAME


class BetFlowTests(EngineCase):
    def test_real_chat_bets_are_counted(self):
        self.feed("!bet A 100", user_id="1", username="u1", mid="m1")
        self.feed("!bet a 300", user_id="2", username="u2", mid="m2")
        self.feed("!bet B 600", user_id="3", username="u3", mid="m3")
        self.feed("hello", user_id="4", username="u4", mid="m4")
        self.bottly("!bet Z 999", mid="m5")                       # response bot never counts
        st = self.engine.bet_stats(self.key)
        self.assertEqual((st["top"], st["total_bets"], st["total_users"], st["total_points"]), ("A", 3, 3, 1000))

    def test_same_message_twice_counts_once(self):
        self.feed("!bet A 100", user_id="1", username="u1", mid="same")
        self.feed("!bet A 100", user_id="1", username="u1", mid="same")
        self.assertEqual(self.engine.bet_stats(self.key)["total_bets"], 1)

    def test_own_account_bet_is_counted_and_still_resets_timer(self):
        self.feed("!bet C 50")                                      # sent by our own account
        self.assertEqual(self.engine.bet_stats(self.key)["top"], "C")
        self.assertGreater(self.timer()["lastActivityAt"], self.old)

    def test_profile_bet_saved_and_sent_live(self):
        self.cfg.settings.demo_mode = False
        self.channel.channel_id = "999"
        from models import Runtime
        self.engine.runtime["ch1"] = Runtime(live=True, session_id="s1", connection_status="Connected")
        r = self.engine.place_bet("ch1", "acc1", "b", "2k")
        self.assertTrue(r["ok"], r)
        self.assertEqual(self.kick.sent[-1][2], "!bet B 2000")
        self.assertEqual(self.engine.get_profile_bet("acc1"), {"letter": "B", "points": 2000})
        self.assertFalse(self.engine.place_bet("ch1", "acc1", "bb", "5")["ok"])

    def test_demo_bet_is_simulated_never_sent(self):
        self.engine.switch_mode(True)
        r = self.engine.place_bet("ch1", "acc1", "c", "300")
        self.assertTrue(r["ok"] and r["demo"])
        self.assertEqual(self.kick.sent, [])
        self.assertEqual(self.engine.bet_stats(self.key)["total_bets"], 1)

    def test_switch_mode_starts_clean(self):
        self.feed("!bet A 100", user_id="1", username="u1", mid="m1")           # a real bet (LIVE)
        self.engine.switch_mode(True)
        self.engine._simulate_demo_bets(self.channel, "demo-1")
        self.assertGreater(self.engine.bet_stats(self.key)["total_bets"], 0)      # demo numbers
        self.engine.switch_mode(False)
        self.assertEqual(self.engine.bet_tracker.stats(self.key, demo=True)["total_bets"], 0)
        self.assertEqual(self.engine.bet_stats(self.key)["total_bets"], 1)        # real data untouched
        self.assertFalse(self.engine.sim_live)

    def test_typed_commands_are_remembered(self):
        self.engine.remember_command("!hello world")
        self.engine.remember_command("!bet A 5")
        self.engine.remember_command("just text")
        self.assertEqual(self.engine.saved_commands(), ["!hello"])


if __name__ == "__main__":
    unittest.main()
