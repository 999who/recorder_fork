#!/usr/bin/env python
"""
Benchmark silników rozpoznawania mowy (Parakeet / Whisper) na pliku WAV.

Plik jest dzielony na bloki tą samą logiką co tryb na żywo (Silero VAD, pre-roll, pauza automatyczna,
reguła cięcia z profilu silnika, nakładka przy wymuszonym cięciu), a następnie każdy blok trafia do silnika
i jest mierzony. Każdy silnik działa w osobnym procesie, więc pomiar pamięci nie miesza się między silnikami.

Wynik dla każdego silnika:
  - czas przetwarzania każdego bloku oraz RTF (czas przetwarzania / czas trwania audio, < 1 = szybciej niż czas rzeczywisty),
  - zużycie CPU: sekundy CPU na sekundę audio (VAD i silnik osobno) oraz średnia zajętość CPU w trakcie przetwarzania,
  - szczytowa pamięć RAM procesu (RSS) oraz RAM po załadowaniu modelu,
  - rozpoznany tekst.

Przykłady:
  python scripts/bench_asr.py nagranie.wav
  python scripts/bench_asr.py nagranie.wav --engines parakeet,whisper:small,whisper:large-v3-turbo --threads 3
  python scripts/bench_asr.py nagranie.wav --json wynik.json

Uwaga: liczby zależą od komputera. Uruchom skrypt na docelowym komputerze biurowym (bez GPU).
"""
import argparse
import json
import os
import platform
import statistics
import subprocess
import sys
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

import numpy as np

RESULT_MARKER = "BENCH_JSON:"
CHUNK_SAMPLES = 512          # 32 ms, tyle samo co okno Silero VAD
DEFAULT_ENGINES = "parakeet,whisper:small"


# ---------------------------------------------------------------------------
# Wczytywanie audio i budowa silnika
# ---------------------------------------------------------------------------

def load_wav_16k(path: str, max_sec: float = 0.0) -> np.ndarray:
    """Wczytuje plik audio jako float32 mono 16 kHz."""
    import soundfile as sf
    from recorder.audio.converter import mix_to_mono, resample_to_16k

    audio, sr = sf.read(path, dtype="float32")
    audio = mix_to_mono(audio)
    if sr != 16000:
        audio = resample_to_16k(audio, sr)
    audio = np.ascontiguousarray(audio, dtype=np.float32)
    if max_sec and max_sec > 0:
        audio = audio[: int(max_sec * 16000)]
    return audio


class MockEngine:
    """Silnik atrapa do samodzielnego testu skryptu (bez pobierania modeli): 1 słowo na sekundę audio."""
    engine_id = "mock"
    display_name = "mock"

    def load_model(self, status_cb=None):
        return None

    def transcribe_block(self, audio_float, beam_size=None):
        dur = len(audio_float) / 16000.0
        return [{"word": (" " if i else "") + f"slowo{i}", "start": float(i), "end": float(i) + 0.5, "probability": 1.0}
                for i in range(int(dur))]


def build_engine(spec: str, threads: int) -> Tuple[Any, str]:
    """Zwraca (silnik, identyfikator profilu cięcia). spec: parakeet | whisper[:rozmiar] | mock."""
    kind, _, arg = spec.partition(":")
    kind = kind.strip().lower()
    if kind == "parakeet":
        from recorder.config import get_onnx_threads, get_parakeet_model_path
        from recorder.core.parakeet_engine import ParakeetEngine
        return ParakeetEngine(threads=threads or get_onnx_threads(), model_path=get_parakeet_model_path()), "parakeet"
    if kind == "whisper":
        from recorder.core.transcriber import WhisperEngine
        return WhisperEngine(model_size=arg or "small", cpu_threads=threads or None), "whisper"
    if kind == "mock":
        return MockEngine(), "parakeet"
    raise SystemExit(f"Nieznany silnik: '{spec}'. Dozwolone: parakeet, whisper:<rozmiar>, mock")


