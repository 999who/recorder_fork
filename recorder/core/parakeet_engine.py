"""
Silnik NVIDIA Parakeet TDT 0.6B v3 uruchamiany przez onnx-asr (onnxruntime, CPU, int8).

Koszt inferencji rośnie proporcjonalnie do długości dźwięku (brak stałego okna 30 s jak w Whisperze),
więc krótkie bloki na żywo są tanie. Model nie ma odpowiednika initial_prompt - słownik terminów
działa jako tabela autokorekt po rozpoznaniu (patrz recorder.core.replacements).
"""
import os
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np
import soundfile as sf

from recorder.audio.converter import preprocess_speech_audio, highpass_filter_audio, normalize_audio
from recorder.core.langfilter import is_foreign_language
from recorder.core.asr_engine import AsrEngine, ENGINE_PARAKEET, StatusCallback, Word
from recorder.core.transcriber import clean_repeated_text, is_hallucination, filter_repeated_words_list

PARAKEET_MODEL_NAME = "nemo-parakeet-tdt-0.6b-v3"
PARAKEET_QUANTIZATION = "int8"
SAMPLE_RATE = 16000
MIN_AUDIO_SEC = 0.4          # krótsze fragmenty to szum
MAX_CHUNK_SEC = 20.0         # bezpieczny limit długości jednego wywołania modelu (dokumentacja: 20-30 s)
TOKEN_STEP_SEC = 0.08        # ostatni token nie ma następnika - przyjmujemy jedną ramkę TDT


def tokens_to_words(tokens: List[str], timestamps: List[float], audio_dur_sec: float) -> List[Word]:
    """
    Składa słowa z tokenów SentencePiece zwróconych przez onnx-asr.
    Token zaczynający się od spacji rozpoczyna nowe słowo, pozostałe (np. przecinek, końcówka)
    dopisują się do bieżącego. Koniec słowa = początek następnego słowa (lub ostatni token + jedna ramka).
    """
    if not tokens or timestamps is None or len(tokens) != len(timestamps):
        return []

    groups: List[List[Tuple[str, float]]] = []
    for tok, ts in zip(tokens, timestamps):
        if not tok:
            continue
        if tok.startswith(" ") or not groups:
            groups.append([(tok, float(ts))])
        else:
            groups[-1].append((tok, float(ts)))

    words: List[Word] = []
    for i, grp in enumerate(groups):
        text = "".join(t for t, _ in grp).strip()
        if not text:
            continue
        start = grp[0][1]
        if i + 1 < len(groups):
            end = groups[i + 1][0][1]
        else:
            end = grp[-1][1] + TOKEN_STEP_SEC
        end = min(max(end, start + 0.02), max(audio_dur_sec, start + 0.02))
        words.append({
            "word": (" " + text) if words else text,
            "start": round(start, 3),
            "end": round(end, 3),
            "probability": 1.0,
        })
    return words


def split_at_quiet_points(audio: np.ndarray, max_sec: float = MAX_CHUNK_SEC,
                          search_sec: float = 4.0) -> List[Tuple[int, int]]:
    """
    Dzieli długie nagranie na fragmenty <= max_sec, tnąc w najcichszym miejscu ostatnich search_sec
    każdego okna (okno RMS 50 ms). Zwraca listę (start_sample, end_sample).
    """
    n = len(audio)
    max_len = int(max_sec * SAMPLE_RATE)
    if n <= max_len:
        return [(0, n)]

    hop = int(0.05 * SAMPLE_RATE)
    spans: List[Tuple[int, int]] = []
    start = 0
    while n - start > max_len:
        lo = start + max_len - int(search_sec * SAMPLE_RATE)
        hi = start + max_len
        seg = audio[lo:hi]
        frames = len(seg) // hop
        if frames > 0:
            rms = np.sqrt(np.mean(seg[:frames * hop].reshape(frames, hop) ** 2, axis=1))
            cut = lo + int(np.argmin(rms)) * hop + hop // 2
        else:
            cut = hi
        spans.append((start, cut))
        start = cut
    spans.append((start, n))
    return spans


