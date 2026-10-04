import json
import tempfile
import unittest
from pathlib import Path

import tests.helpers  # noqa: F401
from dash_layout import (COLUMNS, DEFAULT_ORDER, MIN_SPAN, NAV_DEFAULT, TYPES, LayoutModel, NavModel,
                         grid_positions)


class LayoutModelTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.path = self.dir / "ui_layout.json"
        self.m = LayoutModel(self.path)

    def types(self):
        return [i["type"] for i in self.m.items]

    def test_defaults(self):
        self.assertEqual(self.types(), DEFAULT_ORDER)

    def test_move_down_and_up(self):
        a, b, c = (self.m.items[i]["id"] for i in (1, 2, 3))      # streams, accounts, values
        self.m.move(a, c)                                         # down -> after target
        self.assertEqual(self.types()[1:4], ["accounts", "values", "streams"])
        self.m.move(a, b)                                         # up -> before target
        self.assertEqual(self.types()[1:4], ["streams", "accounts", "values"])

    def test_move_to_end_and_unknown(self):
        first = self.m.items[0]["id"]
        self.assertTrue(self.m.move(first, None))
        self.assertEqual(self.types()[-1], "metrics")
        self.assertFalse(self.m.move("nope", None))
        self.assertFalse(self.m.move(first, first))

    def test_add_remove_and_single_instance_rule(self):
        self.assertIsNone(self.m.add("streams"))                  # already present
        top = self.m.add("top_points")
        self.assertIsNotNone(top)
        self.assertEqual(self.types()[-1], "top_points")
        self.assertTrue(self.m.remove(top["id"]))
        self.assertNotIn("top_points", self.types())
        self.assertIsNotNone(self.m.add("top_points"))            # can be added again
        # multi-instance types
        self.assertIsNotNone(self.m.add("chat", {"channel_id": "a"}))
        self.assertIsNotNone(self.m.add("chat", {"channel_id": "b"}))
        self.assertEqual(self.types().count("chat"), 2)
        self.assertIn("chat", self.m.available_types())

    def test_removed_default_can_come_back(self):
        sid = self.m.items[1]["id"]
        self.m.remove(sid)
        self.assertIn("streams", self.m.available_types())

    def test_collapse_and_span_toggle(self):
        i = self.m.items[1]["id"]
        self.assertTrue(self.m.toggle_collapsed(i))
        self.assertFalse(self.m.toggle_collapsed(i))
        self.assertEqual(self.m.toggle_span(i), COLUMNS)
        self.assertEqual(self.m.toggle_span(i), COLUMNS // 2)

    def test_persistence_roundtrip(self):
        i = self.m.items[2]["id"]
        self.m.toggle_collapsed(i)
        chat = self.m.add("chat", {"channel_id": "xyz"})
        self.m.move(chat["id"], self.m.items[0]["id"])
        again = LayoutModel(self.path)
        self.assertEqual([x["id"] for x in again.items], [x["id"] for x in self.m.items])
        self.assertTrue(again.get(i)["collapsed"])
        self.assertEqual(again.get(chat["id"])["params"], {"channel_id": "xyz"})

    def test_corrupt_or_hostile_file_falls_back(self):
        self.path.write_text("{not json", encoding="utf-8")
        self.assertEqual([i["type"] for i in LayoutModel(self.path).items], DEFAULT_ORDER)
        self.path.write_text(json.dumps({"items": [
            {"id": "a", "type": "streams"}, {"id": "a", "type": "accounts"},       # duplicate id
            {"id": "b", "type": "streams"},                                         # duplicate single type
            {"id": "c", "type": "evil"}, {"id": 5, "type": "log"},                  # unknown / bad id
            {"id": "d", "type": "chat", "span": 9, "params": "x"}]}), encoding="utf-8")
        got = LayoutModel(self.path).items
        self.assertEqual([(i["id"], i["type"]) for i in got], [("a", "streams"), ("d", "chat")])
        self.assertEqual(got[1]["span"], COLUMNS)
        self.assertEqual(got[1]["params"], {})

    def test_all_hidden_is_allowed_and_saved(self):
        for i in list(self.m.items):
            self.m.remove(i["id"])
        self.assertEqual(LayoutModel(self.path).items, [])

    def test_reset(self):
        self.m.remove(self.m.items[0]["id"])
        self.m.reset()
        self.assertEqual(self.types(), DEFAULT_ORDER)


    def test_free_resize_is_clamped_and_saved(self):
        i = self.m.items[1]["id"]
        self.assertTrue(self.m.set_size(i, span=8, height=500))
        self.assertEqual((self.m.get(i)["span"], self.m.get(i)["height"]), (8, 500))
        self.m.set_size(i, span=1, height=10)                     # too small -> clamped
        self.assertEqual(self.m.get(i)["span"], MIN_SPAN)
        self.assertEqual(self.m.get(i)["height"], 120)
        self.m.set_size(i, span=99, height=0)                     # too wide -> full row, 0 = auto
        self.assertEqual((self.m.get(i)["span"], self.m.get(i)["height"]), (COLUMNS, 0))
        self.assertFalse(self.m.set_size("nope", span=6))
        self.m.set_size(i, span=7, height=333)
        again = LayoutModel(self.path)
        self.assertEqual((again.get(i)["span"], again.get(i)["height"]), (7, 333))

    def test_v1_file_is_migrated(self):
        self.path.write_text(json.dumps({"version": 1, "items": [
            {"id": "m", "type": "metrics", "span": 2, "params": {"cards": ["streams", "time"]}},
            {"id": "s", "type": "streams", "span": 1}]}), encoding="utf-8")
        got = LayoutModel(self.path)
        self.assertEqual([(i["span"], i["height"]) for i in got.items], [(12, 0), (6, 0)])
        self.assertEqual(got.get("m")["params"]["cards"], ["streams", "time", "top_points", "top_time"])
        self.assertEqual(json.loads(self.path.read_text(encoding="utf-8"))["version"], 2)
        self.assertEqual(LayoutModel(self.path).get("m")["params"]["cards"],
                         ["streams", "time", "top_points", "top_time"])      # migrated only once


class GridTests(unittest.TestCase):
    def test_pairs_and_full_rows(self):
        items = [{"id": "m", "span": 12}, {"id": "a", "span": 6}, {"id": "b", "span": 6},
                 {"id": "c", "span": 6}, {"id": "w", "span": 12}, {"id": "d", "span": 6}]
        self.assertEqual(grid_positions(items), [
            ("m", 0, 0, 12), ("a", 1, 0, 6), ("b", 1, 6, 6), ("c", 2, 0, 6), ("w", 3, 0, 12), ("d", 4, 0, 6)])

    def test_uneven_widths_pack_into_rows(self):
        items = [{"id": "a", "span": 8}, {"id": "b", "span": 4}, {"id": "c", "span": 5}, {"id": "d", "span": 8}]
        self.assertEqual(grid_positions(items), [
            ("a", 0, 0, 8), ("b", 0, 8, 4), ("c", 1, 0, 5), ("d", 2, 0, 8)])      # d does not fit next to c

    def test_every_type_has_a_title(self):
        for t, (title, span, h, multi) in TYPES.items():
            self.assertTrue(title and MIN_SPAN <= span <= COLUMNS and h >= 0, t)


class NavModelTests(unittest.TestCase):
    def setUp(self):
        self.path = Path(tempfile.mkdtemp()) / "nav_layout.json"
        self.n = NavModel(self.path)

    def test_defaults(self):
        self.assertEqual(self.n.order, NAV_DEFAULT)
        self.assertEqual(self.n.hidden, set())

    def test_move_hide_width_and_persistence(self):
        self.assertTrue(self.n.move("help", "home"))                         # up -> before target
        self.assertEqual(self.n.order.index("help") + 1, self.n.order.index("home"))
        self.assertTrue(self.n.move("sec_main", None))                       # to the end
        self.assertEqual(self.n.order[-1], "sec_main")
        self.assertFalse(self.n.move("nope", None))
        self.assertFalse(self.n.move("help", "help"))
        self.assertTrue(self.n.set_hidden("send", True))
        self.assertNotIn("send", self.n.visible_order())
        self.assertFalse(self.n.set_hidden("home", True))                    # the way home stays
        self.assertEqual(self.n.set_width(9999), 360)
        self.assertEqual(self.n.set_width(10), 200)
        self.n.set_width(300)
        again = NavModel(self.path)
        self.assertEqual((again.order, again.hidden, again.width), (self.n.order, self.n.hidden, 300))
        self.n.reset()
        self.assertEqual((self.n.order, self.n.hidden, self.n.width), (NAV_DEFAULT, set(), 240))

    def test_hostile_file(self):
        self.path.write_text(json.dumps({"order": ["help", "evil", "help", "home"], "hidden": ["home", "x", "send"],
                                         "width": "abc"}), encoding="utf-8")
        n = NavModel(self.path)
        self.assertEqual(n.order[:2], ["help", "home"])
        self.assertEqual(sorted(n.order), sorted(NAV_DEFAULT))               # missing items come back
        self.assertEqual(n.hidden, {"send"})
        self.assertEqual(n.width, 240)
        self.path.write_text("{bad", encoding="utf-8")
        self.assertEqual(NavModel(self.path).order, NAV_DEFAULT)


if __name__ == "__main__":
    unittest.main()
