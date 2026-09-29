"""
Kontrolki głównego okna w stylu „notatnika”: przyciski-ikony, wskaźniki poziomu,
pływający panel nagrywania, wysuwane powiadomienie i panel historii.

Kolory pochodzą z tokenów aktywnego motywu (recorder.ui.theme), dlatego każda kontrolka
ma metodę apply_theme() wywoływaną przez okno po zmianie motywu.
"""

import os
from datetime import datetime, date, timedelta
from typing import List, Optional, Tuple

from PySide6.QtCore import (
    Qt, QTimer, Signal, QPoint, QPropertyAnimation, QEasingCurve, QRectF, QSize
)
from PySide6.QtGui import QColor, QPainter, QPen, QFont
from PySide6.QtWidgets import (
    QWidget, QFrame, QLabel, QPushButton, QHBoxLayout, QVBoxLayout,
    QGraphicsDropShadowEffect, QListWidget, QListWidgetItem, QMenu, QSizePolicy
)

from recorder.ui.icons import make_icon


def current_tokens():
    """Zwraca definicję aktywnego motywu (tokeny kolorów)."""
    from recorder.config import get_theme as get_theme_id
    from recorder.ui.theme import get_theme
    return get_theme(get_theme_id())


def _refresh_style(widget: QWidget) -> None:
    widget.style().unpolish(widget)
    widget.style().polish(widget)
    widget.update()


class IconButton(QPushButton):
    """Przycisk z samą ikoną i podpowiedzią (tooltip) zamiast tekstu."""

    def __init__(self, icon_name: str, tooltip: str, parent: Optional[QWidget] = None,
                 size: int = 36, icon_px: int = 19, object_name: str = "IconBtn",
                 color_role: str = "text_secondary"):
        super().__init__(parent)
        self._icon_name = icon_name
        self._icon_px = icon_px
        self._color_role = color_role
        self.setObjectName(object_name)
        self.setToolTip(tooltip)
        self.setAccessibleName(tooltip)
        self.setFixedSize(size, size)
        self.setIconSize(QSize(icon_px, icon_px))
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.apply_theme()

    def icon_name(self) -> str:
        return self._icon_name

    def set_icon_name(self, name: str) -> None:
        if name != self._icon_name:
            self._icon_name = name
            self.apply_theme()

    def set_color_role(self, role: str) -> None:
        if role != self._color_role:
            self._color_role = role
            self.apply_theme()

    def apply_theme(self) -> None:
        t = current_tokens()
        color = getattr(t, self._color_role, t.text_secondary)
        self.setIcon(make_icon(self._icon_name, color, self._icon_px, disabled_color=t.border_strong))


class LevelMeter(QWidget):
    """Mini wskaźnik poziomu: kilka zaokrąglonych słupków pokazujących ostatnie wartości."""

    BARS = 7

    def __init__(self, parent: Optional[QWidget] = None, color_role: str = "speaker_mic"):
        super().__init__(parent)
        self._color_role = color_role
        self._values: List[float] = [0.0] * self.BARS
        self._muted = False
        self.setFixedSize(self.BARS * 5 - 2, 18)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

    def push_level(self, level: float) -> None:
        level = max(0.0, min(100.0, float(level or 0.0)))
        self._values = self._values[1:] + [level]
        self.update()

    def reset(self) -> None:
        self._values = [0.0] * self.BARS
        self.update()

    def set_muted(self, muted: bool) -> None:
        self._muted = bool(muted)
        self.update()

    def paintEvent(self, event):
        t = current_tokens()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        h = self.height()
        on = QColor(getattr(t, self._color_role, t.accent))
        off = QColor(t.border_strong)
        for i, v in enumerate(self._values):
            active = (not self._muted) and v > 4.0
            bar_h = 3.0 if not active else max(4.0, min(float(h), 3.0 + (v / 100.0) * (h - 3.0) * 1.6))
            x = i * 5.0
            y = h - bar_h
            p.setBrush(on if active else off)
            p.drawRoundedRect(QRectF(x, y, 3.0, bar_h), 1.5, 1.5)
        p.end()


