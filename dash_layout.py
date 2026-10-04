"""Dashboard layout model (no Qt): which containers exist, their order, width and collapsed state.

The UI (ui.py) renders this model and edits it through drag & drop / buttons; everything that
can be wrong (ordering, spans, persistence, defaults) lives here so it is unit-tested.

Layout file: <app_dir>/ui_layout.json
    {"version": 2, "items": [{"id": "c1", "type": "streams", "span": 6, "height": 0,
                              "collapsed": false, "params": {}}]}

Width is measured in 1/12 columns (``span``: 4..12, 6 = half, 12 = full row) and the height in
pixels (``height``: 0 = automatic).  Version-1 files (span 1 = half, 2 = full) are converted on load.

The navigation (sidebar) has its own model, NavModel, saved in <app_dir>/nav_layout.json.
"""
from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Optional

COLUMNS = 12
MIN_SPAN = 4            # narrowest container: a third of the row
MIN_HEIGHT = 120        # smallest manual height in px
MAX_HEIGHT = 2400

# type -> (title, default span in 1/12 columns, min height px, may appear many times)
TYPES: dict[str, tuple[str, int, int, bool]] = {
    "metrics":    ("Статистика",                       12, 0,   False),
    "streams":    ("Стриймове",                        6, 380, False),
    "accounts":   ("Акаунти",                          6, 380, False),
    "values":     ("Последни стойности",               6, 380, False),
    "recent":     ("Последни действия",                6, 380, False),
    "top_points": ("Акаунт с най-много точки",         6, 170, False),
    "top_time":   ("Акаунт с най-много време",         6, 170, False),
    "timers":     ("Таймери на акаунтите",             6, 300, False),
    "chat":       ("Чат на стрийм",                    6, 420, True),
    "send":       ("Изпрати съобщение",                6, 380, True),
    "bets":       ("Залози — статистика",              6, 430, False),
    "log":        ("Системен лог",                     12, 300, False),
}

DEFAULT_ORDER = ["metrics", "streams", "accounts", "values", "recent", "bets"]


def new_item(type_: str, params: Optional[dict] = None) -> dict:
    title, span, _h, _multi = TYPES[type_]
    return {"id": "c" + uuid.uuid4().hex[:8], "type": type_, "span": span, "height": 0,
            "collapsed": False, "params": dict(params or {})}


def clamp_span(value, default: int = 6) -> int:
    try:
        v = int(value)
    except (TypeError, ValueError):
        v = default
    return max(MIN_SPAN, min(COLUMNS, v))


def clamp_height(value) -> int:
    """0 (automatic) or MIN_HEIGHT..MAX_HEIGHT pixels."""
    try:
        v = int(value)
    except (TypeError, ValueError):
        return 0
    if v <= 0:
        return 0
    return max(MIN_HEIGHT, min(MAX_HEIGHT, v))


def default_items() -> list[dict]:
    return [new_item(t) for t in DEFAULT_ORDER]


