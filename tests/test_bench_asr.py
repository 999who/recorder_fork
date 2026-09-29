"""Test skryptu scripts/bench_asr.py na atrapie silnika (bez modeli)."""
import json
import os
import sys

import numpy as np
import pytest
import soundfile as sf

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import bench_asr  # noqa: E402
from recorder.core.blocks import BlockSegmenter  # noqa: E402
from recorder.config import BlockProfile, get_block_profile  # noqa: E402


class StubVad:
    """Mowa w oknach oznaczonych maską: True dla pierwszych 'speech_sec' każdej sekundy niezerowej."""

    def process_chunk(self, chunk, samplerate=16000, rms_level=0.0):
        return bool(np.max(np.abs(chunk)) > 0.01), 1.0


def _speech_with_gap(sec_speech=20.0, gap=3.0):
    sr = 16000
    t = np.arange(int(sec_speech * sr)) / sr
    a = (0.3 * np.sin(2 * np.pi * 200 * t)).astype(np.float32)
    return np.concatenate([a[: 10 * sr], np.zeros(int(gap * sr), np.float32), a[10 * sr:]])


def test_segmenter_respects_profile_limits():
    prof = get_block_profile("parakeet")
    seg = BlockSegmenter(prof, StubVad())
    audio = _speech_with_gap()
    blocks = []
    for i in range(0, len(audio), 512):
        blocks.extend(seg.feed(audio[i:i + 512]))
    blocks.extend(seg.flush())
    assert blocks
    assert all(len(a) / 16000 <= prof.max_sec + 0.1 for _, _, a in blocks)
    # bloki nie cofają się w czasie poza nakładką
    assert all(b[0] <= b[1] for b in blocks)


def test_bench_runs_with_mock_engine(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(bench_asr, "segment_audio", lambda audio, eng, detector=None: _segment_with_stub(audio, eng))
    wav = tmp_path / "t.wav"
    sf.write(str(wav), _speech_with_gap(), 16000)
    audio = bench_asr.load_wav_16k(str(wav))
    res = bench_asr.run_engine("mock", audio, threads=2)
    s = res["summary"]
    assert s["blocks"] == len(res["blocks"]) >= 2
    assert s["rtf"] is not None and s["peak_rss_mb"] > 0
    assert all(r["text"] for r in res["blocks"])
    bench_asr.print_report([res], str(wav), len(audio) / 16000, 2)
    out = capsys.readouterr().out
    assert "RTF" in out and "ROZPOZNANY TEKST" in out
    json.dumps(res)


def _segment_with_stub(audio, engine):
    seg = BlockSegmenter(get_block_profile(engine), StubVad())
    blocks = []
    for i in range(0, len(audio), bench_asr.CHUNK_SAMPLES):
        blocks.extend(seg.feed(audio[i:i + bench_asr.CHUNK_SAMPLES]))
    blocks.extend(seg.flush())
    return blocks
