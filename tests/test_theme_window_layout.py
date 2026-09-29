"""
Testy hierarchii układu widżetów okna i dynamicznych właściwości motywów.
Weryfikacja braku osieroconych kontrolek, braku duplikatów w layoutach oraz poprawności stylów.
"""

import os
import sys
import time
import tracemalloc
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from PySide6.QtWidgets import QApplication, QWidget, QLayout, QPushButton, QLabel, QComboBox, QGroupBox, QFrame
from PySide6.QtCore import Qt

from recorder.ui.window import SmartDictaphoneWindow
from recorder.ui import theme
from recorder.config import RecordSourceMode


@pytest.fixture
def window(qapp):
    win = SmartDictaphoneWindow()
    yield win
    try:
        win._force_quit = True
        win.close()
        win.deleteLater()
        qapp.processEvents()
    except Exception:
        pass


def test_top_bar_icon_only_controls(window):
    """
    Pasek górny: nazwa aplikacji, opis źródeł dźwięku i trzy przyciski z samymi ikonami.
    Każdy przycisk ma podpowiedź po polsku zamiast tekstu.
    """
    assert window.lbl_brand.text()
    assert window.btn_source_pill.text(), "Opis źródeł dźwięku powinien być widoczny w pasku"
    for btn in (window.btn_upload, window.btn_history, window.btn_settings):
        assert btn.text() == "", f"{btn.toolTip()} nie powinien mieć tekstu"
        assert btn.toolTip()
        assert not btn.icon().isNull()
    # Wybór mikrofonu, wyjścia i modelu przeniesiono do ustawień
    for removed in ("combo_source_mode", "combo_devices", "combo_loopback_devices", "combo_model", "lbl_cloud_status"):
        assert not hasattr(window, removed), f"{removed} powinien być tylko w ustawieniach"


def test_no_orphan_widgets_in_entire_window(window):
    """
    Challenge 2: Verify that every QWidget attribute on SmartDictaphoneWindow
    is properly parented and belongs to the window's widget tree.
    """
    orphans = []
    for name in dir(window):
        if name.startswith("_"):
            continue
        try:
            val = getattr(window, name)
            if isinstance(val, QWidget) and val is not window:
                if val.window() is not window:
                    orphans.append((name, type(val).__name__, val.objectName()))
        except Exception:
            pass

    assert len(orphans) == 0, f"Found orphan widgets not belonging to window: {orphans}"


def test_no_duplicate_widgets_across_layouts(window):
    """
    Challenge 3: Traverse entire layout tree and verify no widget is added more than once.
    """
    main_layout = window.centralWidget().layout()

    widget_occurrences = {}

    def walk_layout(layout, path):
        for i in range(layout.count()):
            item = layout.itemAt(i)
            w = item.widget()
            sub_l = item.layout()
            if w is not None:
                widget_occurrences.setdefault(w, []).append((path, i))
                if w.layout() is not None:
                    walk_layout(w.layout(), f"{path}->{w.objectName() or type(w).__name__}.layout")
            if sub_l is not None:
                walk_layout(sub_l, f"{path}->sub_{type(sub_l).__name__}[{i}]")

    walk_layout(main_layout, "main")

    duplicates = {w: paths for w, paths in widget_occurrences.items() if len(paths) > 1}
    assert len(duplicates) == 0, f"Found duplicate widgets in layout tree: {duplicates}"