class StatusDot(QWidget):
    """Kropka stanu nagrywania: czerwona (nagrywanie), bursztynowa z pierścieniem ciszy, lub wskaźnik pracy."""

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setFixedSize(24, 24)
        self._mode = "recording"
        self._ring = 0.0
        self._angle = 0
        self._spin = QTimer(self)
        self._spin.setInterval(40)
        self._spin.timeout.connect(self._on_spin)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

    def set_mode(self, mode: str) -> None:
        self._mode = mode
        if mode == "processing":
            self._spin.start()
        else:
            self._spin.stop()
        self.update()

    def mode(self) -> str:
        return self._mode

    def ring(self) -> float:
        return self._ring

    def set_ring(self, fraction: float) -> None:
        value = max(0.0, min(1.0, float(fraction or 0.0)))
        if value != self._ring:
            self._ring = value
            self.update()

    def _on_spin(self):
        self._angle = (self._angle + 12) % 360
        self.update()

    def paintEvent(self, event):
        t = current_tokens()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        c = QRectF(self.rect()).center()
        if self._mode == "processing":
            pen = QPen(QColor(t.border_strong), 2.2)
            p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            r = QRectF(c.x() - 8, c.y() - 8, 16, 16)
            p.drawEllipse(r)
            pen.setColor(QColor(t.accent))
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            p.setPen(pen)
            p.drawArc(r, -self._angle * 16, 100 * 16)
            p.end()
            return

        if self._mode in ("autopaused",):
            color = QColor(t.status_autopaused_bg)
        elif self._mode == "manualpaused":
            color = QColor(t.text_secondary)
        else:
            color = QColor(t.btn_start_bg)

        ring = 1.0 if self._mode == "autopaused" else self._ring
        if ring > 0.0:
            # Pierścień odmierzający ciszę do automatycznej pauzy
            warn = QColor(t.status_autopaused_bg)
            ring_rect = QRectF(c.x() - 9, c.y() - 9, 18, 18)
            pen = QPen(QColor(t.border_strong), 2.4)
            p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(ring_rect)
            pen.setColor(warn)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            p.setPen(pen)
            if ring >= 0.999:
                p.drawEllipse(ring_rect)
            else:
                p.drawArc(ring_rect, 90 * 16, -int(ring * 360 * 16))
        else:
            halo = QColor(color)
            halo.setAlpha(55)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(halo)
            p.drawEllipse(c, 9.0, 9.0)

        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(color)
        p.drawEllipse(c, 5.0, 5.0)
        p.end()


class ChannelToggle(QPushButton):
    """Kanał audio w panelu: ikona + wskaźnik poziomu. Kliknięcie wycisza lub włącza kanał."""

    def __init__(self, kind: str, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._kind = kind  # "mic" | "sys"
        self._muted = False
        self.setObjectName("DockChannel")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(40)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 0, 12, 0)
        lay.setSpacing(7)
        self.lbl_icon = QLabel(self)
        self.lbl_icon.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.lbl_icon.setFixedSize(18, 18)
        self.meter = LevelMeter(self, color_role="speaker_mic" if kind == "mic" else "speaker_system")
        lay.addWidget(self.lbl_icon)
        lay.addWidget(self.meter)
        self.setMinimumWidth(10 + 18 + 7 + self.meter.width() + 12)
        self._update_tooltip()
        self.apply_theme()

    def is_muted(self) -> bool:
        return self._muted

    def set_muted(self, muted: bool) -> None:
        self._muted = bool(muted)
        self.setProperty("muted", "true" if self._muted else "false")
        self.meter.set_muted(self._muted)
        if self._muted:
            self.meter.reset()
        self._update_tooltip()
        self.apply_theme()
        _refresh_style(self)

    def _update_tooltip(self):
        if self._kind == "mic":
            self.setToolTip("Włącz mikrofon" if self._muted else "Wycisz mikrofon")
        else:
            self.setToolTip("Włącz dźwięk systemu" if self._muted else "Wycisz dźwięk systemu")
        self.setAccessibleName(self.toolTip())

    def apply_theme(self) -> None:
        t = current_tokens()
        base = "mic" if self._kind == "mic" else "headphones"
        name = f"{base}_off" if self._muted else base
        color = t.text_muted if self._muted else t.text_primary
        self.lbl_icon.setPixmap(make_icon(name, color, 18).pixmap(18, 18))
        self.meter.update()


