"""Testy interfejsu silników ASR (Whisper i Parakeet) z atrapami modeli - bez pobierania wag."""
import subprocess
import sys
from types import SimpleNamespace

import numpy as np
import pytest

from recorder.core.asr_engine import AsrEngine, create_asr_engine, normalize_engine_id, ENGINE_PARAKEET, ENGINE_WHISPER
from recorder.core.parakeet_engine import ParakeetEngine, tokens_to_words, split_at_quiet_points
from recorder.core.transcriber import WhisperEngine
from recorder.core.replacements import apply_word_replacements, apply_text_replacements

SR = 16000


def _tone(sec: float) -> np.ndarray:
    t = np.arange(int(sec * SR)) / SR
    return (0.3 * np.sin(2 * np.pi * 200 * t)).astype(np.float32)


class FakeOnnxModel:
    """Udaje model onnx-asr: zwraca stałe tokeny z krokiem 80 ms."""

    def __init__(self, tokens, text):
        self.tokens, self.text = tokens, text
        self.calls = []

    def recognize(self, audio, sample_rate=16000):
        self.calls.append(len(audio) / sample_rate)
        n = len(self.calls)
        # każde wywołanie zwraca inne słowa, żeby filtr powtórzeń ich nie wycinał
        tokens = [f"{t}{'x' * (n - 1)}" if t.startswith(" ") else t for t in self.tokens]
        text = " ".join(t.strip() for t in tokens)
        ts = [round(0.16 * i + 0.24, 3) for i in range(len(tokens))]
        return SimpleNamespace(text=text, tokens=tokens, timestamps=ts, logprobs=None)


def _parakeet(tokens, text, replacements=None):
    eng = ParakeetEngine(threads=2, replacements=replacements)
    eng._model = FakeOnnxModel(tokens, text)
    return eng


def test_factory_selects_engines():
    assert isinstance(create_asr_engine(model_size="parakeet"), ParakeetEngine)
    assert isinstance(create_asr_engine(engine_id=ENGINE_WHISPER, model_size="small"), WhisperEngine)
    assert create_asr_engine(engine_id=ENGINE_PARAKEET).engine_id == ENGINE_PARAKEET
    assert normalize_engine_id("cokolwiek") == ENGINE_WHISPER


def test_both_engines_implement_interface():
    for cls in (ParakeetEngine, WhisperEngine):
        assert issubclass(cls, AsrEngine)
    assert not ParakeetEngine().is_loaded
    assert ParakeetEngine().display_name


def test_tokens_to_words_assembles_words_and_times():
    tokens = [" Dzień", " do", "bry", ",", " pan", "ie"]
    ts = [0.24, 0.72, 0.8, 0.88, 1.2, 1.28]
    words = tokens_to_words(tokens, ts, audio_dur_sec=2.0)
    assert [w["word"] for w in words] == ["Dzień", " dobry,", " panie"]
    assert words[0]["start"] == 0.24 and words[0]["end"] == 0.72
    assert words[1]["start"] == 0.72 and words[1]["end"] == 1.2
    assert words[2]["end"] > words[2]["start"]
    assert tokens_to_words([], [], 1.0) == []


def test_parakeet_transcribe_block_local_time_and_contract():
    eng = _parakeet([" Proszę", " o", " fakturę", " na", " jutro"], "Proszę o fakturę na jutro")
    words = eng.transcribe_block(_tone(3.0))
    assert "".join(w["word"] for w in words).strip() == "Proszę o fakturę na jutro"
    for w in words:
        assert set(w) >= {"word", "start", "end", "probability"}
        assert 0.0 <= w["start"] < w["end"]
    assert eng.transcribe_block(_tone(0.1)) == []


def test_parakeet_long_block_is_split_and_offset():
    eng = _parakeet([" Zamówienie", " gotowe"], "Zamówienie gotowe")
    words = eng.transcribe_block(_tone(45.0))
    assert len(eng._model.calls) >= 3 and max(eng._model.calls) <= 20.0
    starts = [w["start"] for w in words]
    assert max(starts) > 20.0          # słowa z późniejszych fragmentów są przesunięte o offset


def test_split_at_quiet_points_covers_audio():
    audio = _tone(50.0)
    spans = split_at_quiet_points(audio)
    assert spans[0][0] == 0 and spans[-1][1] == len(audio)
    assert all(b == c for (_, b), (c, _) in zip(spans, spans[1:]))


def test_parakeet_applies_replacements():
    eng = _parakeet([" Sprawdź", " kolonka", " z", " danymi"], "Sprawdź kolonka z danymi",
                    replacements=[("kolonka", "kolumna")])
    text = "".join(w["word"] for w in eng.transcribe_block(_tone(2.0)))
    assert "kolumna" in text and "kolonka" not in text


def test_replacements_helpers():
    assert apply_text_replacements("Wyślij do Ka Er Em", [("ka er em", "CRM")]) == "Wyślij do CRM"
    words = [{"word": "Ka", "start": 0.0, "end": 0.2}, {"word": " er,", "start": 0.2, "end": 0.4}]
    out = apply_word_replacements(words, [("ka er", "KR")])
    assert len(out) == 1 and out[0]["word"].startswith("KR") and out[0]["word"].endswith(",")
    assert out[0]["start"] == 0.0 and out[0]["end"] == 0.4


def test_whisper_transcribe_block_uses_segment_fallback():
    eng = WhisperEngine(model_size="small", device="cpu", compute_type="int8", cpu_threads=2)
    seg = SimpleNamespace(text=" Bardzo dobra wiadomość od klienta", start=0.5, end=3.5, words=None)
    captured = {}

    class FakeWhisper:
        def transcribe(self, audio, **kw):
            captured.update(kw)
            return iter([seg]), None

    eng._model = FakeWhisper()
    words = eng.transcribe_block(_tone(4.0), beam_size=3)
    assert captured["beam_size"] == 3 and captured["vad_filter"] is False and captured["word_timestamps"] is False
    assert len(words) == 5
    assert words[0]["start"] == pytest.approx(0.5) and words[-1]["end"] == pytest.approx(3.5)


def test_rolling_global_time_and_cloud_format(monkeypatch):
    """Segment trafiający do CRM ma niezmieniony format, a czas słów jest globalny (początek bloku + czas lokalny)."""
    from recorder.core.session import format_words_to_turns
    eng = _parakeet([" Test", " jeden", " dwa"], "Test jeden dwa")
    local = eng.transcribe_block(_tone(3.0))
    block_start = 100.0
    words = [{**w, "start": w["start"] + block_start, "end": w["end"] + block_start} for w in local]
    _html, _plain, turns = format_words_to_turns(words)
    assert turns and all(isinstance(t["start"], float) and isinstance(t["end"], float) for t in turns)
    assert turns[0]["start"] >= block_start
    for key in ("id", "speaker", "text"):
        assert key in turns[0]


def test_torch_is_not_imported():
    code = (
        "import sys\n"
        "import recorder.config, recorder.core.asr_engine, recorder.core.parakeet_engine\n"
        "import recorder.core.transcriber, recorder.core.vad, recorder.core.blocks\n"
        "bad = [m for m in ('torch', 'torchaudio', 'pyannote', 'PyQt6') if m in sys.modules]\n"
        "sys.exit(1 if bad else 0)\n"
    )
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
