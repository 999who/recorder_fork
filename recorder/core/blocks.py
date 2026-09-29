"""
Reguła cięcia bloków mowy na żywo (wspólna dla kanału mikrofonu i dźwięku systemu).
"""
from typing import Any, Dict, List, Tuple

from recorder.config import BlockProfile
from recorder.core.transcriber import filter_repeated_words_list


def should_cut_block(cur_dur: float, sil_dur: float, auto_paused: bool, profile: BlockProfile) -> Tuple[bool, bool]:
    """
    Decyduje, czy zamknąć bieżący blok. Zwraca (czy_ciąć, czy_wymuszone).
    Wymuszone cięcie (bez pauzy w mowie) może rozciąć słowo, dlatego następny blok dostaje nakładkę audio.
    """
    if cur_dur < profile.min_emit_sec:
        return False, False
    natural = (
        (cur_dur >= profile.min_sec and sil_dur >= profile.silence_sec)
        or (cur_dur >= profile.long_sec and sil_dur >= profile.long_silence_sec)
        or (cur_dur >= profile.pause_flush_sec and auto_paused)
    )
    if natural:
        return True, False
    if cur_dur >= profile.max_sec:
        return True, True
    return False, False


class OverlapDeduper:
    """
    Usuwa dubel słów na styku bloków. Wymuszone cięcie w środku mowy kopiuje ogon audio (nakładkę)
    do początku następnego bloku, więc te same słowa mogą zostać rozpoznane dwa razy. Nakładkę rozpoznajemy
    po tym, że blok zaczyna się przed końcem poprzedniego bloku tego samego kanału. Powtórzenia na styku
    usuwa istniejący filtr powtórzeń (filter_repeated_words_list) działający na ogonie poprzedniego bloku
    i głowie nowego. Stan jest osobny dla każdego kanału (mikrofon / system).
    """
    HEAD_WORDS = 6      # ile pierwszych słów nowego bloku sprawdzamy na styku
    TAIL_WORDS = 6      # ile ostatnich słów poprzedniego bloku pamiętamy

    def __init__(self):
        self._last_block_end: Dict[str, float] = {}
        self._tail: Dict[str, List[Dict[str, Any]]] = {}

    def reset(self):
        self._last_block_end = {}
        self._tail = {}

    def process(self, channel: str, start_sec: float, end_sec: float, words: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Zwraca słowa bloku (czas globalny) bez powtórzeń z nakładki i zapamiętuje ich ogon."""
        prev_end = self._last_block_end.get(channel)
        tail = self._tail.get(channel, [])
        self._last_block_end[channel] = max(prev_end or 0.0, float(end_sec))

        if words and tail and prev_end is not None and start_sec < prev_end - 0.01:
            junction_tail = [w for w in tail if w["end"] > start_sec - 0.2]
            if junction_tail:
                head, rest = words[:self.HEAD_WORDS], words[self.HEAD_WORDS:]
                kept = {id(w) for w in filter_repeated_words_list(junction_tail + head, max_consecutive=1)}
                words = [w for w in head if id(w) in kept] + rest

        if words:
            self._tail[channel] = (tail + words)[-self.TAIL_WORDS:]
        return words


class BlockSegmenter:
    """
    Offline'owa wersja logiki cięcia bloków z SmartAudioWorker (jeden kanał): VAD, pre-roll, pauza
    automatyczna, reguła should_cut_block i nakładka przy wymuszonym cięciu. Służy do odtwarzania
    trybu na żywo na gotowym pliku (scripts/bench_asr.py). Czas bloków liczony jest tak jak w trybie
    na żywo: w sekundach zarejestrowanego audio (bez pominiętych pauz).
    """
    SAMPLE_RATE = 16000

    def __init__(self, profile: BlockProfile, detector, auto_pause_sec: float = 5.0, pre_speech_chunks: int = 15):
        import collections
        import numpy as np
        self._np = np
        self.profile = profile
        self.detector = detector
        self.auto_pause_sec = auto_pause_sec
        self.overlap_samples = int(profile.overlap_sec * self.SAMPLE_RATE)
        self._pre_roll = collections.deque(maxlen=pre_speech_chunks)
        self._chunks: list = []
        self._block_silence = 0          # cisza od ostatniej mowy (próbki), zerowana po cięciu
        self._continuous_silence = 0     # ciągła cisza do wykrycia automatycznej pauzy
        self._paused = False
        self._recorded = 0               # próbki zarejestrowanego audio (oś czasu bloków)

    def _emit(self):
        np = self._np
        arr = np.concatenate(self._chunks)
        dur = len(arr) / self.SAMPLE_RATE
        end_sec = round(self._recorded / self.SAMPLE_RATE, 2)
        start_sec = max(0.0, round(end_sec - dur, 2))
        return start_sec, end_sec, arr

    def feed(self, chunk):
        """Przetwarza kawałek audio (float32, 16 kHz). Zwraca listę bloków (start_sec, end_sec, audio)."""
        np = self._np
        blocks = []
        rms = float(np.linalg.norm(chunk) / np.sqrt(len(chunk))) if len(chunk) else 0.0
        level = min(100.0, max(0.0, (rms ** 0.65) * 180.0))
        is_speech, _ = self.detector.process_chunk(chunk, samplerate=self.SAMPLE_RATE, rms_level=level)

        if is_speech:
            if self._paused:
                self._paused = False
                while self._pre_roll:
                    pre = self._pre_roll.popleft()
                    self._chunks.append(pre)
                    self._recorded += len(pre)
            self._block_silence = 0
            self._continuous_silence = 0
        else:
            self._block_silence += len(chunk)
            self._continuous_silence += len(chunk)
            self._pre_roll.append(chunk.copy())
            if not self._paused and self._continuous_silence / self.SAMPLE_RATE >= self.auto_pause_sec:
                self._paused = True

        if not self._paused:
            self._chunks.append(chunk.copy())
            self._recorded += len(chunk)

        if self._chunks:
            cur_dur = sum(len(c) for c in self._chunks) / self.SAMPLE_RATE
            sil_dur = self._block_silence / self.SAMPLE_RATE
            cut, forced = should_cut_block(cur_dur, sil_dur, self._paused, self.profile)
            if cut:
                start_sec, end_sec, arr = self._emit()
                blocks.append((start_sec, end_sec, arr))
                self._chunks = [arr[-self.overlap_samples:].copy()] if (forced and self.overlap_samples > 0) else []
                self._block_silence = 0
        return blocks

    def flush(self):
        """Zwraca ostatni, niezamknięty blok (jeśli ma co najmniej 0.5 s)."""
        if not self._chunks:
            return []
        start_sec, end_sec, arr = self._emit()
        self._chunks = []
        if len(arr) / self.SAMPLE_RATE < 0.5:
            return []
        return [(start_sec, end_sec, arr)]