def grid_positions(items: list[dict], columns: int = COLUMNS) -> list[tuple[str, int, int, int]]:
    """[(id, row, col, colspan)] - items are laid out left to right; a new row starts when the next
    item does not fit in the remaining columns."""
    out, row, col = [], 0, 0
    for it in items:
        span = max(1, min(columns, int(it.get("span", columns // 2))))
        if col + span > columns:
            row, col = row + 1, 0
        out.append((it["id"], row, col, span))
        col += span
        if col >= columns:
            row, col = row + 1, 0
    return out


class LayoutModel:
    def __init__(self, path: Optional[Path] = None):
        self.path = Path(path) if path else None
        self.items: list[dict] = default_items()
        self.edit_mode = False
        if self.path:
            self.load()

    # ---------------------------------------------------------------- lookup
    def get(self, item_id: str) -> Optional[dict]:
        return next((i for i in self.items if i["id"] == item_id), None)

    def index(self, item_id: str) -> int:
        return next((n for n, i in enumerate(self.items) if i["id"] == item_id), -1)

    def available_types(self) -> list[str]:
        """Types that can still be added (single-instance types only once)."""
        present = {i["type"] for i in self.items}
        return [t for t, (_n, _s, _h, multi) in TYPES.items() if multi or t not in present]

    # ---------------------------------------------------------------- edits
    def add(self, type_: str, params: Optional[dict] = None) -> Optional[dict]:
        if type_ not in TYPES or type_ not in self.available_types():
            return None
        it = new_item(type_, params)
        self.items.append(it)
        self.save()
        return it

    def remove(self, item_id: str) -> bool:
        n = self.index(item_id)
        if n < 0:
            return False
        del self.items[n]
        self.save()
        return True

    def move(self, item_id: str, target_id: Optional[str]) -> bool:
        """Drop *item_id* on *target_id*: it takes the target's place (others shift).
        target_id None -> move to the end."""
        src = self.index(item_id)
        if src < 0 or item_id == target_id:
            return False
        item = self.items.pop(src)
        if target_id is None:
            self.items.append(item)
        else:
            dst = self.index(target_id)
            if dst < 0:
                self.items.insert(src, item)
                return False
            # moving down the list -> land AFTER the target, moving up -> BEFORE it
            self.items.insert(dst + 1 if src < dst else dst, item)
        self.save()
        return True

    def toggle_collapsed(self, item_id: str) -> bool:
        it = self.get(item_id)
        if it is None:
            return False
        it["collapsed"] = not it.get("collapsed", False)
        self.save()
        return it["collapsed"]

    def toggle_span(self, item_id: str) -> int:
        """Quick button: full row <-> half row."""
        it = self.get(item_id)
        if it is None:
            return 0
        it["span"] = COLUMNS // 2 if int(it.get("span", COLUMNS)) >= COLUMNS else COLUMNS
        self.save()
        return it["span"]

    def set_size(self, item_id: str, span: Optional[int] = None, height: Optional[int] = None) -> bool:
        """Free resizing: *span* in 1/12 columns (clamped 4..12), *height* in px (0 = automatic)."""
        it = self.get(item_id)
        if it is None:
            return False
        if span is not None:
            it["span"] = clamp_span(span)
        if height is not None:
            it["height"] = clamp_height(height)
        self.save()
        return True

    def set_param(self, item_id: str, key: str, value) -> None:
        it = self.get(item_id)
        if it is not None:
            it.setdefault("params", {})[key] = value
            self.save()

    def reset(self) -> None:
        self.items = default_items()
        self.save()

    # ---------------------------------------------------------------- persistence
    def save(self) -> None:
        if not self.path:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"version": 2, "items": self.items}, ensure_ascii=False, indent=1),
                           encoding="utf-8")
            tmp.replace(self.path)
        except OSError:
            pass

    def load(self) -> None:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            old = int(data.get("version", 1)) < 2          # v1: span 1/2 columns -> v2: 6/12
            items, seen, ids = [], set(), set()
            for raw in data.get("items", []):
                t = raw.get("type")
                if t not in TYPES or not isinstance(raw.get("id"), str) or raw["id"] in ids:
                    continue
                if not TYPES[t][3] and t in seen:
                    continue                      # single-instance type listed twice
                seen.add(t)
                ids.add(raw["id"])
                params = raw.get("params") if isinstance(raw.get("params"), dict) else {}
                if old:
                    span = COLUMNS if int(raw.get("span", 1)) >= 2 else COLUMNS // 2
                    if t == "metrics" and isinstance(params.get("cards"), list):
                        params = dict(params)      # the two "top account" cards are new in v2
                        params["cards"] = list(params["cards"]) + [
                            c for c in ("top_points", "top_time") if c not in params["cards"]]
                else:
                    span = clamp_span(raw.get("span"), TYPES[t][1])
                items.append({"id": raw["id"], "type": t, "span": span,
                              "height": clamp_height(raw.get("height", 0)),
                              "collapsed": bool(raw.get("collapsed", False)),
                              "params": params})
            if items or data.get("items") == []:
                self.items = items
            if old:
                self.save()
        except (OSError, ValueError, AttributeError, TypeError):
            self.items = default_items()          # missing / corrupt file -> defaults


# ===================================================================== navigation
# id -> kind.  "page" items open a page, "section" items are the small group captions.
NAV_DEFAULT = ["sec_main", "home", "accounts", "channels",
               "sec_activity", "send", "profiles", "activity",
               "sec_info", "help"]
NAV_SECTIONS = {"sec_main": "СТРИЙМ БОТ", "sec_activity": "ДЕЙНОСТ", "sec_info": "ИНФОРМАЦИЯ"}
NAV_PAGES = ["home", "accounts", "channels", "send", "profiles", "activity", "help"]
NAV_LOCKED = {"home"}                      # can never be hidden (the way back to the dashboard)
NAV_MIN_WIDTH, NAV_MAX_WIDTH, NAV_DEFAULT_WIDTH = 200, 360, 240


def nav_is_section(nav_id: str) -> bool:
    return nav_id in NAV_SECTIONS


class NavModel:
    """Order, visibility and width of the sidebar.  Everything is validated on load."""

    def __init__(self, path: Optional[Path] = None):
        self.path = Path(path) if path else None
        self.order: list[str] = list(NAV_DEFAULT)
        self.hidden: set[str] = set()
        self.width: int = NAV_DEFAULT_WIDTH
        self.edit_mode = False
        if self.path:
            self.load()

    def is_hidden(self, nav_id: str) -> bool:
        return nav_id in self.hidden

    def visible_order(self) -> list[str]:
        return [i for i in self.order if i not in self.hidden]

    def move(self, nav_id: str, target_id: Optional[str]) -> bool:
        """Same rule as LayoutModel.move: down -> after the target, up -> before it, None -> end."""
        src = self.order.index(nav_id) if nav_id in self.order else -1
        if src < 0 or nav_id == target_id:
            return False
        self.order.pop(src)
        if target_id is None:
            self.order.append(nav_id)
        else:
            if target_id not in self.order:
                self.order.insert(src, nav_id)
                return False
            dst = self.order.index(target_id)
            self.order.insert(dst + 1 if src < dst else dst, nav_id)
        self.save()
        return True

    def set_hidden(self, nav_id: str, hidden: bool) -> bool:
        if nav_id not in self.order or nav_id in NAV_LOCKED:
            return False
        if hidden:
            self.hidden.add(nav_id)
        else:
            self.hidden.discard(nav_id)
        self.save()
        return True

    def set_width(self, width: int) -> int:
        try:
            w = int(width)
        except (TypeError, ValueError):
            w = NAV_DEFAULT_WIDTH
        self.width = max(NAV_MIN_WIDTH, min(NAV_MAX_WIDTH, w))
        self.save()
        return self.width

    def reset(self) -> None:
        self.order, self.hidden, self.width = list(NAV_DEFAULT), set(), NAV_DEFAULT_WIDTH
        self.save()

    def save(self) -> None:
        if not self.path:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"version": 1, "order": self.order, "hidden": sorted(self.hidden),
                                       "width": self.width}, ensure_ascii=False, indent=1), encoding="utf-8")
            tmp.replace(self.path)
        except OSError:
            pass

    def load(self) -> None:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            known = set(NAV_DEFAULT)
            order = []
            for i in data.get("order", []):
                if i in known and i not in order:
                    order.append(i)
            order += [i for i in NAV_DEFAULT if i not in order]       # anything missing comes back
            self.order = order
            self.hidden = {i for i in data.get("hidden", []) if i in known and i not in NAV_LOCKED}
            try:
                self.width = max(NAV_MIN_WIDTH, min(NAV_MAX_WIDTH, int(data.get("width", NAV_DEFAULT_WIDTH))))
            except (TypeError, ValueError):
                self.width = NAV_DEFAULT_WIDTH
        except (OSError, ValueError, AttributeError, TypeError):
            self.order, self.hidden, self.width = list(NAV_DEFAULT), set(), NAV_DEFAULT_WIDTH
