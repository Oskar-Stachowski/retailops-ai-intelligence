# AI 05.7c — odczyt ocen modeli

`GET /api/v1/evaluations` i `GET /api/v1/evaluations/{evaluation_id}` czytają
niezmienne projekcje historycznych ocen AI 04 z PostgreSQL. Wymagają jawnego
`forecast:read`. [Odbiór](evidence/05-07-evaluations.md) obejmuje rzeczywisty
HTTP/PostgreSQL, restart i archiwalny pakiet, bez treningu ani promocji.

## Uprawnienia i lista

Raport zawiera zbiorcze wyniki wielu jednostek. Cały jego scope musi mieścić
się w grancie użytkownika i żądanym zakresie: wszystkie produkty, selling
locations i kanały. Scope importu pochodzi z memberships ról `validation`
i `development_holdout`, również rekordów wykluczonych/cenzurowanych.
Nie wyznacza go scope opublikowanej prognozy ani deklaracja klienta.

Przykład: ocena produktów A i B nie jest widoczna dla użytkownika mającego
dostęp tylko do A. Filtr `product_id=A` również nie udostępnia zbiorczych
metryk A+B. API nie przelicza części raportu jako nowej oceny.
Brak lub niewidoczny evaluation ID daje wspólne `404 evaluation-not-found`;
pusta lista daje `200/no_data` i `total=0`.

Filtry listy: `product_id`, `selling_location_id`, `channel`, `quality_status`
(`passed`, `failed`, `not_ready`), `limit`, `offset`, `view_sha256`.
Szczegół przyjmuje tylko trzy filtry scope. Nieznane/powtórzone parametry są
odrzucane. Wymagany cały zakres żądania mieści się w server grant; żądana
jednostka spoza niego daje `422 evaluation-scope-invalid`.
Maksimum żądania to 20 produktów × 5 lokalizacji × 2 kanały; większy grant
wymaga zawężenia (`422 evaluation-scope-limit`).

Listy mają `items`, `pagination:{limit,offset,total,next_offset}`, `generated_at`,
`data_status`, `view_sha256`. SQL filtruje środowisko, **cały scope** i status
przed count/limit. `limit` domyślnie wynosi 50, maksimum 200; offset 0–256.
Sortowanie: czas utworzenia oryginalnej oceny malejąco, evaluation ID malejąco.
Hash obejmuje principal, zakres, filtr statusu i hashe wszystkich widocznych
ocen. Dalsza strona wymaga hasha (`409 evaluation-view-required`), zmiana
widoku/grantu/filtra daje `409 evaluation-view-changed`. Zmiana zegara i
oceny całkowicie poza scope nie zmieniają widoku.

## Znaczenie wyniku

Lista zawiera ID oryginalnej oceny jakości, hash projekcji, export run ID,
pełny autoryzowany scope, zapisany status jakości, source/curated/features/
labels/split/backtest IDs, SHA obu repo, oryginalny lock hash oraz czas oceny.
Nie jest to czas importu ani nowego treningu. Szczegół dodaje 14 grup:
2 role × 7 metod (trzy baseline'y, RF, HGB, validation-selected i
validation-baseline). Wybór metody pozostaje oryginalnym wyborem per fold.

Każda grupa podaje total/excluded rows oraz wspólny `MetricResult` AI 04:
liczby eligible/predicted, MAE, WAPE, sumy błędów/actuals i status ważności.
`point.status=passed` oznacza kompletne, obliczalne metryki; nie zatwierdza
jakości ani wdrożenia. WAPE jest ilorazem sum, a nie średnią procentów.
Zerowy mianownik daje null z `zero_denominator`; brakujących prognoz nie
zastępuje zero i nie publikuje częściowych miar jako kompletnych.

`metric_verification=replayed_from_saved_predictions_and_labels` opisuje
odtworzenie MAE/WAPE. `quality_verification=recorded_status_not_requalified`
oddziela oryginalny wynik bramek od nowej kwalifikacji. API nie publikuje
pełnej model card, surowych gates, URI plików, rekordów etykiet ani raportów
spoza scope. Nie nadaje registered version: `registered_model_version=null`
i `serving_eligible=false`. Historyczny export nie reprezentuje release'u.
Freshness pozostaje `unknown/historical_evaluation_runtime_not_observed`;
nie ma potwierdzenia bieżącego runtime, driftu ani ważności dla nowych danych.

## Przygotowanie i zapis

