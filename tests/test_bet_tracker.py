import sys, tempfile, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import bet_tracker as bt


class ParseTests(unittest.TestCase):
    def test_parse_ok(self):
        self.assertEqual(bt.parse_bet("!bet a 500"), ("A", 500))
        self.assertEqual(bt.parse_bet("  !BET b 1,000 "), ("B", 1000))
        self.assertEqual(bt.parse_bet("!bet в 2k"), ("В", 2000))
        self.assertEqual(bt.parse_bet("!bet c 1.5k"), ("C", 1500))
        self.assertEqual(bt.parse_bet("!bet D all"), ("D", -1))
        self.assertEqual(bt.parse_bet("!bet d 1.000.000"), ("D", 1000000))

    def test_parse_bad(self):
        for t in ("!bet", "!bet ab 100", "!bet a", "!bet a 0", "!bet a -5", "!bet 5 100", "hello !bet a 5",
                  "!bet a 12 34", "!betting a 5", "!bet a 5x"):
            self.assertIsNone(bt.parse_bet(t), t)

    def test_looks_like_bet(self):
        self.assertTrue(bt.looks_like_bet("!bet"))
        self.assertTrue(bt.looks_like_bet("!bet zzz"))
        self.assertFalse(bt.looks_like_bet("!betting"))

    def test_validate_input(self):
        self.assertEqual(bt.validate_bet_input("a", "500"), ("A", 500))
        with self.assertRaises(ValueError): bt.validate_bet_input("ab", "5")
        with self.assertRaises(ValueError): bt.validate_bet_input("a", "x")

    def test_format(self):
        self.assertEqual(bt.format_bet("a", 500), "!bet A 500")
        self.assertEqual(bt.format_bet("a", -1), "!bet A all")


class TrackerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.t = bt.BetTracker(Path(self.tmp.name))
    def tearDown(self):
        self.t.close(); self.tmp.cleanup()

    def add(self, user, text, mid, **kw):
        return self.t.ingest(stream_key="kick:x", text=text, username=user, message_id=mid, **kw)

    def test_stats_real(self):
        self.add("u1", "!bet A 100", "1"); self.add("u2", "!bet A 300", "2"); self.add("u3", "!bet B 600", "3")
        self.add("u1", "!bet A 100", "4")          # same user again: counts as bet, not as new user
        s = self.t.stats("kick:x")
        self.assertEqual(s["total_bets"], 4); self.assertEqual(s["total_users"], 3); self.assertEqual(s["total_points"], 1100)
        self.assertEqual(s["top"], "A")
        a = s["letters"][0]
        self.assertEqual((a["letter"], a["bets"], a["users"], a["points"]), ("A", 3, 2, 500))
        self.assertAlmostEqual(a["pct_bets"], 75.0)

    def test_dedupe_and_bot(self):
        self.assertIsNotNone(self.add("u1", "!bet A 5", "1"))
        self.assertIsNone(self.add("u1", "!bet A 5", "1"))           # same message id
        self.assertIsNone(self.add("BOTTLY", "!bet A 5", "2", is_bot=True))
        self.assertIsNone(self.add("u2", "hello", "3"))
        self.assertEqual(self.t.stats("kick:x")["total_bets"], 1)

    def test_demo_isolated(self):
        self.add("u1", "!bet A 5", "1")
        self.add("d1", "!bet Z 9", "1", demo=True)
        self.assertEqual(self.t.stats("kick:x")["top"], "A")
        self.assertEqual(self.t.stats("kick:x", demo=True)["top"], "Z")
        self.t.clear_demo()
        self.assertEqual(self.t.stats("kick:x", demo=True)["total_bets"], 0)
        self.assertEqual(self.t.stats("kick:x")["total_bets"], 1)

    def test_rounds_and_session(self):
        self.t.sync_session("kick:x", "s1")
        self.add("u1", "!bet A 5", "1")
        self.assertFalse(self.t.sync_session("kick:x", "s1"))
        self.assertTrue(self.t.sync_session("kick:x", "s2"))        # new live session -> new round
        self.assertEqual(self.t.stats("kick:x")["total_bets"], 0)
        self.add("u1", "!bet B 5", "2")
        self.assertEqual(self.t.stats("kick:x")["top"], "B")
        self.assertEqual(self.t.stats("kick:x", round_id=1)["top"], "A")   # history kept
        self.t.new_round("kick:x")
        self.assertEqual(self.t.stats("kick:x")["total_bets"], 0)

    def test_all_in_counts_bet_not_points(self):
        self.add("u1", "!bet A all", "1")
        s = self.t.stats("kick:x")
        self.assertEqual((s["total_bets"], s["total_points"], s["letters"][0]["all_in"]), (1, 0, 1))


class CommandTests(unittest.TestCase):
    def test_command_word(self):
        self.assertEqual(bt.command_word("!Bet A 5"), "!bet")
        self.assertEqual(bt.command_word("hello"), "")
        self.assertEqual(bt.command_word("! x"), "")

    def test_merge(self):
        s = bt.merge_commands([], "!hello world"); self.assertEqual(s, ["!hello"])
        s = bt.merge_commands(s, "!hello again"); self.assertEqual(s, ["!hello"])
        s = bt.merge_commands(s, "!bet A 5"); self.assertEqual(s, ["!hello"])     # builtin not duplicated


if __name__ == "__main__":
    unittest.main()