# ---------------------------------------------------------------------------
# Pomiar zasobów
# ---------------------------------------------------------------------------

class ResourceMonitor:
    """Próbkuje RSS procesu w tle i zapamiętuje szczyt (na Windows dodatkowo peak_wset)."""

    def __init__(self, interval: float = 0.05):
        import psutil
        self._proc = psutil.Process()
        self._interval = interval
        self._stop = threading.Event()
        self.peak_rss = self._proc.memory_info().rss
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _run(self):
        while not self._stop.is_set():
            try:
                self.peak_rss = max(self.peak_rss, self._proc.memory_info().rss)
            except Exception:
                pass
            self._stop.wait(self._interval)

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._stop.set()
        self._thread.join(timeout=1.0)
        try:
            mi = self._proc.memory_info()
            self.peak_rss = max(self.peak_rss, mi.rss, getattr(mi, "peak_wset", 0) or 0)
        except Exception:
            pass


def cpu_seconds() -> float:
    import psutil
    t = psutil.Process().cpu_times()
    return float(t.user + t.system)


def rss_mb() -> float:
    import psutil
    return psutil.Process().memory_info().rss / (1024 * 1024)


# ---------------------------------------------------------------------------
# Segmentacja jak w trybie na żywo
# ---------------------------------------------------------------------------

def segment_audio(audio: np.ndarray, profile_engine: str, detector=None) -> List[Tuple[float, float, np.ndarray]]:
    """Dzieli nagranie na bloki tak jak SmartAudioWorker przy podanym profilu silnika."""
    from recorder.config import get_block_profile, get_vad_speech_threshold
    from recorder.core.blocks import BlockSegmenter
    from recorder.core.vad import SileroVADDetector

    detector = detector or SileroVADDetector(speech_threshold=get_vad_speech_threshold())
    seg = BlockSegmenter(get_block_profile(profile_engine), detector)
    blocks: List[Tuple[float, float, np.ndarray]] = []
    for i in range(0, len(audio), CHUNK_SAMPLES):
        blocks.extend(seg.feed(audio[i:i + CHUNK_SAMPLES]))
    blocks.extend(seg.flush())
    return blocks


# ---------------------------------------------------------------------------
# Pomiar jednego silnika (tryb child)
# ---------------------------------------------------------------------------

