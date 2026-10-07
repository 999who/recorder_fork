"""Podpisy mówców zmieniane z paska źródeł na ekranie głównym (bez otwierania Ustawień)."""
from types import SimpleNamespace

import pytest


def _settings(monkeypatch, data):
    from recorder import config
    store = dict(data)
    monkeypatch.setattr(config, "load_user_settings", lambda force_reload=False: dict(store))
    return store


def test_mic_and_system_names_follow_settings(monkeypatch):
    from recorder.config import get_channel_speaker_name
    from recorder.core.rolling_transcriber import RollingTranscriptionWorker as W
    _settings(monkeypatch, {"mic_name": "Gleb", "system_name": "Klient"})
    assert get_channel_speaker_name("mic") == "Gleb"
    assert W._default_speaker_for_channel("mic") == "Gleb"
    assert W._default_speaker_for_channel("system") == "Klient"


def test_empty_names_fall_back_to_defaults(monkeypatch):
    from recorder.config import get_channel_speaker_name
    _settings(monkeypatch, {"mic_name": "  ", "system_name": ""})
    assert get_channel_speaker_name("mic") == "Mikrofon"
    assert get_channel_speaker_name("system") == "Dźwięk Systemu"


def test_popover_emits_trimmed_names(qapp):
    from recorder.ui.widgets import SpeakerNamesPopover
    pop = SpeakerNamesPopover([
        ("mic", "Mikrofon", "Mikrofon", "Mikrofon"),
        ("system", "Dźwięk systemu", "Dźwięk Systemu", "Klient"),
    ])
    assert pop.edit_for("mic").text() == ""                 # nazwa domyślna = puste pole z podpowiedzią
    assert pop.edit_for("mic").placeholderText() == "Mikrofon"
    assert pop.edit_for("system").text() == "Klient"
    got = []
    pop.names_saved.connect(got.append)
    pop.edit_for("mic").setText("  Gleb   Shylovich ")
    pop.edit_for("mic").returnPressed.emit()
    assert got == [{"mic": "Gleb Shylovich", "system": "Klient"}]


def test_relabel_live_turns_only_touches_old_default_name_of_that_channel():
    from recorder.ui.window import SmartDictaphoneWindow
    turns = [
        {"channel": "mic", "speaker": "Mikrofon", "text": "a"},
        {"channel": "mic", "speaker": "Anna", "text": "b"},           # nazwa nadana ręcznie zostaje
        {"channel": "system", "speaker": "Mikrofon", "text": "c"},    # inny kanał zostaje
        {"speaker": "Mikrofon", "text": "d"},                         # brak kanału = mikrofon
    ]
    fake = SimpleNamespace(rolling_worker=None, current_turns=turns)
    SmartDictaphoneWindow._relabel_live_turns(fake, {"mic": ("Mikrofon", "Gleb")})
    assert [t["speaker"] for t in turns] == ["Gleb", "Anna", "Mikrofon", "Gleb"]