def test_no_overlapping_sibling_controls(window):
    """
    Challenge 4: Check for overlapping bounding rectangles between sibling controls
    at various window resolutions (800x600, 1024x768, 1440x900).
    """
    resolutions = [(800, 600), (1024, 768), (1440, 900)]
    for w_val, h_val in resolutions:
        window.resize(w_val, h_val)
        window.show()
        QApplication.processEvents()

        parent_map = {}
        for child in window.findChildren(QWidget):
            if child.isVisible():
                p = child.parentWidget()
                if p:
                    parent_map.setdefault(p, []).append(child)

        overlaps = []
        for p, children in parent_map.items():
            if type(p).__name__ in ("QStackedWidget", "QTabWidget"):
                continue
            vis_children = [c for c in children if c.isVisible() and c.width() > 0 and c.height() > 0]
            for i in range(len(vis_children)):
                for j in range(i + 1, len(vis_children)):
                    c1 = vis_children[i]
                    c2 = vis_children[j]
                    if c1.isAncestorOf(c2) or c2.isAncestorOf(c1):
                        continue
                    r1 = c1.geometry()
                    r2 = c2.geometry()
                    intersection = r1.intersected(r2)
                    # Ignore 1px border overlaps, check for real overlaps > 2px in both dimensions
                    if not intersection.isEmpty() and intersection.width() > 2 and intersection.height() > 2:
                        overlaps.append((
                            p.objectName() or type(p).__name__,
                            c1.objectName() or type(c1).__name__,
                            c2.objectName() or type(c2).__name__,
                            intersection.getRect()
                        ))

        assert len(overlaps) == 0, f"Overlapping controls at {w_val}x{h_val}: {overlaps}"


def test_silence_ring_high_frequency_alternation_stress(window):
    """
    Szybkie przełączanie mowa/cisza (pierścień wokół kropki nagrywania) 3000 razy
    bez skoków pamięci i bez błędów.
    """
    tracemalloc.start()
    gc_before = tracemalloc.take_snapshot()
    start_time = time.perf_counter()

    window.dock.set_mode("recording")
    for i in range(3000):
        frac = 0.0 if i % 2 == 0 else 0.6
        window.dock.set_silence_fraction(frac)
        assert window.dock.dot.ring() == frac

    elapsed = time.perf_counter() - start_time
    gc_after = tracemalloc.take_snapshot()
    tracemalloc.stop()

    top_stats = gc_after.compare_to(gc_before, "lineno")
    mem_delta_kb = sum(stat.size_diff for stat in top_stats) / 1024.0

    print(f"\n[3,000 silence ring alternations] Elapsed: {elapsed:.3f}s, Memory delta: {mem_delta_kb:.2f} KB")
    assert elapsed < 2.0, f"Przełączanie pierścienia trwało zbyt długo: {elapsed:.3f}s"
    assert mem_delta_kb < 1024, f"Przełączanie pierścienia zwiększyło pamięć o {mem_delta_kb:.2f} KB"


def test_dynamic_properties_persistence_across_all_themes(window):
    """
    Stany (wyciszenie kanału, tryb panelu, rodzaj powiadomienia) przetrwają zmianę motywu,
    a ikony są przerysowane w nowych kolorach.
    """
    window._toggle_mic_mute()
    window.dock.set_mode("manualpaused")
    window._show_cloud_problem("Błąd synchronizacji", "Brak połączenia.")

    all_themes = ["classic_dark", "classic_light", "emanager_dark", "emanager_light", "classic_dark"]
    for th in all_themes:
        theme.apply_theme(window, th)
        window._apply_theme_extras()

        assert window.dock.ch_mic.is_muted()
        assert not window.dock.ch_sys.is_muted()
        assert window.dock.mode() == "manualpaused"
        assert window.dock.btn_pause.icon_name() == "play"
        assert window.cloud_toast.property("kind") == "error"
        assert not window.btn_settings.icon().isNull()


def test_dynamic_property_edge_cases_and_adversarial_inputs(window):
    """
    Nietypowe treści komunikatów chmury: pusty tekst, 10 000 znaków, polskie znaki i emoji,
    znaczniki HTML. Treść jest pokazywana jako zwykły tekst, nigdy jako HTML.
    """
    edge_cases = [
        ("", "info"),
        ("A" * 10000, "warning"),
        ("🔥 Zażółć gęślą jaźń / 语音识别 / 🎧", "purple"),
        ("<script>alert('xss')</script>", "error"),
        ("<b>HTML Tagged Status</b>", "success"),
        ("Custom unrecognized status text", "non_existent_status_123"),
    ]

    for text, status in edge_cases:
        window._set_cloud_status(text, status)
        assert window._last_status_text == text
        assert window._last_status_kind == status
        window._show_cloud_problem("Nie udało się wysłać", text)
        assert window.cloud_toast.lbl_desc.text() == text
        assert window.cloud_toast.lbl_desc.textFormat() == Qt.TextFormat.PlainText
        assert window.cloud_toast.width() <= max(window.centralWidget().width(), 332)
