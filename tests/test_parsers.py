import unittest
import tests.helpers  # noqa: F401
import profile_parser as pp


class PointsParserTests(unittest.TestCase):
    def test_basic(self):
        self.assertEqual(pp.parse_points_response("@username има 6826 точки!"),
                         {"username": "username", "points": 6826})

    def test_variants(self):
        self.assertEqual(pp.parse_points_response("@BOTTLY: @desperbg има 12 500 точки")["points"], 12500)
        self.assertEqual(pp.parse_points_response("desperbg има 17 точки")["username"], "desperbg")
        self.assertEqual(pp.parse_points_response("Имаш 1,234 points")["points"], 1234)
        self.assertEqual(pp.parse_points_response("Points: 99")["points"], 99)
        self.assertIsNone(pp.parse_points_response("Имаш 1,234 points")["username"])

    def test_invalid_returns_none(self):
        for t in ("", "здравей", "има много точки", "@x е гледал 2 часа"):
            self.assertIsNone(pp.parse_points_response(t), t)


class WatchTimeParserTests(unittest.TestCase):
    def test_basic(self):
        self.assertEqual(pp.parse_watch_time_response("@username е гледал 2 часа и 35 минути"),
                         {"username": "username", "watch_seconds": 9300})

    def test_variants(self):
        self.assertEqual(pp.parse_watch_time_response("@u Твоето ниво 0 — имаш 13h 58m гледане.")["watch_seconds"], 50280)
        self.assertEqual(pp.parse_watch_time_response("watched 1 day 3 hours")["watch_seconds"], 97200)
        self.assertEqual(pp.parse_watch_time_response("@u гледа 1:05:30 time")["watch_seconds"], 3930)

    def test_invalid_returns_none(self):
        for t in ("", "здравей", "ниво 3", "@u има 5 точки"):
            self.assertIsNone(pp.parse_watch_time_response(t), t)


if __name__ == "__main__":
    unittest.main()
