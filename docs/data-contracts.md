# Kontrakty danych i wykonania v1

**2026-09-28 · wdrożone modele i walidacja offline etapu 01.**
[Rejestr](../contracts/intelligence/v1/registry.json) obsługuje dokładnie `1.0`.
Źródłem schematów jest `src/retailops_ai/data_contracts/`. JSON Schema Draft
2020-12 oraz małe przykłady znajdują się w `contracts/intelligence/v1/`.
[Status](STATUS.md) i [dowody](evidence/01-contracts.md) określają aktualny odbiór.

## Rodziny i tożsamości

| Rodzina CLI | Treść |
|---|---|
| `dataset` | Source/curated/features/labels/split/predictions: identity, klasyfikacja, provenance, watermark, artefakty i deklarowane readiness |
| `feature` | Pełny klucz prognozy i jawna allowlista cech, wartości, źródła i czasy dostępności |
| `label` | Dojrzały outcome observed_sales_units albo censored z null i powodem |
| `split` | Fixed-origin train/validation/optional calibration/test z purge ≥ maksymalny horyzont |
| `model` | Wersja numeryczna MLflow, training run, wejścia, cutoffs, konfiguracja i checksum artefaktu |
| `prediction` | Observed sales, jednostka, pełne lineage, przypięty model/release, świeżość i jawna obecność/nieobecność przedziału |
| `run` | Training/forecast batch, tożsamość wykonania, wejścia i kontrolowany stan/output/error |
| `tool_request` / `tool_result` | Ograniczone, read-only get_demand_forecast; ok/no_data/error |
| `bundle` | Zamknięty graf powyższych manifestów i rekordów, spójność rodziców, treści i stanów |

Content IDs mają rozłączne prefiksy `source/curated/features/labels/split/predictions/model/prediction-sha256-…`.
`run-<32 hex>` jest niezależnym ID wykonania. Nie jest hashem rekordu zawierającego
output ani dataset ID. Nowy run oznacza nowe provenance dla jego wyników;
ponowienie tego samego runa zachowuje przypięte wejścia.

Dataset ID jest SHA-256 kanonicznego descriptoru: rola, klasyfikacja, parent IDs,
requested/resolved config, seed/scenario, kalendarz, wersje, provenance kodu/lockfile
oraz hash logicznych danych. Wszystkie pola parents są jawne, z null dla nieobecnych.
Source należy do RetailOps, produkty pochodne do AI. Dirty code wymaga patch SHA.
Own ID, ścieżka artefaktu, generated_at i checksum bajtów nie wchodzą do descriptoru.

Kanonizacja `retailops-canonical-json-v1`: JSON UTF-8, klucze sortowane, kompaktowe
separatory, bez NaN/Infinity, jawne null, zachowane typy liczb. `1` i `1.0` są
różnymi reprezentacjami. Pieniądze to integer minor units z currency, nie float.
Datetime w rekordach serializuje się do UTC ISO; JSON string dziedziczy swoją
reprezentację. Nowe typy/decimal wymagają jawnej reguły przed dodaniem do kontraktu.
Logiczne wiersze sortuje się po kanonicznym stabilnym kluczu; brak/duplikat klucza
jest błędem. Partycjonowanie i metadata writera mogą zmienić byte checksum,
zachowując logiczną tożsamość. Helper hashowania działa w pamięci dla małych
rekordów; nie jest importerem dużych zbiorów.

Logical hash features/labels pomija ich own set ID; split pomija own split ID.
Prediction pomija own ID, prediction dataset ID, generated_at i freshness.
Dataset predictions używa tego samego payloadu oraz osobnych rodziców model/run.
Model pomija own model ID, ale zachowuje training run i dane.
Dzięki niezależnym run IDs nie powstaje cykl hashów.

## Grain, wiedza i braki

Pierwsza wersja prognozuje `observed_sales_units` dla:
`product_id × selling_location_id × channel × forecast_origin × target_date`.
Kanał to store/online. Origin zamyka dobę UTC **dokładnie 23:59:59**,
`target_date = origin_date + horizon_days`, horyzont 1–14.
Timestampy dopuszczają Z/+00:00 i maksymalnie sześć cyfr ułamka sekundy.
Inna business timezone/DST nie jest obsługiwana w v1.

Cechy: lag_1_units, rolling_mean_7_units, target_weekday,
planned_price_minor_units, planned_promotion. Inventory i truth są wyłączone.
`source_available_at <= forecast_origin`, także w granicy mikrosekundy.
Observed features nie obejmują przyszłej daty. Calendar/known_plan mogą dotyczyć
target date, lecz użyta wersja musi być dostępna w origin. Plany nie zastępują
zrealizowanych przyszłych faktów. Source manifest klasyfikuje osobno facts,
simulation_truth, raw_events i source_operational_output; graf feature/training
dopuszcza tylko facts. Runtime dostępu do truth i izolacja plików będą odbierane
razem z importerem/pipeline, nie są zapewnione samą deklaracją klasyfikacji.

