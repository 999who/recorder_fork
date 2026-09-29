import os
import sys
import threading
import numpy as np

# Silero VAD uruchamiany bezpośrednio przez onnxruntime (bez torch).
# Model: recorder/resources/vad/silero_vad.onnx (licencja MIT, patrz LICENSE-silero-vad.txt).
_silero_session = None
_silero_available = False
_silero_lock = threading.Lock()

_CONTEXT_SAMPLES = 64    # kontekst dołączany do każdego okna 512 próbek (wymóg modelu przy 16 kHz)
_WINDOW_SAMPLES = 512    # 32 ms @ 16 kHz


def _find_model_path() -> str:
    candidates = []
    meipass = getattr(sys, "_MEIPASS", "")
    if meipass:
        candidates.append(os.path.join(meipass, "recorder", "resources", "vad", "silero_vad.onnx"))
    candidates.append(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "resources", "vad", "silero_vad.onnx"))
    for c in candidates:
        if os.path.exists(c):
            return c
    return ""


try:
    import onnxruntime as _ort

    _model_path = _find_model_path()
    if not _model_path:
        raise FileNotFoundError("brak pliku recorder/resources/vad/silero_vad.onnx")
    _opts = _ort.SessionOptions()
    _opts.intra_op_num_threads = 1
    _opts.inter_op_num_threads = 1
    _silero_session = _ort.InferenceSession(_model_path, sess_options=_opts, providers=["CPUExecutionProvider"])
    _silero_available = True
    print("Sukces: Model Silero VAD (ONNX) został pomyślnie załadowany do pamięci!")
except Exception as e:
    print(f"Informacja VAD: {e}")


def is_silero_available() -> bool:
    return _silero_available and _silero_session is not None


class SileroVADDetector:
    """
    Klasa odpowiedzialna za detekcję aktywności głosowej (Voice Activity Detection)
    z użyciem sieci neuronowej Silero VAD (ONNX). Obsługuje dowolne rozmiary próbek wejściowych
    dzięki wewnętrznemu buforowaniu do wymaganych okien (512 próbek / 32ms @ 16kHz).
    Każdy detektor ma własny stan sieci, więc kanał mikrofonu i systemu nie zakłócają się nawzajem.
    """
    def __init__(self, speech_threshold: float = 0.35, default_samplerate: int = 16000):
        self.speech_threshold = speech_threshold
        self.samplerate = default_samplerate
        self._buffer = np.array([], dtype=np.float32)
        self._last_speech_prob = 0.0
        self._state = np.zeros((2, 1, 128), dtype=np.float32)
        self._context = np.zeros((1, _CONTEXT_SAMPLES), dtype=np.float32)

    def reset(self):
        """Czyści wewnętrzny bufor próbek i stan sieci."""
        self._buffer = np.array([], dtype=np.float32)
        self._last_speech_prob = 0.0
        self._state = np.zeros((2, 1, 128), dtype=np.float32)
        self._context = np.zeros((1, _CONTEXT_SAMPLES), dtype=np.float32)

    def _infer_window(self, window: np.ndarray) -> float:
        """Jedno okno 512 próbek -> prawdopodobieństwo mowy (aktualizuje stan i kontekst detektora)."""
        frame = np.concatenate([self._context, window.reshape(1, -1)], axis=1)
        with _silero_lock:
            out, new_state = _silero_session.run(
                ["output", "stateN"],
                {"input": frame, "state": self._state, "sr": np.array(16000, dtype=np.int64)},
            )
        self._state = new_state
        self._context = frame[:, -_CONTEXT_SAMPLES:]
        return float(out[0][0])

    def process_chunk(self, audio_data: np.ndarray, samplerate: int = 16000, rms_level: float = 0.0):
        """
        Analizuje fragment audio i zwraca (is_speech, speech_prob).
        Bezpiecznie przetwarza pakiety o dowolnej długości (thread-safe).
        """
        if audio_data is None or len(audio_data) == 0:
            return (self._last_speech_prob >= self.speech_threshold), self._last_speech_prob

        flat_audio = audio_data.flatten().astype(np.float32)
        self._buffer = np.append(self._buffer, flat_audio)

        # Obliczenie RMS bieżącego fragmentu
        norm = float(np.linalg.norm(flat_audio))
        chunk_rms = (norm / np.sqrt(len(flat_audio))) if len(flat_audio) > 0 else 0.0

        if is_silero_available():
            while len(self._buffer) >= _WINDOW_SAMPLES:
                window = self._buffer[:_WINDOW_SAMPLES]
                self._buffer = self._buffer[_WINDOW_SAMPLES:]
                try:
                    self._last_speech_prob = self._infer_window(window)
                except Exception:
                    pass
        else:
            if len(self._buffer) > 2048:
                self._buffer = self._buffer[-512:]
            self._last_speech_prob = 1.0 if rms_level > 5.0 or chunk_rms > 0.02 else 0.0

        # Mowa wykryta gdy Silero VAD przekracza próg lub przy słyszalnym poziomie głośności
        is_speech = (self._last_speech_prob >= self.speech_threshold) or (rms_level > 4.0) or (chunk_rms > 0.01)
        return is_speech, self._last_speech_prob
