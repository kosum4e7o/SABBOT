"""Accounts must never write automatically at (almost) the same time."""
import time
import unittest

from tests.helpers import EngineCase
from models import Account, Runtime


class StaggerTests(EngineCase):
    def setUp(self):
        super().setUp()
        s = self.cfg.settings
        s.account_stagger_min_minutes = 3.0
        s.account_stagger_max_minutes = 3.0          # fixed 3 min -> deterministic assertions
        for i in (2, 3):
            self.cfg.accounts.append(Account(id=f"acc{i}", platform="kick", display_name=f"Second{i}", external_id=str(900 + i)))
            self.channel.account_ids.append(f"acc{i}")
            self.engine._account_timers[f"acc{i}"] = {"lastActivityAt": None, "lastMessageAt": None,
                                                       "nextActivityAt": None, "enabled": True, "state": "READY"}
        self.engine.runtime["ch1"] = Runtime(live=True, connection_status="Connected", session_id="s1", status="Running")

    def dues(self):
        return sorted(float(self.engine._account_timers[a]["nextActivityAt"]) for a in ("acc1", "acc2", "acc3"))

    def test_first_schedule_after_going_live_is_spread_out(self):
        for a in ("acc1", "acc2", "acc3"):
            self.engine._account_timers[a].update(lastActivityAt=None, nextActivityAt=None)
        self.engine._ensure_live_account_timers(self.channel)
        d = self.dues()
        self.assertGreaterEqual(d[1] - d[0], 179)
        self.assertGreaterEqual(d[2] - d[1], 179)

    def test_identical_intervals_do_not_collide(self):
        t = time.time()
        for a in ("acc1", "acc2", "acc3"):
            self.engine.reset_activity_timer(self.cfg.get_account(a), t, "USER_MESSAGE", interval_sec=1740)
        d = self.dues()
        self.assertGreaterEqual(d[1] - d[0], 179)
        self.assertGreaterEqual(d[2] - d[1], 179)

    def test_send_is_postponed_when_another_account_just_wrote(self):
        self.engine.send_automatic_message("acc2")
        self.assertEqual(len(self.kick.sent), 1)
        mark = self.engine._account_timers["acc3"]["nextActivityAt"]
        self.engine._account_timers["acc3"]["nextActivityAt"] = time.time() - 1
        self.engine.send_automatic_message("acc3")
        self.assertEqual(len(self.kick.sent), 1)                         # NOT sent together
        self.assertGreater(self.engine._account_timers["acc3"]["nextActivityAt"], time.time() + 170)

    def test_single_account_is_not_delayed(self):
        for a in ("acc2", "acc3"):
            self.engine._account_timers[a]["enabled"] = False
        t = time.time()
        self.engine.reset_activity_timer(self.account, t, "USER_MESSAGE", interval_sec=1740)
        self.assertAlmostEqual(self.engine._account_timers["acc1"]["nextActivityAt"], t + 1740, delta=1)

    def test_disabled_gap_keeps_old_behaviour(self):
        self.cfg.settings.account_stagger_min_minutes = 0
        self.cfg.settings.account_stagger_max_minutes = 0
        t = time.time()
        self.engine.reset_activity_timer(self.cfg.get_account("acc1"), t, "USER_MESSAGE", interval_sec=1740)
        self.engine.reset_activity_timer(self.cfg.get_account("acc2"), t, "USER_MESSAGE", interval_sec=1740)
        a, b = (float(self.engine._account_timers[x]["nextActivityAt"]) for x in ("acc1", "acc2"))
        self.assertAlmostEqual(a, b, delta=1)


if __name__ == "__main__":
    unittest.main()
