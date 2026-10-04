"""Qt widgets for bets, the "!" command hints and the DEMO/LIVE switch.

Kept out of ui.py so the logic (bet_tracker.py) and the widgets can evolve separately.
The widgets only talk to the engine through: bet_stats / bet_recent / new_bet_round /
get_profile_bet / save_profile_bet / place_bet / saved_commands.
"""
from __future__ import annotations

import threading
import time

from PySide6.QtCore import Qt, QEasingCurve, QPropertyAnimation, QRect, QRectF, QVariantAnimation, Signal
from PySide6.QtGui import QColor, QFont, QLinearGradient, QPainter
from PySide6.QtWidgets import (QComboBox, QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton,
                               QScrollArea, QVBoxLayout, QWidget)

from bet_tracker import BUILTIN_COMMANDS, command_word

FG = "#eaf3f1"; MUTED = "#7f918f"; AMBER = "#f2b84b"; BLUE = "#4f8cff"; GREEN = "#2ee08a"; RED = "#ff6b7a"
PLATFORM_LABEL = {"kick": "Kick", "youtube": "YouTube"}


def repolish(w: QWidget) -> None:
    w.style().unpolish(w)
    w.style().polish(w)


# ============================================================================ animated bars
class BetBars(QWidget):
    """One horizontal bar per letter; bars glide to their new length when the statistics change."""
    ROW_H = 50

    def __init__(self, parent=None):
        super().__init__(parent)
        self._rows: list = []
        self._sig = None
        self._from: dict = {}
        self._to: dict = {}
        self._t = 1.0
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(520)
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self._anim.valueChanged.connect(self._on_t)
        self.setMinimumHeight(90)

    def _on_t(self, v):
        self._t = float(v)
        self.update()

    def _display(self, letter: str) -> float:
        a = self._from.get(letter, 0.0)
        b = self._to.get(letter, 0.0)
        return a + (b - a) * self._t

    def set_rows(self, rows: list) -> None:
        sig = [(r["letter"], r["bets"], r["points"], r["users"]) for r in rows]
        if sig == self._sig:
            return
        self._sig = sig
        self._from = {r["letter"]: self._display(r["letter"]) for r in rows}
        mx = max([r["bets"] for r in rows] or [1])
        self._to = {r["letter"]: r["bets"] / mx for r in rows}
        self._rows = rows
        self.setMinimumHeight(max(90, len(rows) * self.ROW_H + 6))
        self.updateGeometry()
        self._anim.stop()
        self._t = 0.0
        self._anim.start()

    def paintEvent(self, ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w = self.width()
        if not self._rows:
            p.setPen(QColor(MUTED))
            p.drawText(self.rect(), Qt.AlignCenter, "Още няма залози в този рунд.\nКогато някой напише  !bet буква точки  ще се появи тук.")
            return
        right_w = 168
        x0 = 54
        tw = max(40, w - x0 - right_w - 10)
        for i, r in enumerate(self._rows):
            y = i * self.ROW_H
            col = QColor(AMBER if i == 0 else BLUE)
            badge = QRectF(0, y + 5, 40, 40)
            bg = QColor(col)
            bg.setAlpha(46)
            p.setPen(Qt.NoPen)
            p.setBrush(bg)
            p.drawRoundedRect(badge, 12, 12)
            p.setPen(col)
            f = QFont(self.font())
            f.setPointSize(15)
            f.setBold(True)
            p.setFont(f)
            p.drawText(badge, Qt.AlignCenter, r["letter"])

            track = QRectF(x0, y + 16, tw, 18)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor("#13201f"))
            p.drawRoundedRect(track, 9, 9)
            fw = tw * self._display(r["letter"])
            if fw > 1:
                fill = QRectF(x0, y + 16, max(fw, 18.0), 18)
                g = QLinearGradient(fill.left(), 0, fill.right(), 0)
                g.setColorAt(0.0, col.darker(165))
                g.setColorAt(1.0, col)
                p.setBrush(g)
                p.drawRoundedRect(fill, 9, 9)

            f2 = QFont(self.font())
            f2.setPointSize(10)
            f2.setBold(True)
            p.setFont(f2)
            p.setPen(QColor(FG))
            tag = "  ★ ТОП" if i == 0 else ""
            p.drawText(QRectF(x0 + tw + 10, y + 5, right_w, 21), Qt.AlignRight | Qt.AlignVCenter,
                       f"{r['pct_bets']:.0f}%  ·  {r['bets']} зал.{tag}")
            f3 = QFont(self.font())
            f3.setPointSize(8)
            p.setFont(f3)
            p.setPen(QColor(MUTED))
            extra = f" · {r['all_in']} all-in" if r.get("all_in") else ""
            p.drawText(QRectF(x0 + tw + 10, y + 26, right_w, 18), Qt.AlignRight | Qt.AlignVCenter,
                       f"{r['users']} души · {r['points']:,} т.{extra}")


