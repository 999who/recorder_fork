"""
Wspólny interfejs silników rozpoznawania mowy (Whisper, Parakeet).

Kontrakt jest celowo wąski: RollingTranscriptionWorker, LiveTranscriptionWorker oraz
workery plikowe rozmawiają wyłącznie z tym interfejsem i nie znają szczegółów modelu.
"""
from abc import ABC, abstractmethod
from typing import Any, Callable, Dict, List, Optional

import numpy as np

# Identyfikatory silników zapisywane w user_settings.json
ENGINE_WHISPER = "whisper"
ENGINE_PARAKEET = "parakeet"
VALID_ENGINES = (ENGINE_WHISPER, ENGINE_PARAKEET)

# Słowo w lokalnym czasie bloku: {"word", "start", "end", "probability"}
Word = Dict[str, Any]
StatusCallback = Callable[[str], None]


class AsrEngine(ABC):
    """Abstrakcyjny silnik ASR pracujący na tablicach float32 mono 16 kHz."""

    engine_id: str = ""

    @property
    @abstractmethod
    def is_loaded(self) -> bool:
        """Czy model jest już w pamięci."""

    @property
    def display_name(self) -> str:
        """Nazwa silnika do komunikatów w interfejsie."""
        return self.engine_id

    @abstractmethod
    def load_model(self, status_cb: Optional[StatusCallback] = None):
        """Ładuje (i w razie potrzeby pobiera) model. status_cb dostaje krótkie komunikaty po polsku."""

    @abstractmethod
    def transcribe_block(self, audio_float: np.ndarray, beam_size: Optional[int] = None) -> List[Word]:
        """
        Transkrybuje blok mowy. Zwraca słowa z czasem lokalnym (sekundy od początku bloku),
        po filtrach anty-halucynacyjnych i deduplikacji powtórzeń.
        """

    @abstractmethod
    def transcribe_live_chunk(self, audio_float: np.ndarray, language: str = "pl",
                              context_prompt: str = "", beam_size: Optional[int] = None) -> str:
        """Transkrybuje krótką frazę i zwraca oczyszczony tekst (pusty gdy halucynacja)."""

    @abstractmethod
    def transcribe_file_with_words(self, audio_path: str, language: str = "pl",
                                   progress_callback: Optional[Callable[[float, float], None]] = None,
                                   duration_sec: float = 0.0,
                                   beam_size: Optional[int] = None) -> List[Dict[str, Any]]:
        """Transkrybuje cały plik i zwraca słowa ze znacznikami czasu."""


def normalize_engine_id(engine_id: Optional[str]) -> str:
    """Zwraca poprawny identyfikator silnika (nieznane wartości -> Whisper)."""
    eid = str(engine_id or "").strip().lower()
    return eid if eid in VALID_ENGINES else ENGINE_WHISPER


def create_asr_engine(engine_id: Optional[str] = None, model_size: Optional[str] = None) -> AsrEngine:
    """
    Fabryka silników. model_size to identyfikator pozycji z listy modeli w interfejsie:
    'parakeet' wybiera Parakeet, każda inna wartość to rozmiar modelu Whisper.
    Bez argumentów decyduje ustawienie asr_engine (domyślnie Parakeet bez CUDA).
    Importy są leniwe, aby nie ładować ciężkich bibliotek bez potrzeby.
    """
    if model_size == ENGINE_PARAKEET:
        engine_id, model_size = ENGINE_PARAKEET, None
    if engine_id is None:
        from recorder.config import get_asr_engine
        engine_id = get_asr_engine()
    engine_id = normalize_engine_id(engine_id)
    from recorder.flavor import WHISPER_ENABLED
    if not WHISPER_ENABLED:
        engine_id = ENGINE_PARAKEET
    if engine_id == ENGINE_PARAKEET:
        from recorder.config import get_onnx_threads, get_parakeet_model_path
        from recorder.core.parakeet_engine import ParakeetEngine
        return ParakeetEngine(threads=get_onnx_threads(), model_path=get_parakeet_model_path())
    from recorder.core.transcriber import WhisperEngine
    if model_size:
        return WhisperEngine(model_size=model_size)
    return WhisperEngine()
