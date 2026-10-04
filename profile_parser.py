"""Parsing of chat-bot replies to !points and !time, plus matching them to your own accounts.

Examples of replies this module understands (the leading "@BOTTLY:" is optional):

    @desperbg има <брой> точки!
    @desperbg Твоето ниво 0 — имаш 13h 58m гледане.

Pure functions only (no I/O) so they are easy to test.
"""
from __future__ import annotations

import re
from typing import Optional

# --------------------------------------------------------------- names ------
_MENTION_RE = re.compile(r"@([\w][\w.\-]*)", re.UNICODE)
_TOKEN_RE = re.compile(r"[\w][\w.\-]*", re.UNICODE)


def norm_name(name: str) -> str:
    """Compare-friendly form of a nickname: case-insensitive, ignores '@', '_', '-', '.', spaces.

    Kick turns '_' into '-' in slugs, so 'My_Name', 'my-name' and '@MyName' all match.
    """
    return re.sub(r"[\W_]+", "", (name or "").casefold(), flags=re.UNICODE)


def extract_mentions(text: str) -> list[str]:
    """Every @name in the text, in order (trailing '.' / '-' removed)."""
    out = []
    for m in _MENTION_RE.finditer(text or ""):
        n = m.group(1).rstrip(".-")
        if n:
            out.append(n)
    return out


def match_account(text: str, accounts, *, reply_to: str = "", sender: str = ""):
    """Return the first of *accounts* this bot reply is addressed to, or None.

    Priority: 1) the author of the message that was replied to (Kick reply metadata),
    2) @mentions in the text (the normal "@BOTTLY: @desperbg ..." case),
    3) a bare nickname used as a word (e.g. "desperbg има <брой> точки").
    The sender (the bot itself) is never treated as the target.
    """
    index: dict[str, object] = {}
    for a in accounts:
        names = [a.display_name, getattr(a, "username", "")] + list(getattr(a, "aliases", None) or [])
        for n in names:
            key = norm_name(n)
            if key and key not in index:
                index[key] = a
    if not index:
        return None
    skip = norm_name(sender)

    def lookup(name):
        key = norm_name(name)
        if key and key != skip:
            return index.get(key)
        return None

    if reply_to:
        hit = lookup(reply_to)
        if hit:
            return hit
    for name in extract_mentions(text):
        hit = lookup(name)
        if hit:
            return hit
    for tok in _TOKEN_RE.findall(text or ""):
        hit = lookup(tok.rstrip(".-"))
        if hit:
            return hit
    return None


# -------------------------------------------------------------- points ------
_POINT_WORD = r"(?:точки|точка|точк\w*|points?|pts)"
_NUM = r"(\d[\d\s.,'’]*)"
_POINTS_AFTER = re.compile(_NUM + r"\s*" + _POINT_WORD + r"(?!\w)", re.IGNORECASE | re.UNICODE)
_POINTS_BEFORE = re.compile(r"(?<!\w)" + _POINT_WORD + r"\s*[:=\-]?\s*" + _NUM, re.IGNORECASE | re.UNICODE)


def _digits(raw: str) -> Optional[int]:
    """'6826' / '6 826' / '6,826' / '6.826' -> 6826.  '12.5' / '12,5' (decimal) -> 12."""
    raw = (raw or "").strip().strip(".,'’ ")
    if not re.search(r"\d", raw):
        return None
    m = re.fullmatch(r"(\d+)[.,](\d{1,2})", raw)          # a decimal fraction, not a thousands group
    if m:
        return int(m.group(1))
    d = re.sub(r"\D", "", raw)
    return int(d) if d else None


def parse_points(text: str) -> Optional[int]:
    """Extract the actual points value from the reply; the number is never hard-coded.

    Examples: ``има 17 точки`` -> 17 and ``има 12 500 точки`` -> 12500.
    """
    text = text or ""
    m = _POINTS_AFTER.search(text)
    if m:
        v = _digits(m.group(1))
        if v is not None:
            return v
    m = _POINTS_BEFORE.search(text)
    if m:
        return _digits(m.group(1))
    return None


# ---------------------------------------------------------------- time ------
_LEVEL_RE = re.compile(r"(?:ниво|level|lvl)\s*(?:е|is)?\s*[:#=]?\s*(\d+)", re.IGNORECASE | re.UNICODE)
_UNIT_RE = re.compile(
    r"(\d+)\s*(дни|ден|дн|д|days?|d|часа|час|ч|hours?|hrs?|h|минути|минута|мин|м|minutes?|mins?|m|"
    r"секунди|секунда|сек|с|seconds?|secs?|s)(?![\w])",
    re.IGNORECASE | re.UNICODE)
