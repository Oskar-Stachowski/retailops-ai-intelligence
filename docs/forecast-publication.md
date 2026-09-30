# AI 05.6 — przyjęcie i atomowa publikacja prognoz

Kolejka łączy [rejestr wejść](forecast-input-store.md),
[loader modelu](forecast-runtime.md) i ograniczony supervisor z trwałym wynikiem.
[Odbiór techniczny](evidence/05-06-publication.md) obejmuje testy jednostkowe
i rzeczywisty PostgreSQL na kontrolowanych fixture.
W tym worktree nadal brakuje spójnego, zakwalifikowanego release’u AI 04;
odbiór techniczny nie zatwierdza jakości modelu ani całego AI 05.

## Przyjęcie runu

W `APP_ENV=local` istniejący `POST /api/v1/forecast-runs` przyjmuje
zarejestrowany profil ze swojego środowiska. Wymaga `pipeline`, `forecast:run`
i całego żądanego scope. Puste filtry rozwijają się do przyznanego zakresu.
Maksimum to 20 produktów × 5 lokalizacji × 14 dziennych horyzontów,
czyli 1400 wartości, dla jednego kanału i origin zamykającego dzień UTC.

Przyjęcie wymaga zatwierdzonego release’u `retailops-demand-forecast`,
wszystkich gates `passed`, zakończonych decyzji lifecycle i zgodnego lock
features/modelu. Brak release’u daje `409 model-not-approved`; brak
rejestracji `422 input-not-prepared`, zły origin `422 input-origin-mismatch`,
niepełne pokrycie `422 input-coverage-mismatch`, zły lock
`422 input-runtime-incompatible`. Nie powstaje wtedy run.
Idempotencja, scope i limity kolejki zachowują zasady [workera](forecast-worker.md).

Run przypina oryginalny profil i pełne source/curated/features lineage,
release/model version/checksumy, image, scope, origin i politykę. Dla podzbioru
lub okna 7 dni worker wylicza deterministyczny, content-addressed profil
wykonania. Child otrzymuje wyłącznie jego wiersze i historię; manifest wyniku
zachowuje ID oryginalnej rejestracji oraz `execution_profile_id`.

`APP_ENV=test` zachowuje oddzielną kolejkę mechanics. Zaufany kod testowy
może jawnie utworzyć `PostgresBatchQueue(..., mechanics=False)` do odbioru
kwalifikowanej ścieżki. Żądanie HTTP nie wybiera namespace ani model version.
Worker kwalifikowany i mechanics przejmują wyłącznie runy swojego purpose.

## Wykonanie

W prywatnym procesie z `DATABASE_URL`, rzeczywistym `IMAGE_DIGEST`
i pozostałymi settings:

```bash
python -m retailops_ai.forecast_jobs.worker --once
python -m retailops_ai.forecast_jobs.worker --cancel run-<32-hex>
```

Worker odczytuje dokładny release zapisany w runie; nie odczytuje ponownie
aktualnego aliasu. Supervisor odnawia lease co przypięty heartbeat, ogranicza
czas do pozostałego timeoutu próby i maksymalnie 120 s, CPU do 60 s,
RSS do 1024 MiB oraz IO. Child ma izolowany interpreter, pojedynczy wątek
numeryczny i nie dziedziczy poświadczeń ani lease. Weryfikuje faktyczny lock
pakietu, Registry, kwalifikację, config/signature i dokładnie ładowane bajty.
Loader ocenia historię, temporalny cutoff, freshness i pokrycie całego wejścia.
RF/HGB przetwarzają wejścia w chunkach do 256 wierszy, bez refit.

Wynik obliczenia ma purpose `qualified_forecast_computation`; sam nie jest
opublikowanym artefaktem. Preflight pozostaje `runtime_preflight_only` i nie
może zakończyć runu sukcesem. Błąd childa zamyka próbę i podlega bounded retry.
Utrata lease nie pozwala staremu workerowi zapisać błędu nowej próby ani wyniku.

## Jedna transakcja publikacji

Migracja `0012_forecast_outputs` dodaje trzy tabele:

- `forecast_output_manifests`: niezmienny kompletny manifest, po jednym na run;
- `forecast_output_partitions`: niezmienne, numerowane partycje do 256 wierszy;
- `forecast_output_heads`: ostatni udany wynik dla środowiska, dokładnego scope i horyzontu.

Publikacja ponownie sprawdza token, stan, lease, timeout próby i całego runu
pod blokadą wiersza, z czasem bazy. Waliduje skończone nieujemne wartości,
pełny count i unikalny, uporządkowany grain, kanał, origin i target dates.
Każda partycja ma receipt SHA-256/count; manifest ma content identity,
checksum wszystkich predykcji, pełne piny modelu/danych, czas i peak RSS.
Aplikacyjny JSON partycji i manifestu ma limit 256 KiB; JSONB w bazie 512 KiB.

W tej samej transakcji powstają wszystkie partycje, manifest, `succeeded`,
historia próby i pointer. Lease jest sprawdzany ponownie po ostatnim insercie.
SQL blokuje sukces po terminie; deferred constraints wymagają kompletnego
grain, zgodnych receipts, sukcesu i historii przy commit. Update/delete
manifestu i partycji są zabronione. Surowy insert samego manifestu nie commitnie.

Czytelnik innego połączenia nie widzi stagingu. Błąd w dowolnej partycji
cofa całą publikację i zachowuje poprzedni udany pointer. Retry zachowuje
piny, dostaje nowy token i publikuje najwyżej jeden artefakt na run.
Dwa równoległe zakończenia nie dublują danych. Starszy origin, a przy tym
samym origin starsze żądanie, nie zastępuje nowszego udanego wyniku.
Read API z paginacją i oceną freshness należy do AI 05.7.

## Odbiór i dalsza praca

```bash
make forecast-publication-smoke
python scripts/check_forecast_queue.py --report reports/forecast-queue-regression.json
```

Pierwsza komenda buduje zainstalowany pakiet w prywatnym Dockerze i tworzy
świeżą bazę bez portów hosta. Jawne synthetic inputs i SQL-only stub release
sprawdzają transakcje; nie tworzą model version ani promocji w rzeczywistym
MLflow. Rzeczywisty numeric child odrzuca brak dowodów Registry. Kontroler
porównuje pełny stan po SIGKILL/restart i usuwa swój projekt/wolumen.
Bramka jest wymagana przez Required CI; zdalny CI brancha pozostaje nieodebrany.

Pełny odbiór na modelu wymaga spójnego i zakwalifikowanego handoff AI 04,
importu, review/promocji oraz pomiaru rzeczywistego batchu na jego release.
Następny niezależny zakres to AI 05.7: scoped forecast/model/evaluation read
API, paginacja i freshness. Zdarzenia/outbox pozostają w AI 10. Backup pełnego
schematu `ai` obejmuje nowe tabele; downgrade wymaga
[backup/restore](lifecycle-backup.md), bez usuwania historii.