class RecordDock(QFrame):
    """Pływający panel nagrywania: stan, czas, poziomy kanałów, pauza i stop."""

    pause_clicked = Signal()
    stop_clicked = Signal()
    mic_toggled = Signal()
    sys_toggled = Signal()

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setObjectName("RecordDock")
        self.setFixedHeight(56)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 6, 6, 6)
        lay.setSpacing(6)

        self.dot = StatusDot(self)
        self.lbl_time = QLabel("00:00", self)
        self.lbl_time.setObjectName("DockTime")
        self.lbl_time.setMinimumWidth(58)

        self.sep1 = QFrame(self)
        self.sep1.setObjectName("DockSeparator")
        self.sep1.setFixedSize(1, 24)

        self.ch_mic = ChannelToggle("mic", self)
        self.ch_sys = ChannelToggle("sys", self)
        self.ch_mic.clicked.connect(self.mic_toggled.emit)
        self.ch_sys.clicked.connect(self.sys_toggled.emit)

        self.sep2 = QFrame(self)
        self.sep2.setObjectName("DockSeparator")
        self.sep2.setFixedSize(1, 24)

        self.btn_pause = IconButton("pause", "Wstrzymaj nagrywanie", self, size=44, icon_px=18,
                                    object_name="DockPauseBtn", color_role="text_primary")
        self.btn_stop = IconButton("stop", "Zakończ i zapisz", self, size=44, icon_px=18,
                                   object_name="DockStopBtn", color_role="btn_stop_text")
        self.btn_pause.clicked.connect(self.pause_clicked.emit)
        self.btn_stop.clicked.connect(self.stop_clicked.emit)

        lay.addSpacing(4)
        lay.addWidget(self.dot)
        lay.addWidget(self.lbl_time)
        lay.addWidget(self.sep1)
        lay.addWidget(self.ch_mic)
        lay.addWidget(self.ch_sys)
        lay.addWidget(self.sep2)
        lay.addWidget(self.btn_pause)
        lay.addWidget(self.btn_stop)

        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(28)
        shadow.setOffset(0, 8)
        shadow.setColor(QColor(0, 0, 0, 110))
        self.setGraphicsEffect(shadow)
        self._mode = "recording"
        self.set_mode("recording")

    def mode(self) -> str:
        return self._mode

    def set_mode(self, mode: str, text: str = "") -> None:
        """Tryby: recording, autopaused, manualpaused, processing."""
        self._mode = mode
        self.dot.set_mode(mode)
        processing = mode == "processing"
        for w in (self.sep1, self.ch_mic, self.ch_sys, self.sep2, self.btn_pause, self.btn_stop):
            w.setVisible(not processing)
        if processing:
            self.lbl_time.setText(text or "Kończę transkrypcję…")
            self.lbl_time.setProperty("processing", "true")
        else:
            self.lbl_time.setProperty("processing", "false")
            self._apply_channel_visibility()
        _refresh_style(self.lbl_time)
        if mode == "manualpaused":
            self.btn_pause.set_icon_name("play")
            self.btn_pause.setToolTip("Wznów nagrywanie")
        else:
            self.btn_pause.set_icon_name("pause")
            self.btn_pause.setToolTip("Wstrzymaj nagrywanie")
        if mode not in ("recording",):
            self.dot.set_ring(0.0)
        self.adjustSize()

    def set_time(self, text: str) -> None:
        if self._mode != "processing" and self.lbl_time.text() != text:
            self.lbl_time.setText(text)

    def set_processing_text(self, text: str) -> None:
        if self._mode == "processing":
            self.lbl_time.setText(text)
            self.adjustSize()

    def set_levels(self, mic: float, sys_lvl: float) -> None:
        self.ch_mic.meter.push_level(0.0 if self.ch_mic.is_muted() else mic)
        self.ch_sys.meter.push_level(0.0 if self.ch_sys.is_muted() else sys_lvl)

    def reset_levels(self) -> None:
        self.ch_mic.meter.reset()
        self.ch_sys.meter.reset()

    def set_silence_fraction(self, fraction: float) -> None:
        self.dot.set_ring(fraction)

    _channels = (True, True)

    def set_channels(self, mic_visible: bool, sys_visible: bool) -> None:
        self._channels = (bool(mic_visible), bool(sys_visible))
        self._apply_channel_visibility()

    def _apply_channel_visibility(self):
        if self._mode == "processing":
            return
        self.ch_mic.setVisible(self._channels[0])
        self.ch_sys.setVisible(self._channels[1])
        self.adjustSize()

    def apply_theme(self) -> None:
        for w in (self.btn_pause, self.btn_stop, self.ch_mic, self.ch_sys):
            w.apply_theme()
        self.dot.update()


