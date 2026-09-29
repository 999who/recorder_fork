"""Filtr angielskich fragmentów w transkrypcji Parakeet (model v3 jest wielojęzyczny)."""
import pytest

from recorder.core.langfilter import is_foreign_language
from tests.test_asr_engine import _parakeet, _tone


@pytest.mark.parametrize("text", [
    "Yeah.", "Okay.", "Oh good.", "Well I got you.", "What the fuck? I like that.",
])
def test_english_blocks_are_dropped(text):
    assert is_foreign_language(text)


@pytest.mark.parametrize("text", [
    "Nie wątpię, że się tam bawili.",
    "Tylko też coś z tymi angielskimi zrobić.",
    "Lub firmy jak ci wygodne.",
    "Zwiększ liczbę spotkań sprzedażowych dzięki nowoczesnemu oprogramowaniu.",
    "Wyślij mail do klienta, okay?",       # mieszany: polskie słowa zostają
    "Sprawdź faktura z CRM",
    "",
])
def test_polish_and_mixed_blocks_are_kept(text):
    assert not is_foreign_language(text)


def test_parakeet_drops_english_block_when_enabled():
    eng = _parakeet([" Well", " I", " got", " you"], "Well I got you")
    eng.polish_only = True
    assert eng.transcribe_block(_tone(3.0)) == []
    eng.polish_only = False
    assert eng.transcribe_block(_tone(3.0))
