import wave
import sys
from typing import List


def save_wav_file(file_path: str, frames: List[bytes], channels: int = 1, samplerate: int = 16000) -> bool:
    """
    Zapisuje surowe ramki bajtów audio (16-bit PCM) do pliku w formacie WAV.
    """
    if not frames:
        return False
    try:
        with wave.open(file_path, 'wb') as wf:
            wf.setnchannels(channels)
            wf.setsampwidth(2)  # 16-bit PCM
            wf.setframerate(samplerate)
            wf.writeframes(b''.join(frames))
        return True
    except Exception as e:
        if sys.stderr:
            print(f"Błąd zapisu pliku WAV ({file_path}): {e}", file=sys.stderr)
        return False


class StreamingWavWriter:
    """
    Strumieniowy rejestrator WAV na dysku.
    Zapisuje ramki 16-bit PCM w partiach, dzięki czemu aplikacja nie kumuluje
    setek megabajtów audio w pamięci RAM podczas 8-godzinnych sesji nagraniowych.

    append=True dopisuje do istniejącego pliku (jedno nagranie na dzień: Stop → Start kontynuuje ten sam plik),
    o ile ma te same parametry (kanały, częstotliwość, 16 bit). W przeciwnym razie plik jest tworzony od nowa,
    więc wywołujący powinien sprawdzić zgodność wcześniej (wav_params()).
    """
    def __init__(self, file_path: str, channels: int = 1, samplerate: int = 16000, append: bool = False):
        self.file_path = file_path
        self.channels = channels
        self.samplerate = samplerate
        self._wf = None
        self._raw = None          # surowy plik przy dopisywaniu (nagłówek poprawiany przy zamknięciu)
        self._data_size_pos = 0
        self._total_frames = 0
        self.existing_frames = 0  # ramki obecne w pliku przed dopisywaniem
        if append:
            params = wav_params(file_path)
            if params == (channels, samplerate, 2):
                self._open_append()
            elif params is not None:
                # Inny format (np. zmiana trybu mikrofon/oba) - nigdy nie nadpisujemy nagrania z tego dnia
                self.file_path = next_part_path(file_path)
        if self._raw is None:
            self._open()

    def _open(self):
        try:
            self._wf = wave.open(self.file_path, 'wb')
            self._wf.setnchannels(self.channels)
            self._wf.setsampwidth(2)
            self._wf.setframerate(self.samplerate)
        except Exception as e:
            if sys.stderr:
                print(f"Błąd otwarcia StreamingWavWriter ({self.file_path}): {e}", file=sys.stderr)
            self._wf = None

    def _open_append(self):
        try:
            data_pos, data_size = _find_data_chunk(self.file_path)
            block = 2 * self.channels
            data_size -= data_size % block
            raw = open(self.file_path, 'r+b')
            raw.truncate(data_pos + data_size)  # odcina ewentualne bloki dopisane za danymi
            raw.seek(0, 2)
            self._raw = raw
            self._data_size_pos = data_pos - 4
            self.existing_frames = data_size // block
        except Exception as e:
            if sys.stderr:
                print(f"Nie można dopisać do WAV ({self.file_path}), tworzę nowy: {e}", file=sys.stderr)
            self._raw = None
            self.existing_frames = 0

    def write_frames(self, data: bytes):
        if not data:
            return
        try:
            if self._raw is not None:
                self._raw.write(data)
            elif self._wf is not None:
                self._wf.writeframes(data)
            else:
                return
            self._total_frames += len(data) // (2 * self.channels)
        except Exception as e:
            if sys.stderr:
                print(f"Błąd zapisu ramek w StreamingWavWriter: {e}", file=sys.stderr)

    @property
    def duration_seconds(self) -> float:
        if self.samplerate > 0:
            return round(self._total_frames / float(self.samplerate), 2)
        return 0.0

    def close(self) -> bool:
        if self._raw is not None:
            try:
                import struct
                end = self._raw.tell()
                self._raw.seek(4)
                self._raw.write(struct.pack('<I', min(0xFFFFFFFF, end - 8)))
                self._raw.seek(self._data_size_pos)
                self._raw.write(struct.pack('<I', min(0xFFFFFFFF, end - self._data_size_pos - 4)))
                self._raw.close()
                self._raw = None
                return True
            except Exception as e:
                if sys.stderr:
                    print(f"Błąd zamykania dopisywanego pliku WAV: {e}", file=sys.stderr)
                return False
        if self._wf is not None:
            try:
                self._wf.close()
                self._wf = None
                return True
            except Exception as e:
                if sys.stderr:
                    print(f"Błąd zamykania pliku StreamingWavWriter: {e}", file=sys.stderr)
        return False


def _find_data_chunk(file_path: str):
    """Zwraca (pozycja początku danych, rozmiar danych) chunku 'data' pliku WAV."""
    import os
    import struct
    with open(file_path, 'rb') as f:
        head = f.read(12)
        if len(head) < 12 or head[:4] != b'RIFF' or head[8:12] != b'WAVE':
            raise ValueError("to nie jest plik WAV")
        file_size = os.path.getsize(file_path)
        while True:
            ch = f.read(8)
            if len(ch) < 8:
                raise ValueError("brak chunku 'data'")
            cid, size = ch[:4], struct.unpack('<I', ch[4:])[0]
            if cid == b'data':
                pos = f.tell()
                available = file_size - pos
                # Przerwany zapis (np. awaria zasilania) zostawia w nagłówku 0 - liczy się to, co jest na dysku
                return pos, size if 0 < size <= available else available
            f.seek(size + (size & 1), 1)


def next_part_path(file_path: str) -> str:
    """Pierwsza wolna ścieżka „<nazwa>_cz2.wav”, „<nazwa>_cz3.wav”… obok podanego pliku."""
    import os
    stem, ext = os.path.splitext(file_path)
    n = 2
    while os.path.exists(f"{stem}_cz{n}{ext}"):
        n += 1
    return f"{stem}_cz{n}{ext}"


def wav_params(file_path: str):
    """(kanały, częstotliwość, bajty na próbkę) istniejącego pliku WAV albo None."""
    import os
    try:
        if not file_path or not os.path.exists(file_path) or os.path.getsize(file_path) <= 44:
            return None
        with wave.open(file_path, 'rb') as wf:
            return (wf.getnchannels(), wf.getframerate(), wf.getsampwidth())
    except Exception:
        return None


def wav_duration_seconds(file_path: str) -> float:
    """Długość istniejącego pliku WAV w sekundach (0.0 gdy brak lub uszkodzony)."""
    try:
        with wave.open(file_path, 'rb') as wf:
            rate = wf.getframerate()
            return round(wf.getnframes() / float(rate), 2) if rate else 0.0
    except Exception:
        return 0.0