Available feature ma wartość i czas; missing ma null/null i powód.
Label eligible ma nieujemne całkowite units i dostępność po zamknięciu target day.
Censored ma null/null i powód. Zero jest poprawną obserwacją, nie wypełnieniem braku.
Brak przedziału prognozy ma jawny interval_reason. No_data narzędzia ma pustą listę,
missing freshness i nie fabrykuje daty dostępności ani predykcji.

Bundle sprawdza rodziców i role, pełny grain, sumę rows, zbiorcze zakresy dat i
logical hash rekordów feature/label/prediction oraz split. Wymaga zgodnego,
zakończonego training/inference runa i przypiętej wersji modelu. Kontroluje dojrzałość
horyzontów train/selection, dostępność treningowych etykiet i selection przed testem.
Prediction generated_at mieści się w czasie jej runa.
Nie dowodzi, że rzeczywisty algorytm zastosował split ani że metryki zdały politykę.

## Run i tool

Legalne przejścia: queued → running/cancelled; running → succeeded/failed/cancelled.
Succeeded publikuje wyłącznie complete output. Failed/cancelled nie mają output,
a błąd ma stały kod bez dowolnej treści. Identity, attempt, principal, request,
wejścia i model pozostają przypięte; nie można przepisać wyniku terminalnego.
Identyczne ponowienie rekordu jest idempotentne. `transition_run` waliduje parę
rekordów; nie zapisuje ich do DB i nie implementuje retry/queue/atomic publication.

Get_demand_forecast ogranicza zakres do 20 produktów, 5 selling locations,
jednego kanału, targetów origin+1..14 i maksymalnie 50 wyników.
Odpowiedź zachowuje scope, model, source_ref, as_of i freshness, odrzuca duplikaty.
Błędy mają ustalone opisy i retryability. Caller nie przekazuje principal w tool
request. To wire contract, bez executora, uwierzytelnienia, audytu ani agenta.

## Walidacja i zmiana wersji

```bash
uv run --locked retailops-ai contract-check bundle contracts/intelligence/v1/bundle.v1.example.json
make contracts-check
make contracts
make ci-local
```

`contract-check` działa bez dotenv, DB i usług: sukces exit 0, błąd exit 2 ze stałym
`invalid_contract_document`, bez wartości dokumentu/ścieżki/traceback w błędzie.
JSON z duplicate keys, NaN/Infinity i dokument >2 MB jest odrzucany.
Bundle ma limity liczby rekordów i nie służy do przesyłania całego dużego datasetu.

JSON Schema sprawdza kształt/typ/wersję, a model Pydantic także relacje pól,
PIT i hashe. Walidacja samego JSON Schema nie wystarcza do odbioru dokumentu.
Osobny rodzaj bundle jest potrzebny do powiązań między dokumentami.
Walidacja nie odczytuje plików wskazanych w artifact references, nie porównuje
ich byte checksums i nie wykonuje source quality gates. To zakres 02–03.

W v1 obowiązuje dokładna wersja `1.0`, required fields, brak nieznanych pól
i brak coercji string → liczba. Nie ma implicit defaults dla wersji ani migracji.
Nowe pole, zmiana znaczenia/null/typu/grain/PIT/allowlisty lub kanonizacji wymaga
jawnej nowej wersji, równoległych schemas/examples i przeglądu kompatybilności.
Consumer obsługuje wyłącznie wymienione wersje; eksport RetailOps v1 nie jest
automatycznie zgodny z tym kontraktem AI v1. HTTP diagnostyczne OpenAPI pozostaje
osobnym kontraktem; ta zmiana nie dodaje endpointów.

`make contracts` regeneruje snapshots do przeglądu. `contracts-check` porównuje
je z kodem i deterministycznymi przykładami, nie naprawia ich w CI.
Hand-authored `negative-cases.json` nie jest regenerowany. Testy walidują przykłady
także niezależnym jsonschema i wykazują odrzucenie błędów oraz celowego osłabienia
snapshotu. Required CI uruchamia `make bootstrap check` bez path filters.

Przykłady są **syntetycznymi metadanymi**: ścieżki, checksums, commit/model/run
i flagi passed ilustrują wire format. Nie ma wskazanych plików Parquet, treningu
ani prawdziwej bramki gotowości. Inventory=false, stockout/anomaly=not_ready.
Modele, event execution i streaming pozostają planowane.

Źródła implementacyjne: [Pydantic validators](https://docs.pydantic.dev/latest/concepts/validators/),
[strict mode](https://docs.pydantic.dev/latest/concepts/strict_mode/),
[Python JSON](https://docs.python.org/3/library/json.html),
[jsonschema validators](https://python-jsonschema.readthedocs.io/en/stable/api/jsonschema/validators/).

