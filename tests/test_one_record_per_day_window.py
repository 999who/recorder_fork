"""Start → Stop → Start tego samego dnia w prawdziwym oknie: ta sama transkrypcja, WAV dopisywany, bez nowego nagrania."""
import os
from unittest.mock import MagicMock, patch

import numpy as np
import pytest


@pytest.fixture
def win(qapp, tmp_path, monkeypatch):
    from recorder.ui import window as W
    monkeypatch.setattr(W, "is_one_record_per_day", lambda: True)
    monkeypatch.setattr(W, "get_record_source_mode", lambda: W.RecordSourceMode.HYBRID_DUAL)
    w = W.SmartDictaphoneWindow()
    w.transcriptions_dir = str(tmp_path / "t")
    w.recordings_dir = str(tmp_path / "r")
    os.makedirs(w.transcriptions_dir)
    os.makedirs(w.recordings_dir)
    w._resolve_mic_device = lambda: (None, "")
    w.cloud_sync.config["auto_sync"] = False
    w.cloud_sync.config["live_streaming"] = False
    yield w, W
    w.timer.stop()
    w.close()


def _record(w, W, seconds, text):
    rolling = MagicMock()
    with patch.object(W, "RollingTranscriptionWorker", return_value=rolling), \
         patch.object(w.worker, "start", lambda *a, **k: None):
        w._on_start_clicked()
    w.worker.wav_writer.write_frames(np.zeros(16000 * 2 * seconds, dtype=np.int16).tobytes())
    with patch.object(w.worker, "wait", lambda *a, **k: True), \
         patch.object(w.worker, "get_remaining_blocks", lambda: []):
        w._on_stop_clicked()
    prior = list(rolling.preload_session.call_args.args[0]) if rolling.preload_session.called else []
    turns = prior + [{"start": 0.0, "end": 1.0, "speaker": "Mikrofon", "text": text, "channel": "mic"}]
    from recorder.core.session import TranscriptionSession, get_session_path_for_txt
    s = TranscriptionSession(turns=turns)
    s.save_to_json(get_session_path_for_txt(w.current_live_txt_path))
    w._on_rolling_finished("<p>x</p>", text, turns)
    return rolling


def test_second_start_same_day_continues_the_record(win):
    w, W = win
    from recorder.audio.capture import wav_duration_seconds
    _record(w, W, 3, "pierwsza")
    first_txt, first_wav = w.current_live_txt_path, w.current_live_wav_path
    rolling = _record(w, W, 2, "druga")
    assert w.current_live_txt_path == first_txt
    assert w.current_live_wav_path == first_wav
    assert wav_duration_seconds(first_wav) == 5.0                     # 3 s + 2 s w jednym pliku
    turns, words = rolling.preload_session.call_args.args[:2]
    assert [t["text"] for t in turns] == ["pierwsza"]
    assert rolling.preload_session.call_args.kwargs["offset_sec"] == 3.0
    assert len([n for n in os.listdir(w.transcriptions_dir) if n.endswith(".txt")]) == 1