class RecordButton(QPushButton):
    """Duży okrągły przycisk rozpoczęcia nagrywania (stan gotowości)."""

    def __init__(self, parent: Optional[QWidget] = None, diameter: int = 64):
        super().__init__(parent)
        self.setObjectName("BigRecordBtn")
        self._d = diameter
        halo = 10
        self.setFixedSize(diameter + halo * 2, diameter + halo * 2)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("Rozpocznij nagrywanie")
        self.setAccessibleName("Rozpocznij nagrywanie")
        self._hover = False

    def set_diameter(self, diameter: int) -> None:
        self._d = int(diameter)
        halo = 10 if diameter >= 56 else 6
        self.setFixedSize(self._d + halo * 2, self._d + halo * 2)
        self.update()

    def enterEvent(self, event):
        self._hover = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hover = False
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, event):
        t = current_tokens()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        c = QRectF(self.rect()).center()
        enabled = self.isEnabled()
        base = QColor(t.btn_start_hover if (self._hover and enabled) else t.btn_start_bg)
        if not enabled:
            base = QColor(t.border_strong)
        halo = QColor(base)
        halo.setAlpha(40 if enabled else 0)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(halo)
        r_out = self.width() / 2.0
        p.drawEllipse(c, r_out, r_out)
        p.setBrush(base)
        p.drawEllipse(c, self._d / 2.0, self._d / 2.0)
        p.setBrush(QColor("#ffffff") if enabled else QColor(t.text_muted))
        p.drawEllipse(c, 7.0, 7.0)
        if self.hasFocus():
            pen = QPen(QColor(t.border_focus), 2)
            p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(c, r_out - 1.5, r_out - 1.5)
        p.end()


