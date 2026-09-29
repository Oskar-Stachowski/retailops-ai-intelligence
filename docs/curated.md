# Curated 1.0.0 — budowa i odczyt historyczny

Repo AI przyjmuje już zweryfikowany [import source](source-snapshot-import.md).
Osobne CLI `retailops-ai-curated` nie uruchamia API, generatora, bazy ani AWS.
Wymaga tego samego extra `snapshot`; nie zmienia CLI używanego przez AI 12.

```bash
uv sync --locked --extra snapshot
uv run --locked --extra snapshot retailops-ai-curated build \
  --import-dir data/generated/snapshots/<source_dataset_id> \
  --generated-root data/generated
uv run --locked --extra snapshot retailops-ai-curated verify \
  --curated-dir data/generated/curated/<curated_dataset_id>
uv run --locked --extra snapshot retailops-ai-curated as-of \
  --curated-dir data/generated/curated/<curated_dataset_id> \
  --origin 2026-07-31T23:59:59Z --business-date 2026-07-15
make curated-check
```

`as-of` domyślnie czyta wyłącznie `daily_demand_versions`. Dla planów należy
podać `--table price_plans` (lub promotion/assortment/assignment/routes) oraz
`--business-date`. Przyszły okres planu jest dopuszczalny, jeśli użyta wersja
była dostępna w origin. CLI ogranicza wynik do 1000 wierszy, maksymalnie 10000
przez `--limit`; przekroczenie zwraca exit 2, bez częściowego wyniku.
Biblioteczny `rows_as_of` jest iteratorem z sortowaniem na dysku.

## Układ i publikacja

```text
data/generated/
  snapshots/<source_id>/             # niezmieniony import źródła
  curated/<curated_id>/              # wyłącznie zbiór bez odrzuceń
    curated_manifest.json
    manifest.sha256
    curated/<table>/part-000000.parquet
    quarantine/part-000000.parquet   # jawny pusty zbiór
  curated-rejected/<curated_id>/     # diagnostyka, nigdy ready input
  .curated-build-*/                  # prywatny staging, nie gotowy ID
```

Builder ponownie weryfikuje import, tworzy prywatną, sealed kopię wejścia,
weryfikuje jej typy/hash/gates, a potem przetwarza tylko 25 tabel faktów/planów.
SQLite utrzymuje indeks relacji i sortowanie, Parquet jest odczytywany/zapisywany
porcjami. Wszystkie zaakceptowane i odrzucone wiersze muszą uzgodnić source counts.
Gotowy payload jest przeliczany i walidowany przed fsync/no-replace rename.
Identyczny re-build weryfikuje istniejący zbiór i zachowuje jego bajty;
pusty, uszkodzony lub sprzeczny destination nie jest nadpisywany.

Każde odrzucenie blokuje `forecast_source=passed`. Diagnostic bundle trafia
wyłącznie do `curated-rejected`, z `forecast_source=failed`; build CLI zwraca 2.
Zwykłe verify/as-of odmawia użycia takiego zbioru. Biblioteczne
`verify_curated(require_ready=False)` służy tylko do przeglądu diagnostyki.

## Normalizacja, mapping i kwarantanna

Kontrakt source 2.6 ma już typed Arrow: date32, microsecond UTC, int64 quantity,
decimal128(38,2) money. Curated zachowuje te typy i wszystkie wersje. Tekst
normalizuje do NFC; jawne słowniki normalizują kanały, scope, statusy, jednostki,
country/currency. V1 obsługuje business UTC, PL/DE, saleable `pcs`, pack `g/ml/pcs`,
waluty EUR/PLN. `--currency` definiuje pełną allowlistę walut i zmienia identity.
Nie wykonuje FX ani mnożenia liczby sprzedanych sztuk przez zawartość opakowania.
Nie zaokrągla ilości ani nie zamienia null w zero.

Product ID uzgadnia products i product_catalog, SKU nie może mieć whitespace.
Selling/channel mapping pochodzi z channel_assignments; physical stock location
z fulfillment_routes. Wymagane assortment/lifecycle oraz currency/unit muszą
pasować. Przedziały są półotwarte `[effective_from,effective_to)`; wybór respektuje
`available_at` i wersję. Brak, niejednoznaczność lub jeszcze niedostępny mapping
nie otrzymuje zastępczego warehouse/store. Zwroty zachowują selling grain i trasę
z dnia zakupu, także poza history end w zadeklarowanym return tail.

