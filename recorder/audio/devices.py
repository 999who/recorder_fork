import sounddevice as sd
from typing import List, Dict, Any, Optional

try:
    import pyaudiowpatch as pyaudio
    HAS_PYAUDIOWPATCH = True
except ImportError:
    pyaudio = None
    HAS_PYAUDIOWPATCH = False

try:
    from pycaw.pycaw import AudioUtilities
    HAS_PYCAW = True
except ImportError:
    HAS_PYCAW = False

import re


def clean_device_name(raw_name: str) -> str:
    """
    Oczyszcza nazwę urządzenia audio z technicznych dopisków interfejsu
    (np. MME, WASAPI, DirectSound, Loopback).
    """
    if not raw_name:
        return ""
    name = raw_name.replace(" [Loopback]", "").strip()
    name = re.sub(
        r'\s*\((?:MME|Windows DirectSound|Windows WASAPI|WASAPI|DirectSound|WDM-KS)\)\s*$',
        '',
        name,
        flags=re.IGNORECASE
    ).strip()
    return name


def is_mapper_pseudo_device(raw_name: str) -> bool:
    """
    Sprawdza, czy urządzenie jest sztucznym aliasem mapera audio systemu Windows
    (np. 'Mapowanie dźwięku Microsoft - Input', 'Podstawowy sterownik przechwytywania dźwięku').
    """
    if not raw_name:
        return False
    name_lower = raw_name.lower()
    mapper_keywords = (
        "mapowanie d", "mapper", "podstawowy sterownik", 
        "primary sound", "default audio capture"
    )
    return any(k in name_lower for k in mapper_keywords)


def _merge_truncated_name_groups(groups: Dict[str, Dict[str, Any]]) -> None:
    """
    MME obcina nazwy urządzeń do 31 znaków, więc ten sam mikrofon występuje pod dwiema nazwami
    (np. 'Mikrofon (2 - Wireless micropho' i 'Mikrofon (2 - Wireless microphone)'). Skrócona nazwa
    jest przedrostkiem pełnej, więc takie grupy scalamy pod pełną nazwą.
    """
    names = sorted(groups, key=len)
    for short in names:
        if short not in groups or len(short) < 12:
            continue
        for full in sorted(groups, key=len, reverse=True):
            if full != short and len(full) > len(short) and full.startswith(short):
                groups[full]['variants'].extend(groups[short]['variants'])
                groups[full]['is_default'] = groups[full]['is_default'] or groups[short]['is_default']
                del groups[short]
                break


