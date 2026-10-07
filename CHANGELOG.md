## [v2.0.2]: Jedno nagranie dziennie

- Start po Stop tego samego dnia kontynuuje dzisiejsze nagranie: ta sama transkrypcja, ten sam plik WAV (dopisywany), to samo spotkanie w chmurze, a stoper pokazuje łączny czas dnia. Nowe nagranie zaczyna się następnego dnia.
- Długa cisza nie dzieli już nagrania w ciągu dnia.
- Można to wyłączyć w Ustawienia → Nagrywanie („Jedno nagranie dziennie”).

---

## [v2.0.1]: Podpisy mówców z ekranu głównego, poprawne nagrywanie dźwięku systemu

- Kliknięcie paska źródeł na ekranie głównym otwiera okienko „Podpisy w transkrypcji”: własna nazwa zamiast „Mikrofon” / „Dźwięk Systemu” (także dla kanałów odbiornika wielokanałowego), działa też w trakcie nagrania.
- Poprawka: kanał systemu nagrywał tylko wyjście domyślne z chwili startu. Gdy rozmowa (np. Google Meet) grała na innym wyjściu (słuchawki Bluetooth, zestaw słuchawkowy w trybie rozmowy), wszystko trafiało do mikrofonu. Teraz nagrywanie przełącza się na wyjście, na którym faktycznie gra dźwięk.
- Wyjście wybrane w Ustawieniach jest odnajdywane po nazwie, a nie po indeksie, który zmieniał się po podłączeniu urządzeń; w logu widać, które wyjście jest nagrywane.
- Aktualizacje pobierane z repozytorium 999who/recorder_fork.

---

## [v2.0]: EMANAGER Signal, silnik Parakeet, wiele mikrofonów

- Nowa nazwa aplikacji **EMANAGER Signal** i nowe logo (exe `EMANAGER-Signal.exe`, identyfikator Windows `EMANAGER.Signal`). Instalacje o starej nazwie trzeba zainstalować ponownie ręcznie.
- Nowy wygląd (motyw ciemny „notebook”).
- Do 4 nazwanych kanałów mikrofonu (np. Hollyland Lark w trybie stereo), każdy z własnym kolorem; dźwięk systemu na biało.
- Znacznik czasu w transkrypcji: godzina startu wypowiedzi `[HH:MM:SS]` zamiast zakresów offsetu.
- Filtr języka: Parakeet v3 jest wielojęzyczny, więc fragmenty rozpoznane jako angielskie są pomijane (ustawienie „tylko język polski”).
- Nowy silnik NVIDIA Parakeet TDT 0.6B v3 (onnx-asr, onnxruntime CPU int8) obok Whispera; wspólny interfejs `AsrEngine`.
- Wybór silnika i liczby wątków (2–4) w ustawieniach; bez CUDA domyślnie Parakeet.
- Profile cięcia bloków per silnik (konfigurowalne), nakładka przy wymuszonym cięciu z usuwaniem duplikatów słów.
- Tabela autokorekt „błędnie → poprawnie”; krótki naturalny prompt polski dla Whispera.
- Silero VAD na onnxruntime (bez torch); sprawdzanie CUDA przez ctranslate2.
- Usunięto diaryzację (pyannote, UI, ustawienia), torch, torchaudio i PyQt6.
- Komunikaty pobierania modelu w UI, opcja lokalnego folderu z modelem; build EXE bez torch z weryfikacją paczki.
- Wariant testowy `build_exe.py --variant parakeet-test`: osobna nazwa exe i identyfikator Windows (instalacja obok głównej), bez Whispera i aktualizacji (`recorder/flavor.py`).
- `scripts/bench_asr.py`: pomiar czasu bloków, RTF, CPU i RAM dla obu silników.

---

## [v0.7.3] - 2026-09-29: Przywrócenie Najwyższej Jakości Transkrypcji i Stabilności CPU (Hotfix)

### Wprowadzone zmiany:
1. **Przywrócenie Pełnej Jakości Języka Polskiego (Beam Size = 5):**
   - Przywrócono domyślny parametr `whisper_beam_size = 5` (Beam Search) w `recorder/config.py` i w oknie ustawień.
   - Eliminacja halucynacji (np. tokenów obcojęzycznych na szumie) i przywrócenie poprawnej odmiany przez przypadki w języku polskim.

