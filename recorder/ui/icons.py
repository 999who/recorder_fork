"""
Zestaw ikon liniowych (styl Lucide, licencja ISC) rysowanych z SVG w kolorze motywu.

Ikony są trzymane w kodzie jako ścieżki SVG, dzięki czemu nie trzeba dołączać plików
do paczki PyInstallera, a kolor dopasowuje się do aktywnego motywu.
"""

from typing import Dict, Optional, Tuple

from PySide6.QtCore import QByteArray, QRectF, QSize, Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap, QGuiApplication

try:
    from PySide6.QtSvg import QSvgRenderer
except Exception:  # pragma: no cover - QtSvg jest częścią PySide6, ale chronimy start aplikacji
    QSvgRenderer = None


_PATHS: Dict[str, str] = {
    "mic": '<rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5 11a7 7 0 0 0 14 0M12 18v3"/>',
    "mic_off": '<rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5 11a7 7 0 0 0 14 0M12 18v3M3 3l18 18"/>',
    "headphones": '<path d="M4 15v-3a8 8 0 0 1 16 0v3"/><rect x="3" y="14" width="4" height="6" rx="1.5"/><rect x="17" y="14" width="4" height="6" rx="1.5"/>',
    "headphones_off": '<path d="M4 15v-3a8 8 0 0 1 16 0v3"/><rect x="3" y="14" width="4" height="6" rx="1.5"/><rect x="17" y="14" width="4" height="6" rx="1.5"/><path d="M3 3l18 18"/>',
    "pause": '<rect x="6.5" y="5" width="3.5" height="14" rx="1" fill="{c}" stroke="none"/><rect x="14" y="5" width="3.5" height="14" rx="1" fill="{c}" stroke="none"/>',
    "play": '<path d="M7.5 5.5l11 6.5-11 6.5z" fill="{c}" stroke="none"/>',
    "stop": '<rect x="6" y="6" width="12" height="12" rx="2.5" fill="{c}" stroke="none"/>',
    "record": '<circle cx="12" cy="12" r="6" fill="{c}" stroke="none"/>',
    "upload": '<path d="M12 15V4M7 9l5-5 5 5M4 15v4a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-4"/>',
    "history": '<path d="M3 12a9 9 0 1 0 2.6-6.4L3 8M3 3v5h5M12 7v5l3 2"/>',
    "settings": '<path d="M4 6h9M17 6h3M4 12h3M11 12h9M4 18h11M19 18h1"/><circle cx="15" cy="6" r="2"/><circle cx="9" cy="12" r="2"/><circle cx="17" cy="18" r="2"/>',
    "copy": '<rect x="8" y="8" width="12" height="12" rx="2"/><path d="M16 8V6a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v8a2 2 0 0 0 2 2h2"/>',
    "check": '<path d="M5 12.5l4.5 4.5L19 7.5"/>',
    "download": '<path d="M12 4v11M7 10l5 5 5-5M4 20h16"/>',
    "cloud": '<path d="M7 18a5 5 0 1 1 1.2-9.9A6 6 0 0 1 19.5 10 4 4 0 0 1 18 18z"/>',
    "cloud_off": '<path d="M7 18a5 5 0 1 1 1.2-9.9M11 5.5A6 6 0 0 1 19.5 10 4 4 0 0 1 20 17.5M3 3l18 18"/>',
    "folder": '<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>',
    "wave": '<path d="M3 12h2M7 8v8M11 5v14M15 9v6M19 11v2"/>',
    "doc": '<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5M9 13h6M9 17h4"/>',
    "refresh": '<path d="M20 11a8 8 0 0 0-14.3-4.9L4 8M4 3v5h5M4 13a8 8 0 0 0 14.3 4.9L20 16M20 21v-5h-5"/>',
    "book": '<path d="M4 5a2 2 0 0 1 2-2h13v16H6a2 2 0 0 0-2 2z"/><path d="M4 19V5M8 7h7"/>',
    "palette": '<circle cx="12" cy="12" r="9"/><circle cx="8" cy="10" r="1.2"/><circle cx="12" cy="7.5" r="1.2"/><circle cx="16" cy="10" r="1.2"/><path d="M12 21a2.5 2.5 0 0 1 0-5h2a3 3 0 0 0 3-3"/>',
    "wand": '<path d="M15 4V2M15 10V8M11 6H9M21 6h-2M4 20l10-10M17.8 3.2l-1.4 1.4M17.8 8.8l-1.4-1.4"/>',
    "x": '<path d="M6 6l12 12M18 6L6 18"/>',
    "sliders": '<path d="M4 6h9M17 6h3M4 12h3M11 12h9M4 18h11M19 18h1"/><circle cx="15" cy="6" r="2"/><circle cx="9" cy="12" r="2"/><circle cx="17" cy="18" r="2"/>',
    "alert": '<circle cx="12" cy="12" r="9"/><path d="M12 7.5v5.5M12 16.5v.01"/>',
    "rocket": '<path d="M5 15c-1.5 1.5-2 5-2 5s3.5-.5 5-2M9 12l3 3M14.5 3.5C18 3 21 6 20.5 9.5L13 17l-6-6z"/><circle cx="15" cy="9" r="1.5"/>',
}

_cache: Dict[Tuple[str, str, int, float], QIcon] = {}


def _svg(name: str, color: str, stroke_width: float = 1.8) -> bytes:
    body = _PATHS[name].replace("{c}", color)
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
        f'stroke="{color}" stroke-width="{stroke_width}" stroke-linecap="round" stroke-linejoin="round">'
        f"{body}</svg>"
    ).encode("utf-8")


def icon_pixmap(name: str, color: str, size: int = 20, stroke_width: float = 1.8) -> QPixmap:
    """Renderuje ikonę do QPixmap z uwzględnieniem skalowania ekranu (HiDPI)."""
    ratio = 1.0
    app = QGuiApplication.instance()
    if app is not None and QGuiApplication.primaryScreen() is not None:
        ratio = max(1.0, float(QGuiApplication.primaryScreen().devicePixelRatio()))
    px = int(round(size * ratio))
    pix = QPixmap(px, px)
    pix.fill(Qt.GlobalColor.transparent)
    if QSvgRenderer is None or name not in _PATHS:
        return pix
    renderer = QSvgRenderer(QByteArray(_svg(name, color, stroke_width)))
    painter = QPainter(pix)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    renderer.render(painter, QRectF(0, 0, px, px))
    painter.end()
    pix.setDevicePixelRatio(ratio)
    return pix


def make_icon(name: str, color: str, size: int = 20, disabled_color: Optional[str] = None,
              stroke_width: float = 1.8) -> QIcon:
    """Zwraca QIcon w podanym kolorze (z osobnym kolorem dla stanu wyłączonego)."""
    key = (name, f"{color}|{disabled_color}", size, stroke_width)
    cached = _cache.get(key)
    if cached is not None:
        return cached
    ic = QIcon()
    ic.addPixmap(icon_pixmap(name, color, size, stroke_width), QIcon.Mode.Normal)
    if disabled_color:
        ic.addPixmap(icon_pixmap(name, disabled_color, size, stroke_width), QIcon.Mode.Disabled)
    _cache[key] = ic
    return ic


def icon_names():
    return sorted(_PATHS.keys())


def clear_icon_cache() -> None:
    _cache.clear()


def icon_size(size: int) -> QSize:
    return QSize(size, size)