class ParakeetEngine(AsrEngine):
    """Silnik ASR Parakeet TDT 0.6B v3 (onnx-asr, tylko CPU, kwantyzacja int8)."""

    engine_id = ENGINE_PARAKEET

    def __init__(self, threads: int = 3, model_path: str = "", replacements: Optional[List[Tuple[str, str]]] = None,
                 polish_only: Optional[bool] = None):
        self.threads = max(1, int(threads))
        self.model_path = (model_path or "").strip()
        self.replacements = replacements
        self.polish_only = polish_only
        self._model = None

    @property
    def is_loaded(self) -> bool:
        return self._model is not None

    @property
    def display_name(self) -> str:
        return "Parakeet TDT 0.6B v3"

    def load_model(self, status_cb: Optional[StatusCallback] = None):
        """Ładuje model. Bez lokalnej ścieżki onnx-asr pobiera go z HuggingFace przy pierwszym uruchomieniu."""
        if self._model is not None:
            return self._model

        try:
            import onnxruntime as rt
            import onnx_asr
        except ImportError as e:
            raise RuntimeError(f"Brak biblioteki onnx-asr / onnxruntime: {e}") from e

        path = None
        if self.model_path:
            if not os.path.isdir(self.model_path):
                raise RuntimeError(f"Nie znaleziono folderu z modelem Parakeet: {self.model_path}")
            path = self.model_path
            if status_cb:
                status_cb("Ładowanie modelu Parakeet z lokalnego folderu...")
        elif status_cb:
            status_cb("Ładowanie modelu Parakeet (przy pierwszym uruchomieniu zostanie pobrany z internetu)...")

        opts = rt.SessionOptions()
        opts.intra_op_num_threads = self.threads
        opts.inter_op_num_threads = 1

        print(f"[PARAKEET] Ładowanie modelu (int8, CPU, wątki: {self.threads})...")
        self._model = onnx_asr.load_model(
            PARAKEET_MODEL_NAME,
            path,
            quantization=PARAKEET_QUANTIZATION,
            sess_options=opts,
            providers=["CPUExecutionProvider"],
        ).with_timestamps()
        print("[PARAKEET] Model załadowany.")
        if status_cb:
            status_cb("Parakeet: model gotowy")
        return self._model

    def _recognize(self, audio: np.ndarray) -> Tuple[str, List[Word]]:
        """Jedno wywołanie modelu na fragmencie <= MAX_CHUNK_SEC. Zwraca (tekst, słowa w czasie lokalnym)."""
        res = self._model.recognize(np.ascontiguousarray(audio, dtype=np.float32), sample_rate=SAMPLE_RATE)
        text = (res.text or "").strip()
        words = tokens_to_words(res.tokens or [], res.timestamps, len(audio) / SAMPLE_RATE)
        return text, words

    def _polish_only(self) -> bool:
        """Czy odrzucać bloki rozpoznane jako angielskie (ustawienie; można je wyłączyć w ustawieniach)."""
        if self.polish_only is not None:
            return bool(self.polish_only)
        from recorder.config import is_polish_only_filter
        return is_polish_only_filter()

    def _postprocess(self, text: str, words: List[Word]) -> List[Word]:
        """Filtry anty-halucynacyjne, deduplikacja powtórzeń i autokorekty słownika."""
        cleaned = clean_repeated_text(text)
        if not words or not cleaned or is_hallucination(text, cleaned):
            return []
        if self._polish_only() and is_foreign_language(cleaned):
            return []
        words = filter_repeated_words_list(words, max_consecutive=2)
        from recorder.core.replacements import apply_word_replacements
        return apply_word_replacements(words, self.replacements)

    def transcribe_block(self, audio_float: np.ndarray, beam_size: Optional[int] = None) -> List[Word]:
        """beam_size jest ignorowany (dekodowanie zachłanne TDT) - parametr istnieje dla zgodności z interfejsem."""
        if self._model is None:
            self.load_model()
        if len(audio_float) < int(MIN_AUDIO_SEC * SAMPLE_RATE):
            return []

        audio = normalize_audio(highpass_filter_audio(audio_float, sr=SAMPLE_RATE, cutoff_hz=80.0), target_peak=0.92)

        text_parts: List[str] = []
        words: List[Word] = []
        for lo, hi in split_at_quiet_points(audio):
            part_text, part_words = self._recognize(audio[lo:hi])
            offset = lo / SAMPLE_RATE
            text_parts.append(part_text)
            for w in part_words:
                words.append({**w, "start": round(w["start"] + offset, 3), "end": round(w["end"] + offset, 3)})
        return self._postprocess(" ".join(text_parts), words)

    def transcribe_live_chunk(self, audio_float: np.ndarray, language: str = "pl",
                              context_prompt: str = "", beam_size: Optional[int] = None) -> str:
        """Parakeet nie używa kontekstu ani języka (v3 wykrywa język sam) - parametry dla zgodności."""
        words = self.transcribe_block(audio_float)
        return "".join(w["word"] for w in words).strip()

    def transcribe_file_with_words(self, audio_path: str, language: str = "pl",
                                   progress_callback: Optional[Callable[[float, float], None]] = None,
                                   duration_sec: float = 0.0,
                                   beam_size: Optional[int] = None) -> List[Dict[str, Any]]:
        """Transkrybuje cały plik fragmentami <= 20 s (cięcie w najcichszych miejscach)."""
        if self._model is None:
            self.load_model()

        audio_arr, sr = sf.read(audio_path, dtype="float32")
        audio = preprocess_speech_audio(audio_arr, orig_sr=sr)
        total = duration_sec if duration_sec > 0 else len(audio) / SAMPLE_RATE

        spans = split_at_quiet_points(audio)
        print(f"[PARAKEET] Transkrypcja pliku: {os.path.basename(audio_path)} ({total:.0f}s, {len(spans)} fragmentów)")
        result: List[Dict[str, Any]] = []
        for lo, hi in spans:
            chunk = audio[lo:hi]
            offset = lo / SAMPLE_RATE
            if len(chunk) >= int(MIN_AUDIO_SEC * SAMPLE_RATE):
                text, words = self._recognize(chunk)
                for w in self._postprocess(text, words):
                    result.append({
                        "word": w["word"],
                        "start": round(w["start"] + offset, 3),
                        "end": round(w["end"] + offset, 3),
                    })
            if progress_callback:
                cur = hi / SAMPLE_RATE
                progress_callback(min(1.0, cur / total) if total > 0 else 1.0, cur)
        print(f"[PARAKEET] Transkrypcja zakończona. Rozpoznano {len(result)} słów.")
        return result