# ============================================================================ stats container
class BetPanel(QWidget):
    """Dashboard container: which letter is bet most (real numbers), per stream and round."""

    def __init__(self, win):
        super().__init__()
        self.win = win
        self._chans = None
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)

        top = QHBoxLayout()
        top.setSpacing(8)
        self.channel = QComboBox()
        self.channel.currentIndexChanged.connect(lambda _=0: self.refresh())
        self.round_btn = QPushButton("Нов рунд")
        self.round_btn.setObjectName("MiniBtn")
        self.round_btn.setCursor(Qt.PointingHandCursor)
        self.round_btn.setToolTip("Започва нова статистика. Старата остава записана.")
        self.round_btn.clicked.connect(self._new_round)
        top.addWidget(self.channel, 1)
        top.addWidget(self.round_btn)
        lay.addLayout(top)

        self.kpi = QLabel("")
        self.kpi.setObjectName("BetKpi")
        self.kpi.setWordWrap(True)
        self.kpi.setTextFormat(Qt.RichText)
        self.demo_tag = QLabel("ДЕМО — симулирани залози, не са реални.")
        self.demo_tag.setObjectName("Chip")
        self.demo_tag.setProperty("tone", "amber")
        lay.addWidget(self.kpi)
        lay.addWidget(self.demo_tag)

        self.bars = BetBars()
        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setFrameShape(QFrame.NoFrame)
        area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        area.setStyleSheet("QScrollArea{background:transparent;border:0;} QScrollArea>QWidget>QWidget{background:transparent;}")
        area.setWidget(self.bars)
        lay.addWidget(area, 1)

        self.recent = QLabel("")
        self.recent.setObjectName("StreamMeta")
        self.recent.setWordWrap(True)
        self.recent.setTextFormat(Qt.RichText)
        lay.addWidget(self.recent)

    def _stream_key(self):
        cid = self.channel.currentData()
        ch = self.win.config.get_channel(cid) if cid else None
        return self.win.engine.stream_key(ch) if ch is not None else ""

    def _new_round(self, _=False):
        key = self._stream_key()
        if key:
            self.win.engine.new_bet_round(key)
            self.refresh()

    def refresh(self):
        cfg = self.win.config
        chans = [(c.id, f"{c.display_name or c.url} · {PLATFORM_LABEL.get(c.platform, c.platform)}") for c in cfg.channels]
        if chans != self._chans:
            cur = self.channel.currentData()
            self.channel.blockSignals(True)
            self.channel.clear()
            for cid, label in chans:
                self.channel.addItem(label, cid)
            idx = self.channel.findData(cur)
            if idx >= 0:
                self.channel.setCurrentIndex(idx)
            self.channel.blockSignals(False)
            self._chans = chans
        demo = bool(cfg.settings.demo_mode)
        self.demo_tag.setVisible(demo)
        key = self._stream_key()
        if not key:
            self.kpi.setText("Добави стрийм, за да следиш залозите.")
            self.bars.set_rows([])
            self.recent.setText("")
            return
        st = self.win.engine.bet_stats(key)
        top = f"  ·  най-залагана: <b style='color:{AMBER}'>{st['top']}</b>" if st["top"] else ""
        self.kpi.setText(f"Рунд #{st['round_id']}  ·  {st['total_bets']} залога  ·  {st['total_users']} души  ·  "
                         f"{st['total_points']:,} точки{top}")
        self.bars.set_rows(st["letters"])
        rec = self.win.engine.bet_recent(key, 6)
        lines = []
        for r in rec:
            pts = "all-in" if int(r["points"]) < 0 else f"{int(r['points']):,}"
            lines.append(f"{time.strftime('%H:%M:%S', time.localtime(r['ts']))}  {r['username'] or '?'}  →  "
                         f"<b>{r['letter']}</b>  {pts}")
        self.recent.setText("<br>".join(lines))


