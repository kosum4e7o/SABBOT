from __future__ import annotations

import html, logging, queue, threading, time, webbrowser
from collections import deque
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, Signal, QObject, QByteArray, QSize, QRect, QRectF, QSettings, QVariantAnimation, QPoint, QMimeData, QEvent
from PySide6.QtGui import QFont, QIcon, QPixmap, QPainter, QColor, QDrag, QPen
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QDialog, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QPushButton, QLineEdit, QTextEdit, QPlainTextEdit, QCheckBox, QSpinBox,
    QDoubleSpinBox, QComboBox, QListWidget, QListWidgetItem, QTabWidget, QFrame,
    QMessageBox, QFileDialog, QSizePolicy, QScrollArea, QFormLayout, QDialogButtonBox, QTableWidget, QMenu,
    QTableWidgetItem, QHeaderView, QStackedWidget, QButtonGroup, QAbstractItemView, QGraphicsDropShadowEffect,
    QTextBrowser, QGraphicsOpacityEffect, QSplitter
)

import accounts as acc
import help_texts as ht
from engine import Engine
from kick_api import KickAPI
from models import Account, Channel, PLATFORM_KICK, PLATFORM_YT, PLATFORM_LABEL, new_id, parse_channel_url, validate_messages, default_display_name, detect_platform
from oauth import OAuthError, TokenManager
from storage import Config, TokenStore, app_dir
from chat_storage import ChatStore
from dash_layout import (LayoutModel, TYPES, grid_positions, COLUMNS, MIN_SPAN, MIN_HEIGHT, NavModel, NAV_SECTIONS,
                         NAV_LOCKED, NAV_MIN_WIDTH, NAV_MAX_WIDTH, NAV_DEFAULT_WIDTH, nav_is_section)
from ui_player import PlayerPane, platform_badge, stream_dialog_qss, theme_for
from ui_bets import BetPanel, CommandBar, ModeSwitch, ProfileBetBox, animate_reflow
from youtube_api import YouTubeAPI

log = logging.getLogger("stream_activity_bot")

BG="#0f2125"; PANEL="#132a2f"; CARD="#17303a"; INPUT="#10252a"; BORDER="#2a464c"
FG="#eaf3f1"; MUTED="#7f918f"; GREEN="#2ee08a"; GREEN_D="#16a864"; RED="#ff6b7a"; AMBER="#f2b84b"; BLUE="#4f8cff"; BLUE_L="#7fb0ff"

ICON_PATHS = {
    "home": '<path d="M3 11.5 12 4l9 7.5"/><path d="M5.5 10v10h4.5v-6h4v6h4.5V10"/>',
    "user": '<circle cx="12" cy="8" r="3.6"/><path d="M5 20.5v-1.2A5.3 5.3 0 0 1 10.3 14h3.4a5.3 5.3 0 0 1 5.3 5.3v1.2"/>',
    "stream": '<path d="M5.5 8.5a6.5 6.5 0 0 0 0 7M2.5 5.5a10.5 10.5 0 0 0 0 13M18.5 8.5a6.5 6.5 0 0 1 0 7M21.5 5.5a10.5 10.5 0 0 1 0 13"/><circle cx="12" cy="12" r="2.6"/>',
    "message": '<path d="M20.5 11.5a8 8 0 0 1-8 8H8l-4.5 2 1.3-4.3A8 8 0 1 1 20.5 11.5z"/><path d="M8.5 11.5h7M8.5 14.5h4"/>',
    "trophy": '<path d="M8 4h8v5.2a4 4 0 0 1-8 0V4z"/><path d="M8 6H4v1.2A3.8 3.8 0 0 0 7.8 11M16 6h4v1.2A3.8 3.8 0 0 1 16.2 11M12 13.2V17M8.5 20.5h7M10 17h4v3.5h-4z"/>',
    "star": '<path d="M12 3.5l2.6 5.4 5.9.8-4.3 4.2 1 5.9L12 17l-5.2 2.8 1-5.9L3.5 9.7l5.9-.8L12 3.5z"/>',
    "activity": '<path d="M3 12h4l3-8 4 16 3-8h4"/>',
    "help": '<circle cx="12" cy="12" r="9"/><path d="M9.4 9.4a2.7 2.7 0 1 1 3.8 2.5c-.8.4-1.2 1-1.2 1.8M12 17v.4"/>',
    "api": '<circle cx="7.5" cy="16.5" r="4"/><path d="M10.4 13.6 20 4M16 8l3 3M13.5 10.5l2 2"/>',
    "logs": '<path d="M6 3h8.5L19 7.5V21H6z"/><path d="M9 12h7M9 16h7M9 8h3"/>',
    "error": '<path d="M12 3.5 21.5 20h-19L12 3.5z"/><path d="M12 10v4.5M12 17.4v.4"/>',
    "plus": '<path d="M12 5v14M5 12h14"/>',
    "save": '<path d="M5 3h11l4 4v14H5z"/><path d="M8 3v5h7V3M8 21v-7h8v7"/>',
    "play": '<path d="M7 4.5v15l12.5-7.5z"/>',
    "stop": '<rect x="6" y="6" width="12" height="12" rx="2.5"/>',
    "edit": '<path d="M4 20h4L19 9l-4-4L4 16z"/><path d="M13.5 6.5l4 4"/>',
    "login": '<path d="M14 4h5v16h-5"/><path d="M4 12h10M10 8l4 4-4 4"/>',
    "close": '<path d="M6 6l12 12M18 6L6 18"/>',
    "chev_left": '<path d="M13.5 6 7.5 12l6 6M19 6l-6 6 6 6"/>',
    "chev_right": '<path d="M10.5 6l6 6-6 6M5 6l6 6-6 6"/>',
    "send": '<path d="M21 3 10.5 13.5M21 3l-6.5 18-4-7.5L3 9.5z"/>',
    "clock": '<circle cx="12" cy="12" r="9"/><path d="M12 7v5.2l3.4 2"/>',
    "info": '<circle cx="12" cy="12" r="9"/><path d="M12 11v5.5M12 7.6v.4"/>',
    "power": '<path d="M12 3.5v8.5"/><path d="M6.8 6.9a8 8 0 1 0 10.4 0"/>',
    "flask": '<path d="M9.5 3.5h5M10 3.5v6L4.8 18.2A2 2 0 0 0 6.5 21h11a2 2 0 0 0 1.7-2.8L14 9.5v-6"/><path d="M7.6 15h8.8"/>',
    "live": '<circle cx="12" cy="12" r="2.4"/><path d="M7.8 7.8a6 6 0 0 0 0 8.4M16.2 7.8a6 6 0 0 1 0 8.4M4.9 4.9a10 10 0 0 0 0 14.2M19.1 4.9a10 10 0 0 1 0 14.2"/>',
    "plus_bold": '<path d="M12 5.5v13M5.5 12h13"/>',
    "chev_up": '<path d="m6 15 6-6 6 6"/>',
    "chev_down": '<path d="m6 9 6 6 6-6"/>',
    "bolt": '<path d="M13 3 5 13.5h6L10 21l8-10.5h-6z"/>',
    "sliders": '<path d="M4 7h9M19 7h1M4 17h1M11 17h9"/><circle cx="16" cy="7" r="2.4"/><circle cx="8" cy="17" r="2.4"/>',
    "check": '<path d="M5 12.5l4.5 4.5L19 7"/>',
    "eye": '<path d="M2.5 12S6 5.5 12 5.5 21.5 12 21.5 12 18 18.5 12 18.5 2.5 12 2.5 12z"/><circle cx="12" cy="12" r="2.8"/>',
    "eye_off": '<path d="M3.5 4.5l17 15"/><path d="M9.6 5.8A9.6 9.6 0 0 1 12 5.5c6 0 9.5 6.5 9.5 6.5a16 16 0 0 1-3 3.7M6.2 7.8A15.6 15.6 0 0 0 2.5 12S6 18.5 12 18.5a9.6 9.6 0 0 0 4-.9"/>',
}
# Closed shapes get a soft translucent fill -> "duotone" look. Open/arc icons stay line-only.
DUOTONE = {"user","clock","info","help","star","home","message","save","logs","bolt","flask","error"}
ICON_ALIAS = {"account": "user", "profile": "star", "points": "trophy"}

def _svg(name, color, sw=1.9):
    key = ICON_ALIAS.get(name, name)
    body = ICON_PATHS.get(key, ICON_PATHS["plus"])
    fill = f'fill="{color}" fill-opacity="0.18"' if key in DUOTONE else 'fill="none"'
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" {fill} stroke="{color}" '
            f'stroke-width="{sw}" stroke-linecap="round" stroke-linejoin="round">{body}</svg>').encode("utf-8")

def _pix(name, size, color, sw=1.9):
    from PySide6.QtSvg import QSvgRenderer
    scale = 2
    pm = QPixmap(size * scale, size * scale); pm.fill(Qt.transparent)
    p = QPainter(pm); p.setRenderHint(QPainter.Antialiasing)
    QSvgRenderer(QByteArray(_svg(name, color, sw))).render(p, QRectF(0, 0, size * scale, size * scale)); p.end()
    pm.setDevicePixelRatio(scale)
    return pm

def svg_icon(name, size=22, color="#ffffff", sw=1.9):
    ic = QIcon(); ic.addPixmap(_pix(name, size, color, sw)); return ic

def nav_icon(name, size=22):
    """Every menu icon has its own accent colour; the selected page switches to white."""
    c = NAV_ACCENT.get(name, "#8ea3a0"); ic = QIcon()
    ic.addPixmap(_pix(name, size, c), QIcon.Normal, QIcon.Off)
    ic.addPixmap(_pix(name, size, "#ffffff"), QIcon.Normal, QIcon.On)
    return ic

def tint(color, pct):
    q = QColor(color); return f"rgba({q.red()},{q.green()},{q.blue()},{int(pct)}%)"

def level_text(v):
    """Ниво 0 е валидно ниво - показваме '—' само когато нивото още не е известно."""
    return "—" if v is None or v=="" else str(v)

NAV_ACCENT = {"check":"#2ee08a","eye":"#8ea3a0","eye_off":"#8ea3a0","home":"#2ee08a","user":"#6aa2ff","stream":"#ff8f99","message":"#b69bff","trophy":"#f2b84b","activity":"#2ee0d0","help":"#7fc8ff","api":"#6aa2ff","bolt":"#f2b84b","sliders":"#b69bff","logs":"#7fc8ff","error":"#ff8f99"}
METRIC_COLOR = {"stream":"#ff8f99","account":"#6aa2ff","clock":"#f2b84b","points":"#ffd166"}

def nav_icon_fancy(name, size=24):
    c=NAV_ACCENT.get(name,"#8ea3a0"); ic=QIcon()
    ic.addPixmap(_pix(name,size,c),QIcon.Normal,QIcon.Off)
    ic.addPixmap(_pix(name,size,"#ffffff"),QIcon.Normal,QIcon.On)
    return ic

def fmt_duration(sec):
    sec=int(max(0,sec or 0)); d,rem=divmod(sec,86400); h,rem=divmod(rem,3600); m=rem//60
    if d: return f"{d}d {h}h"
    if h: return f"{h}h {m}m"
    return f"{m}m"

def fmt_clock(sec):
    sec=max(0,int(sec or 0)); h,rem=divmod(sec,3600); m,s=divmod(rem,60)
    return f"{h:02d}:{m:02d}:{s:02d}"

def fmt_ago(ts):
    if not ts: return "Никога"
    d=max(0,int(time.time()-float(ts)))
    if d < 60: return f"преди {d} сек"
    if d < 3600: return f"преди {d//60} мин"
    if d < 86400: return f"преди {d//3600} ч"
    return f"преди {d//86400} дни"

def center(widget, parent=None):
    widget.adjustSize(); size=widget.sizeHint();
    if parent:
        geo=parent.frameGeometry(); x=geo.x()+(geo.width()-size.width())//2; y=geo.y()+(geo.height()-size.height())//2
    else:
        screen=QApplication.primaryScreen().availableGeometry(); x=screen.x()+(screen.width()-size.width())//2; y=screen.y()+(screen.height()-size.height())//2
    widget.move(max(0,x),max(0,y))

def tip(w,text,color=None):
    c=color or GREEN
    w.setToolTip(text)
    w.setStyleSheet(f"QToolTip{{background:{c};color:#04100b;border:1px solid {c};padding:6px 12px;font-weight:700;font-size:10pt;}}")

def icon_button(icon,tip_text,obj="IconBtn",size=40,isize=20,color="#cfe0dd",tipc=None):
    b=QPushButton(); b.setObjectName(obj); b.setIcon(svg_icon(icon,isize,color,3.0 if obj=="AddBtn" else 1.9)); b.setIconSize(QSize(isize,isize))
    b.setFixedSize(size,size); tip(b,tip_text,tipc); b.setCursor(Qt.PointingHandCursor)
    if obj=="AddBtn":
        b.setStyleSheet(f"QPushButton#AddBtn{{border-radius:{size//2}px;}}")        # a true circle at every size
        fx=QGraphicsDropShadowEffect(b); fx.setBlurRadius(18); fx.setOffset(0,0); fx.setColor(QColor(46,224,138,150)); b.setGraphicsEffect(fx)
    return b

class WorkerSignals(QObject):
    done=Signal(object); error=Signal(str)

class Card(QFrame):
    def __init__(self, title="", subtitle="", parent=None):
        super().__init__(parent); self.setObjectName("Card")
        l=QVBoxLayout(self); l.setContentsMargins(18,16,18,16); l.setSpacing(10)
        self.header=None
        if title:
            row=QHBoxLayout(); row.setSpacing(8); t=QLabel(title); t.setObjectName("CardTitle"); row.addWidget(t)
            if subtitle: s=QLabel(subtitle); s.setObjectName("Muted"); row.addWidget(s)
            row.addStretch(); l.addLayout(row); self.header=row
        self.body=l
    def add_action(self,btn):
        if self.header is not None: self.header.addWidget(btn,0,Qt.AlignRight|Qt.AlignVCenter)

class SetupDialog(QDialog):
    def __init__(self, parent, config, tokens):
        super().__init__(parent); self.config=config; self.tokens=tokens; self.setWindowTitle("API и OAuth настройка"); self.setMinimumWidth(700); self.setModal(True)
        s=config.settings; root=QVBoxLayout(self); root.setContentsMargins(26,24,26,24); root.setSpacing(14)
        title=QLabel("Свържи платформите"); title.setObjectName("DialogTitle"); root.addWidget(title)
        root.addWidget(QLabel("Тази настройка е нужна само веднъж. Попълни само платформите, които ще използваш."))
        form=QFormLayout(); form.setHorizontalSpacing(18); form.setVerticalSpacing(10)
        self.kid=QLineEdit(s.kick_client_id); self.ksecret=QLineEdit(); self.ksecret.setEchoMode(QLineEdit.Password)
        self.port=QSpinBox(); self.port.setRange(1024,65535); self.port.setValue(int(s.kick_redirect_port))
        self.google=QLineEdit(s.google_client_json); browse=QPushButton("Избери JSON"); browse.clicked.connect(self.browse)
        gbox=QHBoxLayout(); gbox.addWidget(self.google); gbox.addWidget(browse)
        form.addRow("Kick Client ID",self.kid); form.addRow("Kick Client Secret",self.ksecret); form.addRow("Kick Redirect Port",self.port); form.addRow("Google OAuth JSON",gbox)
        root.addLayout(form)
        uri=QLabel(); uri.setObjectName("AccentText"); self.uri=uri; root.addWidget(uri); self.port.valueChanged.connect(self.update_uri); self.update_uri()
        sec=QGridLayout(); self.kicksec=QSpinBox(); self.kicksec.setRange(15,600); self.kicksec.setValue(s.live_check_kick_sec)
        self.ytsec=QSpinBox(); self.ytsec.setRange(30,1800); self.ytsec.setValue(s.live_check_youtube_sec)
        self.first=QSpinBox(); self.first.setRange(0,600); self.first.setValue(s.first_message_delay_sec)
        self.search=QSpinBox(); self.search.setRange(0,240); self.search.setValue(s.youtube_search_fallback_minutes)
        self.stag_min=QDoubleSpinBox(); self.stag_min.setRange(0,60); self.stag_min.setDecimals(1); self.stag_min.setSingleStep(0.5); self.stag_min.setValue(s.account_stagger_min_minutes)
        self.stag_max=QDoubleSpinBox(); self.stag_max.setRange(0,120); self.stag_max.setDecimals(1); self.stag_max.setSingleStep(0.5); self.stag_max.setValue(s.account_stagger_max_minutes)
        for r,(lab,w) in enumerate((("Kick LIVE check (sec)",self.kicksec),("YouTube LIVE check (sec)",self.ytsec),("Първо съобщение след LIVE (sec)",self.first),("YouTube fallback search (min)",self.search),("Разминаване между акаунтите – от (мин)",self.stag_min),("Разминаване между акаунтите – до (мин)",self.stag_max))): sec.addWidget(QLabel(lab),r,0); sec.addWidget(w,r,1)
        root.addLayout(sec)
        self.err=QLabel(); self.err.setObjectName("Error"); root.addWidget(self.err)
        buttons=QDialogButtonBox(QDialogButtonBox.Save|QDialogButtonBox.Cancel); buttons.accepted.connect(self.save); buttons.rejected.connect(self.reject); root.addWidget(buttons)
    def browse(self):
        p,_=QFileDialog.getOpenFileName(self,"Google OAuth client JSON","","JSON (*.json)");
        if p:self.google.setText(p)
    def update_uri(self): self.uri.setText(f"Kick Redirect URI: http://localhost:{self.port.value()}/callback")
    def save(self):
        g=self.google.text().strip()
        if g and acc.load_google_client(g) is None: self.err.setText("Google OAuth JSON файлът не е валиден Desktop client."); return
        s=self.config.settings; s.kick_client_id=self.kid.text().strip(); s.kick_redirect_port=self.port.value(); s.google_client_json=g
        s.live_check_kick_sec=self.kicksec.value(); s.live_check_youtube_sec=self.ytsec.value(); s.first_message_delay_sec=self.first.value(); s.youtube_search_fallback_minutes=self.search.value()
        s.account_stagger_min_minutes=self.stag_min.value(); s.account_stagger_max_minutes=max(self.stag_min.value(),self.stag_max.value())
        secret=self.ksecret.text().strip()
        if secret:self.tokens.set_secret("kick_client_secret",secret)
        self.config.save(); self.accept()