Prywatny importer sprawdza cały pakiet przez `verify_run`, lineage i receipts,
a następnie odtwarza point metrics z zapisanych predykcji i etykiet przy użyciu
`MetricAccumulator` i fold pooling AI 04. Kontroluje wspólne klucze, kompletność
pięciu metod, wybrane strategie, count i scope. Powtórnie sprawdza cały export
po odczycie. Nie trenuje, nie wybiera nowego modelu ani nie przelicza bramek
jakości dla obecnego runtime. Starsze piny pozostają zapisane bez przepisywania.
Inne/niekompletne fold selections wymagają osobnej obsługi i są odrzucane.
Limit odtwarzania to 100000 memberships dwóch ról; większy pakiet jest odrzucany.

W tym zakresie wykonano offline preparation i odbiór na jednorazowej bazie:

```bash
.venv/bin/python scripts/prepare_evaluation.py \
  --run-dir /path/to/forecast-runs/<run_id> \
  --output reports/evaluation-evidence.json
.venv/bin/python scripts/check_evaluations.py \
  --historical-evidence reports/evaluation-evidence.json \
  --report reports/ai05-evaluations-acceptance.json
```

`prepare_evaluation` nie zapisuje do DB. Tworzy nowy plik; nie nadpisuje
istniejącego ani nie dodaje pliku do źródłowego archiwum. Generowane projekcje
pozostają poza Git. Standardowy smoke używa jawnych synthetic fixtures;
parametr historical dodaje przygotowany rzeczywisty raport do jednorazowej bazy.

Standardowy odbiór fixtures w Required CI uruchamia `make evaluations-smoke`.

Prywatna operacja do importu do własnego stosu (nie wykonywano jej na trwałym
stosie podczas tego odbioru):

```bash
.venv/bin/python -m retailops_ai.model_lifecycle.evaluation_cli \
  --run-dir /path/to/forecast-runs/<run_id> \
  --policy-file /private/path/api-access-policy.json \
  --credentials-file /private/path/api-client-credentials.json
```

Wymaga poprawnej konfiguracji DB oraz uwierzytelnionego operatora z rolą
`promoter` i `model:decide`; admin nie dziedziczy tych praw. Weryfikacja
operatora poprzedza import. Nie ma endpointu HTTP do rejestracji ocen.
CLI wyznacza dane ze zweryfikowanego archiwum, nie przyjmuje klientowskiego
JSON scope ani dowolnego URL/model binary. Nie zmienia Registry lub release'u.

## Migracja i odporność

Tabela ocen pochodzi z migracji `0013_forecast_evaluations`; `/ready` i wspólny
DB guard wymagają bieżącego head `0015_v12_lifecycle`. Przy aktualizacji istniejącego stosu uruchom jawne
migracje przed nowym API, zgodnie z [runbookiem Compose](local-stack.md).
Nowa tabela `ai.forecast_evaluations` znajduje się w schemacie objętym
[backupem lifecycle](lifecycle-backup.md).

Zapis jest transakcyjny, z blokadą i limitem 256 ocen na środowisko. Ten sam
ID i identyczny dowód są idempotentne; różny dowód z tym ID wymaga przeglądu
i daje konflikt bez zastąpienia rekordu. Projekcja ma maksimum 64 KiB
canonical JSON; DB ogranicza rozmiar JSONB do 128 KiB. SQL trigger odmawia
UPDATE/DELETE. Synthetic evidence jest dopuszczalne wyłącznie w `APP_ENV=test`.
Odczyt jest `REPEATABLE READ READ ONLY`, z timeoutem SQL 3 s i budżetem
256 raportów. Przekroczenie daje `429 evaluation-read-budget`, uszkodzenie
hasha/metryk/pinów daje bezpieczne `503 evaluation-evidence-invalid`.
Nie ma wywołań MLflow ani Bedrock podczas odczytu.

Kontroler odbioru tworzy własne kontenery/wolumeny bez portów hosta, sprawdza
SIGKILL/restart oraz usuwa własny stos i tag obrazu. `evaluations-smoke`
należy do persistence Required CI. **AI 05 pozostaje otwarte:** potrzebne
są spójny qualified handoff AI 04 z rzeczywistym batchem oraz zdalny Required CI
tego brancha. [Freshness prognoz 05.7d](forecast-freshness.md) ma osobny odbiór.
Outbox/zdarzenia pozostają w AI 10.
