"""In-app stream player (Qt WebEngine) + a look that matches the stream's platform.

QtWebEngine must be imported BEFORE the QApplication is created -> ui.py imports this module at
the top.  If WebEngine is missing, the pane shows a friendly fallback with an "open in browser" button.
"""
from __future__ import annotations

import webbrowser

from PySide6.QtCore import QPointF, QRectF, Qt, QUrl
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import QLabel, QPushButton, QStackedLayout, QVBoxLayout, QWidget

from stream_embed import build_target

try:                                                           # pip: PySide6 (full) includes PySide6-Addons
    from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile, QWebEngineSettings
    from PySide6.QtWebEngineWidgets import QWebEngineView
    WEB_OK = True
except Exception:                                              # pragma: no cover - depends on the install
    WEB_OK = False

THEMES = {
    "kick":    {"name": "Kick",    "accent": "#53fc18", "accent_dk": "#2c8a0d", "on_accent": "#06130a",
                "bg1": "#0b120b", "bg2": "#070b07", "panel": "#0e170e", "line": "#1f3a1a", "chat": "#080d08"},
    "youtube": {"name": "YouTube", "accent": "#ff3b47", "accent_dk": "#b3121c", "on_accent": "#ffffff",
                "bg1": "#150b0c", "bg2": "#0d0708", "panel": "#1a0f10", "line": "#3d1c20", "chat": "#0c0708"},
}


def theme_for(platform: str) -> dict:
    return THEMES.get(platform, THEMES["kick"])


def platform_badge(platform: str, size: int = 46) -> QPixmap:
    """Small logo-like badge: green K for Kick, red rounded play button for YouTube."""
    scale = 2
    pm = QPixmap(size * scale, size * scale)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    t = theme_for(platform)
    s = size * scale
    p.setPen(Qt.NoPen)
    if platform == "youtube":
        p.setBrush(QColor(t["accent"]))
        p.drawRoundedRect(QRectF(s * 0.04, s * 0.2, s * 0.92, s * 0.6), s * 0.18, s * 0.18)
        tri = QPainterPath()
        tri.moveTo(QPointF(s * 0.41, s * 0.36))
        tri.lineTo(QPointF(s * 0.41, s * 0.64))
        tri.lineTo(QPointF(s * 0.66, s * 0.5))
        tri.closeSubpath()
        p.setBrush(QColor("#ffffff"))
        p.drawPath(tri)
    else:
        p.setBrush(QColor(t["accent"]))
        p.drawRoundedRect(QRectF(s * 0.06, s * 0.06, s * 0.88, s * 0.88), s * 0.22, s * 0.22)
        f = QFont("Segoe UI")
        f.setBold(True)
        f.setPixelSize(int(s * 0.62))
        p.setFont(f)
        p.setPen(QColor(t["on_accent"]))
        p.drawText(QRectF(0, 0, s, s * 0.96), Qt.AlignCenter, "K")
    p.end()
    pm.setDevicePixelRatio(scale)
    return pm


def stream_dialog_qss(platform: str) -> str:
    """Stylesheet of the stream window - everything is tinted with the platform's accent colour."""
    t = theme_for(platform)
    a, dk, on = t["accent"], t["accent_dk"], t["on_accent"]
    return f"""
QDialog#StreamDlg{{background:qlineargradient(x1:0,y1:0,x2:0,y2:1,stop:0 {t['bg1']},stop:1 {t['bg2']});}}
QFrame#SDHeader{{background:qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 {t['panel']},stop:1 {t['bg2']});border:1px solid {t['line']};border-bottom:2px solid {a};border-radius:18px;}}
QLabel#SDName{{font-size:17pt;font-weight:850;color:#ffffff;}}
QLabel#SDSub{{color:#9aa9a7;font-size:9.5pt;}}
QLabel#SDPill{{border-radius:11px;padding:3px 12px;font-size:9pt;font-weight:850;background:rgba(255,255,255,7%);border:1px solid rgba(255,255,255,18%);color:#cfdad8;}}
QLabel#SDPill[live="true"]{{background:{a};border:1px solid {a};color:{on};}}
QPushButton#SDBtn{{background:{t['panel']};border:1px solid {t['line']};border-radius:17px;padding:0 16px;min-height:34px;font-weight:750;color:#e7f2ef;}}
QPushButton#SDBtn:hover{{border-color:{a};color:#ffffff;}}
QPushButton#SDBtn:checked{{background:{a};border-color:{a};color:{on};}}
QFrame#SDPlayer{{background:#000000;border:1px solid {t['line']};border-radius:16px;}}
QLabel#SDMsg{{color:#aebbb8;font-size:11pt;}}
QFrame#SDSide{{background:{t['panel']};border:1px solid {t['line']};border-radius:16px;}}
QTabWidget#SDTabs::pane{{border:0;background:transparent;}}
QTabBar::tab{{background:transparent;color:#8fa09d;padding:9px 18px;margin-right:4px;border:0;border-bottom:2px solid transparent;font-weight:800;}}
QTabBar::tab:selected{{color:#ffffff;border-bottom:2px solid {a};}}
QTabBar::tab:hover{{color:#ffffff;}}
QDialog#StreamDlg QTextBrowser#StreamChat{{background:{t['chat']};border:1px solid {t['line']};border-radius:14px;}}
QDialog#StreamDlg QLineEdit,QDialog#StreamDlg QComboBox{{background:{t['chat']};border:1px solid {t['line']};border-radius:14px;padding:9px 12px;}}
QDialog#StreamDlg QLineEdit:focus,QDialog#StreamDlg QComboBox:focus{{border:1px solid {a};}}
QDialog#StreamDlg QPushButton#Primary{{background:qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 {dk},stop:1 {a});border:1px solid {a};color:{on};border-radius:18px;min-height:36px;padding:0 20px;font-weight:850;}}
QDialog#StreamDlg QPushButton#Primary:hover{{border-color:#ffffff;}}
QSplitter::handle{{background:transparent;}}
"""