class ChannelDialog(QDialog):
    def __init__(self,parent,config,channel=None):
        super().__init__(parent); self.config=config; self.editing=channel; self.setWindowTitle("Редакция на стрийм" if channel else "Добави стрийм"); self.setMinimumWidth(680)
        root=QVBoxLayout(self); root.setContentsMargins(26,24,26,24); root.setSpacing(12)
        title=QLabel(self.windowTitle()); title.setObjectName("DialogTitle"); root.addWidget(title)
        root.addWidget(QLabel("Постави Kick или YouTube линк. Платформата се разпознава автоматично."))
        self.url=QLineEdit(channel.url if channel else ""); self.detect=QLabel(); self.detect.setObjectName("AccentText"); self.url.textChanged.connect(self.detect_platform); root.addWidget(self.url); root.addWidget(self.detect)
        form=QFormLayout(); self.interval=QSpinBox(); self.interval.setRange(1,1440); self.interval.setValue(int(channel.interval_minutes) if channel else 29)
        self.jitter=QSpinBox(); self.jitter.setRange(0,1439); self.jitter.setValue(int(channel.jitter_minutes) if channel else 0); form.addRow("Интервал (минути)",self.interval); form.addRow("Случайно ± (минути)",self.jitter)
        self.chatroom=QLineEdit((channel.extra.get("chatroom_id") or "") if channel else ""); form.addRow("Kick Chatroom ID (по желание)",self.chatroom); root.addLayout(form)
        root.addWidget(QLabel("Акаунти за този стрийм")); self.accounts_box=QVBoxLayout(); root.addLayout(self.accounts_box)
        self.msg=QPlainTextEdit(); self.msg.setPlaceholderText("По едно автоматично съобщение на ред…");
        if channel:self.msg.setPlainText("\n".join(channel.messages))
        root.addWidget(self.msg)
        self.enabled=QCheckBox("Стриймът е активен"); self.enabled.setChecked(channel.enabled if channel else True); root.addWidget(self.enabled)
        self.err=QLabel(); self.err.setObjectName("Error"); root.addWidget(self.err)
        bb=QDialogButtonBox(QDialogButtonBox.Save|QDialogButtonBox.Cancel); bb.accepted.connect(self.save); bb.rejected.connect(self.reject); root.addWidget(bb)
        self.detect_platform(); center(self,parent)
    def detect_platform(self):
        p=detect_platform(self.url.text()); self.detect.setText(f"● {PLATFORM_LABEL[p]} разпознат" if p else "Въведи валиден Kick или YouTube линк")
        while self.accounts_box.count():
            item=self.accounts_box.takeAt(0); w=item.widget();
            if w:w.deleteLater()
        existing=set(self.editing.connected_account_ids()) if self.editing else set();
        if p:
            for a in self.config.accounts_for(p):
                cb=QCheckBox(f"{a.display_name}  ·  {PLATFORM_LABEL[p]}"); cb.setProperty("account_id",a.id); cb.setChecked(a.id in existing); self.accounts_box.addWidget(cb)
    def save(self):
        try: platform,ref=parse_channel_url(None,self.url.text().strip()); msgs=validate_messages(platform,self.msg.toPlainText().splitlines())
        except ValueError as e:self.err.setText(str(e));return
        if self.jitter.value()>=self.interval.value(): self.err.setText("Случайният диапазон трябва да е по-малък от интервала.");return
        ids=[]
        for i in range(self.accounts_box.count()):
            w=self.accounts_box.itemAt(i).widget()
            if isinstance(w,QCheckBox) and w.isChecked():ids.append(w.property("account_id"))
        ch=self.editing.copy() if self.editing else Channel(id=new_id(),platform=platform,url=self.url.text().strip())
        if ch.platform!=platform or ch.url!=self.url.text().strip(): ch.channel_id="";ch.display_name="";ch.extra={}
        ch.platform=platform;ch.url=self.url.text().strip();ch.display_name=ch.display_name or default_display_name(ref);ch.account_ids=ids;ch.account_id=ids[0] if ids else "";ch.messages=msgs;ch.interval_minutes=self.interval.value();ch.jitter_minutes=self.jitter.value();ch.enabled=self.enabled.isChecked()
        if platform==PLATFORM_KICK and self.chatroom.text().strip():
            if not self.chatroom.text().strip().isdigit():self.err.setText("Kick Chatroom ID трябва да е число.");return
            ch.extra["chatroom_id"]=self.chatroom.text().strip()
        else:ch.extra.pop("chatroom_id",None)
        self.result=ch;self.accept()

class TextDialog(QDialog):
    def __init__(self,parent,title,text):
        super().__init__(parent);self.setWindowTitle(title);self.resize(760,520);l=QVBoxLayout(self);t=QPlainTextEdit();t.setReadOnly(True);t.setPlainText(text);l.addWidget(t);b=QPushButton("Затвори");b.clicked.connect(self.accept);l.addWidget(b);center(self,parent)

def collect_chat_rows(engine, config, chat_store, ch, rt):
    """[(ts, who, text, is_self, is_bot, message_id)] for one stream: live feed first, DB as fallback."""
    key = engine.stream_key(ch); rows = []
    for item in list(getattr(engine, "live_chat_messages", {}).get(key, []))[-120:]:
        rows.append((float(item.get("ts") or time.time()), str(item.get("display_name") or item.get("username") or "unknown"),
                     str(item.get("text") or ""), bool(item.get("is_self")), bool(item.get("is_bot")), str(item.get("message_id") or "")))
    if not rows:
        target_ids = {str(ch.channel_id or ""), str(rt.channel_id or "")}
        target_names = {str(ch.display_name or "").casefold(), str(ch.url or "").casefold(), str(rt.streamer or "").casefold()}
        bots = [str(b).casefold() for b in (config.settings.response_bot_names or [])] or ["bottly"]
        for row in reversed(chat_store.query(limit=120)):
            if str(row[5] or "") in target_ids or str(row[2] or "").casefold() in target_names:
                who = str(row[3]); rows.append((time.time(), who, str(row[4]), False, who.casefold() in bots, ""))
    return rows

def chat_html(rows):
    """Styled chat: [time] coloured username (+BOT / ТИ badge) and the message."""
    if not rows:
        return '<p style="color:#7f918f;">Чатът все още няма получени съобщения.</p>'
    parts = []
    for ts, who, msg, is_self, is_bot, _ in rows:
        color = "#f2b84b" if is_bot else ("#2ee08a" if is_self else "#6aa2ff")
        badge = (' <span style="color:#04100b;background:#f2b84b;font-size:8pt;font-weight:800;">&nbsp;BOT&nbsp;</span>' if is_bot else
                 (' <span style="color:#04100b;background:#2ee08a;font-size:8pt;font-weight:800;">&nbsp;ТИ&nbsp;</span>' if is_self else ""))
        parts.append('<div style="margin:3px 0;"><span style="color:#5f7573;font-size:9pt;">[%s]</span> '
                     '<b style="color:%s;">%s</b>%s<span style="color:#7f918f;">:</span> '
                     '<span style="color:#e7f2ef;">%s</span></div>' % (time.strftime("%H:%M:%S", time.localtime(ts)), color, html.escape(who), badge, html.escape(msg)))
    return "".join(parts)

def set_chat_html(browser, rows):
    bar = browser.verticalScrollBar(); at_end = bar.value() >= bar.maximum() - 4
    browser.setHtml(chat_html(rows))
    if at_end: bar.setValue(bar.maximum())

class StreamDetailsDialog(QDialog):
    """Stream window: the stream plays inside the app (player on the left), chat + info on the right.
    The whole window is tinted with the stream's platform (Kick = green, YouTube = red)."""
    def __init__(self, parent, ch, engine, config, chat_store):
        super().__init__(parent); self.ch=ch; self.engine=engine; self.config=config; self.chat_store=chat_store
        self._was_live=None; self._chat_sig=None; self._closed=False
        self.setObjectName("StreamDlg"); self.setProperty("platform",ch.platform); self.setStyleSheet(stream_dialog_qss(ch.platform))
        self.setWindowTitle(f"{PLATFORM_LABEL[ch.platform]} · {ch.display_name or ch.url}"); self.resize(1380,820); self.setMinimumSize(980,620)
        root=QVBoxLayout(self); root.setContentsMargins(18,16,18,16); root.setSpacing(12)

        # --- header: platform badge, name, LIVE pill, viewers, buttons
        head=QFrame(); head.setObjectName("SDHeader"); hl=QHBoxLayout(head); hl.setContentsMargins(16,12,16,12); hl.setSpacing(14)
        badge=QLabel(); badge.setPixmap(platform_badge(ch.platform,46)); badge.setFixedSize(50,50); badge.setAlignment(Qt.AlignCenter); hl.addWidget(badge)
        col=QVBoxLayout(); col.setSpacing(2)
        self.title=QLabel(ch.display_name or ch.url); self.title.setObjectName("SDName"); self.sub=QLabel(""); self.sub.setObjectName("SDSub"); self.sub.setWordWrap(True)
        col.addWidget(self.title); col.addWidget(self.sub); hl.addLayout(col,1)
        self.live_pill=QLabel("OFFLINE"); self.live_pill.setObjectName("SDPill"); self.view_pill=QLabel(""); self.view_pill.setObjectName("SDPill")
        hl.addWidget(self.live_pill); hl.addWidget(self.view_pill)
        def hbtn(text,fn,checkable=False):
            b=QPushButton(text); b.setObjectName("SDBtn"); b.setCursor(Qt.PointingHandCursor); b.setCheckable(checkable); b.clicked.connect(fn); hl.addWidget(b); return b
        hbtn("Презареди",lambda _=False:self.player.reload())
        hbtn(f"Отвори в {theme_for(ch.platform)['name']}",lambda _=False:self.player.open_external())
        self.side_btn=hbtn("Чат / Инфо",self.toggle_side,True); self.side_btn.setChecked(True)
        root.addWidget(head)

        # --- body: player | side panel
        self.split=QSplitter(Qt.Horizontal); self.split.setChildrenCollapsible(False); self.split.setHandleWidth(10)
        pframe=QFrame(); pframe.setObjectName("SDPlayer"); pl=QVBoxLayout(pframe); pl.setContentsMargins(2,2,2,2)
        self.player=PlayerPane(ch.platform); pl.addWidget(self.player); self.split.addWidget(pframe)
        self.side=QFrame(); self.side.setObjectName("SDSide"); sl=QVBoxLayout(self.side); sl.setContentsMargins(12,8,12,12); sl.setSpacing(8)
        self.tabs=QTabWidget(); self.tabs.setObjectName("SDTabs"); sl.addWidget(self.tabs,1)
        chat_page=QWidget(); cl=QVBoxLayout(chat_page); cl.setContentsMargins(0,8,0,0); cl.setSpacing(8)
        self.chat=QTextBrowser(); self.chat.setObjectName("StreamChat"); self.chat.setOpenLinks(False); cl.addWidget(self.chat,1)
        self.account=QComboBox(); cl.addWidget(self.account)
        row=QHBoxLayout(); row.setSpacing(8); self.text=QLineEdit(); self.text.setPlaceholderText("Съобщение...  (! за команда)"); self.text.returnPressed.connect(self.send_message)
        self.cmd_bar=CommandBar(); self.cmd_bar.picked.connect(self._pick_command); self.text.textEdited.connect(lambda t:self.cmd_bar.update_for(t,self.engine.saved_commands()))
        self.send=QPushButton("ИЗПРАТИ"); self.send.setObjectName("Primary"); self.send.setCursor(Qt.PointingHandCursor); self.send.clicked.connect(self.send_message)
        row.addWidget(self.text,1); row.addWidget(self.send,0); cl.addWidget(self.cmd_bar); cl.addLayout(row)
        self.tabs.addTab(chat_page,"Чат")
        info_area=QScrollArea(); info_area.setWidgetResizable(True); info_area.setFrameShape(QFrame.NoFrame); info_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.info=QLabel(); self.info.setWordWrap(True); self.info.setObjectName("StreamMeta"); self.info.setTextInteractionFlags(Qt.TextSelectableByMouse); self.info.setAlignment(Qt.AlignTop|Qt.AlignLeft)
        info_area.setWidget(self.info); self.tabs.addTab(info_area,"Инфо")
        self.split.addWidget(self.side); self.split.setStretchFactor(0,1); self.split.setStretchFactor(1,0); self.split.setSizes([960,400])
        root.addWidget(self.split,1)

        self.refresh(); self.load_player()
        self.timer=QTimer(self); self.timer.timeout.connect(self.refresh_dynamic); self.timer.start(1000)
    # ------------------------------------------------------------ player
    def load_player(self):
        rt=self.engine.get_runtime(self.ch.id)
        self.player.load(self.ch.url,channel_id=rt.channel_id or self.ch.channel_id or "",stream_url=rt.stream_url or "",session_id=rt.session_id or "")
    def toggle_side(self,on=False):
        self.side.setVisible(bool(on))
    def _pick_command(self,cmd):
        parts=self.text.text().split(None,1); rest=parts[1] if len(parts)>1 else ""
        self.text.setText(cmd+" "+rest); self.text.setFocus(); self.text.setCursorPosition(len(self.text.text()))
        self.cmd_bar.update_for(self.text.text(),self.engine.saved_commands())
    def done(self,r):
        if not self._closed:
            self._closed=True; self.timer.stop(); self.player.stop()      # silence the stream
        super().done(r)
    # ------------------------------------------------------------ data
    def refresh_dynamic(self): self.refresh(info_only=True)
    def refresh(self, info_only=False):
        rt=self.engine.get_runtime(self.ch.id)
        status="🟢 LIVE" if rt.live else ("🟡 UNKNOWN / CONNECTION ERROR" if rt.connection_status not in ("Offline","Connected") else "⚫ OFFLINE")
        duration=fmt_clock(time.time()-rt.started_at) if rt.live and rt.started_at else "Unavailable"
        chat_key=self.engine.stream_key(self.ch)
        chat_state=self.engine.chat_status.get(chat_key, "not started")
        self.live_pill.setText(f"● LIVE  {duration}" if rt.live else "OFFLINE"); self.live_pill.setProperty("live",bool(rt.live)); self.live_pill.style().unpolish(self.live_pill); self.live_pill.style().polish(self.live_pill)
        self.view_pill.setText(f"👁 {rt.viewers:,}" if rt.viewers is not None else ""); self.view_pill.setVisible(rt.viewers is not None)
        self.sub.setText("  ·  ".join(x for x in (rt.title,rt.category or rt.game) if x) or PLATFORM_LABEL[self.ch.platform])
        if rt.streamer: self.title.setText(rt.streamer)
        if self._was_live is False and rt.live: self.load_player()         # went LIVE while the window is open
        self._was_live=bool(rt.live)
        vals=[f"{status}   {duration}",f"Streamer: {rt.streamer or 'Unavailable'}",f"Username/channel: {self.ch.display_name or self.ch.url}",
              f"Platform: {PLATFORM_LABEL[self.ch.platform]}",f"Channel ID: {rt.channel_id or self.ch.channel_id or 'Unavailable'}",
              f"Start time: {time.strftime('%Y-%m-%d %H:%M:%S',time.localtime(rt.started_at)) if rt.started_at else 'Unavailable'}",
              f"Title: {rt.title or 'Unavailable'}",f"Category/Game: {rt.category or rt.game or 'Unavailable'}",
              f"Viewers: {rt.viewers if rt.viewers is not None else 'Unavailable'}",
              f"Thumbnail: {rt.thumbnail or 'Unavailable'}",f"Stream URL: {rt.stream_url or self.ch.url}",
              f"Connection: {rt.connection_status}",f"Chat: {chat_state}"]
        self.info.setText("\n\n".join(vals))
        if not info_only:
            self.account.clear()
            for a in self.config.accounts:
                if a.id in self.ch.connected_account_ids(): self.account.addItem(a.display_name,a.id)
        self.render_chat(chat_key, rt)
    def render_chat(self, chat_key, rt):
        """Styled chat: coloured username, [time], message; bot (BOTTLY) and own messages stand out."""
        rows=collect_chat_rows(self.engine,self.config,self.chat_store,self.ch,rt)
        sig=(len(rows), rows[-1][5] if rows else "", rows[-1][2] if rows else "")
        if sig==self._chat_sig: return
        self._chat_sig=sig
        set_chat_html(self.chat,rows)
    def send_message(self):
        aid=self.account.currentData(); txt=self.text.text().strip()
        if not aid or not txt: return
        self.send.setEnabled(False)
        def worker():
            r=self.engine.manual_send(self.ch.id,[aid],txt); self.parent().events.put(("stream_send_result",r))
            if r.get("ok"): self.engine.remember_command(txt)
        threading.Thread(target=worker,daemon=True).start()
        self.text.clear(); self.cmd_bar.update_for("",[]); self.send.setEnabled(True)