# ============================================================================ per-profile !bet form
class ProfileBetBox(QFrame):
    """In the profile window: pick a letter + points, save them as the profile's default, send !bet."""
    done = Signal(object)

    def __init__(self, engine, config, account):
        super().__init__()
        self.engine, self.config, self.account = engine, config, account
        self.setObjectName("ProfileDetails")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 14, 18, 14)
        lay.setSpacing(10)
        h = QLabel("!BET ЗА ТОЗИ ПРОФИЛ")
        h.setObjectName("Eyebrow")
        lay.addWidget(h)

        row = QHBoxLayout()
        row.setSpacing(10)
        self.channel = QComboBox()
        for c in config.channels:
            if account.id in c.connected_account_ids() and c.platform == account.platform:
                self.channel.addItem(f"{c.display_name or c.url}", c.id)
        self.letter = QLineEdit()
        self.letter.setMaxLength(1)
        self.letter.setPlaceholderText("Буква")
        self.letter.setFixedWidth(84)
        self.letter.setAlignment(Qt.AlignCenter)
        self.points = QLineEdit()
        self.points.setPlaceholderText("Точки (500, 1,000, 2k, all)")
        self.btn_save = QPushButton("Запази")
        self.btn_save.setObjectName("ToolBtn")
        self.btn_save.setCursor(Qt.PointingHandCursor)
        self.btn_bet = QPushButton("Заложи")
        self.btn_bet.setObjectName("Primary")
        self.btn_bet.setFixedHeight(42)
        self.btn_bet.setCursor(Qt.PointingHandCursor)
        row.addWidget(self.channel, 2)
        row.addWidget(self.letter)
        row.addWidget(self.points, 2)
        row.addWidget(self.btn_save)
        row.addWidget(self.btn_bet)
        lay.addLayout(row)

        self.info = QLabel("")
        self.info.setObjectName("StreamMeta")
        self.info.setWordWrap(True)
        lay.addWidget(self.info)

        saved = engine.get_profile_bet(account.id)
        if saved:
            self.letter.setText(str(saved.get("letter", "")))
            p = int(saved.get("points", 0) or 0)
            self.points.setText("all" if p < 0 else (str(p) if p else ""))
        self.btn_save.clicked.connect(self._save)
        self.btn_bet.clicked.connect(self._bet)
        self.letter.returnPressed.connect(self._bet)
        self.points.returnPressed.connect(self._bet)
        self.done.connect(self._done)

    def _save(self, _=False):
        try:
            l, p = self.engine.save_profile_bet(self.account.id, self.letter.text(), self.points.text())
        except ValueError as e:
            self.info.setText(str(e))
            return
        self.letter.setText(l)
        self.info.setText(f"Запазено за този профил: {l} · {'all-in' if p < 0 else f'{p:,}'} точки.")

    def _bet(self, _=False):
        cid = self.channel.currentData()
        if not cid:
            self.info.setText("Профилът не е свързан с нито един стрийм.")
            return
        self.btn_bet.setEnabled(False)
        self.info.setText("Изпращане...")
        args = (cid, self.account.id, self.letter.text(), self.points.text())
        threading.Thread(target=self._worker, args=args, daemon=True).start()

    def _worker(self, cid, aid, letter, points):
        try:
            r = self.engine.place_bet(cid, aid, letter, points)
        except Exception as e:  # never kill the UI thread silently
            r = {"ok": False, "error": f"Грешка: {e}"}
        self.done.emit(r)

    def _done(self, r):
        self.btn_bet.setEnabled(True)
        if r.get("ok"):
            suffix = "  (ДЕМО — само симулация)" if r.get("demo") else ""
            self.info.setText(f"Изпратено: {r.get('text', '')}{suffix}")
        else:
            fails = "; ".join(f"{n}: {m}" for n, m in (r.get("failures") or []))
            self.info.setText(str(r.get("error") or fails or "Неуспешно изпращане."))


# ============================================================================ "!" command hints
class CommandBar(QWidget):
    """Shown under the message box while the text starts with "!" - never blocks typing."""
    picked = Signal(str)

    def __init__(self):
        super().__init__()
        self._sig = None
        self.lay = QHBoxLayout(self)
        self.lay.setContentsMargins(0, 0, 0, 0)
        self.lay.setSpacing(6)
        self.badge = QLabel("")
        self.badge.setObjectName("Chip")
        self.badge.setProperty("tone", "green")
        self.lay.addWidget(self.badge)
        self.chips_host = QWidget()
        self.chips = QHBoxLayout(self.chips_host)
        self.chips.setContentsMargins(0, 0, 0, 0)
        self.chips.setSpacing(6)
        self.lay.addWidget(self.chips_host, 1)
        self.hide()

    def update_for(self, text: str, saved: list) -> None:
        t = text or ""
        if not t.startswith("!") or t.startswith("! "):
            self.hide()
            return
        word = command_word(t) or "!"
        typing_first_word = " " not in t.strip()
        pool = list(dict.fromkeys(BUILTIN_COMMANDS + [c for c in (saved or []) if isinstance(c, str)]))
        options = [c for c in pool if c.startswith(word) and c != word][:8] if typing_first_word else []
        sig = (word, tuple(options), typing_first_word)
        self.badge.setText("⌘ Команда  " + word)
        if sig != self._sig:
            self._sig = sig
            while self.chips.count():
                it = self.chips.takeAt(0)
                w = it.widget()
                if w:
                    w.deleteLater()
            for c in options:
                b = QPushButton(c)
                b.setObjectName("CmdChip")
                b.setCursor(Qt.PointingHandCursor)
                b.setFocusPolicy(Qt.NoFocus)          # the line edit keeps the focus -> you keep typing
                b.clicked.connect(lambda _=False, cmd=c: self.picked.emit(cmd))
                self.chips.addWidget(b)
            self.chips.addStretch()
        self.show()


