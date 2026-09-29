# Implementation Plan: Przywrócenie Najwyższej Precyzji i Stabilności CPU (Hotfix v0.7.3)

## 1. Dogłębna Diagnoza Problemu (Post-Mortem v0.7.2)

Po wdrożeniu wersji `v0.7.2` zaobserwowano trzy krytyczne anomalie:
1. **Dramatyczny spadek jakości języka polskiego i halucynacje:**
   - Obniżenie `whisper_beam_size` z 5 do 1 (Greedy Decoding) pozbawiło model `large-v3-turbo` możliwości weryfikacji alternatywnych hipotez leksykalnych.
   - W języku polskim (silna fleksja, homofony, trudna fonetyka) algorytm zachłanny gubi końcówki, przekręca słowa, a przy mikro-pauzach wpada w halucynacje (w logach odnotowano halucynacje w języku islandzkim: `Er ekki noð rauðin, það er spanda.`).
2. **Stałe obciążenie 25% CPU Non-Stop (Zator Kolejki / Thrashing):**
   - Agresywne cięcie bloków VAD (`cur_dur >= 2.0s` przy pauzie `sil_dur >= 0.25s`) spowodowało, że audio było dzielone na dziesiątki mikrokawałków (15–20 na minutę).
   - Whisper to model sekwencyjny wytrenowany na 30-sekundowych oknach. Na CPU inferencja bloku 2-sekundowego trwa ~1.5 sekundy.
   - Bloki przychodziły szybciej, niż procesor był w stanie je przetworzyć, co doprowadziło do **ciągłego zatoru kolejki**. Zamiast krótkich pików (~36%) i natychmiastowego powrotu do 0% idle, silnik pracował bez przerwy pod obciążeniem ~25% CPU non-stop.
3. **Zwiększone opóźnienie w odczuciu użytkownika:**
   - Ze względu na korek w kolejce bloków mowy, kolejne zdania czekały w buforze dłużej, niż gdyby model przetworzył jedno pełne, logiczne zdanie (6–14s).
4. **Kluczowa prawda o architekturze Whisper Flow:**
   - Whisper Flow to **klient chmurowy (SaaS)** – zużywa 500 MB RAM i 4% CPU, ponieważ jedynie wysyła strumień audio przez WebSocket do farmy serwerów GPU (NVIDIA H100).
   - Nasza aplikacja działa w **100% lokalnie, prywatnie i bezpłatnie na procesorze laptopa**.
   - Na CPU optymalną strategią nie jest mikrosiekanie co 2 sekundy (które dławi procesor i niszczy kontekst zdaniowy), lecz zbieranie pełnych, spójnych zdań z naturalnymi pauzami (v0.7.1).

---

## 2. Plan Naprawczy (Przywrócenie Sprawdzonej Architektury v0.7.1)

### Krok 1: Przywrócenie Naturalnego Cięcia Bloków VAD (Pełne Zdania)
- **Plik:** `recorder/ui/workers.py` oraz `recorder/config.py`.
- Przywracamy sprawdzone progi z v0.7.1:
  - `(cur_dur >= 6.0 and sil_dur >= 0.5)` – naturalna pauza po pełnym zdaniu (min. 6s).
  - `(cur_dur >= 14.0 and sil_dur >= 0.3)` – cięcie przy dłuższej wypowiedzi.
  - `(cur_dur >= 25.0)` – bezpieczny górny limit monologu.
  - `(cur_dur >= 2.0 and state == SmartRecordState.AUTO_PAUSED)` – wypchnięcie po przerwaniu mówienia.
  - Minimalna długość bloku audio: `cur_dur >= 1.5s`.
- Zapewnia to pełen kontekst gramatyczny dla Whispera i pozwala procesorowi odpoczywać (0% CPU) między wypowiedziami.

### Krok 2: Przywrócenie Beam Size = 5 (Pełna Jakość Języka Polskiego)
- **Pliki:** `recorder/config.py`, `recorder/ui/settings_dialog.py`, `user_settings.json`.
- Domyślna wartość `whisper_beam_size = 5`.
- W dialogu ustawień: oznaczenie Beam Size = 5 jako zalecanego i domyślnego.

### Krok 3: Przywrócenie Pełnej Mocy Wielowątkowości CPU
- **Plik:** `recorder/config.py`.
- Usunięcie ograniczenia do 4 wątków na Ryzen AI. Przywrócenie dynamicznego `safe_threads = max(1, min(6, total_cores - 1))`, co daje pełne 6 wątków roboczych i skraca czas inferencji.

### Krok 4: Przywrócenie Normalnego Interwału Renderowania UI
- **Plik:** `recorder/core/rolling_transcriber.py`.
- Dławienie odświeżania podglądu transkrypcji z powrotem na 1.5s, aby nie obciążać pętli zdarzeń Qt.

### Krok 5: Weryfikacja Testów i Wydanie Wersji v0.7.3
- Uruchomienie testów jednostkowych.
- Podbicie wersji do `v0.7.3`.
- Zbudowanie i opublikowanie nowego wydania instalacyjnego `.exe` przez GitHub Actions.