class CloudToast(QFrame):
    """Powiadomienie wysuwane z góry okna (np. błąd synchronizacji z chmurą)."""

    retry_clicked = Signal()

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setObjectName("CloudToast")
        self.setFixedWidth(520)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 10, 10, 10)
        lay.setSpacing(12)

        self.lbl_icon = QLabel(self)
        self.lbl_icon.setObjectName("CloudToastIcon")
        self.lbl_icon.setFixedSize(30, 30)
        self.lbl_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self.lbl_icon)

        text_col = QVBoxLayout()
        text_col.setSpacing(1)
        self.lbl_title = QLabel(self)
        self.lbl_title.setObjectName("CloudToastTitle")
        self.lbl_desc = QLabel(self)
        self.lbl_desc.setObjectName("CloudToastDesc")
        self.lbl_desc.setWordWrap(True)
        # Treść błędów pochodzi z sieci, więc nigdy nie interpretujemy jej jako HTML
        self.lbl_title.setTextFormat(Qt.TextFormat.PlainText)
        self.lbl_desc.setTextFormat(Qt.TextFormat.PlainText)
        text_col.addWidget(self.lbl_title)
        text_col.addWidget(self.lbl_desc)
        lay.addLayout(text_col, stretch=1)

        self.btn_retry = IconButton("refresh", "Spróbuj ponownie teraz", self, size=32, icon_px=16,
                                    object_name="CloudToastBtn", color_role="text_primary")
        self.btn_close = IconButton("x", "Zamknij", self, size=32, icon_px=16,
                                    object_name="CloudToastBtn", color_role="text_primary")
        self.btn_retry.clicked.connect(self._on_retry)
        self.btn_close.clicked.connect(self.dismiss)
        lay.addWidget(self.btn_retry)
        lay.addWidget(self.btn_close)

        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(30)
        shadow.setOffset(0, 10)
        shadow.setColor(QColor(0, 0, 0, 130))
        self.setGraphicsEffect(shadow)

        self._anim = QPropertyAnimation(self, b"pos", self)
        self._anim.setDuration(220)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self.dismiss)
        self._top = 16
        self.hide()

    def set_anchor_top(self, y: int) -> None:
        self._top = int(y)
        if self.isVisible():
            self._place(final=True)

    def _target_pos(self) -> QPoint:
        pw = self.parentWidget().width() if self.parentWidget() else self.width()
        return QPoint(max(8, (pw - self.width()) // 2), self._top)

    def _place(self, final: bool = True):
        self._fit_size()
        self.move(self._target_pos())

    def _fit_size(self) -> None:
        """Szerokość dopasowana do okna, wysokość do zawiniętego opisu (bez ucinania tekstu)."""
        pw = self.parentWidget().width() if self.parentWidget() else 560
        width = max(300, min(520, pw - 32))
        self.setFixedWidth(width)
        lay = self.layout()
        lay.activate()
        h = lay.totalHeightForWidth(width) if lay.hasHeightForWidth() else lay.totalSizeHint().height()
        self.setFixedHeight(max(52, h))

    def show_message(self, title: str, desc: str, kind: str = "error", retry: bool = True,
                     auto_hide_ms: int = 0) -> None:
        t = current_tokens()
        self.setProperty("kind", kind)
        self.lbl_title.setText(title)
        self.lbl_desc.setText(desc)
        icon_name = "cloud_off" if kind == "error" else "alert"
        color = t.btn_start_bg if kind == "error" else t.status_autopaused_bg
        self.lbl_icon.setPixmap(make_icon(icon_name, color, 16).pixmap(16, 16))
        self.btn_retry.setVisible(retry)
        self.btn_retry.apply_theme()
        self.btn_close.apply_theme()
        _refresh_style(self)
        self._fit_size()
        target = self._target_pos()
        was_visible = self.isVisible()
        self.raise_()
        self.show()
        if not was_visible:
            self._anim.stop()
            self._anim.setStartValue(QPoint(target.x(), target.y() - self.height() - 12))
            self._anim.setEndValue(target)
            self._anim.start()
        else:
            self.move(target)
        if auto_hide_ms > 0:
            self._hide_timer.start(auto_hide_ms)
        else:
            self._hide_timer.stop()

    def dismiss(self) -> None:
        self._hide_timer.stop()
        self.hide()

    def _on_retry(self):
        self.retry_clicked.emit()

    def apply_theme(self) -> None:
        self.btn_retry.apply_theme()
        self.btn_close.apply_theme()


_MONTHS_GEN = [
    "stycznia", "lutego", "marca", "kwietnia", "maja", "czerwca",
    "lipca", "sierpnia", "września", "października", "listopada", "grudnia",
]


def polish_date_title(dt: datetime) -> str:
    """Np. 'Nagranie 29 września, 12:18'."""
    return f"Nagranie {dt.day} {_MONTHS_GEN[dt.month - 1]}, {dt.strftime('%H:%M')}"


def format_duration(seconds: float) -> str:
    seconds = int(max(0, round(seconds or 0)))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h} h {m:02d} min"
    if m:
        return f"{m} min {s} s" if s else f"{m} min"
    return f"{s} s"


class HistoryEntry:
    """Jeden wpis historii: transkrypcja (.txt) i/lub nagranie (.wav) z tej samej sesji."""

    def __init__(self, key: str):
        self.key = key
        self.txt_path: Optional[str] = None
        self.wav_path: Optional[str] = None
        self.when: Optional[datetime] = None
        self.mtime: float = 0.0

    @property
    def title(self) -> str:
        if self.when is not None and self.key.startswith("20"):
            return f"Nagranie {self.when.strftime('%H:%M')}"
        return self.key

    def subtitle(self) -> str:
        parts = []
        if self.wav_path:
            try:
                import soundfile as sf
                parts.append(format_duration(float(sf.info(self.wav_path).duration)))
            except Exception:
                pass
        if self.txt_path:
            try:
                kb = os.path.getsize(self.txt_path) / 1024.0
                parts.append("tekst" if kb < 1 else f"tekst {kb:.0f} KB")
            except Exception:
                pass
        elif self.wav_path:
            parts.append("bez transkrypcji")
        return " · ".join(parts)


def collect_history(recordings_dir: str, transcriptions_dir: str) -> List[HistoryEntry]:
    """Łączy pliki .wav i .txt tej samej sesji w jeden wpis, najnowsze na górze."""
    from recorder.core.session import extract_datetime_from_filename

    entries = {}

    def entry_for(key: str) -> HistoryEntry:
        if key not in entries:
            entries[key] = HistoryEntry(key)
        return entries[key]

    if transcriptions_dir and os.path.isdir(transcriptions_dir):
        for f in os.listdir(transcriptions_dir):
            if f.lower().endswith(".txt"):
                stem = os.path.splitext(f)[0]
                key = stem[len("transkrypcja_"):] if stem.startswith("transkrypcja_") else stem
                key = key.replace("inteligentne_nagranie_", "")
                e = entry_for(key)
                e.txt_path = os.path.join(transcriptions_dir, f)
    if recordings_dir and os.path.isdir(recordings_dir):
        for f in os.listdir(recordings_dir):
            if f.lower().endswith(".wav"):
                stem = os.path.splitext(f)[0]
                key = stem.replace("inteligentne_nagranie_", "")
                e = entry_for(key)
                e.wav_path = os.path.join(recordings_dir, f)

    for e in entries.values():
        paths = [p for p in (e.txt_path, e.wav_path) if p]
        try:
            e.mtime = max(os.path.getmtime(p) for p in paths)
        except Exception:
            e.mtime = 0.0
        e.when = None
        for p in paths:
            try:
                e.when = extract_datetime_from_filename(p)
            except Exception:
                e.when = None
            if e.when:
                break
        if e.when is None and e.mtime:
            e.when = datetime.fromtimestamp(e.mtime)

    return sorted(entries.values(), key=lambda x: (x.when or datetime.min, x.mtime), reverse=True)


def day_group_label(d: date, today: Optional[date] = None) -> str:
    today = today or date.today()
    if d == today:
        return "Dzisiaj"
    if d == today - timedelta(days=1):
        return "Wczoraj"
    return f"{d.day} {_MONTHS_GEN[d.month - 1]}"


class _HistoryRow(QWidget):
    open_audio = Signal(str)

    def __init__(self, entry: HistoryEntry, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.entry = entry
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 8, 6, 8)
        lay.setSpacing(8)
        col = QVBoxLayout()
        col.setSpacing(1)
        self.lbl_title = QLabel(entry.title, self)
        self.lbl_title.setObjectName("HistoryTitle")
        self.lbl_sub = QLabel(entry.subtitle(), self)
        self.lbl_sub.setObjectName("HistorySub")
        col.addWidget(self.lbl_title)
        col.addWidget(self.lbl_sub)
        lay.addLayout(col, stretch=1)
        self.btn_audio = None
        if entry.wav_path:
            self.btn_audio = IconButton("wave", "Odtwórz nagranie", self, size=28, icon_px=15,
                                        object_name="HistoryIconBtn", color_role="text_muted")
            self.btn_audio.clicked.connect(lambda: self.open_audio.emit(entry.wav_path))
            lay.addWidget(self.btn_audio)
        if entry.txt_path:
            lbl_doc = QLabel(self)
            lbl_doc.setToolTip("Transkrypcja dostępna, kliknij dwukrotnie wiersz")
            lbl_doc.setFixedSize(28, 28)
            lbl_doc.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl_doc.setPixmap(make_icon("doc", current_tokens().text_muted, 15).pixmap(15, 15))
            lay.addWidget(lbl_doc)


class HistoryPanel(QFrame):
    """Wysuwany z prawej panel historii nagrań i transkrypcji."""

    transcript_requested = Signal(str)
    audio_requested = Signal(str)
    open_recordings_folder = Signal()
    open_transcriptions_folder = Signal()
    closed = Signal()

    WIDTH = 340

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setObjectName("HistoryPanel")
        self.setFixedWidth(self.WIDTH)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 16, 12, 16)
        lay.setSpacing(8)

        head = QHBoxLayout()
        head.setSpacing(4)
        lbl = QLabel("Historia", self)
        lbl.setObjectName("HistoryHeader")
        head.addWidget(lbl, stretch=1)
        self.btn_folder = IconButton("folder", "Otwórz folder", self, size=32, icon_px=17)
        self.btn_close = IconButton("x", "Zamknij historię", self, size=32, icon_px=17)
        folder_menu = QMenu(self)
        folder_menu.addAction("Folder nagrań (.wav)", self.open_recordings_folder.emit)
        folder_menu.addAction("Folder transkrypcji (.txt)", self.open_transcriptions_folder.emit)
        self.btn_folder.setMenu(folder_menu)
        self.btn_close.clicked.connect(self.close_panel)
        head.addWidget(self.btn_folder)
        head.addWidget(self.btn_close)
        lay.addLayout(head)

        self.list = QListWidget(self)
        self.list.setObjectName("HistoryList")
        self.list.setFrameShape(QFrame.Shape.NoFrame)
        self.list.setVerticalScrollMode(QListWidget.ScrollMode.ScrollPerPixel)
        self.list.itemDoubleClicked.connect(self._on_item_activated)
        lay.addWidget(self.list, stretch=1)

        self.lbl_empty = QLabel("Brak nagrań. Pierwsze pojawi się tutaj po zakończeniu nagrywania.", self)
        self.lbl_empty.setObjectName("HistorySub")
        self.lbl_empty.setWordWrap(True)
        lay.addWidget(self.lbl_empty)

        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(40)
        shadow.setOffset(-12, 0)
        shadow.setColor(QColor(0, 0, 0, 110))
        self.setGraphicsEffect(shadow)
        self.hide()

    def populate(self, entries: List[HistoryEntry]) -> None:
        self.list.clear()
        last_group = None
        for e in entries:
            group = day_group_label(e.when.date()) if e.when else "Starsze"
            if group != last_group:
                head = QListWidgetItem(group.upper())
                head.setFlags(Qt.ItemFlag.NoItemFlags)
                head.setData(Qt.ItemDataRole.UserRole, None)
                head.setData(Qt.ItemDataRole.UserRole + 1, "group")
                self.list.addItem(head)
                last_group = group
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, e.txt_path)
            item.setData(Qt.ItemDataRole.UserRole + 2, e.wav_path)
            row = _HistoryRow(e, self.list)
            row.open_audio.connect(self.audio_requested.emit)
            item.setSizeHint(QSize(self.WIDTH - 40, 58))
            if e.txt_path:
                item.setToolTip("Kliknij dwukrotnie, aby otworzyć transkrypcję")
            self.list.addItem(item)
            self.list.setItemWidget(item, row)
        self.lbl_empty.setVisible(not entries)
        self.list.setVisible(bool(entries))

    def _on_item_activated(self, item: QListWidgetItem):
        txt = item.data(Qt.ItemDataRole.UserRole)
        wav = item.data(Qt.ItemDataRole.UserRole + 2)
        if txt:
            self.transcript_requested.emit(txt)
        elif wav:
            self.audio_requested.emit(wav)

    def open_panel(self) -> None:
        self.reposition()
        self.raise_()
        self.show()

    def close_panel(self) -> None:
        if self.isVisible():
            self.hide()
            self.closed.emit()

    def reposition(self) -> None:
        p = self.parentWidget()
        if p is not None:
            self.setGeometry(p.width() - self.WIDTH, 0, self.WIDTH, p.height())

    def apply_theme(self) -> None:
        self.btn_folder.apply_theme()
        self.btn_close.apply_theme()


