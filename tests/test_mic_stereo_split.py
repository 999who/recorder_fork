"""Mikrofon wielokanałowy (np. Hollyland Lark): scalanie nazw MME, nazwy i kolory kanałów 1-4."""
import pytest

from recorder.audio.devices import _merge_truncated_name_groups
from recorder.config import THEME_SPEAKER_COLORS, get_mic_channel_names
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


@pytest.mark.parametrize("channel,css", [("mic1", "sm1"), ("mic2", "sm2"), ("mic3", "sm3"), ("mic4", "sm4"),
                                         ("mic", "sm"), ("system", "ss")])
def test_each_lane_has_own_css_class(channel, css):
    assert speaker_channel_class("Anna", channel) == css


@pytest.mark.parametrize("theme", sorted(THEME_SPEAKER_COLORS))
def test_every_theme_defines_four_distinct_lane_colors_and_system(theme):
    colors = THEME_SPEAKER_COLORS[theme]
    lane = [colors[f"mic{i}"] for i in range(1, 5)]
    assert len(set(lane)) == 4
    assert colors["system"] not in lane


def test_dark_theme_colors_follow_the_spec():
    c = THEME_SPEAKER_COLORS["classic_dark"]
    assert c["mic1"].lower() in ("#ff5c5c",)          # czerwony
    assert c["mic2"].lower() == "#e7a93f"             # żółty (dawny kolor systemu)
    assert c["mic3"].lower() == "#3fc9b4"             # miętowy (dawny kolor mikrofonu)
    assert c["mic4"].lower() == "#a78bfa"             # fioletowy
    assert c["system"].lower() == "#f1f5f9"           # biały


def test_channel_names_follow_settings(monkeypatch):
    from recorder import config
    from recorder.core.rolling_transcriber import RollingTranscriptionWorker as W
    monkeypatch.setattr(config, "load_user_settings", lambda force_reload=False: {
        "mic_name_1": "Anna", "mic_name_2": "Piotr", "mic_name_3": "", "mic_name_4": "Ewa"})
    assert get_mic_channel_names() == ["Anna", "Piotr", "Osoba 3", "Ewa"]
    assert W._default_speaker_for_channel("mic1") == "Anna"
    assert W._default_speaker_for_channel("mic2") == "Piotr"
    assert W._default_speaker_for_channel("mic3") == "Osoba 3"
    assert W._default_speaker_for_channel("mic4") == "Ewa"
    assert W._default_speaker_for_channel("mic") == "Mikrofon"
    assert W._default_speaker_for_channel("system") == "Dźwięk Systemu"


@pytest.fixture
def tmp_settings(tmp_path, monkeypatch):
    from recorder import config
    monkeypatch.setattr(config, "SETTINGS_FILE", str(tmp_path / "user_settings.json"))
    monkeypatch.setattr(config, "_CACHED_USER_SETTINGS", None)
    monkeypatch.setattr(config, "_CACHED_SETTINGS_TIME", 0.0)
    yield
    monkeypatch.setattr(config, "_CACHED_USER_SETTINGS", None)
    monkeypatch.setattr(config, "_CACHED_SETTINGS_TIME", 0.0)


def test_settings_dialog_has_four_name_fields(qapp, tmp_settings):
    from recorder import config
    from recorder.ui.settings_dialog import SettingsDialog
    config.save_user_settings({"mic_stereo_split": True, "mic_name_1": "Anna", "mic_name_3": "Ewa"})
    dlg = SettingsDialog()
    assert dlg.chk_mic_stereo.isChecked()
    assert [e.text() for e in dlg.edit_mic_names] == ["Anna", "Osoba 2", "Ewa", "Osoba 4"]
    dlg.deleteLater()


def test_settings_dialog_toggle_enables_name_fields(qapp, tmp_settings):
    from recorder.ui.settings_dialog import SettingsDialog
    dlg = SettingsDialog()
    assert len(dlg.edit_mic_names) == 4
    assert not any(e.isEnabled() for e in dlg.edit_mic_names)
    dlg.chk_mic_stereo.setChecked(True)
    assert all(e.isEnabled() for e in dlg.edit_mic_names)
    dlg.deleteLater()


def test_extra_lanes_emit_blocks_with_their_own_tags(qapp):
    """Syntetyczne audio w kanałach 2-4: każdy kanał tnie bloki niezależnie i dostaje tag mic2..mic4."""
    import numpy as np
    from recorder.config import SmartRecordState
    from recorder.ui.workers import SmartAudioWorker

    w = SmartAudioWorker()
    w.state = SmartRecordState.RECORDING_SPEECH
    got = []
    w.rolling_block_ready_signal.connect(lambda idx, st, en, arr, ch: got.append((ch, len(arr))))

    t = np.arange(1024) / 16000.0
    tone = (0.3 * np.sin(2 * np.pi * 220 * t)).astype(np.float32)
    for _ in range(int(10 * 16000 / 1024)):          # 10 s ciągłej mowy → wymuszone cięcie przy max_sec
        w._process_extra_lane(0, tone)               # kanał 2
        w._process_extra_lane(2, tone)               # kanał 4 (kanał 3 milczy)

    tags = {ch for ch, _ in got}
    assert tags == {"mic2", "mic4"}
    assert all(n > 16000 for _, n in got)
    assert w.current_extra_block_chunks[1] == []     # kanał 3 nic nie nagrał


def test_lane1_tag_depends_on_split(qapp):
    from recorder.ui.workers import SmartAudioWorker
    w = SmartAudioWorker()
    assert w._lane1_tag() == "mic"
    w._mic_lanes = 2
    assert w._lane1_tag() == "mic1"