def run_engine(spec: str, audio: np.ndarray, threads: int) -> Dict[str, Any]:
    from recorder.core.blocks import OverlapDeduper

    engine, profile_engine = build_engine(spec, threads)
    audio_sec = len(audio) / 16000.0

    with ResourceMonitor() as mon:
        rss_before = rss_mb()
        t0 = time.perf_counter()
        engine.load_model()
        load_s = time.perf_counter() - t0
        rss_after_load = rss_mb()

        # Faza 1: VAD i cięcie na bloki (koszt obecny także w trybie na żywo)
        cpu0, t0 = cpu_seconds(), time.perf_counter()
        blocks = segment_audio(audio, profile_engine)
        vad_wall = time.perf_counter() - t0
        vad_cpu = cpu_seconds() - cpu0

        # Faza 2: rozpoznawanie bloków
        deduper = OverlapDeduper()
        rows: List[Dict[str, Any]] = []
        cpu0, t0 = cpu_seconds(), time.perf_counter()
        for idx, (start_sec, end_sec, arr) in enumerate(blocks, 1):
            b0 = time.perf_counter()
            local_words = engine.transcribe_block(arr)
            proc_s = time.perf_counter() - b0
            words = [{**w, "start": start_sec + w["start"], "end": start_sec + w["end"]} for w in local_words]
            words = deduper.process("mic", start_sec, end_sec, words)
            dur = len(arr) / 16000.0
            rows.append({
                "block": idx,
                "start_sec": start_sec,
                "end_sec": end_sec,
                "audio_sec": round(dur, 3),
                "proc_sec": round(proc_s, 4),
                "rtf": round(proc_s / dur, 4) if dur > 0 else None,
                "words": len(words),
                "text": "".join(w["word"] for w in words).strip(),
            })
        asr_wall = time.perf_counter() - t0
        asr_cpu = cpu_seconds() - cpu0

    procs = [r["proc_sec"] for r in rows]
    durs = [r["audio_sec"] for r in rows]
    total_block_audio = sum(durs)
    total_proc = sum(procs)
    cores = os.cpu_count() or 1

    summary = {
        "engine": spec,
        "display_name": getattr(engine, "display_name", spec),
        "audio_sec": round(audio_sec, 2),
        "blocks": len(rows),
        "block_audio_sec": round(total_block_audio, 2),
        "mean_block_sec": round(statistics.mean(durs), 2) if durs else 0.0,
        "load_sec": round(load_s, 2),
        "proc_sec_total": round(total_proc, 2),
        "proc_sec_mean_per_block": round(statistics.mean(procs), 3) if procs else 0.0,
        "proc_sec_median_per_block": round(statistics.median(procs), 3) if procs else 0.0,
        "proc_sec_p95_per_block": round(float(np.percentile(procs, 95)), 3) if procs else 0.0,
        "rtf": round(total_proc / total_block_audio, 4) if total_block_audio > 0 else None,
        "est_delay_first_word_sec": round(statistics.mean(d + p for d, p in zip(durs, procs)), 2) if rows else 0.0,
        "cpu_sec_vad": round(vad_cpu, 2),
        "cpu_sec_asr": round(asr_cpu, 2),
        "cpu_core_pct_realtime": round(100.0 * (vad_cpu + asr_cpu) / audio_sec, 2) if audio_sec > 0 else None,
        "cpu_machine_pct_realtime": round(100.0 * (vad_cpu + asr_cpu) / audio_sec / cores, 2) if audio_sec > 0 else None,
        "cpu_avg_busy_pct_of_machine": round(100.0 * asr_cpu / asr_wall / cores, 1) if asr_wall > 0 else None,
        "rss_mb_before_load": round(rss_before, 1),
        "rss_mb_after_load": round(rss_after_load, 1),
        "peak_rss_mb": round(mon.peak_rss / (1024 * 1024), 1),
        "cpu_cores": cores,
        "vad_wall_sec": round(vad_wall, 2),
    }
    return {"summary": summary, "blocks": rows}


# ---------------------------------------------------------------------------
# Raport
# ---------------------------------------------------------------------------