_CLOCK_RE = re.compile(r"(?<!\d)(\d{1,3}):(\d{2})(?::(\d{2}))?(?!\d)")
_UNIT_SECONDS = (("д", 86400), ("d", 86400), ("ч", 3600), ("h", 3600),
                 ("м", 60), ("m", 60), ("с", 1), ("s", 1))


def _unit_seconds(unit: str) -> int:
    u = unit.casefold()
    for prefix, secs in _UNIT_SECONDS:
        if u.startswith(prefix):
            return secs
    return 0


def format_watch(seconds: int) -> str:
    """3 123 456 -> '36d 3h 17m'; 0 -> '0m'."""
    seconds = max(0, int(seconds))
    d, rem = divmod(seconds, 86400)
    h, rem = divmod(rem, 3600)
    m = rem // 60
    parts = []
    if d:
        parts.append(f"{d}d")
    if h:
        parts.append(f"{h}h")
    if m or not parts:
        parts.append(f"{m}m")
    return " ".join(parts)


def parse_time(text: str) -> Optional[dict]:
    """'... Твоето ниво 0 — имаш 13h 58m гледане.' -> {'level': 0, 'seconds': 50280, 'text': '13h 58m'}.

    Returns None when neither a watch duration nor a level can be found.
    """
    text = text or ""
    level = None
    m = _LEVEL_RE.search(text)
    rest = text
    if m:
        level = int(m.group(1))
        rest = text[:m.start()] + " " + text[m.end():]     # the level digits are not a duration
    seconds = None
    units = _UNIT_RE.findall(rest)
    if units:
        seconds = sum(int(n) * _unit_seconds(u) for n, u in units)
    else:
        c = _CLOCK_RE.search(rest)
        if c:
            a, b, s = int(c.group(1)), int(c.group(2)), c.group(3)
            seconds = a * 3600 + b * 60 + (int(s) if s else 0)
    if seconds is None and level is None:
        return None
    return {"level": level, "seconds": seconds,
            "text": format_watch(seconds) if seconds is not None else ""}


# ------------------------------------------------------------- dispatch -----
_TIME_HINT = re.compile(r"ниво|level|lvl|гледа\w*|watch\w*|time|viewed|\bhours?\b|\bчас\w*", re.IGNORECASE | re.UNICODE)
_POINTS_HINT = re.compile(r"точк|points?|pts", re.IGNORECASE | re.UNICODE)


def classify_reply(text: str) -> Optional[tuple[str, object]]:
    """Return ('points', int) or ('time', dict) or None for an arbitrary chat line."""
    text = text or ""
    if _POINTS_HINT.search(text):
        v = parse_points(text)
        if v is not None:
            return "points", v
    if _TIME_HINT.search(text):
        t = parse_time(text)
        if t is not None and (t["seconds"] is not None or t["level"] is not None):
            return "time", t
    return None


# ------------------------------------------------- structured response parsers
_BOT_SKIP = {"bottly"}
_LEADING_NAME_RE = re.compile(
    r"^\s*@?([\w][\w.\-]*)\s*[,:]?\s+(?:има|имаш|е\s+гледал\w*|гледа\w*|has|have|watched|is|ти)\b",
    re.IGNORECASE | re.UNICODE)


def extract_target_username(text: str, skip: tuple = ()) -> Optional[str]:
    """The viewer a bot reply is addressed to: first @mention that is not the bot itself,
    otherwise the leading nickname (``desperbg има 17 точки``).  None if unknown."""
    skip_keys = {norm_name(s) for s in tuple(skip) + tuple(_BOT_SKIP)} - {""}
    for name in extract_mentions(text):
        if norm_name(name) not in skip_keys:
            return name
    m = _LEADING_NAME_RE.match(text or "")
    if m and norm_name(m.group(1)) not in skip_keys:
        return m.group(1)
    return None


def parse_points_response(text: str, skip: tuple = ()) -> Optional[dict]:
    """``@username има 6826 точки!`` -> ``{"username": "username", "points": 6826}``.

    ``username`` is None when the reply does not name anybody.  Returns None when the text
    contains no points value - nothing is ever invented.
    """
    if not _POINTS_HINT.search(text or ""):
        return None
    value = parse_points(text)
    if value is None:
        return None
    return {"username": extract_target_username(text, skip), "points": value}


def parse_watch_time_response(text: str, skip: tuple = ()) -> Optional[dict]:
    """``@username е гледал 2 часа и 35 минути`` -> ``{"username": "username", "watch_seconds": 9300}``.

    Returns None when no duration can be found (a level-only reply is not a watch time).
    """
    if not _TIME_HINT.search(text or ""):
        return None
    parsed = parse_time(text)
    if parsed is None or parsed["seconds"] is None:
        return None
    return {"username": extract_target_username(text, skip), "watch_seconds": int(parsed["seconds"])}