def get_working_input_devices(force_refresh: bool = False) -> List[Dict[str, Any]]:
    """
    Pobiera listę sprawnych urządzeń wejściowych (mikrofonów), ignorując surowe sterowniki WDM-KS.
    Deduplikuje ten sam fizyczny mikrofon występujący w wielu interfejsach (MME, DirectSound, WASAPI),
    automatycznie wybierając najstabilniejszy interfejs Windows WASAPI jako nadrzędny (primary),
    zapisuje alternatywne indeksy jako fallback oraz oznacza domyślny mikrofon systemowy.
    """
    valid_devices = []
    if HAS_PYAUDIOWPATCH:
        p = None
        try:
            p = pyaudio.PyAudio()
            hostapis = {}
            default_wasapi_mic_idx = None
            default_general_mic_idx = None

            try:
                def_dev = p.get_default_input_device_info()
                if def_dev:
                    default_general_mic_idx = def_dev.get('index')
            except Exception:
                pass

            for i in range(p.get_host_api_count()):
                info = p.get_host_api_info_by_index(i)
                api_name = info.get('name', '')
                hostapis[i] = api_name
                if 'WASAPI' in api_name:
                    default_wasapi_mic_idx = info.get('defaultInputDevice')

            target_default_idx = default_wasapi_mic_idx if default_wasapi_mic_idx is not None else default_general_mic_idx

            grouped_devices = {}
            for idx in range(p.get_device_count()):
                dev = p.get_device_info_by_index(idx)
                if dev.get('maxInputChannels', 0) > 0 and not dev.get('isLoopbackDevice', False) and '[Loopback]' not in dev.get('name', ''):
                    hostapi_name = hostapis.get(dev.get('hostApi', 0), '')
                    if "WDM-KS" in hostapi_name:
                        continue

                    raw_name = dev.get('name', '')
                    c_name = clean_device_name(raw_name)
                    is_map = is_mapper_pseudo_device(raw_name)

                    if c_name not in grouped_devices:
                        grouped_devices[c_name] = {
                            'name': c_name,
                            'raw_name': raw_name,
                            'is_mapper': is_map,
                            'variants': [],
                            'is_default': False,
                        }

                    h_upper = hostapi_name.upper()
                    if "WASAPI" in h_upper:
                        rank = 3
                    elif "DIRECTSOUND" in h_upper:
                        rank = 2
                    elif "MME" in h_upper:
                        rank = 1
                    else:
                        rank = 0

                    dev_index = dev.get('index', idx)
                    is_this_def = (dev_index == target_default_idx) or (default_general_mic_idx is not None and dev_index == default_general_mic_idx)
                    if is_this_def:
                        grouped_devices[c_name]['is_default'] = True

                    grouped_devices[c_name]['variants'].append({
                        'rank': rank,
                        'index': dev_index,
                        'hostapi': hostapi_name,
                        'channels': int(dev['maxInputChannels']),
                        'samplerate': int(dev.get('defaultSampleRate', 16000)),
                        'raw_info': dev,
                        'is_default': is_this_def
                    })

            _merge_truncated_name_groups(grouped_devices)

            # Jeśli są dostępne rzeczywiste mikrofony fizyczne, odrzucamy aliasy maperów Windows
            candidate_groups = [g for g in grouped_devices.values() if not g['is_mapper']]
            if not candidate_groups:
                candidate_groups = list(grouped_devices.values())

            for g in candidate_groups:
                g['variants'].sort(key=lambda v: v['rank'], reverse=True)
                primary = g['variants'][0]
                fallback_indices = [v['index'] for v in g['variants'][1:]]
                api_variants = {v['hostapi']: v['index'] for v in g['variants']}

                is_def = g['is_default']
                label = f"🎤 {g['name']}"
                if primary['channels'] >= 2:
                    label += f" [{primary['channels']} kanały]"
                if is_def:
                    label += " (Domyślne)"

                valid_devices.append({
                    'index': primary['index'],
                    'name': g['name'],
                    'label': label,
                    'hostapi': primary['hostapi'],
                    'channels': primary['channels'],
                    'samplerate': primary['samplerate'],
                    'is_default': is_def,
                    'fallback_indices': fallback_indices,
                    'api_variants': api_variants,
                    'raw_info': primary['raw_info']
                })

            # Sortujemy tak, aby mikrofon domyślny był zawsze na samej górze
            valid_devices.sort(key=lambda d: 1 if d.get('is_default') else 0, reverse=True)

        except Exception as e:
            print(f"Błąd wykrywania mikrofonów PyAudio: {e}")
        finally:
            if p:
                try:
                    p.terminate()
                except Exception:
                    pass

        if valid_devices:
            return valid_devices

    try:
        devices = sd.query_devices()
        hostapis = sd.query_hostapis()

        grouped_sd = {}
        for idx, dev in enumerate(devices):
            if dev.get('max_input_channels', 0) > 0:
                hostapi_idx = dev.get('hostapi', 0)
                hostapi_name = hostapis[hostapi_idx]['name'] if hostapi_idx < len(hostapis) else ""
                
                # WDM-KS na Windows powoduje błąd "Blocking API not supported" w PortAudio
                if "WDM-KS" in hostapi_name:
                    continue

                raw_name = dev.get('name', '')
                c_name = clean_device_name(raw_name)
                is_map = is_mapper_pseudo_device(raw_name)

                if c_name not in grouped_sd:
                    grouped_sd[c_name] = {
                        'name': c_name,
                        'raw_name': raw_name,
                        'is_mapper': is_map,
                        'variants': []
                    }

                h_upper = hostapi_name.upper()
                rank = 3 if "WASAPI" in h_upper else (2 if "DIRECTSOUND" in h_upper else (1 if "MME" in h_upper else 0))
                grouped_sd[c_name]['variants'].append({
                    'rank': rank,
                    'index': idx,
                    'hostapi': hostapi_name,
                    'channels': dev['max_input_channels'],
                    'samplerate': int(dev.get('default_samplerate', 16000))
                })

        candidate_sd = [g for g in grouped_sd.values() if not g['is_mapper']] or list(grouped_sd.values())
        for g in candidate_sd:
            g['variants'].sort(key=lambda v: v['rank'], reverse=True)
            primary = g['variants'][0]
            valid_devices.append({
                'index': primary['index'],
                'name': g['name'],
                'label': f"🎤 {g['name']}",
                'hostapi': primary['hostapi'],
                'channels': primary['channels'],
                'samplerate': primary['samplerate'],
                'is_default': False,
                'fallback_indices': [v['index'] for v in g['variants'][1:]]
            })
    except Exception as e:
        print(f"Błąd wykrywania urządzeń audio: {e}")

    return valid_devices