# ============================================================================ DEMO / LIVE switch
class ModeSwitch(QFrame):
    """Segmented switch with a sliding pill.  `requested(True)` = user asked for DEMO, False = LIVE."""
    requested = Signal(bool)

    def __init__(self):
        super().__init__()
        self.setObjectName("ModeSwitch")
        self.setFixedSize(212, 44)
        self._demo = True
        self.pill = QFrame(self)
        self.pill.setObjectName("ModePill")
        self.pill.setProperty("demo", True)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(3, 3, 3, 3)
        lay.setSpacing(0)
        self.b_demo = QPushButton("ДЕМО")
        self.b_live = QPushButton("●  LIVE")
        for b in (self.b_demo, self.b_live):
            b.setObjectName("ModeOpt")
            b.setCursor(Qt.PointingHandCursor)
            b.setFocusPolicy(Qt.NoFocus)
            lay.addWidget(b, 1)
        self.b_demo.clicked.connect(lambda _=False: self.requested.emit(True))
        self.b_live.clicked.connect(lambda _=False: self.requested.emit(False))
        self.anim = QPropertyAnimation(self.pill, b"geometry", self)
        self.anim.setDuration(280)
        self.anim.setEasingCurve(QEasingCurve.OutCubic)
        self.pill.lower()
        self.pill.setGeometry(self._target())
        self._paint_state()

    def _target(self) -> QRect:
        r = self.rect().adjusted(3, 3, -3, -3)
        half = r.width() // 2
        return QRect(r.left() + (0 if self._demo else half), r.top(), half, r.height())

    def _paint_state(self) -> None:
        self.pill.setProperty("demo", self._demo)
        self.b_demo.setProperty("sel", self._demo)
        self.b_live.setProperty("sel", not self._demo)
        for w in (self.pill, self.b_demo, self.b_live):
            repolish(w)

    def set_demo(self, demo: bool, animate: bool = True) -> None:
        demo = bool(demo)
        changed = demo != self._demo
        self._demo = demo
        self._paint_state()
        tgt = self._target()
        if animate and changed and self.isVisible():
            self.anim.stop()
            self.anim.setStartValue(self.pill.geometry())
            self.anim.setEndValue(tgt)
            self.anim.start()
        else:
            self.pill.setGeometry(tgt)

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        self.pill.setGeometry(self._target())


# ============================================================================ drag & drop reflow animation
def animate_reflow(canvas, old_geoms: dict, dropped_id: str = "") -> None:
    """FLIP animation: every container glides from where it WAS to where the layout put it.

    The dropped container overshoots slightly (OutBack) so the drop feels physical.
    """
    from PySide6.QtCore import QParallelAnimationGroup

    canvas.layout().activate()
    group = QParallelAnimationGroup(canvas)
    n = 0
    for iid, box in canvas.boxes.items():
        old = old_geoms.get(iid)
        new = box.geometry()
        if old is None or old == new or new.isEmpty():
            continue
        box.setGeometry(old)
        a = QPropertyAnimation(box, b"geometry", group)
        a.setDuration(460 if iid == dropped_id else 380)
        a.setStartValue(old)
        a.setEndValue(new)
        a.setEasingCurve(QEasingCurve.OutBack if iid == dropped_id else QEasingCurve.OutCubic)
        group.addAnimation(a)
        n += 1
    if not n:
        canvas.animating = False
        return
    canvas.animating = True

    def finished():
        canvas.animating = False
        try:
            canvas.layout().activate()
            canvas.refresh_all()
        except RuntimeError:
            pass

    group.finished.connect(finished)
    canvas._reflow = group          # keep a reference
    group.start()
