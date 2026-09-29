"""
Reguła cięcia bloków mowy na żywo (wspólna dla kanału mikrofonu i dźwięku systemu).
"""
from typing import Tuple

from recorder.config import BlockProfile


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
