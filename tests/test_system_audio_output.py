"""
Kanał „Dźwięk Systemu” nagrywa tylko jedno wyjście audio. Testy sprawdzają, że nagrywanie podąża za wyjściem,
na którym faktycznie gra rozmowa (np. Google Meet na słuchawkach Bluetooth), oraz że ręcznie wybrane wyjście
jest odnajdywane po nazwie, a nie po indeksie PortAudio, który zmienia się po podłączeniu urządzeń.
"""
from unittest.mock import MagicMock, patch

import pytest

from recorder.audio.devices import output_peak, pick_louder_output


SPEAKERS = {"index": 5, "name": "Głośniki (Realtek(R) Audio) [Loopback]", "defaultSampleRate": 48000,
            "maxInputChannels": 2, "isLoopbackDevice": True}
HEADSET = {"index": 7, "name": "Słuchawki (AirPods) [Loopback]", "defaultSampleRate": 48000,
           "maxInputChannels": 2, "isLoopbackDevice": True}


def test_output_peak_matches_portaudio_and_core_audio_names():
    peaks = {"Głośniki (Realtek(R) Audio)": 0.4}
    assert output_peak(peaks, "Głośniki (Realtek(R) Audio) [Loopback]") == 0.4
    assert output_peak(peaks, "Słuchawki (AirPods) [Loopback]") is None
    assert output_peak(peaks, "") is None


def test_pick_louder_output_only_when_recorded_output_is_silent():
    peaks = {"Głośniki (Realtek(R) Audio)": 0.0, "Słuchawki (AirPods)": 0.3}
    assert pick_louder_output(peaks, SPEAKERS["name"]) == "Słuchawki (AirPods)"
    # Nagrywane wyjście gra - nie przełączamy
    assert pick_louder_output({**peaks, "Głośniki (Realtek(R) Audio)": 0.2}, SPEAKERS["name"]) is None
    # Na innym wyjściu tylko cichy szum - nie przełączamy
    assert pick_louder_output({**peaks, "Słuchawki (AirPods)": 0.005}, SPEAKERS["name"]) is None
    # Nagrywane wyjście zniknęło z listy (odłączone) - liczy się jak cisza
    assert pick_louder_output({"Słuchawki (AirPods)": 0.3}, SPEAKERS["name"]) == "Słuchawki (AirPods)"


class _FakeProbe:
    available = True
    peaks_value = {}

    def peaks(self):
        return dict(self.peaks_value)


def _run_system_worker(peaks, settings=None, loopback_index=None, ticks=12):
    from PySide6.QtWidgets import QApplication
    from recorder.ui.workers import SmartAudioWorker, RecordSourceMode, SmartRecordState

    _ = QApplication.instance() or QApplication([])
    worker = SmartAudioWorker()
    worker.source_mode = RecordSourceMode.SYSTEM_ONLY
    worker.loopback_device_index = loopback_index
    worker._is_running = True
    worker.state = SmartRecordState.RECORDING_SPEECH

    pa = MagicMock()
    pa.get_default_wasapi_loopback.return_value = SPEAKERS
    pa.get_loopback_device_info_generator.side_effect = lambda: iter([SPEAKERS, HEADSET])
    pa.get_device_info_by_index.side_effect = lambda i: {5: SPEAKERS, 7: HEADSET}.get(i, {"index": i, "name": "Inne"})
    opened = []

    def _open(**kw):
        opened.append(kw["input_device_index"])
        s = MagicMock()
        s.is_active.return_value = True
        return s

    pa.open.side_effect = _open
    now = [100.0]
    count = [0]

    def _msleep(_ms):
        count[0] += 1
        now[0] += 0.5
        if count[0] >= ticks:
            worker._is_running = False

    _FakeProbe.peaks_value = peaks
    with patch("recorder.ui.workers.pyaudio.PyAudio", return_value=pa), \
         patch("recorder.ui.workers.OutputActivityProbe", _FakeProbe), \
         patch("recorder.ui.workers.load_user_settings", return_value=dict(settings or {})), \
         patch("time.time", side_effect=lambda: now[0]), \
         patch("time.sleep", return_value=None), \
         patch.object(worker, "msleep", side_effect=_msleep):
        worker.run()
    return opened


def test_follows_output_where_the_call_plays():
    opened = _run_system_worker({"Głośniki (Realtek(R) Audio)": 0.0, "Słuchawki (AirPods)": 0.3})
    assert opened[0] == SPEAKERS["index"]          # start: domyślne wyjście
    assert HEADSET["index"] in opened[1:]          # potem przełączenie na grające słuchawki


def test_stays_on_default_output_when_it_plays():
    opened = _run_system_worker({"Głośniki (Realtek(R) Audio)": 0.3, "Słuchawki (AirPods)": 0.3})
    assert opened[0] == SPEAKERS["index"]
    assert HEADSET["index"] not in opened


def test_silent_output_is_not_restarted_in_a_loop():
    # Brak danych z loopbacku przy ciszy na wyjściu jest normalny - bez restartów co 1,5 s
    opened = _run_system_worker({"Głośniki (Realtek(R) Audio)": 0.0}, ticks=30)
    assert opened == [SPEAKERS["index"]]


def test_pinned_output_found_by_name_after_index_shift_and_kept():
    # Indeks 5 wskazuje teraz głośniki, ale użytkownik wybrał słuchawki - szukamy po nazwie
    opened = _run_system_worker(
        {"Głośniki (Realtek(R) Audio)": 0.3, "Słuchawki (AirPods)": 0.0},
        settings={"loopback_device_label": "🎧 Słuchawki (AirPods)"},
        loopback_index=5,
    )
    assert opened == [HEADSET["index"]]            # wybrane wyjście nie jest podmieniane automatycznie
