"""
Wariant aplikacji (flavor). Pozwala zainstalować wersję testową obok głównej: inna nazwa okna,
inny identyfikator Windows (AUMID/skrót/powiadomienia), brak Whispera i brak automatycznych aktualizacji.

Wariant wynika z pliku flavor.json obok exe (tworzy go scripts/build_exe.py --variant parakeet-test)
albo ze zmiennej środowiskowej RECORDER_FLAVOR=parakeet-test (uruchamianie ze źródeł).
Bez pliku i zmiennej aplikacja zachowuje się jak dotychczas.
"""
import json
import os
import sys

PRESETS = {
    "parakeet-test": {
        "id": "parakeet-test",
        "app_id": "InteligentnyDyktafonAI.ParakeetTest",
        "app_name": "Inteligentny Dyktafon AI (Parakeet TEST)",
        "whisper": False,
        "updates": False,
    },
}

DEFAULT = {
    "id": "",
    "app_id": "InteligentnyDyktafonAI",
    "app_name": "Inteligentny Dyktafon AI",
    "whisper": True,
    "updates": True,
}


def _app_dir() -> str:
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load() -> dict:
    data = dict(DEFAULT)
    env = os.environ.get("RECORDER_FLAVOR", "").strip().lower()
    if env in PRESETS:
        data.update(PRESETS[env])
        return data
    path = os.path.join(_app_dir(), "flavor.json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        if isinstance(raw, dict):
            data.update({k: raw[k] for k in DEFAULT if k in raw})
    except Exception:
        pass
    return data


FLAVOR = _load()
APP_ID = str(FLAVOR["app_id"])
APP_NAME = str(FLAVOR["app_name"])
WHISPER_ENABLED = bool(FLAVOR["whisper"])
UPDATES_ENABLED = bool(FLAVOR["updates"])
