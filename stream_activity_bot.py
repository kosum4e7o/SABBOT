"""STREAM ACTIVITY BOT - entry point (version: see version.py)."""
from __future__ import annotations

import logging
import logging.handlers
import sys

from storage import app_dir


def _setup_logging() -> None:
    logger = logging.getLogger("stream_activity_bot")
    logger.setLevel(logging.INFO)
    handler = logging.handlers.RotatingFileHandler(app_dir() / "stream_activity_bot.log",
                                                   maxBytes=512_000, backupCount=2, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
    logger.addHandler(handler)


def _selftest(out_path: str) -> int:
    """`Stream_Activity_Bot.exe --selftest <file>`: used by CI to prove that every runtime
    dependency (python312.dll, PySide6, shiboken6, Qt, websocket-client, sqlite3, ssl) really
    loads from the installed folder.  Writes the result to *out_path* (the EXE is windowed)."""
    lines, ok = [], True
    try:
        from version import __version__
        lines.append(f"version={__version__}")
        import PySide6
        import shiboken6
        from PySide6 import QtCore, QtGui, QtWidgets, QtSvg  # noqa: F401
        lines.append(f"PySide6={PySide6.__version__} Qt={QtCore.qVersion()}")
        import sqlite3, ssl, websocket  # noqa: F401
        lines.append(f"websocket-client={websocket.__version__}")
        import bet_tracker, stream_embed, chat_features, dash_layout, engine, kick_chat, kick_events, profile_parser, ui, ui_bets, ui_player  # noqa: F401
        import ui_player
        lines.append(f"webengine={ui_player.WEB_OK}")
        lines.append("modules=OK")
        lines.append(f"python={sys.version.split()[0]} frozen={getattr(sys, 'frozen', False)}")
    except Exception as e:  # report every failure instead of crashing silently
        ok = False
        lines.append(f"ERROR {type(e).__name__}: {e}")
    lines.append("SELFTEST_OK" if ok else "SELFTEST_FAILED")
    try:
        with open(out_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
    except OSError:
        pass
    return 0 if ok else 1


def main() -> int:
    if len(sys.argv) >= 3 and sys.argv[1] == "--selftest":
        return _selftest(sys.argv[2])
    if sys.version_info < (3, 9):
        print("Python 3.9 or newer is required.")
        return 1
    try:
        import PySide6  # noqa: F401
    except ImportError:
        print("PySide6 is missing. Run: python -m pip install -r requirements.txt")
        return 1
    _setup_logging()
    from ui import main as gui_main
    gui_main()
    return 0


if __name__ == "__main__":
    sys.exit(main())
