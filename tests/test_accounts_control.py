"""Manual send from several accounts, per-account STOP/RESUME."""
import time
from tests.helpers import EngineCase
from models import Account, Runtime


class AccountsControlTests(EngineCase):
    def setUp(self):
        super().setUp()
        self.cfg.settings.demo_mode = False
        for i in (2, 3):
            self.cfg.accounts.append(Account(id=f"acc{i}", platform="kick", display_name=f"Second{i}", external_id=str(900 + i)))
            self.channel.account_ids.append(f"acc{i}")
            self.engine._account_timers[f"acc{i}"] = {"lastActivityAt": self.old, "lastMessageAt": self.old,
                                                       "nextActivityAt": self.old + 1740, "enabled": True, "state": "WAITING"}
        self.engine.runtime["ch1"] = Runtime(live=True, connection_status="Connected", session_id="s1", status="Running")

    def test_manual_send_from_all_accounts_resets_every_timer_and_spreads_them(self):
        r = self.engine.manual_send("ch1", ["acc1", "acc2", "acc3"], "hello")
        self.assertEqual(r["sent"], 3)
        dues = []
        for a in ("acc1", "acc2", "acc3"):
            st = self.engine._account_timers[a]
            self.assertGreater(st["lastActivityAt"], self.old, a)
            self.assertGreater(st["nextActivityAt"], time.time() + 60, a)
            dues.append(st["nextActivityAt"])
        dues.sort()
        self.assertGreaterEqual(dues[1] - dues[0], 119)         # default spread: 2..3 minutes
        self.assertGreaterEqual(dues[2] - dues[1], 119)

    def test_stopped_account_never_sends_and_has_no_next_time(self):
        self.engine.set_account_auto("acc2", False)
        st = self.engine._account_timers["acc2"]
        self.assertEqual((st["enabled"], st["nextActivityAt"], st["state"]), (False, None, "DISABLED"))
        st["nextActivityAt"] = time.time() - 5
        self.engine.send_automatic_message("acc2")
        self.assertEqual(self.kick.sent, [])

    def test_stopped_account_does_not_get_a_schedule_from_chat_or_manual_send(self):
        self.engine.set_account_auto("acc2", False)
        self.engine.manual_send("ch1", ["acc2"], "x")
        st = self.engine._account_timers["acc2"]
        self.assertIsNone(st["nextActivityAt"])
        self.assertEqual(st["state"], "DISABLED")
        self.assertGreater(st["lastActivityAt"], self.old)       # activity is still remembered

    def test_resume_schedules_a_future_send(self):
        self.engine.set_account_auto("acc2", False)
        self.engine.set_account_auto("acc2", True)
        st = self.engine._account_timers["acc2"]
        self.assertTrue(st["enabled"])
        self.assertGreater(st["nextActivityAt"], time.time())

    def test_stop_one_account_does_not_touch_the_others(self):
        before = dict(self.engine._account_timers["acc3"])
        self.engine.set_account_auto("acc2", False)
        self.assertEqual(self.engine._account_timers["acc3"], before)
