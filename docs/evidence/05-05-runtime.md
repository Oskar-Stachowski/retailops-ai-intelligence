# Odbiór lokalny AI 05.5a — wejście i adaptery inferencji

Data: **2026-09-30**. Branch `ai/05-mlflow-serving`.
[Raport](05-05-runtime.json) powstał z pełnego sprawdzenia immutable runu
`run-77a5e7dbff215895baac1709ded1f73f`, przygotowanego pakietu wejścia
i rzeczywistych adapterów AI 04. [Runbook](../forecast-runtime.md) opisuje
format, piny i polecenia. Nie modyfikowano worktree drugiej sesji AI 04.

## Rzeczywiste dane i pomiar

Przygotowanie użyło zweryfikowanych `features` i `curated` z archiwum 04.8.
Origin: **2026-07-12T23:59:59Z**, jeden produkt i lokalizacja, kanał online,
14 dziennych wierszy oraz jedna 28-dniowa historia. Prywatne ID:
`batch-profile-sha256-4ca3b2009a1130f0b64f95dcfc0ae14d87129463eca59b7f9ce429b36e63aa8c`.
Przygotowanie trwało **76,19 s**; ponowny odczyt pakietu przez CLI przeszedł.
Raport zawiera przypięte source/curated/features IDs i checksumy modeli.

| Adapter | Rozmiar modelu | Cold decode/load | Inferencja 14 wierszy |
|---|---:|---:|---:|
| Random Forest, fold 02 | 4 415 272 B | 2,390 s | 0,072 s |
| HistGradientBoosting, fold 02 | 541 375 B | 0,247 s | 0,030 s |
| Baseline `seasonal_naive7` | Lokalny przepis diagnostyczny | Nie mierzono osobno | 14 wartości |

Końcowa weryfikacja archiwum i adapterów trwała **158,20 s**, peak RSS całego procesu
wyniósł **324 632 576 B (~310 MiB)**. Cold load oznacza lokalne dekodowanie
portable JSON i utworzenie adaptera, bez pobierania z MLflow. RF i HGB
dały dokładnie te same wartości co dotychczasowe diagnostyczne `predict`
na przypiętym feature ID. Wyniki mają jedynie hashe w evidence; nie
utworzono trwałych prognoz. Baseline zbudowano z istniejącego przepisu AI 04;
nie jest nowym modelem, oceną jakości ani zatwierdzoną wersją Registry.

## Kontrole kodu

Testy sprawdzają atomową publikację i powtórzenie wejścia, uprawnienia
plików, kompletność scope/horyzontów, checksum, history/lag binding,
odmowę future knowledge oraz symlinków. Oddzielne odmowy obejmują zmianę
zweryfikowanego rodzica w trakcie odczytu i niespójny curated parent.

Loader testowany z jawnie odizolowanym `MockRegistry` odrzuca niezgodny
image/lock, niezaliczoną bramkę, odmowę Registry, zmienione bajty oraz
niepoprawny config/signature nawet przy zgodnych receipts. Sprawdzono
odmowę origin sprzed wyboru modelu, stale history i zamkniętego kalendarza.
RF/HGB reuse sprawdza te same obliczenia, inny inference feature ID
i nową kategorię bez zmiany fitted preprocessing. Mock kwalifikacji
istnieje tylko w unit tests, nie trafia do rzeczywistego Registry ani evidence jakości.

**24 testy runtime** przechodzą: 22 w pełnym uruchomieniu (218,45 s)
i dwa dodatkowe przypadki kompatybilności wejścia (26,93 s), bez nakładania
zakresów. **253 testy regresji** przechodzą (524,94 s): calendar, features,
manifests/preprocessing, baselines, models, backtest, quality, run, kolejka,
model lifecycle, Registry, import MLflow, HTTP i CI contract.
Ruff/format, mypy (192 moduły), snapshoty forecast/intelligence/access/lifecycle
oraz linki i guard Required CI przechodzą. Gitleaks nie znalazł wycieków
w plikach i historii Git. Wheel/sdist buduje się; odizolowany import z wheela poza checkoutem
odczytał prepared inputs, wykonał rzeczywisty RF i otrzymał identyczny hash
14 wartości. Embedded lock odpowiada checkoutowi i modelowi.

## Granice

Archiwum wejściowe zachowuje **145 passed, 79 failed, 8 not_ready**;
model ma status `not_ready`. To wynik zamrożonego runu 04.8, nie audyt
nowszych, niezależnych zmian AI 04. `qualified_release_loaded=false`,
`forecast_quality_approved=false`, **0 opublikowanych prognoz, 0 zmian
MLflow i 0 wywołań AWS**. Nie używano treningu, final test ani nowego budżetu.

Kontrola pełnych pinów ujawniła **inny dependency lock archiwalnych features
niż modelu i aktualnego runtime** (`input_lock_compatible=false`). Raport
zachowuje oba SHA oraz `archived_input_dependency_lock_mismatch`. Testy
potwierdzają odmowę loadera przy takim wejściu i przy zmienionej polityce,
również gdy manifest/profile mają poprawnie przeliczone hashe.
Diagnostyczna zgodność adapterów nie zatwierdza takiego miksu do serving.
Odbiór kwalifikowanego release’u wymaga ponownego przygotowania i weryfikacji
spójnego pakietu cech/modelu w AI 04; nie zmieniono pinów starego archiwum.

Nie odebrano jeszcze loadera na rzeczywistym zakwalifikowanym release,
rejestracji prepared inputs w PostgreSQL ani ich wykonania w trwałej kolejce.
Preflight nie ma limitów procesu produkcyjnego; zmierzony RSS nie zastępuje
resource cap. Atomowa publikacja wyników i read API pozostają otwarte.
Zmiany są lokalne; pełne `make ci-local` i zdalny Required CI pozostają
bramką przed PR. AI 05 nie jest zamknięte.