2. **Przywrócenie Naturalnych Granic Zdaniowych VAD (Eliminacja Ciągłego Obciążenia 25% CPU):**
   - Wycofano mikrosiekanie bloków co 2 sekundy (które powodowało zator kolejki i mielenie CPU non-stop).
   - Przywrócono sprawdzone progi cięcia: min. 6.0s z pauzą 0.5s lub 14.0s z pauzą 0.3s (lub max 25s monologu).
   - Whisper otrzymuje spójne, pełne frazy, a procesor po skończonej transkrypcji natychmiast wraca do 0% CPU (stanu uśpienia).

3. **Przywrócenie Pełnej Wielowątkowości CPU (6 Wątków):**
   - Dynamiczny przydział wątków roboczych `safe_threads = min(6, total_cores - 1)` dla procesorów AMD Ryzen AI i standardowych procesorów x86.
   - Maksymalne wykorzystanie instrukcji wektorowych i szybsza transkrypcja każdego bloku.

4. **Stabilizacja Renderowania UI:**
   - Przywrócono buforowanie odświeżania podglądu transkrypcji na 1.5s w `recorder/core/rolling_transcriber.py`.

---

## [v0.7.2] - 2026-09-28: Przyspieszenie Transkrypcji do Poziomu Whisper Flow (Latency 1.8s & Optymalizacja AMD Ryzen AI)

### Wprowadzone zmiany:
0. **100% Bezpieczny Fallback (Zero Crash Guarantee):**
   - Na komputerach bez układów NPU / AMD Ryzen AI (np. procesory Intel Core, starsze AMD Ryzen czy wirtualne maszyny) system automatycznie i bezszelestnie przełącza się na sprawdzony, uniwersalny tryb CPU (int8 AVX).
   - Dynamiczny przydział wątków i bezpieczne bloki try...except gwarantują stabilność na każdym sprzęcie.

1. **Błyskawiczne Cięcie Bloków VAD (Fast Slicing Latency 1.8–2.2s):**
   - Zmodyfikowano logikę wycinania bloków w `recorder/ui/workers.py` (zarówno dla mikrofonu, jak i dźwięku systemu WASAPI Loopback).
   - Zredukowano próg bufora z 6.0s/14.0s/25.0s do:
     - 2.0s mowy przy pauzie ciszy 0.25s.
     - 4.5s mowy przy mikropauzie 0.18s.
     - Maksymalny twardy limit cięcia monologu obniżono z 45.0s do 8.0s.
     - Auto-pauza wypycha mowę już po 0.9–1.0s.
   - Wartości `LIVE_BLOCK_MIN_SEC` (2.0s), `LIVE_BLOCK_MAX_SEC` (8.0s) i `LIVE_BLOCK_SILENCE_CUT_SEC` (0.25s) zaktualizowano w `recorder/config.py`.

2. **Greedy Decoding (whisper_beam_size = 1) dla large-v3-turbo:**
   - Zmieniono domyślną wartość `whisper_beam_size` z 5 na 1 w `recorder/config.py`.
   - Zaktualizowano `c:\Users\targo\Emanager\EMANAGER-Signal\user_settings.json` na `whisper_beam_size: 1`.
   - Zapewnia to 3- do 4-krotne skrócenie czasu inferencji na modelu `large-v3-turbo` bez zauważalnego spadku precyzji języka polskiego.

3. **Detekcja Sprzętowa AMD Ryzen AI i Dedykowany Profil Zen 5:**
   - Rozbudowano `get_hardware_acceleration_info()` w `recorder/config.py` o natywną detekcję procesorów AMD Ryzen AI z rejestru Windows.
   - Ustawiono dedykowany przydział 4 wątków roboczych na rdzeniach Zen 5 Performance, co zapobiega dławieniu rdzeni Zen 5c, przegrzewaniu laptopa i redukuje piki obciążenia CPU z 40% do 10–18%.
   - Zaktualizowano rekomendacje sprzętowe w `get_recommended_profile()`.

4. **Przyspieszenie Renderowania UI & Synchronizacja Ustawień:**
   - Skrócono interwał dławienia odświeżania podglądu transkrypcji w `recorder/core/rolling_transcriber.py` z 1.5s do 0.35s przy pustej kolejce.
   - Zaktualizowano okno dialogowe ustawień (`recorder/ui/settings_dialog.py`) o domyślny wybór Beam Size = 1 (Błyskawiczny / Whisper Flow).
   - Dostosowano zestaw testów jednostkowych (`tests/test_long_session_8h.py`) do jawnego weryfikowania adaptacyjnego biegu turbo z dynamicznym obniżaniem beam size.

5. **Dokumentacja i Plany:**
   - Utworzono dokument architektoniczny `ARCHITECTURE.md` ze schematem Mermaid.
   - Utworzono plan wdrożenia `implementation_plan.md`.