def get_working_loopback_devices() -> List[Dict[str, Any]]:
    """
    Pobiera listę dostępnych urządzeń WASAPI Loopback (Głośniki / Słuchawki / Dźwięk Systemu).
    Pozwala na bezpośrednie rejestrowanie dźwięku z Discorda, Teamsa, YouTube, itp.
    """
    loopback_devices = []
    if not HAS_PYAUDIOWPATCH:
        return loopback_devices

    p = None
    try:
        p = pyaudio.PyAudio()
        default_loopback_idx = None
        try:
            def_loop = p.get_default_wasapi_loopback()
            if def_loop:
                default_loopback_idx = def_loop.get('index')
        except Exception as e:
            import logging
            logging.getLogger("recorder").debug(f"Brak domyślnego urządzenia loopback WASAPI: {e}")

        for loopback in p.get_loopback_device_info_generator():
            idx = loopback.get('index')
            name = loopback.get('name', 'Nieznane urządzenie loopback')
            is_default = (idx == default_loopback_idx)
            
            clean_name = name.replace(" [Loopback]", "").strip()
            label = f"🎧 {clean_name}"
            if is_default:
                label += " (Domyślne)"

            loopback_devices.append({
                'index': idx,
                'name': name,
                'label': label,
                'channels': int(loopback.get('maxInputChannels', 2)),
                'samplerate': int(loopback.get('defaultSampleRate', 48000)),
                'is_loopback': True,
                'is_default': is_default,
                'raw_info': loopback
            })
    except Exception as e:
        print(f"Błąd pobierania urządzeń WASAPI Loopback: {e}")
    finally:
        if p:
            try:
                p.terminate()
            except Exception:
                pass

    return loopback_devices


def get_active_audio_apps() -> List[Dict[str, Any]]:
    """
    Pobiera listę uruchomionych aplikacji, które aktualnie posiadają aktywną sesję audio w systemie Windows
    (np. Discord.exe, ms-teams.exe, firefox.exe, chrome.exe).
    """
    apps = []
    if not HAS_PYCAW:
        return apps

    try:
        try:
            import comtypes
            comtypes.CoInitialize()
        except Exception:
            pass

        sessions = AudioUtilities.GetAllSessions()
        seen_names = set()
        for session in sessions:
            if session.Process and session.Process.name():
                exe_name = session.Process.name()
                pid = session.Process.pid
                if exe_name.lower() in seen_names or exe_name.lower() in ("system sounds", "svchost.exe"):
                    continue
                seen_names.add(exe_name.lower())
                
                # Czysta, uniwersalna nazwa programu na podstawie pliku wykonywalnego
                clean_name = exe_name[:-4] if exe_name.lower().endswith(".exe") else exe_name
                display_name = clean_name.capitalize() if clean_name.islower() else clean_name

                apps.append({
                    'name': display_name,
                    'exe': exe_name,
                    'pid': pid
                })
    except Exception as e:
        print(f"Błąd pobierania sesji audio aplikacji: {e}")

    return apps


class TargetAppAudioMonitor:
    """
    Monitor aktywności audio wybranego procesu w systemie Windows (np. Discord.exe, ms-teams.exe).
    Wykorzystuje interfejs IAudioMeterInformation z Windows Core Audio (pycaw),
    aby zweryfikować, czy wybrany proces faktycznie generuje dźwięk.
    Pozwala na odfiltrowanie dźwięków tła z innych aplikacji (np. YouTube z przeglądarki).
    """
    def __init__(self, target_filter: str = ""):
        import time
        self.time = time
        self.target_filter = target_filter.lower().strip() if target_filter else ""
        self.meters = []
        self.last_refresh_time = 0.0
        self.refresh_interval = 2.0  # Odświeżanie sesji co 2 sekundy
        self._is_active = bool(self.target_filter and "wszystkie" not in self.target_filter)
        if self._is_active:
            self._refresh_sessions()

    def set_filter(self, target_filter: str):
        self.target_filter = target_filter.lower().strip() if target_filter else ""
        self._is_active = bool(self.target_filter and "wszystkie" not in self.target_filter)
        self.meters = []
        if self._is_active:
            self._refresh_sessions()

    def _refresh_sessions(self):
        if not HAS_PYCAW or not self._is_active:
            self.meters = []
            return
        try:
            try:
                import comtypes
                comtypes.CoInitialize()
            except Exception:
                pass

            sessions = AudioUtilities.GetAllSessions()
            meters = []
            for s in sessions:
                if s.Process and s.Process.name():
                    p_name = s.Process.name().lower()
                    if self.target_filter in p_name or p_name in self.target_filter:
                        try:
                            from pycaw.pycaw import IAudioMeterInformation
                            meter = s._ctl.QueryInterface(IAudioMeterInformation)
                            meters.append(meter)
                        except Exception:
                            pass
            self.meters = meters
            self.last_refresh_time = self.time.time()
        except Exception:
            pass

    def is_target_app_playing(self) -> bool:
        """
        Zwraca True, jeśli wybrana aplikacja aktywnie generuje dźwięk (Peak > 0.0005)
        lub gdy nie ustawiono filtra aplikacji (cały mikser).
        """
        if not self._is_active:
            return True

        now = self.time.time()
        if (now - self.last_refresh_time) > self.refresh_interval or not self.meters:
            self._refresh_sessions()

        if not self.meters:
            return False

        max_peak = 0.0
        need_refresh = False
        for m in self.meters:
            try:
                val = m.GetPeakValue()
                if val > max_peak:
                    max_peak = val
            except Exception:
                need_refresh = True

        if need_refresh:
            self._refresh_sessions()

        return max_peak > 0.0005



