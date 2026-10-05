"""Wariant testowy (Parakeet TEST): osobna tożsamość, brak Whispera i aktualizacji."""
import json
import subprocess
import sys


def _run(code, env_extra=None, cwd=None):
    import os
    env = dict(os.environ, **(env_extra or {}))
    p = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env, cwd=cwd)
    assert p.returncode == 0, p.stderr
    return p.stdout.strip().splitlines()[-1]


CODE = (
    "import json;from recorder import config, flavor;from recorder.core.asr_engine import create_asr_engine\n"
    "print(json.dumps({'app_id':flavor.APP_ID,'models':list(config.ASR_MODELS),'engine':config.get_asr_engine(),"
    "'upd':config.is_auto_check_updates_startup(),'created':create_asr_engine(engine_id='whisper').engine_id}))"
)


def test_default_flavor_unchanged():
    out = json.loads(_run(CODE, {"RECORDER_FLAVOR": ""}))
    assert out["app_id"] == "EMANAGER.Signal"
    assert len(out["models"]) > 1


def test_parakeet_test_flavor():
    out = json.loads(_run(CODE, {"RECORDER_FLAVOR": "parakeet-test"}))
    assert out["app_id"] != "EMANAGER.Signal"
    assert out["models"] == ["parakeet"] or len(out["models"]) == 1
    assert out["engine"] == "parakeet" and out["created"] == "parakeet"
    assert out["upd"] is False