class SegmentedControl(QFrame):
    """Przełącznik segmentowy (np. Mikrofon / System / Oba) z API zbliżonym do QComboBox."""

    currentIndexChanged = Signal(int)

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setObjectName("Segmented")
        self._lay = QHBoxLayout(self)
        self._lay.setContentsMargins(3, 3, 3, 3)
        self._lay.setSpacing(2)
        self._buttons: List[QPushButton] = []
        self._data: List[object] = []
        self._icons: List[Tuple[str, ...]] = []
        self._index = -1
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

    def addItem(self, text: str, data=None, icon_names: Tuple[str, ...] = ()) -> None:
        btn = QPushButton(text, self)
        btn.setObjectName("SegmentBtn")
        btn.setCheckable(True)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        idx = len(self._buttons)
        btn.clicked.connect(lambda _=False, i=idx: self.setCurrentIndex(i))
        self._buttons.append(btn)
        self._data.append(data)
        self._icons.append(tuple(icon_names))
        self._lay.addWidget(btn)
        if self._index == -1:
            self.setCurrentIndex(0)
        self.apply_theme()

    def count(self) -> int:
        return len(self._buttons)

    def currentIndex(self) -> int:
        return self._index

    def currentData(self):
        return self._data[self._index] if 0 <= self._index < len(self._data) else None

    def currentText(self) -> str:
        return self._buttons[self._index].text() if 0 <= self._index < len(self._buttons) else ""

    def itemData(self, index: int):
        return self._data[index] if 0 <= index < len(self._data) else None

    def findData(self, data) -> int:
        try:
            return self._data.index(data)
        except ValueError:
            return -1

    def setCurrentIndex(self, index: int) -> None:
        if not (0 <= index < len(self._buttons)):
            return
        changed = index != self._index
        self._index = index
        for i, b in enumerate(self._buttons):
            b.setChecked(i == index)
        self.apply_theme()
        if changed:
            self.currentIndexChanged.emit(index)

    def setEnabled(self, enabled: bool) -> None:  # noqa: N802 - API Qt
        super().setEnabled(enabled)
        self.apply_theme()

    def apply_theme(self) -> None:
        t = current_tokens()
        for i, b in enumerate(self._buttons):
            names = self._icons[i] if i < len(self._icons) else ()
            if not names:
                continue
            color = t.text_primary if b.isChecked() else t.text_secondary
            if not self.isEnabled():
                color = t.text_muted
            if len(names) == 1:
                b.setIcon(make_icon(names[0], color, 16))
                b.setIconSize(QSize(16, 16))
            else:
                b.setIcon(_combined_icon(names, color))
                b.setIconSize(QSize(34, 16))


def _combined_icon(names: Tuple[str, ...], color: str):
    """Skleja kilka ikon obok siebie (np. mikrofon + słuchawki)."""
    from PySide6.QtGui import QIcon, QPixmap
    from recorder.ui.icons import icon_pixmap
    pix = QPixmap(34 * 2, 16 * 2)
    pix.fill(Qt.GlobalColor.transparent)
    p = QPainter(pix)
    x = 0
    for n in names:
        p.drawPixmap(x, 0, 32, 32, icon_pixmap(n, color, 16))
        x += 36
    p.end()
    pix.setDevicePixelRatio(2.0)
    return QIcon(pix)
