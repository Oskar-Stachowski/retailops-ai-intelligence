# Forecasting — zadanie i kalendarz 04.1

Forecast v1 przewiduje **obserwowaną liczbę sprzedanych jednostek**, nie
nieograniczony popyt. Konfiguracja [task.default.json](../src/retailops_ai/forecasting/task.default.json)
jest wersjonowana i dołączona do pakietu. Techniczna nazwa przyszłego modelu:
`retailops-demand-forecast`. Ten zakres definiuje zadanie i granice danych;
nie trenuje modelu ani nie ocenia jego jakości.

## Jednostka prognozy i daty

Obserwacja: dzień biznesowy × produkt × selling location × kanał.
Prognoza: produkt × selling location × kanał × origin × target date.
Selling location nie jest fizycznym magazynem. Ilość zachowuje jednostkę
sprzedawalnego produktu; brak konwersji zawartości opakowania lub walut.

Kalendarz `forecast-utc-daily-1.0.0` używa UTC i dni kalendarzowych, także
niedziel, świąt i dni zamknięcia. Origin zamyka dzień D dokładnie o
**23:59:59 UTC**, zgodnie z istniejącym forecast wire contract v1.
`target_date = D + horizon_days`, dla wszystkich horyzontów **1–14**.
Przykład: D = 2026-12-31, h=1 → 2027-01-01, h=14 → 2027-01-14.
UTC nie przesuwa się przy europejskiej zmianie czasu.

Okna raportowe obejmują D+1…D+7 oraz D+1…D+14. Nakładające się dni mają
jedną dzienną prognozę: 14 kluczy na serię i origin, nie 21.
Kalendarz sam nie mnoży dat przez asortyment i nie nadaje scoring eligibility.
[Panel i cechy 04.2](forecast-features.md) sprawdzają aktywne kombinacje,
wykorzystują święta/sezony source calendar oraz zachowują zero/missing/closed
i coverage historii. Pełna kwalifikacja oceny należy do dalszych punktów 04.

## Granica dostępności i opóźnienia

Każdy origin ma jeden `availability_cutoff = forecast_origin` oraz
`observed_through_date = D`. Ten sam cutoff obowiązuje dla baseline i
każdego kandydata, we wszystkich 14 horyzontach. To protokół
`fixed_origin_multi_horizon`: sprzedaż poznana w D+1 nie może poprawić
historii prognozy wystawionej w D. Nowy origin jest odrębną prognozą.

Polityka opóźnień jest jawna: `availability_delay_seconds=0`, bez domyślnego
oczekiwania na ingest. Wykorzystujemy tylko wersje z
`curated_available_at <= forecast_origin`. Curated bierze maksimum availability
rekordu i potrzebnych mapowań. Brak availability wyklucza rekord; opóźnione
dane pozostają niedostępne dla starego origin. Nie uzupełniamy ich zerem.

Cutoff ma precyzję sekund, lecz źródłowe timestampy zachowują mikrosekundy.
Rekord dostępny o `23:59:59.000000Z` jest znany, o `23:59:59.000001Z`
już nie. Nie zaokrąglamy ani nie obcinamy availability. Ostatni ułamek
sekundy dnia jest celowo poza granicą wire v1. Dane sprzedaży D, które dotrą
dopiero następnego dnia, mogą pojawić się dopiero w kolejnym origin.
Zmiana godziny, strefy lub opóźnień wymaga nowej polityki/wersji i testów.
W v1 inne strefy niż UTC są odrzucane.

`rows_for_origin` odczytuje historię wyłącznie z `daily_demand_versions`.
Znane plany mogą dotyczyć przyszłej daty, ale także muszą być dostępne w
origin. Reader odrzuca final observations, legacy forecast outputs i truth.
Inventory features są całkowicie wyłączone przed odbiorem 06. Konfiguracja
nie dopuszcza truth features ani operacyjnych wyników jako targetów.

## Polecenia offline

Po [imporcie](source-snapshot-import.md) i [budowie curated](curated.md):

```bash
uv sync --locked --extra snapshot
uv run --locked --extra snapshot retailops-ai-forecast task-check
uv run --locked --extra snapshot retailops-ai-forecast calendar-build \
  --curated-dir data/generated/curated/<curated_dataset_id> \
  --origin-from 2026-07-16 --origin-to 2026-07-17
uv run --locked --extra snapshot retailops-ai-forecast calendar-verify \
  --manifest data/generated/forecast-calendars/<calendar_id>.json \
  --curated-dir data/generated/curated/<curated_dataset_id>
make forecast-calendar-check
```

`--config` pozwala wskazać jawną konfigurację zgodną z v1; nie pozwala
zmieniać jej semantyki bez nowej wersji. Zakres dat jest inkluzywny, maksymalnie
366 originów na manifest. Brak niejawnego „dzisiaj”. Kalendarz może opisywać
przyszłe daty; samo istnienie target date nie dowodzi dostępności etykiety
ani dojrzałości okna ewaluacji. To późniejsze bramki 04.

Manifest zawiera pełną konfigurację, rzeczywiste timestampy cutoffów,
target dates, okna 7/14, zweryfikowane source/snapshot/curated IDs,
hash deskryptora curated i fingerprint kodu. `task_id` zależy od resolved config;
`calendar_id` dodatkowo od rodziców, zakresu dat, kodu i treści kalendarza.
Ścieżki i `generated_at` nie zmieniają content identity. Publikacja jest atomowa,
bez nadpisania istniejącego ID; powtórzenie zachowuje oryginalne bajty i czas.
Weryfikacja sprawdza treść, relacje i zgodność ze zweryfikowanym parent;
hash nie jest podpisem ani zgodą na użycie modelu.

[Schemas](../contracts/forecast/v1/calendar_manifest.schema.json) i walidacja
semantyczna Pydantic są sprawdzane w `make contracts-check`.
`make forecast-calendar-check` przechodzi source fixture → importer → curated
→ powtarzalny kalendarz → history/known plans as-of, bez DB i AWS.
`forecast_model_status` pozostaje **not_ready** do dalszego odbioru modeli.

## Kolejny zakres

[04.3](forecast-manifests.md) ma formalne feature/label/split manifests,
kwalifikację historii/etykiet i train-only preprocessing.
[04.4](forecast-baselines.md) ma wspólny evaluator i trzy baseline'y.
[Modele 04.5](forecast-models.md) dodają RF/HGB na tym samym splicie.
[Backtesting 04.6](forecast-backtesting.md) dodaje chronologiczne foldy.
[Metryki i niepewność 04.7](forecast-quality.md) mają pełne przekroje,
kalibrację i bramki. Kolejny zakres: **04.8 — evidence i lifecycle handoff**.
Dalej: modele, uczciwy backtesting i artefakty.
AI 06 może rozwijać się równolegle w RetailOps; zmienione źródło otrzyma nowe
IDs i będzie wymagało ponownego importu oraz zależnych ocen.

[Dowody 04.1](evidence/04-01-calendar.md),
[plan Etapu 04](https://github.com/Oskar-Stachowski/retailops-cloud-native-platform/blob/main/docs/plans/ai/etapy/04-forecasting.md).
