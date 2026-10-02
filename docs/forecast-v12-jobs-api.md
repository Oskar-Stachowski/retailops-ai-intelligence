# AI 05 — zadania v12 przez API

API korzysta z tej samej trwałej [kolejki v12](forecast-v12-worker.md)
co prywatny CLI. Przyjęcie żądania zapisuje zadanie w PostgreSQL.
Osobny worker wykonuje obliczenia; handler HTTP nie ładuje modelu,
nie trenuje go i nie generuje danych.

## Operacje i uprawnienia

| Operacja | Warunek |
|---|---|
| `POST /api/v1/forecast-runs/v12` | Bearer, rola `pipeline`, capability `forecast:run`, cały scope |
| `GET /api/v1/forecast-runs/v12/{run_id}` | `forecast:read` albo właściciel z `forecast:run`, cały scope |
| `GET /api/v1/forecast-runs/v12/{run_id}/attempts` | Takie same warunki jak odczyt zadania |

POST przyjmuje istniejący kontrakt `BatchRequest`: wcześniej zarejestrowany
`profile_id`, `as_of` zamykające dzień UTC, horyzonty 7/14, ograniczony zakres
produktów/lokalizacji, kanał i jedyny dozwolony alias `champion`.
Nie przyjmuje ścieżek, URL, model version ani tożsamości/roli od klienta.
Puste filtry wybierają cały dozwolony zakres użytkownika, w limitach kolejki.
Nieznane pola JSON oraz parametry query są odrzucane.

`Idempotency-Key` jest wymagany i nie może być powtórzonym nagłówkiem.
Identyczny klucz/żądanie tego samego principal zwraca istniejący run;
zmienione żądanie daje 409. Kolejki v1 i v12 są oddzielne. CLI v12 oraz
API v12 korzystają z tego samego rejestru idempotencji.

Udany POST daje **202** i `Location` wskazujący trwałe zadanie v12.
Nie rozpoczyna pracy w pamięci procesu HTTP. Brak DB lub niezgodna migracja
dają 503; brak dopuszczonego release’u/pending decision blokuje przyjęcie.
Nieistniejące zadanie i zadanie poza scope dają identyczne 404.
Viewer nie może zlecić obliczeń. Zmiana aliasu po przyjęciu nie przepina runu.

## Obliczenia i publikacja

Odpowiedź `V12JobRun` zachowuje status obliczeń, numer próby, bezpieczne
błędy, lineage wejścia oraz immutable model/version, release i image pins.
Nie zawiera pełnego bindingu, URI MLflow, ścieżek pakietów ani raportów operatora.

| `publication_status` | Znaczenie |
|---|---|
| `not_computed` | Run jest queued/running/failed/cancelled; brak kompletnego receipt |
| `awaiting_publication` | Obliczenia succeeded; `computation_receipt_id` istnieje, `output_ref=null` |
| `published` | Cały output i receipt zweryfikowano; `output_ref` wskazuje prognozy |

Publikacja pozostaje osobną prywatną operacją opisaną w
[runbooku publikacji](forecast-v12-publication.md). API przyjmowania zadań
nie promuje modelu ani nie publikuje prognoz. Odczyt zadań nie potrzebuje
działającego MLflow; pokazuje zapisane piny, bez deklaracji aktualnego runtime.
Wygaśnięcie dopuszczenia nie usuwa historycznego odczytu.

Referencja outputu powstaje dopiero po weryfikacji całego zapisanego dokumentu,
receipt, przypiętych danych i source freshness. Uszkodzenie również niewidocznego
wiersza daje 503 bez częściowej odpowiedzi. Budżet output+receipt to 16 MiB
przed pobraniem JSON, timeout zapytań publikacji 3 s.
Historia prób jest uporządkowanym envelope z maksymalnie 5 zakończonymi
próbami, paginacją i czasem odczytu. Bieżące queued/running pokazuje osobny GET.
Piny każdej próby muszą odpowiadać niezmiennemu wejściu i release’owi runu.

## Komendy odbioru

W tym zakresie wykonano:

```bash
.venv/bin/python -m pytest -q tests/test_v12_jobs_api.py
.venv/bin/python -m pytest -q tests/test_v12_jobs_api.py tests/test_access.py tests/test_forecast_jobs.py tests/test_v12_publication.py tests/test_v12_runtime.py tests/test_http.py
.venv/bin/python scripts/update_access_contracts.py --check
.venv/bin/python scripts/update_v12_batch_contracts.py --check
.venv/bin/python scripts/check_v12_publication.py
```

Odbiór SQL sprawdza HTTP 202/Location, powtórzenie i konflikt klucza,
obcy scope, oddzielny namespace testowy, retry zachowujący model,
historię failed/succeeded, oczekiwanie na publikację i późniejszą referencję
kompletnego outputu, odmowę po uszkodzeniu oraz odczyt po restarcie.
Używa prawdziwych, jednorazowych PostgreSQL/MLflow i aplikacji ASGI przez
TestClient. Nie jest odbiorem osobnego wdrożonego serwera TCP HTTP.
Export/source/predictor/review pozostają jawnymi małymi doubles.

[Evidence](evidence/05-v12-jobs-api.json) opisuje wynik i ograniczenia.
Wbudowana konfiguracja API wybiera namespace `retailops-demand-forecast-v12`.
Namespace mechanics wymaga jawnego wstrzyknięcia backendu w testach;
samo `APP_ENV=test` go nie włącza.
Migracja publikacji to `0017_v12_outputs`; `0018_v12_evaluations`
opisuje [katalog i oceny v12](forecast-v12-metadata.md), a aktualny head
`0019_v12_development` [lokalne dopuszczenie](forecast-v12-development.md).
Dotychczasowego długotrwałego stosu nie migrowano.
Dotychczasowe 17 ścieżek OpenAPI i ich 60 definicji schema pozostały identyczne.
Job `persistence` Required CI uruchamia teraz `make v12-backup-smoke`,
który obejmuje registry, kolejkę/API, publikację/odczyt, katalog/oceny,
[wspólną kopię i odtworzenie](forecast-v12-backup.md) oraz restart.
Runner używa już zbudowanego obrazu MLflow z przypiętym sterownikiem
PostgreSQL i sprawdza jego wersje. Nie wykonuje build/pull.
Zmiana workflow jest przygotowana lokalnie; zdalny wynik pozostaje nieodebrany.

[Katalog i oceny v12](forecast-v12-metadata.md) są podłączone osobno.
Do ukończenia AI 05 pozostają zdalny Required CI, rzeczywisty handoff
i pełny odbiór serving.