class AccountProfileDialog(QDialog):
    SRC_LABEL={"chat":"Чат","automatic_send":"Автоматично","manual_send":"Ръчно","bot_response":"BOTTLY"}
    SRC_ICON={"chat":"●","automatic_send":"↗","manual_send":"↗","bot_response":"◆"}
    def __init__(self,parent,account,engine,config,chat_store,tm):
        super().__init__(parent); self.account=account; self.engine=engine; self.config=config; self.chat_store=chat_store; self.tm=tm
        self._hist_sig=None
        self.setWindowTitle(f"Профил · {account.display_name}"); self.resize(920,900)
        root=QVBoxLayout(self); root.setContentsMargins(22,20,22,20); root.setSpacing(14)

        # --- hero: avatar, name, chips, auto-activity switch
        hero=QFrame(); hero.setObjectName("ProfileHero"); hl=QHBoxLayout(hero); hl.setContentsMargins(20,18,20,18); hl.setSpacing(18)
        av=QLabel((account.display_name or "?")[:1].upper()); av.setObjectName("AvatarBig"); av.setAlignment(Qt.AlignCenter); av.setFixedSize(76,76); hl.addWidget(av)
        col=QVBoxLayout(); col.setSpacing(6)
        nm=QLabel(account.display_name); nm.setObjectName("ProfileName"); col.addWidget(nm)
        chips=QHBoxLayout(); chips.setSpacing(8)
        chips.addWidget(self.chip(PLATFORM_LABEL.get(account.platform,account.platform),"green" if account.platform==PLATFORM_KICK else "red"))
        self.conn_chip=self.chip("","green"); chips.addWidget(self.conn_chip)
        if account.username and account.username!=account.display_name: chips.addWidget(self.chip("@"+account.username,"muted"))
        chips.addStretch(); col.addLayout(chips); hl.addLayout(col,1)
        self.auto_btn=QPushButton(); self.auto_btn.setObjectName("AutoSwitch"); self.auto_btn.setCursor(Qt.PointingHandCursor); self.auto_btn.setFixedHeight(44); self.auto_btn.clicked.connect(self.toggle_auto)
        hl.addWidget(self.auto_btn,0,Qt.AlignVCenter); root.addWidget(hero)

        # --- stat tiles
        tiles=QHBoxLayout(); tiles.setSpacing(12)
        self.t_points=self.tile("trophy","#ffd166","ТОЧКИ",tiles); self.t_time=self.tile("clock","#f2b84b","ВРЕМЕ ЗА ГЛЕДАНЕ",tiles)
        self.t_next=self.tile("bolt","#2ee08a","СЛЕДВАЩО СЪОБЩЕНИЕ СЛЕД",tiles); self.t_last=self.tile("activity","#6aa2ff","ПОСЛЕДНА АКТИВНОСТ",tiles)
        root.addLayout(tiles)

        # --- details
        det=QFrame(); det.setObjectName("ProfileDetails"); self.det_grid=QGridLayout(det); self.det_grid.setContentsMargins(18,14,18,14); self.det_grid.setHorizontalSpacing(24); self.det_grid.setVerticalSpacing(8)
        self.det_vals={}
        for i,(key,label) in enumerate((("user","Потребителско име"),("uid","ID на потребителя"),("channels","Свързани стриймове"),("scopes","Права"),("last","Последно съобщение"),("next","Следваща автоматична активност"))):
            k=QLabel(label); k.setObjectName("KVKey"); v=QLabel(); v.setObjectName("KVVal"); v.setWordWrap(True); v.setTextInteractionFlags(Qt.TextSelectableByMouse)
            r,c=divmod(i,2); self.det_grid.addWidget(k,r*2,c); self.det_grid.addWidget(v,r*2+1,c); self.det_vals[key]=v
        root.addWidget(det)
        root.addWidget(ProfileBetBox(engine,config,account))

        # --- history (scrolling)
        h=QLabel("ИСТОРИЯ НА АКТИВНОСТТА"); h.setObjectName("Eyebrow"); root.addWidget(h)
        self.hist_area=QScrollArea(); self.hist_area.setWidgetResizable(True); self.hist_area.setFrameShape(QFrame.NoFrame); self.hist_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.hist_area.setStyleSheet("QScrollArea{background:transparent;border:0;} QScrollArea>QWidget>QWidget{background:transparent;}")
        inner=QWidget(); inner.setObjectName("ScrollInner"); self.hist_lay=QVBoxLayout(inner); self.hist_lay.setContentsMargins(0,0,6,0); self.hist_lay.setSpacing(8)
        self.hist_area.setWidget(inner); root.addWidget(self.hist_area,1)
        self.timer=QTimer(self); self.timer.timeout.connect(self.refresh); self.timer.start(1000); self.refresh()
    @staticmethod
    def chip(text,tone):
        l=QLabel(text); l.setObjectName("Chip"); l.setProperty("tone",tone); return l
    def tile(self,icon,color,title,parent_layout):
        f=QFrame(); f.setObjectName("StatTile"); lay=QHBoxLayout(f); lay.setContentsMargins(14,12,14,12); lay.setSpacing(12)
        ic=QLabel(); ic.setPixmap(_pix(icon,22,color)); ic.setFixedSize(42,42); ic.setAlignment(Qt.AlignCenter)
        ic.setStyleSheet(f"background:{tint(color,14)};border:1px solid {tint(color,45)};border-radius:13px;")
        tx=QVBoxLayout(); tx.setSpacing(0); t=QLabel(title); t.setObjectName("StatLabel"); v=QLabel("—"); v.setObjectName("StatValue"); v.setStyleSheet(f"color:{color};")
        tx.addWidget(t); tx.addWidget(v); lay.addWidget(ic); lay.addLayout(tx,1); parent_layout.addWidget(f,1); return v
    def toggle_auto(self):
        st=self.engine._account_timers.get(self.account.id,{})
        self.engine.set_account_auto(self.account.id, not st.get("enabled",True)); self.refresh()
    def refresh(self):
        a=self.account; st=self.engine._account_timers.get(a.id,{})
        last=st.get("lastMessageAt") or st.get("lastActivityAt"); nxt=st.get("nextActivityAt")
        remain=(float(nxt)-time.time()) if nxt else None
        if st.get("state")=="AWAITING_CHAT": countdown="чака чат събитие"
        elif remain is None: countdown="ГОТОВ"
        else: countdown=fmt_clock(max(0,remain))
        ok=self.tm.has_tokens(a.id)
        self.conn_chip.setText("● Свързан" if ok else "● Изисква вход"); self.conn_chip.setProperty("tone","green" if ok else "amber"); self.conn_chip.style().unpolish(self.conn_chip); self.conn_chip.style().polish(self.conn_chip)
        enabled=st.get("enabled",True)
        self.auto_btn.setText("Автоматична активност: ВКЛ" if enabled else "Автоматична активност: ИЗКЛ"); self.auto_btn.setProperty("on",enabled); self.auto_btn.style().unpolish(self.auto_btn); self.auto_btn.style().polish(self.auto_btn)
        pts=None; secs=0
        for r in self.chat_store.profiles():
            if str(r[3])==str(a.id):
                if r[5] is not None: pts=(pts or 0)+int(r[5])
                secs+=int(r[9] or 0)
        self.t_points.setText(f"{pts:,}" if pts is not None else "—"); self.t_time.setText(fmt_duration(secs) if secs else "—")
        self.t_next.setText(countdown); self.t_last.setText(fmt_ago(last) if last else "—")
        channels=[c.display_name or c.url for c in self.config.channels if a.id in c.connected_account_ids()]
        v=self.det_vals; v["user"].setText(a.username or a.display_name); v["uid"].setText(str(a.external_id or "—"))
        v["channels"].setText(", ".join(channels) if channels else "—"); v["scopes"].setText(", ".join(a.permissions) if a.permissions else "—")
        v["last"].setText(f"{time.strftime('%H:%M:%S',time.localtime(last))}  ({fmt_ago(last)})" if last else "Никога")
        v["next"].setText(time.strftime("%H:%M:%S",time.localtime(nxt)) if nxt else "ГОТОВ")
        rows=self.chat_store.account_activity(a.id,300)
        sig=(len(rows), rows[0][0] if rows else 0)
        if sig==self._hist_sig: return            # rebuild only on change -> the scroll position is kept
        self._hist_sig=sig; keep=self.hist_area.verticalScrollBar().value()
        while self.hist_lay.count():
            it=self.hist_lay.takeAt(0); w=it.widget()
            if w: w.deleteLater()
        if not rows:
            e=QLabel("Още няма записана активност."); e.setObjectName("ActivityEmpty"); self.hist_lay.addWidget(e)
        for r in rows:
            ts,plat,chan,_cid,_sid,msg,src=r[0],r[1],r[2],r[3],r[4],r[5],r[6]
            fr=QFrame(); fr.setObjectName("ActivityRow"); fr.setMinimumHeight(68); rl=QHBoxLayout(fr); rl.setContentsMargins(12,10,12,10); rl.setSpacing(11)
            ic=QLabel(self.SRC_ICON.get(str(src),"●")); ic.setObjectName("ActivityIcon"); ic.setAlignment(Qt.AlignCenter); ic.setFixedSize(34,34); rl.addWidget(ic)
            body=QVBoxLayout(); body.setSpacing(2); top=QHBoxLayout(); top.setSpacing(8)
            top.addWidget(self.chip(self.SRC_LABEL.get(str(src),str(src)),"gold" if src=="bot_response" else "green")); sl=QLabel(f"{chan}  ·  {plat}"); sl.setObjectName("ActivityStream"); top.addWidget(sl); top.addStretch()
            tl=QLabel(f"{time.strftime('%d.%m %H:%M:%S',time.localtime(ts))}  ·  {fmt_ago(ts)}"); tl.setObjectName("ActivityTime"); top.addWidget(tl); body.addLayout(top)
            m=QLabel(str(msg)); m.setObjectName("ActivityMessage"); m.setWordWrap(True); body.addWidget(m); rl.addLayout(body,1); self.hist_lay.addWidget(fr)
        self.hist_lay.addStretch()
        QTimer.singleShot(0,lambda k=keep:self.hist_area.verticalScrollBar().setValue(k))

DRAG_PREFIX = "sab-container:"


class DragHandle(QLabel):
    """The title of a container; in edit mode it can be dragged to move the whole container."""
    def __init__(self, text, box):
        super().__init__(text); self.box = box; self._start = None
    def mousePressEvent(self, ev):
        if ev.button() == Qt.LeftButton: self._start = ev.position().toPoint()
        super().mousePressEvent(ev)
    def mouseReleaseEvent(self, ev):
        self._start = None; super().mouseReleaseEvent(ev)
    def mouseMoveEvent(self, ev):
        if not self.box.edit or self._start is None or not (ev.buttons() & Qt.LeftButton): return
        if (ev.position().toPoint() - self._start).manhattanLength() < QApplication.startDragDistance(): return
        self._start = None
        drag = QDrag(self); mime = QMimeData(); mime.setText(DRAG_PREFIX + self.box.item_id); drag.setMimeData(mime)
        pm = self.box.grab(); pm = pm.scaledToWidth(min(380, pm.width()), Qt.SmoothTransformation)
        drag.setPixmap(pm); drag.setHotSpot(QPoint(24, 14))
        self.box.canvas.dragging = True
        eff = QGraphicsOpacityEffect(self.box); eff.setOpacity(0.35); self.box.setGraphicsEffect(eff)
        try: drag.exec(Qt.MoveAction)
        finally:
            self.box.canvas.dragging = False; self.box.canvas.clear_drop_marks()
            try: self.box.setGraphicsEffect(None)
            except RuntimeError: pass            # the box was already replaced by the animated rebuild


NAV_DRAG = "sab-nav:"


class EdgeGrip(QWidget):
    """Resize handle on a container: right edge ('w'), bottom edge ('h') or the corner ('wh').
    Dragging only moves a green guide frame; the size is applied (and saved) when the mouse is released.
    Double click = automatic height again."""
    TIPS = {"w": "Дръпни, за да промениш ширината", "h": "Дръпни, за да промениш височината (двоен клик = автоматична)",
            "wh": "Дръпни, за да промениш ширината и височината (двоен клик = автоматична височина)"}

    def __init__(self, box, mode):
        super().__init__(box); self.box = box; self.mode = mode; self._press = None; self._hover = False
        self.setCursor({"w": Qt.SizeHorCursor, "h": Qt.SizeVerCursor, "wh": Qt.SizeFDiagCursor}[mode])
        self.setToolTip(self.TIPS[mode]); self.hide()
    def enterEvent(self, ev): self._hover = True; self.update(); super().enterEvent(ev)
    def leaveEvent(self, ev): self._hover = False; self.update(); super().leaveEvent(ev)
    def paintEvent(self, ev):
        p = QPainter(self); p.setRenderHint(QPainter.Antialiasing)
        strong = self._hover or self._press is not None
        col = QColor(46, 224, 138, 240 if strong else 130); w, h = self.width(), self.height()
        if self.mode == "wh":
            pen = QPen(col, 2.0); pen.setCapStyle(Qt.RoundCap); p.setPen(pen)
            for k in (6, 11, 16): p.drawLine(w - 4 - k, h - 4, w - 4, h - 4 - k)
        else:
            p.setPen(Qt.NoPen); p.setBrush(col)
            if self.mode == "w": p.drawRoundedRect(QRectF(w / 2 - 2, h / 2 - 22, 4, 44), 2, 2)
            else: p.drawRoundedRect(QRectF(w / 2 - 22, h / 2 - 2, 44, 4), 2, 2)
        p.end()
    def mousePressEvent(self, ev):
        if ev.button() == Qt.LeftButton:
            self._press = ev.globalPosition().toPoint(); self.box.canvas.begin_resize(self.box); self.update(); ev.accept()
        else: super().mousePressEvent(ev)
    def mouseMoveEvent(self, ev):
        if self._press is None: return
        d = ev.globalPosition().toPoint() - self._press
        self.box.canvas.preview_resize(self.box, d.x() if "w" in self.mode else None, d.y() if "h" in self.mode else None)
    def mouseReleaseEvent(self, ev):
        if self._press is None: return
        self._press = None; self.update(); self.box.canvas.end_resize(True)
    def mouseDoubleClickEvent(self, ev):
        self._press = None; self.box.canvas.end_resize(False)
        if "h" in self.mode: self.box.canvas.reset_height(self.box)


