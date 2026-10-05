"""Stereo mikrofon (np. Hollyland Lark): scalanie nazw MME, nazwy kanałów i tag mic2."""
from recorder.audio.devices import _merge_truncated_name_groups
from recorder.core.session import speaker_channel_class


def _group(name, idx, default=False):
    return {"name": name, "variants": [{"index": idx}], "is_default": default}


def test_merge_truncated_mme_name_into_full_wasapi_name():
    groups = {
        "Mikrofon (2 - Wireless micropho": _group("Mikrofon (2 - Wireless micropho", 1),
        "Mikrofon (2 - Wireless microphone)": _group("Mikrofon (2 - Wireless microphone)", 7, default=True),
        "Inny mikrofon": _group("Inny mikrofon", 3),
    }
    _merge_truncated_name_groups(groups)
    assert set(groups) == {"Mikrofon (2 - Wireless microphone)", "Inny mikrofon"}
    merged = groups["Mikrofon (2 - Wireless microphone)"]
    assert sorted(v["index"] for v in merged["variants"]) == [1, 7]
    assert merged["is_default"] is True


def test_merge_keeps_unrelated_short_names():
    groups = {"Mikrofon": _group("Mikrofon", 1), "Mikrofon USB": _group("Mikrofon USB", 2)}
    _merge_truncated_name_groups(groups)
    assert len(groups) == 2


def test_mic2_channel_has_own_css_class():
    assert speaker_channel_class("Anna", "mic2") == "sm2"
    assert speaker_channel_class("Anna", "mic") == "sm"
    assert speaker_channel_class("x", "system") == "ss"


def test_channel_names_follow_settings(monkeypatch):
    from recorder import config
    from recorder.core.rolling_transcriber import RollingTranscriptionWorker as W
    monkeypatch.setattr(config, "load_user_settings",
                        lambda force_reload=False: {"mic_stereo_split": True, "mic_name_left": "Anna", "mic_name_right": "Piotr"})
    assert W._default_speaker_for_channel("mic") == "Anna"
    assert W._default_speaker_for_channel("mic2") == "Piotr"
    assert W._default_speaker_for_channel("system") == "Dźwięk Systemu"


def test_mic_keeps_old_label_when_split_off(monkeypatch):
    from recorder import config
    from recorder.core.rolling_transcriber import RollingTranscriptionWorker as W
    monkeypatch.setattr(config, "load_user_settings", lambda force_reload=False: {"mic_stereo_split": False})
    assert W._default_speaker_for_channel("mic") == "Mikrofon"
