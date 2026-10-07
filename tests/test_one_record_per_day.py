"""Jedno nagranie na dzień: Start po Stop tego samego dnia kontynuuje dzisiejsze nagranie."""
import os
import wave
from datetime import date, datetime
from types import SimpleNamespace

import numpy as np

from recorder.audio.capture import StreamingWavWriter, wav_duration_seconds
from recorder.core.day_record import find_day_record, timestamp_to_datetime


def _touch(path, text="x"):
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def test_find_day_record_picks_latest_of_that_day(tmp_path):
    for name in ("transkrypcja_20261006_235900_000001.txt", "transkrypcja_20261007_091500_000001.txt",
                 "transkrypcja_20261007_140000_123456.txt", "transkrypcja_moj_plik.txt", "transkrypcja_20261007_091500_000001.json"):
        _touch(tmp_path / name)
    assert find_day_record(str(tmp_path), date(2026, 10, 7)) == "20261007_140000_123456"
    assert find_day_record(str(tmp_path), date(2026, 10, 8)) is None
    assert timestamp_to_datetime("20261007_140000_123456") == datetime(2026, 10, 7, 14, 0, 0, 123456)


def test_wav_append_keeps_earlier_audio(tmp_path):
    p = str(tmp_path / "a.wav")
    w = StreamingWavWriter(p, 2, 16000)
    w.write_frames(np.full(16000 * 2, 1, dtype=np.int16).tobytes())
    w.close()
    w = StreamingWavWriter(p, 2, 16000, append=True)
    assert w.existing_frames == 16000
    w.write_frames(np.full(8000 * 2, 2, dtype=np.int16).tobytes())
    w.close()
    assert wav_duration_seconds(p) == 1.5
    with wave.open(p) as wf:
        data = np.frombuffer(wf.readframes(wf.getnframes()), dtype=np.int16)
    assert data[0] == 1 and data[-1] == 2


def test_wav_append_with_other_format_never_overwrites(tmp_path):
    p = str(tmp_path / "a.wav")
    w = StreamingWavWriter(p, 2, 16000)
    w.write_frames(np.ones(16000 * 2, dtype=np.int16).tobytes())
    w.close()
    w = StreamingWavWriter(p, 1, 16000, append=True)   # np. zmiana trybu z „Oba” na „Mikrofon”
    w.write_frames(np.ones(16000, dtype=np.int16).tobytes())
    w.close()
    assert wav_duration_seconds(p) == 1.0
    assert w.file_path.endswith("a_cz2.wav") and wav_duration_seconds(w.file_path) == 1.0


def test_rolling_worker_continues_timeline_and_keeps_earlier_turns():
    from recorder.core.rolling_transcriber import RollingTranscriptionWorker
    rw = RollingTranscriptionWorker.__new__(RollingTranscriptionWorker)
    rw.time_offset_sec = 0.0
    rw.total_processed_seconds = rw.latest_session_seconds = 0.0
    rw._cached_html = ""
    rw.preload_session([{"start": 0.0, "end": 5.0, "speaker": "Gleb", "text": "rano"}], [{"word": "rano"}], offset_sec=60.0)
    assert [t["text"] for t in rw.all_turns] == ["rano"] and rw.get_all_words() == [{"word": "rano"}]
    import queue
    rw.block_queue = queue.Queue()
    rw._is_running = True
    rw.add_block(1, 2.0, 4.0, np.zeros(32000, dtype=np.float32), channel_source="mic")
    b = rw.block_queue.get_nowait()
    assert (b.start_sec, b.end_sec) == (62.0, 64.0)


def test_load_day_record_reads_session_and_offset(tmp_path):
    from recorder.core.session import TranscriptionSession, get_session_path_for_txt
    from recorder.ui.window import SmartDictaphoneWindow
    tdir, rdir = tmp_path / "t", tmp_path / "r"
    tdir.mkdir(); rdir.mkdir()
    ts = "20261007_091500_000001"
    txt = tdir / f"transkrypcja_{ts}.txt"
    _touch(txt)
    sess = TranscriptionSession()
    sess.turns = [{"start": 0.0, "end": 3.0, "speaker": "Gleb", "text": "a", "channel": "mic"}]
    sess.meeting_id = "11111111-1111-1111-1111-111111111111"
    sess.save_to_json(get_session_path_for_txt(str(txt)))
    w = StreamingWavWriter(str(rdir / f"inteligentne_nagranie_{ts}.wav"), 2, 16000)
    w.write_frames(np.zeros(16000 * 2 * 10, dtype=np.int16).tobytes())
    w.close()
    fake = SimpleNamespace(transcriptions_dir=str(tdir), recordings_dir=str(rdir))
    day = SmartDictaphoneWindow._load_day_record(fake, datetime(2026, 10, 7, 15, 0))
    assert day["timestamp"] == ts
    assert day["meeting_id"] == sess.meeting_id
    assert [t["text"] for t in day["turns"]] == ["a"]
    assert day["offset_sec"] == 10.0
    assert day["start"] == datetime(2026, 10, 7, 9, 15, 0, 1)
    assert SmartDictaphoneWindow._load_day_record(fake, datetime(2026, 10, 8, 9, 0)) is None


def test_long_silence_does_not_split_todays_record(monkeypatch):
    from recorder.ui import window as W
    monkeypatch.setattr(W, "is_one_record_per_day", lambda: True)
    fake = SimpleNamespace(session_start_time=datetime.now())   # brak innych pól: podział by się wysypał
    W.SmartDictaphoneWindow._on_session_split_triggered(fake, "Cisza > 15 min")
