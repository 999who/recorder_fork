import os
import sys
import tempfile

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from recorder.core.session import (
    TranscriptionSession,
    get_session_path_for_txt,
)


def test_session_lifecycle():
    with tempfile.TemporaryDirectory() as tmp_dir:
        json_path = os.path.join(tmp_dir, "transkrypcja_20260820_120000.json")
        txt_path = os.path.join(tmp_dir, "transkrypcja_20260820_120000.txt")
        audio_path = os.path.join(tmp_dir, "inteligentne_nagranie_20260820_120000.wav")

        session = TranscriptionSession(
            source_audio=audio_path,
            prepared_wav=audio_path,
            duration_sec=125.5,
            whisper_model="parakeet",
            has_transcription=True,
            words=[
                {"word": "Cześć", "start": 0.0, "end": 0.5, "probability": 0.98},
                {"word": "wszystkim", "start": 0.6, "end": 1.1, "probability": 0.95}
            ],
            turns=[
                {"start": 0.0, "end": 1.1, "speaker": "Mikrofon", "text": "Cześć wszystkim"}
            ]
        )
        assert session.get_status_badge() == "[📝 Transkrypcja]"
        assert session.save_to_json(json_path) is True

        loaded = TranscriptionSession.load_from_json(json_path)
        assert loaded is not None
        assert loaded.has_transcription is True
        assert loaded.whisper_model == "parakeet"
        assert len(loaded.words) == 2
        assert "Mikrofon: Cześć wszystkim" in loaded.export_to_plain_text()

        assert get_session_path_for_txt(txt_path) == json_path


def test_old_session_json_with_legacy_status_field_still_loads():
    """Stare pliki sesji zawierają pole status.has_diarization - musi być ignorowane bez błędu."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        json_path = os.path.join(tmp_dir, "stara_sesja.json")
        with open(json_path, "w", encoding="utf-8") as f:
            f.write('{"version": 1, "status": {"has_transcription": true, "has_diarization": true}, '
                    '"turns": [{"start": 0.0, "end": 1.0, "speaker": "Ania", "text": "Test"}]}')
        loaded = TranscriptionSession.load_from_json(json_path)
        assert loaded is not None and loaded.has_transcription is True
