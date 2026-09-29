"""
Testy obciążeniowe stanów okna i dynamicznych reguł QSS (układ „notatnik”).
Zakres:
1. Powiadomienie o problemie z chmurą (wysuwany komunikat zamiast stałej linii stanu)
2. Widoczność kanałów w panelu nagrywania zależnie od trybu źródła z ustawień
3. Wyciszanie mikrofonu i dźwięku systemu z panelu nagrywania
4. Przejścia trybów panelu nagrywania i pierścienia ciszy
5. Selektory stylów QSS nowych elementów dla wszystkich 4 motywów
6. Spójność panelu nagrywania (brak zdublowanych przycisków)
7. Brak wycieków pamięci przy 10 000 szybkich operacji UI
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

from PySide6.QtWidgets import QPushButton

from recorder.ui.window import SmartDictaphoneWindow
from recorder.ui import theme
from recorder.ui.widgets import ChannelToggle
from recorder.config import RecordSourceMode


@pytest.fixture
def window(qapp, monkeypatch):
    """Tworzy okno w trybie bez ekranu, bez uruchamiania nagrywania."""
    win = SmartDictaphoneWindow()
    win.resize(900, 700)
    win.show()
    qapp.processEvents()
    yield win
    try:
        win._force_quit = True
        win.close()
        win.deleteLater()
        qapp.processEvents()
    except Exception:
        pass


def test_cloud_problem_toast_and_retry(window, qapp, monkeypatch):
    """Błąd chmury wysuwa komunikat, a przycisk ponowienia uruchamia wysyłkę kolejki offline."""
    assert not window.cloud_toast.isVisible()
    window._show_cloud_problem("Nie udało się wysłać", "Brak połączenia z serwerem.")
    qapp.processEvents()
    assert window.cloud_toast.isVisible()
    assert window.cloud_toast.property("kind") == "error"
    assert window.cloud_toast.lbl_title.text() == "Nie udało się wysłać"
    # Opis nie może być ucięty: wysokość mieści zawinięty tekst
    assert window.cloud_toast.height() >= window.cloud_toast.layout().totalMinimumSize().height()

    calls = []
    monkeypatch.setattr(window.cloud_sync, "process_offline_queue_async", lambda *a, **k: calls.append(1))
    window.cloud_toast.btn_retry.click()
    assert calls == [1]
    assert not window.cloud_toast.isVisible()

    # Zwykłe komunikaty stanu nie pokazują już niczego na ekranie
    window._set_cloud_status("Synchronizacja zakończona", "success")
    assert not window.cloud_toast.isVisible()


@pytest.mark.parametrize("mode, mic_visible, sys_visible", [
    (RecordSourceMode.MIC_ONLY, True, False),
    (RecordSourceMode.SYSTEM_ONLY, False, True),
    (RecordSourceMode.HYBRID_DUAL, True, True),
])
def test_source_mode_channels(window, monkeypatch, mode, mic_visible, sys_visible):
    """Tryb źródła z ustawień decyduje, które kanały widać w panelu nagrywania."""
    import recorder.ui.window as window_module
    monkeypatch.setattr(window_module, "get_record_source_mode", lambda: mode)
    window._apply_source_mode_to_ui()
    assert window.dock.ch_mic.isVisibleTo(window.dock) == mic_visible
    assert window.dock.ch_sys.isVisibleTo(window.dock) == sys_visible


def test_mute_toggle_transitions(window):
    """Kliknięcie kanału wycisza go i przywraca; stan jest spójny po wielu przełączeniach."""
    for toggle, channel in ((window._toggle_mic_mute, window.dock.ch_mic),
                            (window._toggle_sys_mute, window.dock.ch_sys)):
        assert not channel.is_muted()
        toggle()
        assert channel.is_muted()
        toggle()
        assert not channel.is_muted()
        for i in range(200):
            toggle()
            assert channel.is_muted() == (i % 2 == 0)
        assert not channel.is_muted()


def test_dock_mode_transitions(window):
    """Tryby panelu: nagrywanie, auto-pauza, pauza ręczna i kończenie transkrypcji."""
    dock = window.dock
    dock.set_mode("recording")
    dock.set_silence_fraction(0.5)
    assert dock.dot.mode() == "recording"

    dock.set_mode("autopaused")
    assert dock.dot.mode() == "autopaused"
    assert dock.btn_pause.icon_name() == "pause"

    dock.set_mode("manualpaused")
    assert dock.btn_pause.icon_name() == "play"
    assert dock.btn_pause.toolTip() == "Wznów nagrywanie"

    dock.set_mode("processing", "Kończę transkrypcję 40%")
    assert dock.lbl_time.text() == "Kończę transkrypcję 40%"
    assert dock.lbl_time.property("processing") == "true"
    assert not dock.btn_stop.isVisibleTo(dock)
    # Zegar nie nadpisuje tekstu w trakcie kończenia transkrypcji
    dock.set_time("12:00")
    assert dock.lbl_time.text() == "Kończę transkrypcję 40%"

    dock.set_mode("recording")
    dock.set_time("12:01")
    assert dock.lbl_time.text() == "12:01"
    assert dock.lbl_time.property("processing") == "false"
    assert dock.btn_stop.isVisibleTo(dock)


def test_dynamic_qss_rules_all_themes():
    """Każdy motyw zawiera reguły dla elementów nowego układu."""
    required_selectors = [
        "QFrame#TranscriptSheet",
        "QFrame#RecordDock",
        'QLabel#DockTime[processing="true"]',
        "QFrame#CloudToast",
        'QFrame#CloudToast[kind="warning"]',
        "QFrame#HistoryPanel",
        "QPushButton#IconBtn",
        "QPushButton#SegmentBtn:checked",
    ]
    for th_id in ["classic_dark", "classic_light", "emanager_dark", "emanager_light"]:
        qss = theme.generate_theme_qss(th_id)
        for sel in required_selectors:
            assert sel in qss, f"Brak selektora '{sel}' w motywie '{th_id}'"


def test_record_dock_has_no_duplicate_controls(window):
    """Panel nagrywania ma dokładnie jeden przycisk pauzy, jeden stopu i po jednym kanale."""
    dock = window.dock
    buttons = [b for b in dock.findChildren(QPushButton) if b.objectName() in ("DockPauseBtn", "DockStopBtn")]
    assert sorted(b.objectName() for b in buttons) == ["DockPauseBtn", "DockStopBtn"]
    assert window.btn_pause is dock.btn_pause
    assert window.btn_stop is dock.btn_stop
    assert len(dock.findChildren(ChannelToggle)) == 2
    # Przyciski bez tekstu, ale z podpowiedzią
    for b in buttons:
        assert b.text() == ""
        assert b.toolTip()


def test_memory_and_cpu_stress_10000_operations(window):
    """10 000 szybkich operacji UI bez wycieku pamięci (> 2 MB) i skoków CPU (> 2,5 s)."""
    tracemalloc.start()
    before = tracemalloc.take_snapshot()
    start_time = time.perf_counter()

    modes = ["recording", "autopaused", "manualpaused"]
    cloud_states = ["info", "success", "warning", "error", "purple"]

    for i in range(10000):
        window.dock.set_levels(i % 100, (i * 7) % 100)
        window.dock.set_silence_fraction((i % 50) / 50.0)
        if i % 50 == 0:
            window._set_cloud_status(f"Krok synchronizacji {i}", cloud_states[(i // 50) % len(cloud_states)])
        if i % 100 == 0:
            window._toggle_mic_mute()
            window._toggle_sys_mute()
        if i % 250 == 0:
            window.dock.set_mode(modes[(i // 250) % len(modes)])

    elapsed = time.perf_counter() - start_time
    after = tracemalloc.take_snapshot()
    tracemalloc.stop()

    total_mem_delta_kb = sum(stat.size_diff for stat in after.compare_to(before, "lineno")) / 1024.0
    print(f"\n[Stress Test 10,000 ops] Elapsed: {elapsed:.3f}s, Memory delta: {total_mem_delta_kb:.2f} KB")

    assert elapsed < 2.5, f"10 000 operacji trwało {elapsed:.3f}s (limit 2,5 s)"
    assert total_mem_delta_kb < 2048, f"Wyciek pamięci: przyrost {total_mem_delta_kb:.2f} KB (> 2 MB)"