class PlayerPane(QWidget):
    """The video area of the stream window."""

    def __init__(self, platform: str):
        super().__init__()
        self.platform = platform
        self.theme = theme_for(platform)
        self._target = None
        self.view = None
        self._fullscreen_owner = None
        self.stack = QStackedLayout(self)
        self.stack.setContentsMargins(0, 0, 0, 0)

        self.msg_page = QWidget()
        ml = QVBoxLayout(self.msg_page)
        ml.setAlignment(Qt.AlignCenter)
        ml.setSpacing(14)
        self.badge = QLabel()
        self.badge.setPixmap(platform_badge(platform, 72))
        self.badge.setAlignment(Qt.AlignCenter)
        self.msg = QLabel("")
        self.msg.setObjectName("SDMsg")
        self.msg.setAlignment(Qt.AlignCenter)
        self.msg.setWordWrap(True)
        self.open_btn = QPushButton(f"Отвори в {self.theme['name']}")
        self.open_btn.setObjectName("SDBtn")
        self.open_btn.setCursor(Qt.PointingHandCursor)
        self.open_btn.clicked.connect(self.open_external)
        for w in (self.badge, self.msg):
            ml.addWidget(w)
        ml.addWidget(self.open_btn, 0, Qt.AlignCenter)
        self.stack.addWidget(self.msg_page)

        if WEB_OK:
            self.view = QWebEngineView()
            self.view.setStyleSheet("background:#000000;")
            self._profile = self._shared_profile()
            self.view.setPage(QWebEnginePage(self._profile, self.view))
            st = self.view.settings()
            st.setAttribute(QWebEngineSettings.WebAttribute.PlaybackRequiresUserGesture, False)
            st.setAttribute(QWebEngineSettings.WebAttribute.FullScreenSupportEnabled, True)
            self.view.page().fullScreenRequested.connect(self._on_fullscreen)
            self.stack.addWidget(self.view)
        else:
            self.show_message("Вграденият плеър не е наличен (липсва QtWebEngine).\n"
                              "Инсталирай пълния пакет:  pip install PySide6", fallback=True)

    # one profile for the whole app -> cookies (e.g. a YouTube/Kick login) survive between windows
    _PROFILE = None

    @classmethod
    def _shared_profile(cls):
        if cls._PROFILE is None:
            prof = QWebEngineProfile("StreamActivityBot")
            prof.setPersistentCookiesPolicy(QWebEngineProfile.PersistentCookiesPolicy.ForcePersistentCookies)
            cls._PROFILE = prof
        return cls._PROFILE

    def show_message(self, text: str, fallback: bool = False) -> None:
        self.msg.setText(text)
        self.stack.setCurrentWidget(self.msg_page)

    def load(self, url: str, *, channel_id: str = "", stream_url: str = "", session_id: str = "") -> None:
        self._target = build_target(self.platform, url, channel_id=channel_id, stream_url=stream_url,
                                    session_id=session_id)
        if not WEB_OK or self.view is None:
            return
        t = self._target
        if t["mode"] == "url":
            self.view.setUrl(QUrl(t["src"]))
            self.stack.setCurrentWidget(self.view)
        elif t["mode"] == "html":
            self.view.setHtml(t["html"], QUrl(t["base"]))
            self.stack.setCurrentWidget(self.view)
        else:
            self.show_message("Този канал не може да се вгради.\nОтвори го в браузъра.")

    def reload(self) -> None:
        if self._target and WEB_OK and self.view is not None and self._target["mode"] in ("url", "html"):
            t = self._target
            if t["mode"] == "url":
                self.view.setUrl(QUrl(t["src"]))
            else:
                self.view.setHtml(t["html"], QUrl(t["base"]))

    def open_external(self, _=False) -> None:
        if self._target and self._target.get("external"):
            webbrowser.open(self._target["external"])

    def stop(self) -> None:
        """Silence the player (called when the window closes)."""
        if WEB_OK and self.view is not None:
            try:
                self.view.setUrl(QUrl("about:blank"))
            except RuntimeError:
                pass

    def _on_fullscreen(self, request) -> None:
        request.accept()
        win = self.window()
        if request.toggleOn():
            win.showFullScreen()
        else:
            win.showNormal()