class NavRow(QWidget):
    """One sidebar entry (page button or section caption) + its edit-mode "hide" eye. The row is what is dragged."""
    def __init__(self, win, nav_id, inner):
        super().__init__(); self.win = win; self.nav_id = nav_id; self.inner = inner; self._start = None; self._dim = False
        self.setObjectName("NavRow"); self.setAttribute(Qt.WA_StyledBackground, True); self.setProperty("drop", False)
        self._cursor = inner.cursor().shape()
        h = QHBoxLayout(self); h.setContentsMargins(0, 0, 0, 0); h.setSpacing(6); h.addWidget(inner, 1)
        self.eye = icon_button("eye", "Скрий от менюто", "IconBtn", 32, 16, "#cfe0dd", GREEN)
        self.eye.clicked.connect(lambda _=False: self.win.on_nav_toggle(self.nav_id)); self.eye.hide(); h.addWidget(self.eye)
        inner.installEventFilter(self)
    def set_edit(self, on):
        self.eye.setVisible(bool(on) and self.nav_id not in NAV_LOCKED)
        self.inner.setCursor(Qt.SizeAllCursor if on else self._cursor)
    def set_hidden_state(self, hidden):
        self.eye.setIcon(svg_icon("eye_off" if hidden else "eye", 16, "#ff8f99" if hidden else "#cfe0dd"))
        tip(self.eye, "Покажи в менюто" if hidden else "Скрий от менюто", GREEN if hidden else "#ff6b7a")
        if hidden != self._dim:
            self._dim = hidden
            if hidden:
                eff = QGraphicsOpacityEffect(self.inner); eff.setOpacity(0.38); self.inner.setGraphicsEffect(eff)
            else:
                self.inner.setGraphicsEffect(None)
    def mark_drop(self, on):
        if bool(self.property("drop")) != bool(on):
            self.setProperty("drop", bool(on)); self.style().unpolish(self); self.style().polish(self)
    def eventFilter(self, obj, ev):
        if obj is self.inner and self.win.nav_model.edit_mode:
            t = ev.type()
            if t == QEvent.MouseButtonPress and ev.button() == Qt.LeftButton: self._start = ev.position().toPoint()
            elif t == QEvent.MouseButtonRelease: self._start = None
            elif t == QEvent.MouseMove and self._start is not None and (ev.buttons() & Qt.LeftButton):
                if (ev.position().toPoint() - self._start).manhattanLength() >= QApplication.startDragDistance():
                    self._start = None; self.start_drag(); return True
        return super().eventFilter(obj, ev)
    def start_drag(self):
        drag = QDrag(self); mime = QMimeData(); mime.setText(NAV_DRAG + self.nav_id); drag.setMimeData(mime)
        pm = self.grab(); drag.setPixmap(pm); drag.setHotSpot(QPoint(20, pm.height() // 2))
        if hasattr(self.inner, "setDown"): self.inner.setDown(False)
        eff = QGraphicsOpacityEffect(self); eff.setOpacity(0.35); self.setGraphicsEffect(eff)
        try: drag.exec(Qt.MoveAction)
        finally:
            self.win.nav_host.clear_marks()
            try: self.setGraphicsEffect(None)
            except RuntimeError: pass


class NavHost(QWidget):
    """Holds the sidebar rows; in nav edit mode a row can be dropped on another one to reorder."""
    def __init__(self, win):
        super().__init__(); self.win = win; self.setAcceptDrops(True)
    def _src(self, ev):
        md = ev.mimeData()
        if md is not None and md.hasText() and md.text().startswith(NAV_DRAG):
            nid = md.text()[len(NAV_DRAG):]
            return nid if nid in self.win.nav_rows else None
        return None
    def _target_at(self, pos):
        """Nearest visible row to the cursor; None when the cursor is below the last row (-> move to the end)."""
        best, bestd = None, 10 ** 9
        for nid in self.win.nav_model.order:
            row = self.win.nav_rows[nid]
            if not row.isVisible(): continue
            g = row.geometry()
            d = 0 if g.top() <= pos.y() <= g.bottom() else min(abs(pos.y() - g.top()), abs(pos.y() - g.bottom()))
            if d < bestd: best, bestd = nid, d
        last = next((n for n in reversed(self.win.nav_model.order) if self.win.nav_rows[n].isVisible()), None)
        if last is not None and pos.y() > self.win.nav_rows[last].geometry().bottom(): return None
        return best
    def clear_marks(self):
        for row in self.win.nav_rows.values(): row.mark_drop(False)
    def dragEnterEvent(self, ev):
        if self._src(ev): ev.acceptProposedAction()
        else: ev.ignore()
    def dragMoveEvent(self, ev):
        src = self._src(ev)
        if not src: ev.ignore(); return
        target = self._target_at(ev.position().toPoint())
        for nid, row in self.win.nav_rows.items(): row.mark_drop(nid == target and nid != src)
        ev.acceptProposedAction()
    def dragLeaveEvent(self, ev):
        self.clear_marks(); super().dragLeaveEvent(ev)
    def dropEvent(self, ev):
        src = self._src(ev); self.clear_marks()
        if not src: ev.ignore(); return
        target = self._target_at(ev.position().toPoint()); ev.acceptProposedAction()
        QTimer.singleShot(0, lambda s=src, t=target: self.win.on_nav_move(s, t))


class NavGrip(QWidget):
    """Right edge of the sidebar (nav edit mode only): drag to change the menu width, double click = default."""
    def __init__(self, side):
        super().__init__(side); self.side = side; self._press = None; self._w0 = 0; self._hover = False
        self.setCursor(Qt.SizeHorCursor); self.setToolTip("Дръпни, за да промениш ширината на менюто (двоен клик = стандартна)"); self.hide()
    def enterEvent(self, ev): self._hover = True; self.update(); super().enterEvent(ev)
    def leaveEvent(self, ev): self._hover = False; self.update(); super().leaveEvent(ev)
    def paintEvent(self, ev):
        p = QPainter(self); p.setRenderHint(QPainter.Antialiasing); p.setPen(Qt.NoPen)
        p.setBrush(QColor(46, 224, 138, 240 if (self._hover or self._press is not None) else 130))
        p.drawRoundedRect(QRectF(self.width() / 2 - 2, self.height() / 2 - 24, 4, 48), 2, 2); p.end()
    def mousePressEvent(self, ev):
        if ev.button() == Qt.LeftButton: self._press = ev.globalPosition().toPoint().x(); self._w0 = self.side.width(); ev.accept()
    def mouseMoveEvent(self, ev):
        if self._press is not None: self.side.win.set_nav_width(self._w0 + ev.globalPosition().toPoint().x() - self._press, save=False)
    def mouseReleaseEvent(self, ev):
        if self._press is not None: self._press = None; self.side.win.set_nav_width(self.side.width(), save=True)
    def mouseDoubleClickEvent(self, ev):
        self._press = None; self.side.win.set_nav_width(NAV_DEFAULT_WIDTH, save=True)


class NavSidebar(QFrame):
    def __init__(self, win):
        super().__init__(); self.win = win; self.grip = NavGrip(self)
    def resizeEvent(self, ev):
        super().resizeEvent(ev); self.grip.setGeometry(self.width() - 10, 0, 10, self.height()); self.grip.raise_()


class DashContainer(QFrame):
    """One dashboard container: title (drag handle), fold button and, in edit mode, width / remove buttons."""
    def __init__(self, canvas, item, title, min_h):
        super().__init__(); self.canvas = canvas; self.item_id = item["id"]; self.edit = False; self.collapsed = False
        self.min_h = min_h; self._refresh = None; self.live = False
        self.height_px = int(item.get("height", 0) or 0); self.col = 0; self.span = int(item.get("span", COLUMNS // 2)); self.grips = []
        self.setObjectName("DashBox"); self.setProperty("edit", False); self.setProperty("drop", False)
        v = QVBoxLayout(self); v.setContentsMargins(16, 12, 16, 14); v.setSpacing(10)
        head = QHBoxLayout(); head.setSpacing(8)
        self.handle = DragHandle(title, self); self.handle.setObjectName("CardTitle"); head.addWidget(self.handle, 1)
        self.extra = QHBoxLayout(); self.extra.setSpacing(6); head.addLayout(self.extra)
        self.span_btn = QPushButton(); self.span_btn.setObjectName("MiniBtn"); self.span_btn.setCursor(Qt.PointingHandCursor)
        self.span_btn.clicked.connect(lambda _=False: self.canvas.on_span(self.item_id)); head.addWidget(self.span_btn)
        self.rm_btn = icon_button("close", "Премахни контейнера", "DangerIcon", 30, 14, "#ff8f99", "#ff6b7a")
        self.rm_btn.clicked.connect(lambda _=False: self.canvas.on_remove(self.item_id)); head.addWidget(self.rm_btn)
        self.fold_btn = icon_button("chev_up", "Свий контейнера", "IconBtn", 32, 16, "#cfe0dd", GREEN)
        self.fold_btn.clicked.connect(lambda _=False: self.canvas.on_fold(self.item_id)); head.addWidget(self.fold_btn)
        v.addLayout(head)
        self.body = QWidget(); self.body.setObjectName("DashBody"); self.body_lay = QVBoxLayout(self.body)
        self.body_lay.setContentsMargins(0, 0, 0, 0); self.body_lay.setSpacing(8); v.addWidget(self.body, 1)
        self.grips = [EdgeGrip(self, "w"), EdgeGrip(self, "h"), EdgeGrip(self, "wh")]
    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        if len(self.grips) == 3:
            w, h, t, c = self.width(), self.height(), 10, 24
            self.grips[0].setGeometry(w - t, 0, t, max(0, h - c)); self.grips[1].setGeometry(0, h - t, max(0, w - c), t)
            self.grips[2].setGeometry(w - c, h - c, c, c)
            for g in self.grips: g.raise_()
    def show_grips(self):
        for g in self.grips: g.setVisible(self.edit and not self.collapsed); g.raise_()
    def apply_height(self):
        if self.collapsed: self.setMinimumHeight(0); self.setMaximumHeight(64)
        elif self.height_px > 0: self.setMinimumHeight(self.height_px); self.setMaximumHeight(self.height_px)
        else: self.setMinimumHeight(self.min_h); self.setMaximumHeight(16777215)
    def set_body(self, widget, refresh, live, extras):
        self.body_lay.addWidget(widget, 1); self._refresh = refresh; self.live = bool(live)
        for w in extras or []: self.extra.addWidget(w)
    def do_refresh(self):
        if self._refresh is not None and not self.collapsed:
            try: self._refresh()
            except RuntimeError: pass        # a child widget was replaced while refreshing
    def set_edit(self, on):
        self.edit = bool(on); self.setProperty("edit", self.edit)
        self.span_btn.setVisible(self.edit); self.rm_btn.setVisible(self.edit)
        for i in range(self.extra.count()):
            w = self.extra.itemAt(i).widget()
            if w is not None and w.property("editOnly"): w.setVisible(self.edit)
        self.handle.setCursor(Qt.SizeAllCursor if self.edit else Qt.ArrowCursor)
        self.handle.setToolTip("Хвани и премести контейнера" if self.edit else "")
        self.show_grips()
        self.style().unpolish(self); self.style().polish(self)
    def set_collapsed(self, c):
        self.collapsed = bool(c); self.body.setVisible(not self.collapsed)
        self.apply_height(); self.show_grips()
        self.fold_btn.setIcon(svg_icon("chev_down" if self.collapsed else "chev_up", 16, "#cfe0dd"))
        tip(self.fold_btn, "Разгъни контейнера" if self.collapsed else "Свий контейнера", GREEN)
    def set_span_label(self, span):
        self.span_btn.setText("Цяла ширина" if int(span) < COLUMNS else "Половин ширина")
    def mark_drop(self, on):
        if bool(self.property("drop")) != bool(on):
            self.setProperty("drop", bool(on)); self.style().unpolish(self); self.style().polish(self)


class DashCanvas(QWidget):
    """Grid of containers (2 columns). Drag & drop reorders them; every change is saved by the model."""
    def __init__(self, win, model):
        super().__init__(); self.win = win; self.model = model; self.boxes = {}; self.dragging = False; self.animating = False; self._reflow = None
        self.resizing = False; self._rz = None
        self.setAcceptDrops(True)
        self.grid = QGridLayout(self); self.grid.setContentsMargins(0, 0, 8, 0); self.grid.setHorizontalSpacing(12); self.grid.setVerticalSpacing(12)
        for c in range(COLUMNS): self.grid.setColumnStretch(c, 1)
        self.guide = QFrame(self); self.guide.setObjectName("ResizeGuide"); self.guide.setAttribute(Qt.WA_TransparentForMouseEvents, True); self.guide.hide()
        self.empty = QLabel("Няма контейнери.\nНатисни „Редактирай интерфейса“ → „Добави контейнер“."); self.empty.setObjectName("ActivityEmpty")
        self.empty.setAlignment(Qt.AlignCenter); self.grid.addWidget(self.empty, 0, 0, 1, COLUMNS)
        self.rebuild()
    # ------------------------------------------------------------ build
    def rebuild(self, animate=False, dropped=""):
        old = {iid: QRect(b.geometry()) for iid, b in self.boxes.items()} if animate else {}
        for box in list(self.boxes.values()):
            self.grid.removeWidget(box); box.hide(); box.setParent(None); box.deleteLater()
        self.boxes = {}
        self.win.stream_dynamic_labels = {}; self.win.account_countdown_labels = {}
        for r in range(0, 60): self.grid.setRowStretch(r, 0)
        positions = grid_positions(self.model.items); last = 0
        for item in self.model.items:
            box = self.win.make_container(self, item)
            _id, row, col, span = next(p for p in positions if p[0] == item["id"])
            box.col = col; box.span = span
            self.grid.addWidget(box, row, col, 1, span, Qt.AlignTop); self.boxes[item["id"]] = box; last = max(last, row)
        self.grid.setRowStretch(last + 1, 1)
        self.empty.setVisible(not self.model.items)
        self.refresh_all()
        if animate and old: QTimer.singleShot(0, lambda o=old, d=dropped: animate_reflow(self, o, d))
    def set_edit_mode(self, on):
        self.model.edit_mode = bool(on)
        for box in self.boxes.values(): box.set_edit(on)
    def refresh_all(self):
        self.win.stream_dynamic_labels = {}; self.win.account_countdown_labels = {}
        for box in self.boxes.values(): box.do_refresh()
    def tick(self):
        if self.dragging or self.animating or self.resizing: return
        for box in self.boxes.values():
            if box.live: box.do_refresh()
    # ------------------------------------------------------------ button callbacks
    def on_fold(self, item_id):
        c = self.model.toggle_collapsed(item_id); box = self.boxes.get(item_id)
        if box is not None:
            box.set_collapsed(c)
            if not c: box.do_refresh()
    def on_span(self, item_id):
        self.model.toggle_span(item_id); self.rebuild(animate=True, dropped=item_id)
    def on_remove(self, item_id):
        self.model.remove(item_id); self.rebuild(animate=True)
    # ------------------------------------------------------------ free resizing
    def _col_metrics(self):
        gap = self.grid.horizontalSpacing(); inner = max(1, self.width() - 8)         # 8 = right margin of the grid
        return (inner - gap * (COLUMNS - 1)) / COLUMNS, gap
    def begin_resize(self, box):
        self.resizing = True
        self._rz = {"id": box.item_id, "geo": QRect(box.geometry()), "span": None, "height": None, "col": box.col}
        self.guide.setGeometry(box.geometry()); self.guide.show(); self.guide.raise_()
    def preview_resize(self, box, dx, dy):
        st = self._rz
        if not st: return
        geo = st["geo"]; colw, gap = self._col_metrics(); w, h = geo.width(), geo.height()
        if dx is not None:
            span = int(round((geo.width() + dx + gap) / (colw + gap)))
            room = COLUMNS - st["col"]
            span = max(MIN_SPAN, min(room if room >= MIN_SPAN else COLUMNS, span))       # must fit in its row
            st["span"] = span; w = int(span * colw + (span - 1) * gap)
        if dy is not None:
            h = max(MIN_HEIGHT, int(round((geo.height() + dy) / 10.0)) * 10); st["height"] = h
        self.guide.setGeometry(QRect(geo.x(), geo.y(), w, h)); self.guide.raise_()
    def end_resize(self, commit):
        st = self._rz; self._rz = None; self.guide.hide(); self.resizing = False
        if not commit or not st: return
        it = self.model.get(st["id"])
        if it is None: return
        span, height = st["span"], st["height"]
        if (span is None or span == int(it.get("span", 0))) and (height is None or height == int(it.get("height", 0))): return
        self.model.set_size(st["id"], span=span, height=height)
        QTimer.singleShot(0, lambda i=st["id"]: self.rebuild(animate=True, dropped=i))
    def reset_height(self, box):
        self.model.set_size(box.item_id, height=0)
        QTimer.singleShot(0, lambda i=box.item_id: self.rebuild(animate=True, dropped=i))
    # ------------------------------------------------------------ drag & drop
    def _box_at(self, pos):
        for iid, box in self.boxes.items():
            if box.isVisible() and box.geometry().contains(pos): return iid
        return None
    def clear_drop_marks(self):
        for box in self.boxes.values(): box.mark_drop(False)
    def _dragged_id(self, ev):
        md = ev.mimeData()
        if md is not None and md.hasText() and md.text().startswith(DRAG_PREFIX): return md.text()[len(DRAG_PREFIX):]
        return None
    def dragEnterEvent(self, ev):
        if self._dragged_id(ev) in self.boxes: ev.acceptProposedAction()
        else: ev.ignore()
    def dragMoveEvent(self, ev):
        src = self._dragged_id(ev)
        if src not in self.boxes: ev.ignore(); return
        target = self._box_at(ev.position().toPoint())
        for iid, box in self.boxes.items(): box.mark_drop(iid == target and iid != src)
        ev.acceptProposedAction()
    def dragLeaveEvent(self, ev):
        self.clear_drop_marks(); super().dragLeaveEvent(ev)
    def dropEvent(self, ev):
        src = self._dragged_id(ev); self.clear_drop_marks()
        if src not in self.boxes: ev.ignore(); return
        target = self._box_at(ev.position().toPoint())
        ev.acceptProposedAction()
        if self.model.move(src, target): QTimer.singleShot(0, lambda s=src: self.rebuild(animate=True, dropped=s))


class SendBox(QWidget):
    """Manual message: stream, accounts chosen with check boxes, text. Used on the Message page AND as a container."""
    done = Signal(object)
    def __init__(self, win):
        super().__init__(); self.win = win; self._chans = None; self._acc_key = None
        l = QVBoxLayout(self); l.setContentsMargins(0, 0, 0, 0); l.setSpacing(8)
        self.channel = QComboBox(); self.channel.currentIndexChanged.connect(lambda _=0: self._fill_accounts())
        self.all_box = QCheckBox("Избери всички акаунти"); self.all_box.clicked.connect(self._toggle_all)
        self.accounts = QListWidget(); self.accounts.setMinimumHeight(96); self.accounts.itemChanged.connect(lambda _it=None: self._sync_all())
        self.text = QLineEdit(); self.text.setPlaceholderText("Напиши съобщение...  (започни с ! за команда)"); self.text.returnPressed.connect(self.send)
        self.cmd_bar = CommandBar(); self.cmd_bar.picked.connect(self._pick_command)
        self.text.textEdited.connect(self._on_text_edited)
        self.btn = QPushButton("Изпрати"); self.btn.setObjectName("Primary"); self.btn.setFixedHeight(44); self.btn.setCursor(Qt.PointingHandCursor); self.btn.clicked.connect(self.send)
        self.info = QLabel(""); self.info.setObjectName("StreamMeta"); self.info.setWordWrap(True)
        for w in (QLabel("Стрийм"), self.channel, QLabel("Акаунти"), self.all_box): l.addWidget(w)
        l.addWidget(self.accounts, 1); l.addWidget(self.text); l.addWidget(self.cmd_bar); l.addWidget(self.btn); l.addWidget(self.info)
        self.done.connect(self._done); self.refresh()
    def refresh(self):
        cfg = self.win.config
        chans = [(c.id, f"{c.display_name or c.url} · {PLATFORM_LABEL[c.platform]}") for c in cfg.channels]
        if chans != self._chans:
            cur = self.channel.currentData(); self.channel.blockSignals(True); self.channel.clear()
            for cid, label in chans: self.channel.addItem(label, cid)
            idx = self.channel.findData(cur)
            if idx >= 0: self.channel.setCurrentIndex(idx)
            self.channel.blockSignals(False); self._chans = chans; self._acc_key = None
        self._fill_accounts()
    def _on_text_edited(self, t):
        self.cmd_bar.update_for(t, self.win.engine.saved_commands())
    def _pick_command(self, cmd):
        parts = self.text.text().split(None, 1); rest = parts[1] if len(parts) > 1 else ""
        self.text.setText(cmd + " " + rest); self.text.setFocus(); self.text.setCursorPosition(len(self.text.text()))
        self.cmd_bar.update_for(self.text.text(), self.win.engine.saved_commands())
    def _checked_ids(self):
        return [self.accounts.item(i).data(Qt.UserRole) for i in range(self.accounts.count()) if self.accounts.item(i).checkState() == Qt.Checked]
    def _fill_accounts(self):
        cfg = self.win.config; cid = self.channel.currentData(); ch = cfg.get_channel(cid) if cid else None
        rows = [(a.id, a.display_name) for a in cfg.accounts if ch is not None and a.id in ch.connected_account_ids()]
        key = (cid, tuple(rows))
        if key == self._acc_key: return
        checked = set(self._checked_ids()); self.accounts.blockSignals(True); self.accounts.clear()
        for aid, name in rows:
            it = QListWidgetItem(name); it.setData(Qt.UserRole, aid); it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(Qt.Checked if aid in checked else Qt.Unchecked); self.accounts.addItem(it)
        self.accounts.blockSignals(False); self._acc_key = key; self._sync_all()
    def _toggle_all(self, checked=False):
        self.accounts.blockSignals(True)
        for i in range(self.accounts.count()): self.accounts.item(i).setCheckState(Qt.Checked if checked else Qt.Unchecked)
        self.accounts.blockSignals(False)
    def _sync_all(self):
        n = self.accounts.count(); self.all_box.blockSignals(True)
        self.all_box.setChecked(n > 0 and len(self._checked_ids()) == n); self.all_box.blockSignals(False)
    def send(self):
        cid = self.channel.currentData(); text = self.text.text().strip(); ids = self._checked_ids()
        if not cid: self.info.setText("Няма избран стрийм."); return
        if not ids: self.info.setText("Избери поне един акаунт."); return
        if not text: self.info.setText("Напиши съобщение."); return
        self.btn.setEnabled(False); self.info.setText("Изпращане...")
        threading.Thread(target=self._worker, args=(cid, ids, text), daemon=True).start()
    def _worker(self, cid, ids, text):
        try: r = self.win.engine.manual_send(cid, ids, text)
        except Exception as e: r = {"ok": False, "error": f"Грешка: {e}", "sent": 0}
        self.done.emit(r)
    def _done(self, r):
        self.btn.setEnabled(True)
        if r.get("ok"):
            self.win.engine.remember_command(self.text.text())      # "!word" is added to the saved commands automatically
            self.text.clear(); self.cmd_bar.update_for("", []); self.info.setText(f"Изпратено от {r.get('sent', 0)} акаунта.")
        else:
            fails = "; ".join(f"{n}: {m}" for n, m in (r.get("failures") or []))
            self.info.setText(str(r.get("error") or fails or "Неуспешно изпращане."))
        self.win.events.put(("log", f"Ръчно съобщение: {r}"))


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__(); self.setWindowTitle("Stream Activity Bot"); self.resize(1460,920); self.setMinimumSize(1200,760)
        self.events=queue.Queue(); self.config=Config(); self.tokens=TokenStore(); self.tm=TokenManager(self.tokens,lambda p:acc.provider_for(p,self.config.settings,self.tokens)); self.chat_store=ChatStore(app_dir())
        self.engine=Engine(self.config,self.tokens,self.tm,YouTubeAPI(self.tm),KickAPI(self.tm),self.events,self.chat_store)
        self.oauth_cancel=None
        self.log_lines=deque(maxlen=300); self.stream_dynamic_labels={}; self.account_countdown_labels={}
        self.layout_model=LayoutModel(app_dir()/"ui_layout.json"); self.nav_model=NavModel(app_dir()/"nav_layout.json")
        self.nav_rows={}; self.sidebar_collapsed=False
        self.build(); self.refresh_all(); center(self)
        QTimer.singleShot(300,self.first_run); self.timer=QTimer(self);self.timer.timeout.connect(self.pump);self.timer.start(700); self.dynamic_timer=QTimer(self); self.dynamic_timer.timeout.connect(self.update_dynamic_labels); self.dynamic_timer.start(1000)
    def build(self):
        root=QWidget(); self.setCentralWidget(root)
        h=QHBoxLayout(root); h.setContentsMargins(0,0,0,0); h.setSpacing(0)

        # ---------- странична лента ----------
        side=NavSidebar(self); side.setObjectName("Sidebar"); side.setFixedWidth(self.nav_model.width)
        sv=QVBoxLayout(side); sv.setContentsMargins(16,22,16,16); sv.setSpacing(4); self.side_layout=sv
        head=QHBoxLayout(); head.setSpacing(6); head.setContentsMargins(0,0,0,0); self.side_head=head
        brand_box=QWidget(); brand_box.setObjectName("BrandBox"); bb=QVBoxLayout(brand_box); bb.setContentsMargins(0,0,0,0); bb.setSpacing(2)
        brand=QLabel("STREAM<span style='color:#2ee08a'>BOT</span>"); brand.setObjectName("Brand"); brand.setTextFormat(Qt.RichText); bb.addWidget(brand)
        tag=QLabel("KICK  +  YOUTUBE"); tag.setObjectName("BrandTag"); bb.addWidget(tag)
        self.brand=brand; self.brand_tag=tag; self.brand_box=brand_box
        head.addWidget(brand_box,1,Qt.AlignTop)
        self.collapse_btn=icon_button("chev_left","Свий менюто","CollapseBtn",36,18,"#dff0ec",GREEN); self.collapse_btn.clicked.connect(lambda _=False:self.toggle_sidebar())
        head.addWidget(self.collapse_btn,0,Qt.AlignRight|Qt.AlignTop)
        sv.addLayout(head)
        self.brand_mini=QLabel("SB"); self.brand_mini.setObjectName("BrandMini"); self.brand_mini.setAlignment(Qt.AlignCenter); self.brand_mini.hide(); sv.addWidget(self.brand_mini)
        sv.addSpacing(14)
        self.nav_items=[]; self.nav_sections=[]; self.side=side

        self.tabs=QStackedWidget(); self.nav_group=QButtonGroup(self); self.nav_group.setExclusive(True); self.nav_buttons={}
        self.nav_page_widgets={}; self.nav_host=NavHost(self); self.nav_lay=QVBoxLayout(self.nav_host); self.nav_lay.setContentsMargins(0,0,0,0); self.nav_lay.setSpacing(4)
        def add_action(text,icon,fn):
            b=QPushButton("  "+text); b.setObjectName("Nav"); b.setCursor(Qt.PointingHandCursor)
            b.setIcon(nav_icon(icon,22)); b.setIconSize(QSize(22,22)); b.clicked.connect(fn); sv.addWidget(b); self.nav_items.append((b,text,icon)); return b
        def page_row(nid,widget,text,icon):
            self.tabs.addWidget(widget); self.nav_page_widgets[nid]=widget
            b=QPushButton("  "+text); b.setObjectName("Nav"); b.setCheckable(True); b.setCursor(Qt.PointingHandCursor)
            b.setIcon(nav_icon(icon,22)); b.setIconSize(QSize(22,22))
            b.clicked.connect(lambda _,w=widget:self.tabs.setCurrentWidget(w))
            self.nav_group.addButton(b); self.nav_buttons[widget]=b; self.nav_items.append((b,text,icon))
            self.nav_rows[nid]=NavRow(self,nid,b)
        def section_row(nid,text):
            l=QLabel(text); l.setObjectName("NavSection"); row=NavRow(self,nid,l); row.layout().setContentsMargins(0,10,0,2); self.nav_rows[nid]=row

        self.home=self.dashboard(); self.accounts_tab=self.accounts_page(); self.channels_tab=self.channels_page()
        self.send_tab=self.send_page(); self.profiles_tab=self.profiles_page(); self.activity_tab=self.activity_page(); self.help_tab=self.help_page()
        for nid,txt in NAV_SECTIONS.items(): section_row(nid,txt)
        page_row("home",self.home,"Начало","home"); page_row("accounts",self.accounts_tab,"Акаунти","user"); page_row("channels",self.channels_tab,"Стриймове","stream")
        page_row("send",self.send_tab,"Съобщение","message"); page_row("profiles",self.profiles_tab,"Точки и време","trophy"); page_row("activity",self.activity_tab,"Активност","activity")
        page_row("help",self.help_tab,"Помощ","help")
        sv.addWidget(self.nav_host)
        sv.addStretch()
        self.nav_reset_btn=QPushButton("Нулирай навигацията"); self.nav_reset_btn.setObjectName("MiniBtn"); self.nav_reset_btn.setCursor(Qt.PointingHandCursor)
        self.nav_reset_btn.clicked.connect(self.reset_nav); self.nav_reset_btn.hide(); sv.addWidget(self.nav_reset_btn)
        sep=QFrame(); sep.setObjectName("NavSep"); sep.setFixedHeight(1); sv.addWidget(sep); sv.addSpacing(6)
        self.nav_edit_btn=add_action("Редактирай навигацията","sliders",self.toggle_nav_edit)
        self.quick_btn=add_action("Бързи действия","bolt",self.quick_actions_menu)
        self.rebuild_nav()
        self.page_names={self.tabs.widget(i):self.nav_buttons[self.tabs.widget(i)].text().strip() for i in range(self.tabs.count())}
        for pg in self.page_names:
            for lb in pg.findChildren(QLabel):
                if lb.objectName()=="PageTitle": lb.hide()
        self.tabs.currentChanged.connect(lambda i:(self.nav_buttons[self.tabs.widget(i)].setChecked(True),self.page_title.setText(self.page_names.get(self.tabs.widget(i),""))))
        self.nav_buttons[self.home].setChecked(True)
        h.addWidget(side)

        # ---------- основна част ----------
        right=QVBoxLayout(); right.setContentsMargins(34,22,34,26); right.setSpacing(20)
        top=QHBoxLayout(); top.setSpacing(10)
        self.page_title=QLabel("Начало"); self.page_title.setObjectName("TopTitle"); top.addWidget(self.page_title)
        top.addSpacing(6)
        self.status=icon_button("power","Спрян","StatusIcon",44,20,"#8ea3a0","#ff6b7a"); self.status.setParent(root); self.status.hide()   # the "Спрян" pill is gone; kept hidden so update_state_icons() keeps working
        self.mode=ModeSwitch(); self.mode.requested.connect(self.on_mode_requested); top.addWidget(self.mode)
        top.addStretch()
        self.edit_btn=icon_button("sliders","Редактирай интерфейса","HeaderButton",44,22,"#cfe0dd","#b69bff"); self.edit_btn.setProperty("on",False)
        self.edit_btn.clicked.connect(self.toggle_edit); top.addWidget(self.edit_btn)
        save=icon_button("save","Запази","HeaderButton",44,22,"#cfe0dd","#6aa2ff"); save.clicked.connect(self.save); top.addWidget(save)
        self.toggle_btn=icon_button("play","Старт","HeaderPrimary",52,20,"#ffffff",GREEN); self.toggle_btn.clicked.connect(self.toggle_run); top.addWidget(self.toggle_btn)
        self.toggle_btn.setFixedHeight(44); self.toggle_btn.setMinimumWidth(124); self.toggle_btn.setMaximumWidth(200)
        right.addLayout(top)
        right.addWidget(self.tabs,1)
        h.addLayout(right,1)
        self.sidebar_collapsed=False; self.set_sidebar(bool(QSettings("StreamActivityBot","ui").value("sidebar_collapsed",False,type=bool)),False)

    def toggle_sidebar(self): self.set_sidebar(not self.sidebar_collapsed,True)
    def set_sidebar(self,collapsed,animate=False):
        self.sidebar_collapsed=collapsed
        self.brand_box.setVisible(not collapsed); self.refresh_nav_visibility()
        self.brand_mini.setVisible(collapsed)
        self.side_layout.setSpacing(8 if collapsed else 4); self.nav_lay.setSpacing(8 if collapsed else 4)
        self.side_head.setAlignment(self.collapse_btn,(Qt.AlignHCenter if collapsed else Qt.AlignRight)|Qt.AlignTop)
        for b,text,icon in self.nav_items:
            b.setText("" if collapsed else "  "+text); b.setProperty("collapsed",collapsed)
            if collapsed: b.setIcon(nav_icon_fancy(icon,24)); b.setIconSize(QSize(24,24)); tip(b,text,NAV_ACCENT.get(icon,GREEN))
            else: b.setIcon(nav_icon(icon,22)); b.setIconSize(QSize(22,22)); b.setToolTip("")
            b.style().unpolish(b); b.style().polish(b)
        cb=self.collapse_btn
        cb.setIcon(svg_icon("chev_right" if collapsed else "chev_left",18,"#dff0ec")); tip(cb,"Разгъни менюто" if collapsed else "Свий менюто",GREEN)
        target=76 if collapsed else self.nav_model.width
        m=12 if collapsed else 16
        self.side_layout.setContentsMargins(m,22,m,16)       # collapsed: buttons become exact 52x48 squares, centred
        if animate:
            an=QVariantAnimation(self); an.setStartValue(self.side.width()); an.setEndValue(target); an.setDuration(170)
            an.valueChanged.connect(lambda v:self.side.setFixedWidth(int(v))); an.start(); self.side_anim=an
        else: self.side.setFixedWidth(target)
        QSettings("StreamActivityBot","ui").setValue("sidebar_collapsed",collapsed)

    # ---------------------------------------------------------------- navigation editing
    def refresh_nav_visibility(self):
        edit=self.nav_model.edit_mode; col=getattr(self,"sidebar_collapsed",False)
        for nid,row in self.nav_rows.items():
            hid=self.nav_model.is_hidden(nid)
            row.setVisible((edit or not hid) and not (col and nav_is_section(nid)))
    def rebuild_nav(self):
        lay=self.nav_lay
        while lay.count(): lay.takeAt(0)                     # the rows stay alive (children of nav_host); they are re-added in model order
        for nid in self.nav_model.order:
            row=self.nav_rows[nid]; lay.addWidget(row); row.set_hidden_state(self.nav_model.is_hidden(nid)); row.set_edit(self.nav_model.edit_mode)
        self.refresh_nav_visibility()
    def toggle_nav_edit(self,_=False):
        on=not self.nav_model.edit_mode; self.nav_model.edit_mode=on
        if on and self.sidebar_collapsed: self.set_sidebar(False,False)
        self.collapse_btn.setEnabled(not on); self.nav_reset_btn.setVisible(on); self.side.grip.setVisible(on)
        for n,(b,text,icon) in enumerate(self.nav_items):
            if b is self.nav_edit_btn: self.nav_items[n]=(b,"Готово - край на редакцията" if on else "Редактирай навигацията","check" if on else "sliders")
        self.set_sidebar(self.sidebar_collapsed,False)
        self.rebuild_nav()
    def on_nav_move(self,src,target):
        if self.nav_model.move(src,target): self.rebuild_nav()
    def on_nav_toggle(self,nav_id):
        hide=not self.nav_model.is_hidden(nav_id)
        if not self.nav_model.set_hidden(nav_id,hide): return
        if hide and self.tabs.currentWidget() is self.nav_page_widgets.get(nav_id): self.tabs.setCurrentWidget(self.home)     # never stay on a hidden page
        self.rebuild_nav()
    def set_nav_width(self,w,save=True):
        w=max(NAV_MIN_WIDTH,min(NAV_MAX_WIDTH,int(w)))
        if save: self.nav_model.set_width(w)
        else: self.nav_model.width=w
        if not self.sidebar_collapsed: self.side.setFixedWidth(w)
    def reset_nav(self,_=False):
        if QMessageBox.question(self,"Нулиране","Да върна менюто по подразбиране (ред, скрити елементи и ширина)?")!=QMessageBox.Yes: return
        self.nav_model.reset(); self.set_nav_width(self.nav_model.width,save=False); self.rebuild_nav()

    def make_metric(self,title,icon_name,col=None,compact=False):
        c=QFrame(); c.setObjectName("MetricCard")
        row=QHBoxLayout(c); row.setContentsMargins(16,14,16,14); row.setSpacing(12)
        col=col or METRIC_COLOR.get(icon_name,GREEN)
        c.setStyleSheet(f"QFrame#MetricCard{{background:qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 {tint(col,13)},stop:0.65 {CARD},stop:1 {CARD});border:1px solid {tint(col,30)};border-radius:20px;}}")
        icon=QLabel(); icon.setPixmap(_pix(icon_name,26,col)); icon.setFixedSize(50,50); icon.setAlignment(Qt.AlignCenter); icon.setObjectName("MetricIcon")
        icon.setStyleSheet(f"background:{tint(col,14)};border:1px solid {tint(col,45)};border-radius:25px;")
        text=QVBoxLayout(); text.setSpacing(0)
        lab=QLabel(title); lab.setObjectName("MetricLabel")
        val=QLabel("0"); val.setObjectName("Metric"); val.setMinimumWidth(40); val.setStyleSheet(f"color:{col};")
        sub=None
        if compact:        # account name (long text) + the value underneath
            val.setStyleSheet(f"color:{col};font-size:15pt;font-weight:900;"); val.setSizePolicy(QSizePolicy.Ignored,QSizePolicy.Preferred)
            sub=QLabel(""); sub.setObjectName("StreamMeta"); sub.setSizePolicy(QSizePolicy.Ignored,QSizePolicy.Preferred)
        text.addWidget(lab); text.addWidget(val)
        if sub is not None: text.addWidget(sub)
        row.addWidget(icon); row.addLayout(text,1)
        return c,val,sub

    def dashboard(self):
        w=QWidget(); l=QVBoxLayout(w); l.setContentsMargins(0,4,0,0); l.setSpacing(12)
        banner=QFrame(); banner.setObjectName("Banner"); br=QHBoxLayout(banner); br.setContentsMargins(16,12,16,12); br.setSpacing(12)
        bi=QLabel(); bi.setPixmap(_pix("info",22,GREEN)); br.addWidget(bi); bt=QLabel("Всичко важно за стриймовете и акаунтите на едно място."); bt.setObjectName("BannerText"); br.addWidget(bt,1)
        bx=icon_button("close","Затвори","BannerClose",28,14,"#9fd8bf","#ff6b7a"); bx.clicked.connect(self.close_banner); br.addWidget(bx)
        self.banner=banner
        if QSettings("StreamActivityBot","ui").value("banner_closed",False,type=bool): banner.hide()
        l.addWidget(banner)

        bar=QHBoxLayout(); bar.setSpacing(8)
        self.edit_hint=QLabel("Влачи заглавието, за да преместиш контейнер. Дърпай зелените дръжки (десен ръб, долен ръб, ъгъл), за да го оразмериш; двоен клик на дръжка = автоматична височина. ✕ го премахва."); self.edit_hint.setObjectName("StreamMeta"); self.edit_hint.setWordWrap(True); self.edit_hint.hide()
        bar.addWidget(self.edit_hint,1); bar.addStretch(0)
        self.add_box_btn=QPushButton("＋  Добави контейнер"); self.add_box_btn.setObjectName("ToolBtn"); self.add_box_btn.setCursor(Qt.PointingHandCursor); self.add_box_btn.clicked.connect(self.add_container_menu); self.add_box_btn.hide()
        self.reset_box_btn=QPushButton("Нулирай"); self.reset_box_btn.setObjectName("ToolBtn"); self.reset_box_btn.setCursor(Qt.PointingHandCursor); self.reset_box_btn.clicked.connect(self.reset_containers); self.reset_box_btn.hide()
        for b in (self.add_box_btn,self.reset_box_btn): bar.addWidget(b)
        l.addLayout(bar)

        self.canvas=DashCanvas(self,self.layout_model)
        area=QScrollArea(); area.setWidgetResizable(True); area.setFrameShape(QFrame.NoFrame); area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        area.setStyleSheet("QScrollArea{background:transparent;border:0;} QScrollArea>QWidget>QWidget{background:transparent;}")
        area.setWidget(self.canvas); self.dash_area=area; l.addWidget(area,1)
        return w

    # ---------------------------------------------------------------- edit mode
    def toggle_edit(self,_=False):
        on=not self.layout_model.edit_mode
        if on and self.tabs.currentWidget() is not self.home: self.tabs.setCurrentWidget(self.home)       # the layout is edited on the home page
        self.canvas.set_edit_mode(on)
        self.edit_btn.setProperty("on",on); self.edit_btn.setIcon(svg_icon("check" if on else "sliders",22,"#6ee7ad" if on else "#cfe0dd"))
        tip(self.edit_btn,"Готово - край на редакцията" if on else "Редактирай интерфейса",GREEN if on else "#b69bff")
        self.edit_btn.style().unpolish(self.edit_btn); self.edit_btn.style().polish(self.edit_btn)
        for w in (self.add_box_btn,self.reset_box_btn,self.edit_hint): w.setVisible(on)
    def add_container_menu(self,_=False):
        m=QMenu(self); m.setObjectName("QuickMenu"); types=self.layout_model.available_types()
        if not types: m.addAction("Всички контейнери вече са добавени").setEnabled(False)
        for t in types:
            m.addAction(TYPES[t][0],lambda tt=t:self.add_container(tt))
        m.exec(self.add_box_btn.mapToGlobal(QPoint(0,self.add_box_btn.height()+4)))
    def add_container(self,type_):
        it=self.layout_model.add(type_)
        if it is None: return
        self.canvas.rebuild(); self.canvas.set_edit_mode(self.layout_model.edit_mode)
        QTimer.singleShot(50,lambda:self.dash_area.verticalScrollBar().setValue(self.dash_area.verticalScrollBar().maximum()))
    def reset_containers(self,_=False):
        if QMessageBox.question(self,"Нулиране","Да върна контейнерите по подразбиране?")!=QMessageBox.Yes: return
        self.layout_model.reset(); self.canvas.rebuild(); self.canvas.set_edit_mode(self.layout_model.edit_mode)

    # ---------------------------------------------------------------- containers
    def make_container(self,canvas,item):
        t=item["type"]; title,_span,min_h,_multi=TYPES[t]
        box=DashContainer(canvas,item,title,min_h)
        body,refresh,live,extras=getattr(self,"build_c_"+t)(item)
        box.set_body(body,refresh,live,extras); box.set_span_label(item.get("span",COLUMNS//2))
        box.set_edit(self.layout_model.edit_mode); box.set_collapsed(item.get("collapsed",False))
        return box
    def scrolling_list(self,fill):
        """Container body: a scrolling list rebuilt by fill(layout); the scroll position is kept."""
        area,lay=self.scroll_list(8)
        def refresh():
            keep=area.verticalScrollBar().value(); self.clear_layout(lay); fill(lay); lay.addStretch()
            QTimer.singleShot(0,lambda k=keep:area.verticalScrollBar().setValue(k))
        return area,refresh
    # card key -> (title, icon, colour)
    METRIC_CATALOG={"streams":("СТРИЙМОВЕ","stream","#ff8f99"),"live":("LIVE СЕГА","live","#2ee08a"),
                    "accounts":("АКАУНТИ","account","#6aa2ff"),"time":("ВРЕМЕ","clock","#f2b84b"),
                    "points":("ТОЧКИ","points","#ffd166"),"bets":("ЗАЛОЗИ","bolt","#c792ff"),
                    "top_bet":("ТОП БУКВА","star","#ff9f43"),
                    "top_points":("ТОП ПО ТОЧКИ","trophy","#ffd166"),"top_time":("ТОП ПО ВРЕМЕ","clock","#6aa2ff")}
    METRIC_COMPACT={"top_points","top_time"}
    METRIC_DEFAULT=["streams","accounts","time","points"]
    def metric_values(self,keys):
        """Values of the chosen statistic cards (only what is needed is computed)."""
        out={}
        if {"time","points"}&set(keys):
            visible=[r for r in self.chat_store.profiles() if r[6] is not None or r[10] is not None]
            out["time"]=fmt_duration(sum(int(r[9] or 0) for r in visible)); out["points"]=f"{sum(int(r[5] or 0) for r in visible):,}"
        for kind in ("points","time"):
            if f"top_{kind}" in keys:
                ranked=self.top_ranked(kind)
                if ranked: out[f"top_{kind}"]=str(ranked[0][0] or "—"); out[f"top_{kind}_sub"]=(f"{ranked[0][1]:,} т." if kind=="points" else fmt_duration(ranked[0][2]))
                else: out[f"top_{kind}"]="—"; out[f"top_{kind}_sub"]="Още няма данни"
        out["streams"]=str(len(self.config.channels)); out["accounts"]=str(len(self.config.accounts))
        out["live"]=str(sum(1 for c in self.config.channels if self.engine.get_runtime(c.id).live))
        if {"bets","top_bet"}&set(keys):
            total=0; per={}; seen=set()
            for c in self.config.channels:
                k=self.engine.stream_key(c)
                if k in seen: continue
                seen.add(k); st=self.engine.bet_stats(k); total+=st["total_bets"]
                for L in st["letters"]: per[L["letter"]]=per.get(L["letter"],0)+L["bets"]
            out["bets"]=f"{total:,}"; out["top_bet"]=max(per,key=lambda x:(per[x],x)) if per else "—"
        return out
    @staticmethod
    def metric_columns(n,span):
        """How many stat cards fit in a row for a container that is *span*/12 wide."""
        avail=4 if span>=10 else (3 if span>=7 else 2); cols=min(max(1,n),avail)
        if cols==4 and n in (5,6): cols=3                 # 3 + 3 looks better than 4 + 2
        return cols
    def top_ranked(self,kind):
        """Accounts ranked by total points (kind='points') or watch time ('time'): [[name, points, seconds], ...]."""
        agg={}
        for r in self.chat_store.profiles():
            a=agg.setdefault(str(r[3]),[r[4],0,0]); a[1]+=int(r[5] or 0); a[2]+=int(r[9] or 0)
        idx=1 if kind=="points" else 2
        return sorted([x for x in agg.values() if x[idx]>0],key=lambda x:-x[idx])
    def build_c_metrics(self,item):
        w=QWidget(); g=QGridLayout(w); g.setContentsMargins(0,0,0,0); g.setHorizontalSpacing(12); g.setVerticalSpacing(12)
        cards=[k for k in (item.get("params",{}).get("cards") or self.METRIC_DEFAULT) if k in self.METRIC_CATALOG]
        cards=list(dict.fromkeys(cards)); labs={}; subs={}; ncols=self.metric_columns(len(cards),int(item.get("span",COLUMNS)))
        if not cards:
            g.addWidget(self.empty_label("Няма избрани карти. Натисни „Редактирай интерфейса“ → „Карти“."),0,0)
        for n,k in enumerate(cards):
            title,icon,col=self.METRIC_CATALOG[k]; card,val,sub=self.make_metric(title,icon,col,k in self.METRIC_COMPACT); labs[k]=val
            if sub is not None: subs[k]=sub
            g.addWidget(card,n//ncols,n%ncols)
        for c in range(ncols): g.setColumnStretch(c,1)
        def refresh():
            vals=self.metric_values(cards)
            for k,lab in labs.items(): lab.setText(vals.get(k,"—")); lab.setToolTip(vals.get(k,"") if k in subs else "")
            for k,lab in subs.items(): lab.setText(vals.get(k+"_sub",""))
        pick=QPushButton("Карти"); pick.setObjectName("MiniBtn"); pick.setCursor(Qt.PointingHandCursor); pick.setProperty("editOnly",True); pick.hide()
        pick.setToolTip("Избери кои карти да се виждат в статистиката")
        pick.clicked.connect(lambda _=False,b=pick,it=item,cur=list(cards):self.metrics_menu(b,it,cur))
        live=bool({"live","bets","top_bet"}&set(cards))          # DB-heavy cards refresh only when needed
        return w,refresh,live,[pick]
    def metrics_menu(self,anchor,item,current):
        m=QMenu(self); m.setObjectName("QuickMenu")
        for k,(title,_i,_c) in self.METRIC_CATALOG.items():
            a=m.addAction(title); a.setCheckable(True); a.setChecked(k in current)
            def toggle(checked,key=k):
                cur=list(current)
                if checked and key not in cur: cur.append(key)
                if not checked and key in cur: cur.remove(key)
                self.layout_model.set_param(item["id"],"cards",cur)
                QTimer.singleShot(0,lambda:(self.canvas.rebuild(),self.canvas.set_edit_mode(self.layout_model.edit_mode)))
            a.toggled.connect(toggle)
        m.exec(anchor.mapToGlobal(QPoint(0,anchor.height()+4)))
    def build_c_streams(self,item):
        def fill(lay):
            for ch in self.config.channels: lay.addWidget(self.stream_row(ch))
            if not self.config.channels: lay.addWidget(self.empty_label("Няма добавени стриймове."))
        area,refresh=self.scrolling_list(fill)
        add=icon_button("plus_bold","Добави стрийм","AddBtn",38,20,"#ffffff",GREEN); add.clicked.connect(self.add_channel)
        return area,refresh,False,[add]
    def build_c_accounts(self,item):
        def fill(lay):
            stats=self.account_stats()
            for a in self.config.accounts: lay.addWidget(self.account_row(a,stats))
            if not self.config.accounts: lay.addWidget(self.empty_label("Няма свързани акаунти."))
        area,refresh=self.scrolling_list(fill)
        add=icon_button("plus_bold","Добави акаунт","AddBtn",38,20,"#ffffff",GREEN); add.clicked.connect(lambda _=False,b=add:self.account_menu(b))
        return area,refresh,False,[add]
    def build_c_values(self,item):
        area,refresh=self.scrolling_list(self.fill_values); return area,refresh,False,[]
    def build_c_recent(self,item):
        area,refresh=self.scrolling_list(self.fill_recent); return area,refresh,False,[]
    def empty_label(self,text):
        e=QLabel(text); e.setObjectName("ActivityEmpty"); e.setWordWrap(True); return e
    def top_card(self,kind):
        w=QWidget(); v=QVBoxLayout(w); v.setContentsMargins(0,0,0,0); v.setSpacing(6)
        def refresh():
            self.clear_layout(v); idx=1 if kind=="points" else 2
            ranked=self.top_ranked(kind)
            fmt=(lambda x:f"{x:,} т.") if kind=="points" else fmt_duration
            if not ranked:
                v.addWidget(self.empty_label("Още няма получени точки." if kind=="points" else "Още няма получено време за гледане.")); v.addStretch(); return
            fr=QFrame(); fr.setObjectName("ValueRow"); fr.setProperty("kind",kind); fr.setMinimumHeight(76)
            rl=QHBoxLayout(fr); rl.setContentsMargins(14,10,16,10); rl.setSpacing(14)
            av=QLabel((ranked[0][0] or "?")[:1].upper()); av.setObjectName("Avatar"); av.setProperty("ok",True); av.setAlignment(Qt.AlignCenter); av.setFixedSize(46,46); rl.addWidget(av)
            n=QLabel(str(ranked[0][0])); n.setObjectName("StreamName"); rl.addWidget(n,1)
            val=QLabel(fmt(ranked[0][idx])); val.setObjectName("ValueBig"); val.setProperty("kind",kind); rl.addWidget(val); v.addWidget(fr)
            for pos,x in enumerate(ranked[1:3],start=2):
                t=QLabel(f"{pos}.  {x[0]}  —  {fmt(x[idx])}"); t.setObjectName("StreamMeta"); v.addWidget(t)
            v.addStretch()
        return w,refresh,False,[]
    def build_c_top_points(self,item): return self.top_card("points")
    def build_c_top_time(self,item): return self.top_card("time")
    def auto_button(self,a):
        enabled=bool(self.engine._account_timers.get(a.id,{}).get("enabled",a.automatic_activity_enabled))
        b=QPushButton("СТОП" if enabled else "ПУСНИ"); b.setObjectName("AutoBtn"); b.setProperty("on",enabled); b.setFixedHeight(40); b.setMinimumWidth(86); b.setCursor(Qt.PointingHandCursor)
        tip(b,"Спри автоматичните съобщения на този акаунт" if enabled else "Пусни отново автоматичните съобщения",RED if enabled else GREEN)
        b.clicked.connect(lambda _=False,aa=a,en=enabled:self.engine.set_account_auto(aa.id,not en))
        return b
    def build_c_timers(self,item):
        def fill(lay):
            for a in self.config.accounts:
                fr=QFrame(); fr.setObjectName("AccountKick" if a.platform==PLATFORM_KICK else "AccountYouTube"); fr.setMinimumHeight(64)
                rl=QHBoxLayout(fr); rl.setContentsMargins(14,8,12,8); rl.setSpacing(12)
                col=QVBoxLayout(); col.setSpacing(2); n=QLabel(a.display_name); n.setObjectName("StreamName"); col.addWidget(n)
                tl=QLabel(self.acc_timer_text(a.id)); tl.setObjectName("StreamMeta"); col.addWidget(tl); self.account_countdown_labels.setdefault(a.id,[]).append(tl)
                rl.addLayout(col,1); rl.addWidget(self.auto_button(a)); lay.addWidget(fr)
            if not self.config.accounts: lay.addWidget(self.empty_label("Няма свързани акаунти."))
        area,refresh=self.scrolling_list(fill); return area,refresh,False,[]
    def build_c_chat(self,item):
        w=QWidget(); v=QVBoxLayout(w); v.setContentsMargins(0,0,0,0); v.setSpacing(8)
        combo=QComboBox(); browser=QTextBrowser(); browser.setObjectName("StreamChat"); browser.setOpenLinks(False)
        v.addWidget(combo); v.addWidget(browser,1); st={"chans":None,"sig":None}
        def changed(_=0):
            cid=combo.currentData()
            if cid: self.layout_model.set_param(item["id"],"channel_id",cid)
            st["sig"]=None; refresh()
        combo.currentIndexChanged.connect(changed)
        def refresh():
            chans=[(c.id,c.display_name or c.url) for c in self.config.channels]
            if chans!=st["chans"]:
                combo.blockSignals(True); combo.clear()
                for cid,label in chans: combo.addItem(label,cid)
                idx=combo.findData(item.get("params",{}).get("channel_id"))
                if idx>=0: combo.setCurrentIndex(idx)
                combo.blockSignals(False); st["chans"]=chans; st["sig"]=None
            ch=self.config.get_channel(combo.currentData()) if combo.currentData() else None
            if ch is None:
                if st["sig"]!="none": browser.setHtml('<p style="color:#7f918f;">Няма добавен стрийм.</p>'); st["sig"]="none"
                return
            rt=self.engine.get_runtime(ch.id); rows=collect_chat_rows(self.engine,self.config,self.chat_store,ch,rt)
            sig=(ch.id,len(rows),rows[-1][5] if rows else "",rows[-1][2] if rows else "")
            if sig==st["sig"]: return
            st["sig"]=sig; set_chat_html(browser,rows)
        return w,refresh,True,[]
    def build_c_bets(self,item):
        panel=BetPanel(self); return panel,panel.refresh,True,[]
    def build_c_send(self,item):
        box=SendBox(self); return box,box.refresh,False,[]
    def build_c_log(self,item):
        view=QPlainTextEdit(); view.setObjectName("LogView"); view.setReadOnly(True); st={"n":-1}
        def refresh():
            n=len(self.log_lines)
            if n==st["n"]: return
            st["n"]=n; view.setPlainText("\n".join(self.log_lines)); view.verticalScrollBar().setValue(view.verticalScrollBar().maximum())
        return view,refresh,True,[]
    def attach_scroll(self,card):
        """A scrolling list inside a card: rows keep their size, the list scrolls instead of squeezing."""
        area=QScrollArea(); area.setWidgetResizable(True); area.setFrameShape(QFrame.NoFrame); area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        area.setStyleSheet("QScrollArea{background:transparent;border:0;} QScrollArea>QWidget>QWidget{background:transparent;}")
        inner=QWidget(); inner.setObjectName("ScrollInner"); lay=QVBoxLayout(inner); lay.setContentsMargins(0,0,8,0); lay.setSpacing(8)
        area.setWidget(inner); card.body.addWidget(area,1); return area,lay
    @staticmethod
    def clear_layout(lay):
        while lay.count():
            it=lay.takeAt(0); w=it.widget()
            if w: w.deleteLater()
    def account_stats(self):
        st={}
        for pr in self.chat_store.profiles():
            x=st.setdefault(str(pr[3]),[0,0,None]); x[0]+=int(pr[9] or 0); x[1]+=int(pr[5] or 0)
            if pr[7] is not None: x[2]=pr[7] if x[2] is None else max(x[2],pr[7])
        return st
    def acc_timer_text(self,aid,now=None):
        now=now or time.time(); st=self.engine._account_timers.get(aid,{}); nxt=st.get("nextActivityAt")
        if st.get("enabled") is False: return "Автоматичните съобщения са спрени"
        if not nxt: return "Следващо: ГОТОВ  ·  След: ГОТОВ"
        return f"Следващо: {time.strftime('%H:%M',time.localtime(nxt))}  ·  След: {fmt_clock(max(0,nxt-now))}"
    def account_row(self,a,acc_stats):
        """One account card (dashboard + Accounts page): avatar, platform colour, chips, countdown, actions."""
        frame=QFrame(); frame.setObjectName("AccountKick" if a.platform==PLATFORM_KICK else "AccountYouTube"); frame.setMinimumHeight(84)
        row=QHBoxLayout(frame); row.setContentsMargins(14,10,12,10); row.setSpacing(12)
        ok=self.tm.has_tokens(a.id)
        av=QLabel((a.display_name or "?")[:1].upper()); av.setObjectName("Avatar"); av.setProperty("ok",ok); av.setAlignment(Qt.AlignCenter); av.setFixedSize(46,46); tip(av,"Свързан" if ok else "Изисква вход",GREEN if ok else AMBER); row.addWidget(av)
        text=QVBoxLayout(); text.setSpacing(3)
        top=QHBoxLayout(); top.setSpacing(8); an=QLabel(a.display_name); an.setObjectName("StreamName"); top.addWidget(an)
        top.addWidget(AccountProfileDialog.chip(PLATFORM_LABEL.get(a.platform,a.platform),"green" if a.platform==PLATFORM_KICK else "red"))
        if not self.engine._account_timers.get(a.id,{}).get("enabled",a.automatic_activity_enabled): top.addWidget(AccountProfileDialog.chip("СПРЯН","red"))
        top.addStretch(); text.addLayout(top)
        sec,pts,_lvl=acc_stats.get(str(a.id),[0,0,None]); chips=QHBoxLayout(); chips.setSpacing(6)
        if pts: chips.addWidget(AccountProfileDialog.chip(f"{pts:,} т.","gold"))
        if sec: chips.addWidget(AccountProfileDialog.chip(fmt_duration(sec),"blue"))
        if not pts and not sec:
            m=QLabel("Още няма точки / време"); m.setObjectName("StreamMeta"); chips.addWidget(m)
        chips.addStretch(); text.addLayout(chips)
        tl=QLabel(self.acc_timer_text(a.id)); tl.setObjectName("StreamMeta"); text.addWidget(tl); self.account_countdown_labels.setdefault(a.id,[]).append(tl)
        row.addLayout(text,1)
        row.addWidget(self.auto_button(a))
        info=icon_button("user","Профил на акаунта","IconBtn",40,20,"#8fb8ff","#6aa2ff"); info.clicked.connect(lambda _,aa=a:self.open_account_profile(aa)); row.addWidget(info)
        d=icon_button("close","Премахни акаунт","DangerIcon",40,18,"#ff8f99","#ff6b7a"); d.clicked.connect(lambda _,aa=a:self.remove_account(aa)); row.addWidget(d)
        return frame

    def close_banner(self):
        self.banner.hide(); QSettings("StreamActivityBot","ui").setValue("banner_closed",True)
    def refresh_dashboard(self):
        if not self.canvas.dragging: self.canvas.refresh_all()      # never rebuild content under a running drag
    def fill_values(self,lay):
        """Последни стойности: EVERY received !points / !time answer is its own (new) row."""
        hist=self.chat_store.value_history(80)
        if not hist:
            e=QLabel("Още няма получени точки или време.\nНапиши !points или !time в чата."); e.setObjectName("ActivityEmpty"); lay.addWidget(e)
        for _id,aid,aname,sname,plat,kind,vtext,ts in hist:
            pts=(kind=="points")
            fr=QFrame(); fr.setObjectName("ValueRow"); fr.setProperty("kind",kind); fr.setMinimumHeight(72)
            rl=QHBoxLayout(fr); rl.setContentsMargins(12,10,14,10); rl.setSpacing(12)
            ic=QLabel(); ic.setObjectName("ValueIcon"); ic.setProperty("kind",kind); ic.setFixedSize(42,42); ic.setAlignment(Qt.AlignCenter)
            ic.setPixmap(_pix("trophy" if pts else "clock",22,"#ffd166" if pts else BLUE_L)); rl.addWidget(ic)
            tx=QVBoxLayout(); tx.setSpacing(2); n1=QLabel(str(aname)); n1.setObjectName("StreamName"); n2=QLabel(f"{sname}  ·  {'!points' if pts else '!time'}"); n2.setObjectName("StreamMeta"); tx.addWidget(n1); tx.addWidget(n2); rl.addLayout(tx,1)
            try: shown=f"{int(vtext):,} т." if pts else str(vtext)
            except ValueError: shown=str(vtext)
            rt=QVBoxLayout(); rt.setSpacing(2); val=QLabel(shown); val.setObjectName("ValueBig"); val.setProperty("kind",kind); val.setAlignment(Qt.AlignRight)
            ag=QLabel(fmt_ago(ts)); ag.setObjectName("ActivityTime"); ag.setAlignment(Qt.AlignRight); rt.addWidget(val); rt.addWidget(ag); rl.addLayout(rt)
            lay.addWidget(fr)
    def fill_recent(self,lay):
        activities=self.chat_store.recent_account_activity(80)
        shown=0
        for ar in activities:
            aid,ts,plat,channel,channel_id,stream_id,msg,source,mid=ar
            a=self.config.get_account(aid)
            if a is None: continue
            shown+=1
            fr=QFrame(); fr.setObjectName("ActivityRow"); fr.setMinimumHeight(96)
            rl=QHBoxLayout(fr); rl.setContentsMargins(12,10,12,10); rl.setSpacing(11)
            icon=QLabel({"automatic_send":"↗","manual_send":"↗","bot_response":"◆"}.get(str(source),"●")); icon.setObjectName("ActivityIcon"); icon.setAlignment(Qt.AlignCenter); icon.setFixedSize(34,34); rl.addWidget(icon,0,Qt.AlignTop)
            body=QVBoxLayout(); body.setSpacing(3); top=QHBoxLayout(); top.setSpacing(7)
            who=QLabel(("BOTTLY → " if str(source)=="bot_response" else "")+str(a.display_name)); who.setObjectName("ActivityName"); top.addWidget(who)
            badge=QLabel(str(plat)); badge.setObjectName("ActivityBadge"); top.addWidget(badge); top.addStretch()
            ago=QLabel(fmt_ago(ts)); ago.setObjectName("ActivityTime"); top.addWidget(ago); body.addLayout(top)
            sl=QLabel(str(channel)); sl.setObjectName("ActivityStream"); body.addWidget(sl)
            bubble=QLabel(str(msg)); bubble.setObjectName("ActivityMessage"); bubble.setWordWrap(True); body.addWidget(bubble); rl.addLayout(body,1)
            lay.addWidget(fr)
        if not shown:
            e=QLabel("Няма отчетена активност все още."); e.setObjectName("ActivityEmpty"); lay.addWidget(e)
    def add_account_clicked(self,_=False):
        w=self.sender(); self.account_menu(w if isinstance(w,QWidget) else None)
    def set_stream_chip(self,lab,rt,now=None):
        now=now or time.time()
        if rt.live: text,tone=("LIVE  "+(fmt_clock(now-rt.started_at) if rt.started_at else "")).strip(),"live"
        elif rt.connection_status not in ("Offline","Connected"): text,tone="НЯМА ВРЪЗКА","amber"
        else: text,tone="ОФЛАЙН","muted"
        lab.setText(text)
        if lab.property("tone")!=tone:
            lab.setProperty("tone",tone); lab.style().unpolish(lab); lab.style().polish(lab)
    def stream_row(self,ch,with_test=False):
        """One stream card (dashboard + Streams page): live icon, name, chips, actions."""
        rt=self.engine.get_runtime(ch.id); live=rt.live
        frame=QFrame(); frame.setObjectName("StreamKick" if ch.platform==PLATFORM_KICK else "StreamYouTube"); frame.setMinimumHeight(84)
        row=QHBoxLayout(frame); row.setContentsMargins(14,10,12,10); row.setSpacing(12)
        frame.setCursor(Qt.PointingHandCursor); frame.mousePressEvent=lambda ev,c=ch:self.open_stream_details(c)
        col=GREEN if live else "#6f8582"
        ic=QLabel(); ic.setPixmap(_pix("live" if live else "stream",24,col)); ic.setFixedSize(48,48); ic.setAlignment(Qt.AlignCenter)
        ic.setStyleSheet(f"background:{tint(col,14)};border:1px solid {tint(col,40)};border-radius:24px;"); row.addWidget(ic)
        text=QVBoxLayout(); text.setSpacing(4)
        name=QLabel(ch.display_name or ch.url); name.setObjectName("StreamName"); text.addWidget(name)
        chips=QHBoxLayout(); chips.setSpacing(6)
        chips.addWidget(AccountProfileDialog.chip(PLATFORM_LABEL[ch.platform],"green" if ch.platform==PLATFORM_KICK else "red"))
        st=AccountProfileDialog.chip("","muted"); self.set_stream_chip(st,rt); chips.addWidget(st); self.stream_dynamic_labels.setdefault(ch.id,[]).append(st)
        n=len(ch.connected_account_ids()); chips.addWidget(AccountProfileDialog.chip(f"{n} акаунта" if n!=1 else "1 акаунт","blue"))
        if with_test: chips.addWidget(AccountProfileDialog.chip(f"на всеки {ch.interval_minutes:g} мин","muted"))
        chips.addStretch(); text.addLayout(chips); row.addLayout(text,1)
        e=icon_button("edit","Редакция","IconBtn",40,20,"#8fb8ff","#6aa2ff"); e.clicked.connect(lambda _,c=ch:self.edit_channel(c)); row.addWidget(e)
        if with_test:
            t=icon_button("flask","Тест LIVE","IconBtn",40,20,"#f2b84b",AMBER); t.clicked.connect(lambda _,c=ch:self.sim_live(c)); row.addWidget(t)
        x=icon_button("close","Премахни стрийм","DangerIcon",40,18,"#ff8f99","#ff6b7a"); x.clicked.connect(lambda _,c=ch:self.remove_channel(c)); row.addWidget(x)
        return frame
    def scroll_list(self,spacing=10):
        area=QScrollArea(); area.setWidgetResizable(True); area.setFrameShape(QFrame.NoFrame); area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        area.setStyleSheet("QScrollArea{background:transparent;border:0;} QScrollArea>QWidget>QWidget{background:transparent;}")
        inner=QWidget(); inner.setObjectName("ScrollInner"); lay=QVBoxLayout(inner); lay.setContentsMargins(0,0,8,0); lay.setSpacing(spacing); area.setWidget(inner)
        return area,lay
    def page_head(self,title,subtitle,add_cb=None,add_tip=""):
        box=QHBoxLayout(); col=QVBoxLayout(); col.setSpacing(2)
        sub=QLabel(subtitle); sub.setObjectName("PageSub"); col.addWidget(sub)
        box.addLayout(col,1)
        if add_cb:
            b=icon_button("plus_bold",add_tip,"AddBtn",42,22,"#ffffff",GREEN); b.clicked.connect(add_cb); box.addWidget(b,0,Qt.AlignVCenter)
        return box
    def accounts_page(self):
        w=QWidget();l=QVBoxLayout(w);l.setContentsMargins(0,4,0,0);l.setSpacing(14)
        l.addLayout(self.page_head("Свързани акаунти","Акаунтите, с които ботът пише в чата.",self.add_account_clicked,"Добави акаунт"))
        self.accounts_area,self.accounts_lay=self.scroll_list(); l.addWidget(self.accounts_area,1); return w
    def refresh_accounts(self):
        keep=self.accounts_area.verticalScrollBar().value(); self.clear_layout(self.accounts_lay); stats=self.account_stats()
        for a in self.config.accounts: self.accounts_lay.addWidget(self.account_row(a,stats))
        if not self.config.accounts:
            e=QLabel("Няма свързани акаунти. Натисни + за да добавиш."); e.setObjectName("ActivityEmpty"); self.accounts_lay.addWidget(e)
        self.accounts_lay.addStretch(); QTimer.singleShot(0,lambda k=keep:self.accounts_area.verticalScrollBar().setValue(k))
    def channels_page(self):
        w=QWidget();l=QVBoxLayout(w);l.setContentsMargins(0,4,0,0);l.setSpacing(14)
        l.addLayout(self.page_head("Стриймове","Каналите, които ботът следи. Натисни карта за чата на живо.",self.add_channel,"Добави стрийм"))
        self.channels_area,self.channels_lay=self.scroll_list(); l.addWidget(self.channels_area,1); return w
    def style_table(self,t):
        t.setShowGrid(False); t.verticalHeader().setVisible(False); t.verticalHeader().setDefaultSectionSize(58)
        t.setEditTriggers(QAbstractItemView.NoEditTriggers); t.setSelectionBehavior(QAbstractItemView.SelectRows); t.setFocusPolicy(Qt.NoFocus)
        t.horizontalHeader().setMinimumHeight(44)
    def refresh_channels(self):
        keep=self.channels_area.verticalScrollBar().value(); self.clear_layout(self.channels_lay)
        for ch in self.config.channels: self.channels_lay.addWidget(self.stream_row(ch,with_test=True))
        if not self.config.channels:
            e=QLabel("Няма добавени стриймове. Натисни + за да добавиш."); e.setObjectName("ActivityEmpty"); self.channels_lay.addWidget(e)
        self.channels_lay.addStretch(); QTimer.singleShot(0,lambda k=keep:self.channels_area.verticalScrollBar().setValue(k))
    def send_page(self):
        w=QWidget();l=QVBoxLayout(w);l.setContentsMargins(0,4,0,0);l.setSpacing(14)
        l.addLayout(self.page_head("Ръчно съобщение","Изпрати съобщение от избрани акаунти към стрийм."))
        card=Card("Ново съобщение"); card.setObjectName("PanelCard")
        self.send_box=SendBox(self); card.body.addWidget(self.send_box,1)
        l.addWidget(card,1); return w
    def refresh_send(self):
        self.send_box.refresh()
    def profiles_page(self):
        w=QWidget();l=QVBoxLayout(w);l.setContentsMargins(0,4,0,0);l.setSpacing(14)
        l.addLayout(self.page_head("Точки и време","Последно получените стойности за всеки акаунт и стрийм."))
        self.profiles_area,self.profiles_lay=self.scroll_list(); l.addWidget(self.profiles_area,1); return w
    def refresh_profiles(self):
        keep=self.profiles_area.verticalScrollBar().value(); self.clear_layout(self.profiles_lay); shown=0
        for r0 in self.chat_store.profiles():
            _sk,sname,plat,_aid,aname,pts,_pts_ts,_lvl,wtext,wsec,_t_ts=r0
            if pts is None and not (wtext or wsec): continue
            shown+=1
            fr=QFrame(); fr.setObjectName("ValueRow"); fr.setProperty("kind","points"); fr.setMinimumHeight(84)
            rl=QHBoxLayout(fr); rl.setContentsMargins(14,10,16,10); rl.setSpacing(14)
            av=QLabel((aname or "?")[:1].upper()); av.setObjectName("Avatar"); av.setProperty("ok",True); av.setAlignment(Qt.AlignCenter); av.setFixedSize(46,46); rl.addWidget(av)
            tx=QVBoxLayout(); tx.setSpacing(3); n1=QLabel(str(aname)); n1.setObjectName("StreamName"); n2=QLabel(f"{sname}  ·  {PLATFORM_LABEL.get(plat,plat)}"); n2.setObjectName("StreamMeta"); tx.addWidget(n1); tx.addWidget(n2); rl.addLayout(tx,1)
            if pts is not None:
                v=QLabel(f"{int(pts):,} т."); v.setObjectName("ValueBig"); v.setProperty("kind","points"); rl.addWidget(v)
            if wtext or wsec:
                v=QLabel(wtext or fmt_duration(wsec)); v.setObjectName("ValueBig"); v.setProperty("kind","time"); rl.addWidget(v)
            self.profiles_lay.addWidget(fr)
        if not shown:
            e=QLabel("Още няма получени точки или време.\nНапиши !points или !time в чата."); e.setObjectName("ActivityEmpty"); self.profiles_lay.addWidget(e)
        self.profiles_lay.addStretch(); QTimer.singleShot(0,lambda k=keep:self.profiles_area.verticalScrollBar().setValue(k))
    def activity_page(self):
        w=QWidget();l=QVBoxLayout(w);l.setContentsMargins(0,4,0,0);l.setSpacing(14)
        l.addLayout(self.page_head("Активност","Системен лог на живо: връзки, команди, грешки."))
        self.activity=QPlainTextEdit(); self.activity.setObjectName("LogView"); self.activity.setReadOnly(True); self.activity.setMaximumBlockCount(2000); l.addWidget(self.activity,1); return w
    def help_page(self):
        w=QWidget();l=QVBoxLayout(w);l.setContentsMargins(0,4,0,0);l.setSpacing(14)
        l.addLayout(self.page_head("Помощ","Стъпка по стъпка настройка на платформите."))
        for key,title,sub,icon,tone in (("setup_kick","Kick настройка","Как да свържеш Kick акаунт","stream","green"),("setup_google","Google / YouTube настройка","OAuth клиент и разрешения","api","blue"),("youtube_quota","YouTube квота","Колко заявки са ти нужни дневно","help","purple")):
            b=QPushButton(f"   {title}\n   {sub}"); b.setObjectName("QuickTile"); b.setProperty("tone",tone); b.setIcon(svg_icon(icon,26,"#ffffff")); b.setIconSize(QSize(26,26)); b.setFixedHeight(76); b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _,k=key,tt=title:TextDialog(self,tt,ht.HELP[k]).exec()); l.addWidget(b)
        l.addStretch(); return w
    def refresh_all(self):
        self.refresh_dashboard();self.refresh_accounts();self.refresh_channels();self.refresh_send();self.refresh_profiles();self.update_state_icons()
    def update_state_icons(self):
        run=self.engine.running; demo=self.config.settings.demo_mode
        self.status.setText("Работи" if run else "Спрян"); self.mode.set_demo(demo); self.toggle_btn.setText("  СТОП" if run else "  СТАРТ")
        self.status.setIcon(QIcon()); tip(self.status,"Работи" if run else "Спрян",GREEN if run else "#ff6b7a"); self.status.setProperty("on",run)
        tip(self.mode,"ДЕМО: нищо не се праща в чата. Натисни LIVE за реален режим." if demo else "LIVE: съобщенията и залозите се пращат реално. Натисни ДЕМО за тест.",AMBER if demo else "#ff6b7a")
        for w in (self.status,): w.style().unpolish(w); w.style().polish(w)
        tip(self.toggle_btn,"Стоп" if run else "Старт","#ff6b7a" if run else GREEN); self.toggle_btn.setIcon(svg_icon("stop" if run else "play",18,"#ffffff")); self.toggle_btn.setProperty("running",run)
        self.toggle_btn.style().unpolish(self.toggle_btn); self.toggle_btn.style().polish(self.toggle_btn)
    def first_run(self):
        s=self.config.settings;configured=bool((s.kick_client_id and self.tokens.get_secret("kick_client_secret")) or (s.google_client_json and acc.load_google_client(s.google_client_json)))
        if not configured:self.open_setup()
    def open_setup(self):
        if SetupDialog(self,self.config,self.tokens).exec():self.refresh_all()
    def chat_settings(self):TextDialog(self,"Chat настройки","Настройките за chat tracking, !points и !time се пазят в config.\n\nКомандите и отговорите се записват в chat_log.db и chat_points/chat_times JSONL файловете.").exec()
    def show_logs(self):TextDialog(self,"Логове",self.chat_store.chat_file.read_text(encoding="utf-8")[-20000:] if self.chat_store.chat_file.exists() else "Няма логове.").exec()
    def show_errors(self):TextDialog(self,"Грешки","Виж Activity таба за runtime събития и грешки.").exec()
    def add_channel(self):
        d=ChannelDialog(self,self.config);center(d,self)
        if d.exec() and d.result:self.config.add_channel(d.result);self.engine.channel_changed(d.result);self.engine.sync_profiles();self.refresh_all()
    def edit_channel(self,ch):
        d=ChannelDialog(self,self.config,ch);center(d,self)
        if d.exec() and d.result:self.config.replace_channel(d.result);self.engine.channel_changed(d.result);self.engine.sync_profiles();self.refresh_all()
    def remove_channel(self,ch):
        if QMessageBox.question(self,"Премахване",f"Премахване на {ch.display_name or ch.url}?")==QMessageBox.Yes:self.engine.channel_removed(ch.id);self.config.remove_channel(ch.id);self.chat_store.delete_profiles(stream_key=self.engine.stream_key(ch));self.refresh_all()
    def on_mode_requested(self,want_demo):
        s=self.config.settings
        if bool(want_demo)==bool(s.demo_mode): return
        if not want_demo:
            r=QMessageBox.question(self,"LIVE режим","Ботът ще изпраща РЕАЛНИ съобщения и залози в чата на стриймовете с избраните акаунти.\n\nПревключването започва начисто: демо данните и симулираните стриймове се изтриват, а ако ботът работи, се рестартира.\n\nПревключване към LIVE?")
            if r!=QMessageBox.Yes: return
        self.engine.switch_mode(bool(want_demo))
        self.refresh_all()
    def toggle_mode(self):self.on_mode_requested(not self.config.settings.demo_mode)
    def sim_live(self,ch):self.engine.toggle_sim_live(ch.id);self.refresh_all()
    def account_menu(self,anchor=None):
        from PySide6.QtWidgets import QMenu
        m=QMenu(self)
        for p in (PLATFORM_KICK,PLATFORM_YT): m.addAction(PLATFORM_LABEL[p],lambda pp=p:self.connect_account(pp))
        anchor=anchor or self.quick_btn; m.exec(anchor.mapToGlobal(anchor.rect().bottomLeft()))
    def quick_actions_menu(self):
        m=QMenu(self); m.setObjectName("QuickMenu")
        def act(text,icon,fn):
            ac=m.addAction(svg_icon(icon,18,NAV_ACCENT.get(icon,GREEN)),text); ac.triggered.connect(lambda _=False,f=fn:f())
        act("Съобщение","message",lambda:self.tabs.setCurrentWidget(self.send_tab))
        act("API настройки","api",self.open_setup)
        act("Чат настройки","sliders",self.chat_settings)
        m.addSeparator()
        act("Логове","logs",self.show_logs)
        act("Грешки","error",self.show_errors)
        btn=self.quick_btn; h=m.sizeHint().height()
        m.exec(btn.mapToGlobal(QPoint(btn.width()+8,btn.height()-h)))        # opens next to the button, upwards
    def open_stream_details(self,ch):
        wins=self.__dict__.setdefault("_stream_windows",{})
        d=wins.get(ch.id)
        if d is not None:
            try:
                d.show(); d.raise_(); d.activateWindow(); return
            except RuntimeError: wins.pop(ch.id,None)
        d=StreamDetailsDialog(self,ch,self.engine,self.config,self.chat_store); d.setAttribute(Qt.WA_DeleteOnClose,True)
        d.finished.connect(lambda _=0,k=ch.id:wins.pop(k,None)); wins[ch.id]=d; d.show()
    def open_account_profile(self,a):
        AccountProfileDialog(self,a,self.engine,self.config,self.chat_store,self.tm).exec()
    def update_dynamic_labels(self):
        now=time.time()
        try: self.canvas.tick()
        except Exception: pass
        for cid,labels in getattr(self,"stream_dynamic_labels",{}).items():
            if self.config.get_channel(cid) is None: continue
            rt=self.engine.get_runtime(cid)
            for label in labels:
                try: self.set_stream_chip(label,rt,now)
                except RuntimeError: pass            # widget already replaced by a refresh
        for aid,labels in getattr(self,"account_countdown_labels",{}).items():
            text=self.acc_timer_text(aid,now)
            for label in labels:
                try: label.setText(text)
                except RuntimeError: pass            # widget already replaced by a refresh

    def close_login_dialog(self):
        d=getattr(self,"login_dialog",None)
        if d is not None:
            try:d.close()
            except Exception:pass
            self.login_dialog=None
    def connect_account(self,platform):
        self.close_login_dialog();self.oauth_cancel=threading.Event();
        def worker():
            try:
                a,isnew=acc.connect_account(platform,self.config,self.tokens,cancel=self.oauth_cancel);self.events.put(("account",a.display_name,platform))
            except Exception as e:self.events.put(("log",f"OAuth: {e}"));self.events.put(("oauth_end",))
        threading.Thread(target=worker,daemon=True).start()
        dlg=QMessageBox(QMessageBox.Information,"Вход",f"Ще се отвори браузър за {PLATFORM_LABEL[platform]}. След успешен вход прозорецът ще се затвори автоматично.",QMessageBox.Ok,self)
        dlg.setWindowModality(Qt.NonModal);dlg.setAttribute(Qt.WA_DeleteOnClose,True);self.login_dialog=dlg;dlg.show()
    def remove_account(self,a):
        if QMessageBox.question(self,"Премахване",f"Премахване на акаунта {a.display_name}?")==QMessageBox.Yes:self.tokens.delete_tokens(a.id);self.config.remove_account(a.id);self.chat_store.delete_profiles(account_id=a.id);self.engine.account_changed(a.id);self.refresh_all()
    def start(self):
        if not self.config.channels:QMessageBox.information(self,"Старт","Първо добави поне един стрийм.");return
        self.engine.start();self.refresh_all()
    def stop(self):self.engine.stop();self.refresh_all()
    def toggle_run(self):
        if self.engine.running:self.stop()
        else:self.start()
    def save(self):self.config.save();self.engine.sync_profiles();self.refresh_all()
    def pump(self):
        changed=False
        while True:
            try:e=self.events.get_nowait()
            except queue.Empty:break
            if e[0] in ("tick","chat","bets"): continue
            changed=True
            if e[0]=="log": self.activity.appendPlainText(str(e[1])); self.log_lines.append(str(e[1]))
            elif e[0]=="stream_send_result":
                r=e[1]; QMessageBox.information(self,"SEND"," / ".join([str(r.get("ok")),str(r.get("error") or f"sent={r.get('sent',0)}")])) if not r.get("ok") else None
            elif e[0]=="account":self.close_login_dialog();self.activity.appendPlainText(f"Свързан акаунт: {e[1]} ({PLATFORM_LABEL.get(e[2],e[2])})")
            elif e[0]=="oauth_end":self.close_login_dialog()
        if changed:self.refresh_all()
    def closeEvent(self,event):
        for d in list(self.__dict__.get("_stream_windows",{}).values()):
            try: d.close()
            except RuntimeError: pass
        self.engine.stop();self.config.save();self.engine.close();self.chat_store.close();event.accept()

STYLE=f"""
*{{font-family:'Segoe UI';font-size:11pt;color:{FG};}}
QMainWindow{{background:qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 #16313a,stop:0.55 #112a30,stop:1 #0e2227);}}
QWidget{{background:transparent;}}
QMessageBox,QInputDialog,QFileDialog{{background:{PANEL};}}
QLabel{{background:transparent;border:0;}}
QToolTip{{background:#2ee08a;color:#04100b;border:1px solid #2ee08a;padding:6px 12px;font-weight:700;}}

QFrame#Sidebar{{background:qlineargradient(x1:0,y1:0,x2:0,y2:1,stop:0 #112a2f,stop:1 #0c1f23);border:0;border-right:1px solid #234146;}}
QLabel#Brand{{font-size:17pt;font-weight:900;color:#ffffff;letter-spacing:0.5px;}}
QLabel#BrandTag{{font-size:7pt;font-weight:800;letter-spacing:2px;color:#ff6b7a;}}
QLabel#BrandMini{{font-size:15pt;font-weight:900;color:{GREEN};letter-spacing:1px;}}
QLabel#NavSection{{font-size:8pt;font-weight:800;letter-spacing:1.5px;color:#5f7573;padding-left:6px;}}
QFrame#NavSep{{background:#1a2628;border:0;}}
QPushButton#Nav{{background:transparent;border:1px solid transparent;border-radius:12px;padding:0 12px;min-height:42px;text-align:left;color:#aebfbc;font-size:11pt;font-weight:650;}}
QPushButton#Nav:hover{{background:rgba(255,255,255,5%);color:#ffffff;}}
QPushButton#Nav[collapsed="true"]{{padding:0;min-height:48px;max-height:48px;border-radius:14px;background:#16292d;border:1px solid #1b2b2d;}}
QPushButton#Nav[collapsed="true"]:hover{{background:#15262a;border-color:#2f6b57;}}
QPushButton#Nav[collapsed="true"]:checked{{background:qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 #12b568,stop:1 #0b5a3a);border:1px solid #3ee89a;}}
QPushButton#CollapseBtn{{background:#16282a;border:1px solid #3a5a58;border-radius:18px;padding:0;min-height:0;min-width:0;}}
QPushButton#CollapseBtn:hover{{background:#1a2b2d;border-color:#3ee89a;}}
QWidget#BrandBox{{background:transparent;}}
QPushButton#Nav:checked{{background:qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 rgba(46,224,138,24%),stop:1 rgba(46,224,138,3%));border:1px solid rgba(46,224,138,32%);border-left:3px solid #2ee08a;color:#ffffff;}}

QLabel#TopTitle{{font-size:25pt;font-weight:850;color:#ffffff;letter-spacing:0.3px;}}
QLabel#PageSub{{color:#7f918f;font-size:10.5pt;}}
QLabel#Eyebrow{{font-size:9pt;font-weight:800;letter-spacing:3px;color:{GREEN};}}
QLabel#PageTitle{{font-size:30pt;font-weight:800;color:#ffffff;}}
QLabel#DialogTitle{{font-size:20pt;font-weight:800;color:#ffffff;}}
QLabel#CardTitle{{font-size:13.5pt;font-weight:850;color:#f1f8f6;}}
QLabel#Muted{{color:{MUTED};}}
QLabel#AccentText{{color:{GREEN};font-weight:700;}}
QLabel#Error{{color:{RED};font-weight:650;}}

QFrame#Banner{{background:qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 #0f2e23,stop:1 #0d1d1b);border:1px solid #1d6e4d;border-radius:14px;}}
QLabel#BannerText{{font-weight:650;color:#e6f6f0;}}

QFrame#Card,QFrame#PanelCard{{background:qlineargradient(x1:0,y1:0,x2:0,y2:1,stop:0 #1a343a,stop:1 #142a30);border:1px solid {BORDER};border-radius:20px;}}
QFrame#MetricCard{{background:{CARD};border:1px solid {BORDER};border-radius:20px;min-height:96px;}}
QLabel#MetricIcon{{background:#0f2a21;border:1px solid #1f7a57;border-radius:14px;}}
QLabel#MetricLabel{{color:{MUTED};font-size:9pt;font-weight:800;letter-spacing:2px;}}
QLabel#Metric{{font-size:24pt;font-weight:900;color:{GREEN};}}

QLabel#StreamName{{font-size:11.5pt;font-weight:750;color:#f1f8f6;}}
QLabel#StreamMeta{{color:{MUTED};font-size:9.5pt;}}
QFrame#StreamKick{{background:qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 #10231f,stop:1 #0d1819);border:1px solid #1d3a32;border-left:4px solid {GREEN};border-radius:16px;}}
QFrame#StreamKick:hover{{border-color:#2d6c55;border-left:4px solid {GREEN};}}
QFrame#StreamYouTube{{background:qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 #231619,stop:1 #130f10);border:1px solid #3a2428;border-left:4px solid #ff5d63;border-radius:16px;}}
QFrame#StreamYouTube:hover{{border-color:#6b3640;border-left:4px solid #ff5d63;}}
QFrame#AccountKick{{background:qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 #10231f,stop:1 #0d1819);border:1px solid #1d3a32;border-radius:16px;}}
QFrame#AccountYouTube{{background:qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 #231619,stop:1 #130f10);border:1px solid #3a2428;border-radius:16px;}}
QFrame#RankRow{{background:#14292e;border:1px solid #1a2a2c;border-radius:12px;}}
QTextBrowser#StreamChat{{background:#0f2226;border:1px solid #1b2b2d;border-radius:16px;padding:10px;font-size:10.5pt;}}
QFrame#ActivityRow{{background:qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 #15303a,stop:1 #183039);border:1px solid #1b2b2d;border-radius:14px;}}
QFrame#ActivityRow:hover{{border-color:#2d6c55;background:#1a3836;}}
QLabel#ActivityIcon{{background:#0f2b21;border:1px solid #236b50;border-radius:10px;color:#35e993;font-size:13pt;font-weight:900;}}
QLabel#ActivityName{{color:#f1f8f6;font-size:10.5pt;font-weight:800;}}
QLabel#ActivityBadge{{background:#122a24;border:1px solid #245b48;border-radius:8px;color:#6ee7ad;padding:2px 7px;font-size:8pt;font-weight:800;}}
QLabel#ActivityTime{{color:#6f8582;font-size:8.5pt;font-weight:700;}}
QLabel#ActivityStream{{color:#738986;font-size:8.5pt;font-weight:700;}}
QLabel#ActivityMessage{{background:#183039;border:1px solid #1e3032;border-radius:9px;color:#dce9e5;padding:7px 9px;font-size:9.5pt;}}
QLabel#ActivityEmpty{{background:#14292e;border:1px dashed #26393b;border-radius:12px;color:#6f8582;padding:20px;text-align:center;}}
QLabel#RankBadge{{background:#0f2024;border:1px solid #2a3b3d;border-radius:10px;font-weight:800;color:#dbe8e5;}}
QLabel#LevelPill{{background:#0f9d55;color:#ffffff;border-radius:11px;padding:4px 14px;font-size:9pt;font-weight:900;letter-spacing:1px;}}

QLabel#StatusOn{{color:{GREEN};background:#0f2a21;border:1px solid #1f8a5f;border-radius:12px;padding:9px 16px;font-weight:800;}}
QLabel#StatusOff{{color:#aab9b6;background:#121c1e;border:1px solid #243436;border-radius:12px;padding:9px 16px;font-weight:800;}}
QLabel#Pill,QLabel#PillWarn{{color:#c4d6d2;background:#121c1e;border:1px solid #243436;border-radius:12px;padding:9px 16px;font-weight:800;}}
QLabel#PillDanger{{color:#ff8f99;background:#2a1519;border:1px solid #6b3640;border-radius:12px;padding:9px 16px;font-weight:800;}}

QPushButton{{background:#142022;border:1px solid #243436;border-radius:11px;padding:9px 18px;font-weight:700;color:#eaf3f1;min-height:38px;}}
QPushButton:hover{{background:#1a2b2d;border-color:#2f6b57;}}
QPushButton:pressed{{background:#101a1c;}}
QPushButton#Primary,QPushButton#HeaderPrimary{{background:qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 #0f9d55,stop:1 #1fd37a);border:1px solid #3ee89a;color:#ffffff;font-weight:800;border-radius:22px;padding:0 22px;min-height:0;}}
QPushButton#Primary:hover,QPushButton#HeaderPrimary:hover{{border-color:#a5ffd2;}}
QPushButton#HeaderButton{{background:#121c1e;border:1px solid #2a3b3d;border-radius:22px;padding:0;min-height:0;}}
QPushButton#HeaderButton:hover{{background:#1a2b2d;border-color:#3b6fb5;}}
QPushButton#HeaderButton[on="true"]{{background:#0f2a21;border:1px solid #2ee08a;}}
QFrame#ResizeGuide{{background:rgba(46,224,138,12%);border:2px dashed #2ee08a;border-radius:20px;}}
QWidget#NavRow[drop="true"]{{background:rgba(46,224,138,16%);border:1px dashed #2ee08a;border-radius:12px;}}
QPushButton#IconBtn{{background:#121c1e;border:1px solid #2a3b3d;border-radius:20px;padding:0;min-height:0;}}
QPushButton#DangerIcon{{background:#241418;border:1px solid #5e3039;border-radius:20px;padding:0;min-height:0;}}
QPushButton#DangerIcon:hover{{background:#3a1b22;border-color:#ff6b7a;}}
QPushButton#QuickTile{{border-radius:18px;padding:0 20px;min-height:0;border:1px solid transparent;text-align:left;color:#ffffff;font-size:10.5pt;font-weight:800;}}
QPushButton#QuickTile[tone="green"]{{background:qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 #12b568,stop:1 #0b5a3a);border-color:#3ee89a;}}
QPushButton#QuickTile[tone="blue"]{{background:qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 #3b7bff,stop:1 #18398f);border-color:#7fb0ff;}}
QPushButton#QuickTile[tone="purple"]{{background:qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 #9a6bff,stop:1 #4a2a9a);border-color:#c3a9ff;}}
QPushButton#QuickTile[tone="red"]{{background:qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 #f0506a,stop:1 #8a2133);border-color:#ff9aa8;}}
QPushButton#QuickTile:hover{{border-color:#ffffff;}}
QPushButton#QuickTile:pressed{{margin-top:2px;}}
QPushButton#IconBtn:hover{{background:#1a2b2d;border-color:#2f6b57;}}
QPushButton#PlusBtn{{background:#0f2a21;border:1px solid #1f7a57;border-radius:17px;padding:0;min-height:0;min-width:0;}}
QPushButton#PlusBtn:hover{{background:#143a2d;border-color:#3ee89a;}}
QPushButton#StatusIcon,QPushButton#ModeIcon{{background:#121c1e;border:1px solid #243436;border-radius:21px;padding:0 16px;min-height:0;color:#9fb2af;font-weight:800;font-size:10pt;text-align:center;}}
QPushButton#StatusIcon:disabled,QPushButton#ModeIcon:disabled{{background:#121c1e;border:1px solid #243436;}}
QPushButton#StatusIcon[on="true"]{{background:#0f2a21;border:1px solid #1f8a5f;color:#6ee7ad;}}
QPushButton#StatusIcon[on="false"]{{color:#ff8f99;}}
QPushButton#ModeIcon[demo="true"]{{background:#2a2312;border:1px solid #6b5a2a;color:#f2b84b;}}
QPushButton#ModeIcon[demo="false"]{{background:#2a1519;border:1px solid #6b3640;color:#ff8f99;}}
QPushButton#HeaderPrimary[running="true"]{{background:qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 #b3303f,stop:1 #e0495a);border:1px solid #ff8f99;}}
QPushButton#Ghost{{background:#121c1e;border:1px solid #2a3b3d;border-radius:10px;padding:7px 16px;}}
QPushButton#Danger{{color:#ff8f99;background:#241418;border-color:#5e3039;border-radius:10px;}}
QPushButton#QuickPurple,QPushButton#QuickBlue{{min-height:48px;border-radius:12px;padding:10px 18px;font-size:11pt;font-weight:800;color:#ffffff;text-align:left;}}
QPushButton#QuickPurple{{background:#0f2a21;border:1px solid #1f7a57;}}
QPushButton#QuickPurple:hover{{background:#143a2d;border-color:#2fb585;}}
QPushButton#QuickBlue{{background:#102228;border:1px solid #1f5f70;}}
QPushButton#QuickBlue:hover{{background:#163240;border-color:#3b95ad;}}

QLineEdit,QTextEdit,QPlainTextEdit,QSpinBox,QDoubleSpinBox,QComboBox{{background:{INPUT};border:1px solid #243436;border-radius:14px;padding:11px 14px;color:{FG};selection-background-color:#14754f;}}
QLineEdit:focus,QTextEdit:focus,QPlainTextEdit:focus,QSpinBox:focus,QComboBox:focus{{border:1px solid {GREEN};background:#0e1a1c;}}
QComboBox QAbstractItemView{{background:{CARD};border:1px solid {BORDER};selection-background-color:#14754f;outline:0;}}

QListWidget,QTableWidget{{background:{CARD};border:1px solid {BORDER};border-radius:16px;gridline-color:transparent;alternate-background-color:{CARD};}}
QListWidget::item,QTableWidget::item{{padding:10px;border-bottom:1px solid #17252a;background:transparent;}}
QListWidget::item:selected,QTableWidget::item:selected{{background:#0f2a21;color:#ffffff;}}
QWidget#CellBox{{background:transparent;}}
QHeaderView{{background:transparent;}}
QHeaderView::section{{background:#122a2f;color:#6f8582;padding:12px 10px;border:0;border-bottom:1px solid {BORDER};font-size:9pt;font-weight:800;letter-spacing:2px;}}
QTableCornerButton::section{{background:#122a2f;border:0;}}

QCheckBox{{background:transparent;spacing:10px;}}
QCheckBox::indicator{{width:20px;height:20px;border:1px solid #35504b;border-radius:6px;background:{INPUT};}}
QCheckBox::indicator:checked{{background:{GREEN_D};border-color:{GREEN};}}
QDialog{{background:{PANEL};}}
QDialogButtonBox QPushButton{{min-width:120px;}}
QScrollArea{{background:transparent;border:0;}}
QWidget#ScrollInner{{background:transparent;}}
QFrame#DashBox{{background:qlineargradient(x1:0,y1:0,x2:0,y2:1,stop:0 #1a343a,stop:1 #142a30);border:1px solid {BORDER};border-radius:20px;}}
QFrame#DashBox[edit="true"]{{border:1px dashed #2ee08a;}}
QFrame#DashBox[drop="true"]{{border:2px solid #2ee08a;background:#10261f;}}
QWidget#DashBody{{background:transparent;}}
QPushButton#ToolBtn{{background:#121c1e;border:1px solid #2a3b3d;border-radius:18px;padding:0 18px;min-height:36px;font-weight:700;color:#dff0ec;}}
QPushButton#ToolBtn:hover{{background:#16282a;border-color:#2ee08a;}}
QPushButton#ToolBtn[on="true"]{{background:#0f2a21;border:1px solid #2ee08a;color:#6ee7ad;}}
QPushButton#MiniBtn{{background:#121c1e;border:1px solid #2a3b3d;border-radius:14px;padding:0 12px;min-height:28px;font-size:9pt;font-weight:700;color:#cfe0dd;}}
QPushButton#MiniBtn:hover{{border-color:#2ee08a;}}
QPushButton#AutoBtn{{border-radius:20px;padding:0 16px;min-height:0;font-weight:800;font-size:10pt;}}
QPushButton#AutoBtn[on="true"]{{background:#2a1519;border:1px solid #6b3640;color:#ff8f99;}}
QPushButton#AutoBtn[on="true"]:hover{{background:#3a1c22;}}
QPushButton#AutoBtn[on="false"]{{background:#0f2a21;border:1px solid #1f8a5f;color:#6ee7ad;}}
QPushButton#AutoBtn[on="false"]:hover{{background:#143a2d;}}
QListWidget::indicator{{width:18px;height:18px;border:1px solid #35504b;border-radius:6px;background:{INPUT};}}
QListWidget::indicator:checked{{background:{GREEN_D};border-color:{GREEN};}}
QPlainTextEdit#LogView{{background:#0c1c20;border:1px solid {BORDER};border-radius:18px;padding:14px;font-family:'Cascadia Mono','Consolas',monospace;font-size:10pt;color:#a9d8c6;selection-background-color:#14754f;}}
QLabel#ValueBig[kind="time"]{{color:#8fb8ff;}}

QPushButton#AddBtn{{background:qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 #2ee08a,stop:1 #0c8f55);border:1px solid #7dffc0;border-radius:19px;padding:0;min-height:0;min-width:0;}}
QPushButton#AddBtn:hover{{background:qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 #55f0a5,stop:1 #12a863);border-color:#ffffff;}}
QPushButton#AddBtn:pressed{{background:#0c8f55;}}
QPushButton#BannerClose{{background:transparent;border:1px solid transparent;border-radius:14px;padding:0;min-height:0;min-width:0;}}
QPushButton#BannerClose:hover{{background:#2a1519;border-color:#6b3640;}}

QMenu{{background:#16292d;border:1px solid #2a3b3d;border-radius:6px;padding:8px;}}
QMenu::item{{padding:9px 22px 9px 10px;border-radius:8px;color:#e7f2ef;font-weight:650;}}
QMenu::item:selected{{background:#143a2d;color:#ffffff;}}
QMenu::icon{{padding-left:6px;}}
QMenu::separator{{height:1px;background:#1f2e30;margin:6px 8px;}}

QFrame#ProfileHero{{background:qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 #0f2e23,stop:1 #0e1a1c);border:1px solid #1d6e4d;border-radius:18px;}}
QLabel#AvatarBig{{background:qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 #2ee08a,stop:1 #0c6b43);border:2px solid #7dffc0;border-radius:38px;color:#04100b;font-size:26pt;font-weight:900;}}
QLabel#Avatar{{background:qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 #1f8a5f,stop:1 #0c3f2b);border:2px solid #2ee08a;border-radius:23px;color:#ffffff;font-size:15pt;font-weight:900;}}
QLabel#Avatar[ok="false"]{{border-color:#f2b84b;background:qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 #8a6a1f,stop:1 #3f300c);}}
QLabel#ProfileName{{font-size:22pt;font-weight:850;color:#ffffff;}}
QFrame#StatTile{{background:{CARD};border:1px solid {BORDER};border-radius:16px;}}
QLabel#StatLabel{{color:{MUTED};font-size:7.5pt;font-weight:800;letter-spacing:1.5px;}}
QLabel#StatValue{{font-size:16pt;font-weight:850;}}
QFrame#ProfileDetails{{background:{CARD};border:1px solid {BORDER};border-radius:16px;}}
QLabel#KVKey{{color:{MUTED};font-size:8.5pt;font-weight:800;letter-spacing:1px;}}
QLabel#KVVal{{color:#e7f2ef;font-size:10.5pt;font-weight:650;}}
QPushButton#AutoSwitch{{border-radius:14px;padding:0 18px;min-height:0;font-weight:800;}}
QPushButton#AutoSwitch[on="true"]{{background:#0f2a21;border:1px solid #1f8a5f;color:#6ee7ad;}}
QPushButton#AutoSwitch[on="false"]{{background:#2a1519;border:1px solid #6b3640;color:#ff8f99;}}

QLabel#Chip{{border-radius:9px;padding:2px 10px;font-size:8.5pt;font-weight:800;}}
QLabel#Chip[tone="green"]{{background:rgba(46,224,138,14%);border:1px solid rgba(46,224,138,45%);color:#6ee7ad;}}
QLabel#Chip[tone="red"]{{background:rgba(255,107,122,14%);border:1px solid rgba(255,107,122,45%);color:#ff8f99;}}
QLabel#Chip[tone="gold"]{{background:rgba(242,184,75,14%);border:1px solid rgba(242,184,75,45%);color:#f2b84b;}}
QLabel#Chip[tone="blue"]{{background:rgba(106,162,255,14%);border:1px solid rgba(106,162,255,45%);color:#8fb8ff;}}
QLabel#Chip[tone="amber"]{{background:rgba(242,184,75,14%);border:1px solid rgba(242,184,75,45%);color:#f2b84b;}}
QLabel#Chip[tone="live"]{{background:rgba(46,224,138,24%);border:1px solid rgba(46,224,138,70%);color:#9bffcb;}}
QLabel#Chip[tone="muted"]{{background:rgba(127,145,143,12%);border:1px solid rgba(127,145,143,35%);color:#9fb2af;}}

QFrame#ValueRow{{background:qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 #15303a,stop:1 #183039);border:1px solid #1b2b2d;border-left:4px solid #6aa2ff;border-radius:14px;}}
QFrame#ValueRow[kind="points"]{{border-left:4px solid #f2b84b;}}
QFrame#ValueRow:hover{{background:#1a3836;}}
QLabel#ValueIcon{{border-radius:13px;background:rgba(106,162,255,14%);border:1px solid rgba(106,162,255,45%);}}
QLabel#ValueIcon[kind="points"]{{background:rgba(242,184,75,14%);border:1px solid rgba(242,184,75,45%);}}
QLabel#ValueBig{{font-size:14pt;font-weight:850;color:#8fb8ff;}}
QLabel#ValueBig[kind="points"]{{color:#ffd166;}}
QFrame#ModeSwitch{{background:#0b1415;border:1px solid #243436;border-radius:22px;}}
QFrame#ModePill{{border-radius:19px;border:1px solid #6b5a2a;background:qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 #6b5222,stop:1 #f2b84b);}}
QFrame#ModePill[demo="false"]{{border:1px solid #ff8f99;background:qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 #b3202f,stop:1 #ff5a6c);}}
QPushButton#ModeOpt{{background:transparent;border:0;border-radius:19px;padding:0 6px;min-height:0;color:#7f918f;font-weight:850;font-size:10pt;letter-spacing:1px;}}
QPushButton#ModeOpt:hover{{color:#dff0ec;}}
QPushButton#ModeOpt[sel="true"]{{color:#0b0f10;}}
QPushButton#CmdChip{{background:rgba(46,224,138,10%);border:1px solid rgba(46,224,138,40%);border-radius:12px;padding:3px 12px;min-height:0;font-size:9pt;font-weight:800;color:#6ee7ad;}}
QPushButton#CmdChip:hover{{background:rgba(46,224,138,24%);border-color:#2ee08a;color:#ffffff;}}
QLabel#BetKpi{{font-size:10pt;font-weight:700;color:#cfe0dd;background:#112529;border:1px solid #1c2b2d;border-radius:12px;padding:8px 12px;}}
QFrame#DashBox:hover{{border:1px solid #2b4a45;}}
QFrame#DashBox[edit="true"]:hover{{border:1px dashed #6ee7ad;background:qlineargradient(x1:0,y1:0,x2:0,y2:1,stop:0 #1d3b38,stop:1 #162f2d);}}
QFrame#DashBox[drop="true"]{{border:2px solid #2ee08a;background:qlineargradient(x1:0,y1:0,x2:0,y2:1,stop:0 #14392c,stop:1 #0e2a20);}}
QPushButton#Primary:disabled{{background:#1a2a26;border:1px solid #2a3b3d;color:#6f827f;}}
QScrollBar:vertical{{background:transparent;width:8px;margin:2px;}}
QScrollBar::handle:vertical{{background:#243537;border-radius:3px;min-height:40px;}}
QScrollBar::handle:vertical:hover{{background:#2f8a67;}}
QScrollBar::add-page:vertical,QScrollBar::sub-page:vertical{{background:transparent;}}
QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical{{height:0;}}
QScrollBar:horizontal{{background:transparent;height:10px;margin:2px;}}
QScrollBar::handle:horizontal{{background:#2a3d3f;border-radius:4px;min-width:34px;}}
QScrollBar::add-line:horizontal,QScrollBar::sub-line:horizontal{{width:0;}}
"""

def main():
    try: QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
    except Exception: pass
    app=QApplication.instance() or QApplication([]);app.setStyleSheet(STYLE);app.setApplicationName("Stream Activity Bot");w=MainWindow();w.show();app.exec()

if __name__=="__main__":main()
