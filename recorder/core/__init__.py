"""
Moduł Core AI - Czyste przetwarzanie modeli VAD oraz silników rozpoznawania mowy (Parakeet, Whisper) bez zależności od GUI.
"""

from .vad import SileroVADDetector, is_silero_available
from .transcriber import TranscriberEngine
from .asr_engine import AsrEngine, create_asr_engine

__all__ = [
    "SileroVADDetector",
    "is_silero_available",
    "TranscriberEngine",
    "AsrEngine",
    "create_asr_engine",
]