def print_report(results: List[Dict[str, Any]], wav_path: str, audio_sec: float, threads: int):
    print("\n" + "=" * 78)
    print(f"BENCHMARK ASR | plik: {os.path.basename(wav_path)} ({audio_sec:.1f} s) | "
          f"CPU: {platform.processor() or platform.machine()} ({os.cpu_count()} wątków) | wątki silnika: {threads or 'domyślne'}")
    print("=" * 78)

    for res in results:
        s = res["summary"]
        print(f"\n### {s['display_name']}  [{s['engine']}]")
        print(f"Bloki: {s['blocks']} (średnio {s['mean_block_sec']} s), audio w blokach: {s['block_audio_sec']} s")
        print("  blok | początek-koniec [s] | audio [s] | czas [s] |   RTF | słowa")
        for r in res["blocks"]:
            print(f"  {r['block']:>4} | {r['start_sec']:>7.1f}-{r['end_sec']:<7.1f} | {r['audio_sec']:>9.2f} | "
                  f"{r['proc_sec']:>8.3f} | {r['rtf']:>5.2f} | {r['words']:>5}")
        print(f"  Czas na blok: średnio {s['proc_sec_mean_per_block']} s, mediana {s['proc_sec_median_per_block']} s, "
              f"p95 {s['proc_sec_p95_per_block']} s")
        print(f"  RTF (cały plik): {s['rtf']}   (mniej niż 1 = szybciej niż czas rzeczywisty)")
        print(f"  Szacowane opóźnienie pierwszego słowa bloku (długość bloku + przetwarzanie): {s['est_delay_first_word_sec']} s")
        print(f"  CPU: VAD {s['cpu_sec_vad']} s + silnik {s['cpu_sec_asr']} s procesora; "
              f"na sekundę audio = {s['cpu_core_pct_realtime']}% jednego rdzenia "
              f"({s['cpu_machine_pct_realtime']}% mocy {s['cpu_cores']}-wątkowego procesora)")
        print(f"  Średnia zajętość CPU w trakcie przetwarzania bloków: {s['cpu_avg_busy_pct_of_machine']}% procesora")
        print(f"  RAM: przed załadowaniem {s['rss_mb_before_load']} MB, po załadowaniu {s['rss_mb_after_load']} MB, "
              f"szczyt {s['peak_rss_mb']} MB; ładowanie modelu {s['load_sec']} s")

    print("\n" + "-" * 78)
    print("PORÓWNANIE")
    print(f"{'silnik':<28}{'RTF':>8}{'CPU %rdzenia/s audio':>24}{'szczyt RAM [MB]':>18}")
    for res in results:
        s = res["summary"]
        print(f"{s['engine']:<28}{s['rtf']!s:>8}{s['cpu_core_pct_realtime']!s:>24}{s['peak_rss_mb']!s:>18}")

    for res in results:
        s = res["summary"]
        print("\n" + "-" * 78)
        print(f"ROZPOZNANY TEKST: {s['display_name']} [{s['engine']}]")
        print("-" * 78)
        for r in res["blocks"]:
            if r["text"]:
                print(f"[{r['start_sec']:.1f}-{r['end_sec']:.1f}] {r['text']}")


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Benchmark silników ASR na pliku WAV (bloki jak w trybie na żywo).")
    ap.add_argument("wav", help="ścieżka do pliku audio (WAV; inne formaty czytane przez soundfile)")
    ap.add_argument("--engines", default=DEFAULT_ENGINES,
                    help=f"lista silników po przecinku: parakeet, whisper:<rozmiar>, mock (domyślnie {DEFAULT_ENGINES})")
    ap.add_argument("--threads", type=int, default=0, help="liczba wątków silnika (0 = ustawienie aplikacji)")
    ap.add_argument("--max-sec", type=float, default=0.0, help="przytnij nagranie do tylu sekund (0 = całe)")
    ap.add_argument("--json", default="", help="zapisz pełny wynik do pliku JSON")
    ap.add_argument("--child", default="", help=argparse.SUPPRESS)
    args = ap.parse_args(argv)

    audio = load_wav_16k(args.wav, args.max_sec)

    if args.child:
        result = run_engine(args.child, audio, args.threads)
        print(RESULT_MARKER + json.dumps(result, ensure_ascii=False))
        return 0

    results: List[Dict[str, Any]] = []
    for spec in [e.strip() for e in args.engines.split(",") if e.strip()]:
        print(f"[BENCH] Uruchamiam: {spec} ...", flush=True)
        cmd = [sys.executable, os.path.abspath(__file__), args.wav, "--child", spec,
               "--threads", str(args.threads), "--max-sec", str(args.max_sec)]
        proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
        line = next((l for l in proc.stdout.splitlines() if l.startswith(RESULT_MARKER)), None)
        if proc.returncode != 0 or line is None:
            print(f"[BENCH] Silnik '{spec}' zakończył się błędem (kod {proc.returncode}):\n{proc.stderr[-2000:]}", file=sys.stderr)
            continue
        results.append(json.loads(line[len(RESULT_MARKER):]))

    if not results:
        return 1

    print_report(results, args.wav, len(audio) / 16000.0, args.threads)
    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump({"wav": os.path.basename(args.wav), "results": results}, f, ensure_ascii=False, indent=2)
        print(f"\nZapisano wynik: {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
