"""Testy reguły cięcia bloków (profile silników), nakładki i deduplikacji na styku bloków."""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from recorder.config import get_block_profile
from recorder.core.blocks import should_cut_block
from recorder.core.rolling_transcriber import RollingBlock, RollingTranscriptionWorker


def test_profiles_differ_per_engine():
    pk = get_block_profile("parakeet")
    wh = get_block_profile("whisper")
    assert 3.0 <= pk.min_sec and pk.max_sec <= 8.0
    assert wh.min_sec >= 20.0 and wh.max_sec <= 28.0 and wh.silence_sec == 0.4


def test_natural_cut_on_pause_is_not_forced():
    pk = get_block_profile("parakeet")
    assert should_cut_block(3.5, 0.4, False, pk) == (True, False)
    assert should_cut_block(3.5, 0.1, False, pk) == (False, False)


def test_forced_cut_at_max_duration():
    pk = get_block_profile("parakeet")
    assert should_cut_block(pk.max_sec + 0.1, 0.0, False, pk) == (True, True)


def test_whisper_does_not_cut_short_blocks():
    wh = get_block_profile("whisper")
    assert should_cut_block(6.0, 1.0, False, wh) == (False, False)
    assert should_cut_block(21.0, 0.5, False, wh) == (True, False)


def test_auto_pause_flushes_and_min_emit_guard():
    pk = get_block_profile("parakeet")
    assert should_cut_block(2.0, 0.0, True, pk) == (True, False)
    assert should_cut_block(1.0, 5.0, True, pk) == (False, False)


def _w(word, st, en):
    return {"word": word, "start": st, "end": en, "probability": 1.0}


def test_overlap_duplicate_word_removed_at_junction():
    worker = RollingTranscriptionWorker()
    b1 = RollingBlock(1, 0.0, 8.0, None)
    w1 = worker._dedupe_overlap(b1, [_w("Spotkanie", 6.0, 6.5), _w(" jest", 7.0, 7.4)])
    assert len(w1) == 2
    # Następny blok zaczyna się 0.5 s przed końcem poprzedniego i rozpoznaje " jest" ponownie
    b2 = RollingBlock(2, 7.5, 14.0, None)
    w2 = worker._dedupe_overlap(b2, [_w(" jest", 7.5, 7.9), _w(" jutro", 8.0, 8.4)])
    assert [w["word"].strip() for w in w2] == ["jutro"]


def test_no_dedupe_without_overlap():
    worker = RollingTranscriptionWorker()
    worker._dedupe_overlap(RollingBlock(1, 0.0, 5.0, None), [_w("tak", 3.0, 3.4), _w(" jest", 4.0, 4.4)])
    w2 = worker._dedupe_overlap(RollingBlock(2, 6.0, 9.0, None), [_w(" jest", 6.2, 6.5), _w(" ok", 6.6, 6.9)])
    assert [w["word"].strip() for w in w2] == ["jest", "ok"]


def test_channels_do_not_interfere():
    worker = RollingTranscriptionWorker()
    worker._dedupe_overlap(RollingBlock(1, 0.0, 8.0, None, "mic"), [_w("dobrze", 7.0, 7.5)])
    w = worker._dedupe_overlap(RollingBlock(2, 7.5, 12.0, None, "system"), [_w("dobrze", 7.6, 8.0)])
    assert len(w) == 1