class OutputActivityProbe:
    """
    Szczytowy poziom sygnału na każdym aktywnym wyjściu audio Windows (Core Audio, IAudioMeterInformation).

    Pozwala sprawdzić, na które urządzenie faktycznie gra dźwięk (np. rozmowa w Google Meet), bez otwierania
    strumieni PortAudio. Lista wyjść jest odświeżana co REFRESH_SEC, więc widać też urządzenia podłączone
    w trakcie nagrania (słuchawki Bluetooth, tryb zestawu słuchawkowego podczas rozmowy).
    Wywoływać z wątku, który wykonał CoInitialize.
    """
    REFRESH_SEC = 5.0

    def __init__(self):
        import time
        self.time = time
        self._meters: List[Any] = []  # (czysta nazwa wyjścia, IAudioMeterInformation)
        self._last_refresh = 0.0
        self.available = HAS_PYCAW

    def _refresh(self):
        self._last_refresh = self.time.time()
        meters = []
        try:
            import comtypes
            from pycaw.pycaw import IAudioMeterInformation, IMMDeviceEnumerator
            from pycaw.constants import CLSID_MMDeviceEnumerator
            enumerator = comtypes.CoCreateInstance(
                CLSID_MMDeviceEnumerator, IMMDeviceEnumerator, comtypes.CLSCTX_INPROC_SERVER
            )
            collection = enumerator.EnumAudioEndpoints(0, 1)  # eRender, DEVICE_STATE_ACTIVE
            for i in range(collection.GetCount()):
                try:
                    dev = collection.Item(i)
                    name = clean_device_name(str(AudioUtilities.CreateDevice(dev).FriendlyName or ""))
                    iface = dev.Activate(IAudioMeterInformation._iid_, comtypes.CLSCTX_ALL, None)
                    meters.append((name, iface.QueryInterface(IAudioMeterInformation)))
                except Exception:
                    continue
        except Exception as e:
            print(f"[OutputActivityProbe] Nie udało się odczytać listy wyjść audio: {e}")
            self.available = False
        self._meters = meters

    def peaks(self) -> Dict[str, float]:
        """Mapa {nazwa wyjścia: szczyt 0..1} dla aktywnych wyjść (pusta, gdy pycaw/Core Audio niedostępne)."""
        if not self.available:
            return {}
        if not self._meters or (self.time.time() - self._last_refresh) > self.REFRESH_SEC:
            self._refresh()
        out: Dict[str, float] = {}
        stale = False
        for name, meter in self._meters:
            try:
                out[name] = max(out.get(name, 0.0), float(meter.GetPeakValue()))
            except Exception:
                stale = True
        if stale:
            self._last_refresh = 0.0
        return out


def output_peak(peaks: Dict[str, float], device_name: str) -> Optional[float]:
    """Szczyt sygnału wyjścia o podanej nazwie (nazwy PortAudio i Core Audio porównywane po oczyszczeniu)."""
    target = clean_device_name(device_name or "")
    if not target:
        return None
    if target in peaks:
        return peaks[target]
    for name, val in peaks.items():
        if name and (name.startswith(target) or target.startswith(name)):
            return val
    return None


def pick_louder_output(peaks: Dict[str, float], current_name: str,
                       silent_below: float = 0.002, active_above: float = 0.02) -> Optional[str]:
    """
    Zwraca nazwę innego wyjścia, na którym gra dźwięk, gdy nagrywane wyjście milczy (inaczej None).
    Przykład: nagrywamy „Głośniki”, a Google Meet gra na „Słuchawki (Bluetooth)”.
    """
    cur = output_peak(peaks, current_name)
    if cur is not None and cur >= silent_below:
        return None
    target = clean_device_name(current_name or "")
    best_name, best_val = None, active_above
    for name, val in peaks.items():
        if name == target or val < best_val:
            continue
        if target and (name.startswith(target) or target.startswith(name)):
            continue
        best_name, best_val = name, val
    return best_name