Daily `unknown/missing`, null quantity, niekompletne źródło, niewłaściwy status,
ujemna ilość, luka wersji lub regresja availability trafiają do kwarantanny.
Jawne zero i closed pozostają odrębnymi stanami. Closed history ma
`scoring_eligible=false`. Inactive exclusions są osobną tabelą, bez rozbudowy
panelu o niedozwolone kombinacje. Builder nie tworzy features, labels ani modeli;
minimum historii i warmup scoringu należą do etapu 04.

Każdy zaakceptowany wiersz ma source record SHA, source grain JSON, mapping
reference IDs, jawne mapped IDs, quantity unit, curated business date i
`curated_available_at`. Quarantine zachowuje oryginalny typed row jako JSON,
source table/grain/SHA i reason. Nie trafia do API/logów jako treść błędu.
Nieprawidłowy manifest/plik/typ/hash blokuje cały build przed transformacją.

## Dostępność i as-of

`curated_available_at` jest maksimum źródłowych known/ingested/available times
oraz użytych katalogów/mapowań. Generated time nie zastępuje availability.
Statyczne i legacy rekordy bez źródłowego availability pozostają null ze statusem
`not_recorded`; nie uprawniają do deklaracji historycznej poprawności ich atrybutów.

Odczyt historii wybiera najwyższą wersję dla business date/product/selling/channel
z `curated_available_at <= origin`, bez przyszłych business dates. Nie pobiera
ilości z finalnego `daily_demand_observations`. Korekta dostępna mikrosekundę
po cutoff nie zmienia starego origin. Plan wybierany jest po kluczu, aktywnym
okresie i znanej wersji; sam przyszły effective date nie jest przeciekiem.
Konkurencyjne rekordy najwyższej znanej wersji są błędem `ambiguous_as_of_version`;
odczyt nie wybiera jednego z nich według kolejności plików.

## Identity i ograniczenia

[Schema](../contracts/curated/v1/curated_manifest.schema.json) jest samowystarczalna
i dołączona do wheel. `curated-sha256-<hash descriptor>` obejmuje parent source
i snapshot IDs, source config/watermarks, resolved transform config, exact code
files/schema/lock fingerprints, Python/PyArrow versions oraz typed content,
grain/ranges/counts/rejections każdej tabeli. Runtime paths/timestamps i fizyczne
partycjonowanie nie definiują ID. Pliki mają osobne byte SHA i size.

Kanonizacja `retailops-curated-exact-1.0.0` zachowuje pełną precyzję decimal,
NFC, microsecond UTC, date, null, int i bool. Sortuje multiset rekordów na dysku;
grain sprawdza oddzielnie. Jest odrębna od source 1.6 i foundation wire v1;
nie podstawia foundation unique-key hash pod importer source.

Truth nie ma miejsca w curated ani w as-of. Dla importu zawierającego truth
build wymaga `--allow-evaluation-truth`, ale zachowuje ją wyłącznie w osobnym
parent import, bez kopiowania/joinu/feature access. Uprawnienia katalogów nie
izolują procesów tego samego UID. Inventory pozostaje false; modele, anomaly,
stockout i replay pozostają not_ready.

Limity wejścia/wyjścia biblioteki: 2 GiB, 10000 plików, 20 mln wierszy,
batch 8192 (1–65536). Bufor writer ma dodatkowo 32 MiB encoded rows, Arrow batch
64 MiB, row group 128 MiB. Budget przekroczony podczas zapisu przerywa build.
Handled failure usuwa własny staging. SIGKILL może zostawić ukryty prywatny
katalog; przed ręcznym cleanup właściciel sprawdza, czy nie pracuje jego proces.
Builder nie usuwa parent importów, fixture ani istniejących curated IDs.

`make curated-check` mierzy import → build → rebuild → verify → dwa as-of,
dwukrotnie w świeżych procesach, przy 300 s / 1024 MiB. Nie obejmuje generacji
ani qualification upstream; pełna bramka generator → curated należy do 03.6.
Nie kwalifikuje pełnego ai-dev/ai-training, Linux CI ani skuteczności modeli.

[Karta danych](cards/curated.md), [odbiór 03.5](evidence/03-05-curated.md).
Implementacja zapisu korzysta z [PyArrow Parquet](https://arrow.apache.org/docs/python/generated/pyarrow.parquet.write_table.html),
a wybór wersji z [SQLite window functions](https://www.sqlite.org/windowfunctions.html).
