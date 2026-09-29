# Forecasting 04.3 — cechy, etykiety i podział danych

Ten zakres zamraża wejście do wspólnego evaluatora i modeli. Wytwarza formalny
feature set, osobny zbiór etykiet, split z pełnym raportem pokrycia i dopasowany
preprocessing jednego folda. Nie trenuje ani nie dopuszcza modelu do serving.

## Co jest wersjonowane

| Artefakt | Co określa |
|---|---|
| `feature_set_id`, prefiks `features-sha256-` | Source/snapshot/curated/calendar/input IDs, rzeczywista treść cech i historii, allowlist/typy, polityka i kod/lock/runtime |
| `label_dataset_id`, prefiks `labels-sha256-` | Osobne outcomes, wersje i dostępność, cutoffy ról, parenty, polityka oraz kod |
| `split_id`, prefiks `split-sha256-` | Rzeczywiste okna/cutoffy, feature/label IDs, wszystkie membership keys, eligibility, powody wykluczenia i liczebności |
| `preprocessing_id` | Jeden fold, wyłącznie jego eligible train, hash użytych wierszy, recipe, dopasowane parametry i piny |

Manifesty mają schema 1.0.0. Używają wspólnych content-ID prefixes kontraktów
fundamentu, z rozszerzonymi schematami w `contracts/forecast/v1`. Nie zmieniają
minimalnych przykładów wire z AI 01. ID obejmuje canonical descriptor bez
własnego ID, ścieżek i generated time; checksumy plików są osobne.
Zmiana allowlisty, polityki, parenta, kodu, treści albo splitu zmienia właściwe
ID. Powtórzenie zachowuje identyfikator i wcześniejsze bajty publikacji.

Feature set zawiera przypięte typed [inputs 04.2](forecast-features.md)
w podfolderze `inputs/`, wraz z historią i calendar manifest. Builder tworzy
je z curated przez rzeczywisty builder 04.2; nie nadaje formalnego ID
dowolnej dostarczonej macierzy. Labels są poza macierzą cech. Split zawiera
`labels/`, `memberships/` i `split_manifest.json`, bez kopiowania truth.

## Polityka cech i kwalifikacji

[Zamrożona konfiguracja](../contracts/forecast/v1/features.default.json)
obejmuje 36 cech z 04.2, ich typy i następujące reguły:

- minimum 28 aktywnych dni historii oraz 7 znanych observations;
- ostatnia znana observation najdalej 3 dni kalendarzowe przed origin;
- missing pozostaje nullable, także brak laga 1 wynikający z opóźnienia źródła;
- cold start daje `insufficient_data`, bez domyślnego baseline lub prognozy zero;
- closed oraz nieznany target calendar nie dają scoring eligibility;
- inventory i simulation truth są wyłączone;
- preprocessing dopasowuje parametry tylko na eligible train jednego folda.

Każdy aktywny klucz pozostaje w membership, także gdy jest purged, cold,
closed, stale lub ma censored label. Lista powodów jest wspólna dla modeli.
Missing label nie staje się targetem zero. W tym zakresie eligibility jest
już połączeniem historii, freshness, kalendarza i dojrzałej etykiety.
Model-quality gates, krytyczne segmenty i minimalne próby jakości należą
do dalszego evaluatora, a nie do prostego minimum wykonania splitu.

Dowolna stale history w przypisanej roli daje `qualification_status=not_ready`
i blokuje fitting całego splitu. Nie publikujemy częściowego preprocessingu
jako sukcesu. Brak odpowiedniej liczby eligible rows w którejkolwiek roli
również pozostawia split `not_ready`. Raport/artefakt zachowujemy do diagnozy;
CLI zwraca 3. Nie jest to udany run modelu.

## Okna czasowe i dostępność etykiet

[Konfiguracja temporal smoke](../contracts/forecast/v1/split.temporal.default.json)
jest zamrożona przed oceną jakości. Domyślny plan dla 60 originów:

| Rola | Daty originów | Granica dostępności etykiet |
|---|---|---|
| Train | 2026-05-19–2026-05-28 | training cutoff: 2026-06-12 23:59:59 UTC |
| Purge | 2026-05-29–2026-06-12 | Bez fittingu i scoringu |
| Validation | 2026-06-13–2026-06-22 | selection cutoff: 2026-07-07 23:59:59 UTC |
| Purge | 2026-06-23–2026-07-07 | Bez fittingu i scoringu |
| Development holdout | 2026-07-08–2026-07-17 | evaluation cutoff: 2026-08-01 23:59:59 UTC |

Purge wynosi 15 dni, uwzględniając horyzont 14 dni i źródłową dostępność
observations następnego dnia. Mniejszy gap nie zwalnia z kontroli każdej
etykiety. Cutoff treningu musi poprzedzać pierwszy origin walidacji, a cutoff
doboru — pierwszy origin holdoutu. Nie ma portfolio final test w tym planie.

Dla każdej roli wybieramy najwyższą quantity version znaną przed jej cutoffem,
z `daily_demand_versions`. Późniejsza correction nie zastępuje wcześniejszej
etykiety treningowej. Znana niekompletna wersja daje censored, bez cofania się
do starszej kompletnej wersji. Znana complete observation musi być dostępna
nie wcześniej niż koniec target day i nie później niż cutoff roli.
Zeros i closed zeros są dojrzałymi observations, ale closed target pozostaje
poza scoringiem zgodnie z kalendarzem origin.

Split config może zawierać do 10 foldów z jawnymi oknami i cutoffami.
Każdy ma własny fit. Domyślna konfiguracja to jeden development fold;
pełny expanding/rolling backtest i rzeczywiste porównania należą do 04.6.
Plan budowany bez configu rezerwuje ostatnie 10 originów na development holdout,
poprzednie 10 na validation i dwa purge po 15 dni; wymaga minimum 51 originów.
Dwudniowy `ai-smoke` zalicza feature contracts, ale split pozostaje `not_ready`.

## Preprocessing jednego folda

Fitting korzysta wyłącznie z eligible train memberships ze zweryfikowanego
splitu i przypiętego feature setu. Validation, holdout i purged nie uczą żadnych
parametrów. Etykiety zapewniają kwalifikację; ich ilości nie są wejściem
do transformacji cech.

- Numeric missing: mediana z train oraz osobny missing indicator.
- Boolean missing: moda z train; remis daje false, z missing indicator.
- Kolumna całkowicie missing w train: jawna stała 0 i missing indicator.
  To imputacja model input, nie potwierdzenie sprzedaży zero w danych źródłowych.
- Kategorie: posortowane vocabulary tylko z train, one-hot i osobne kolumny
  missing/unknown. Nowa kategoria z walidacji nie rozszerza vocabulary.
- Bez skalowania. Wyjście jest float64; dokładne raw money/currency pozostaje
  zachowane w feature set, bez konwersji FX.

Zapisany JSON zawiera pełne recipe, statystyki, vocabulary, output columns,
train row hash/count i parent IDs. `preprocessing-verify` odtwarza fitting
z przypiętych danych i sprawdza parametry, więc ponowne wyliczenie hasha
zmienionej mediany nie przechodzi. Sam odczyt/transformacja tego JSON nie jest
review ani zgodą na model serving. Limity fittingu: 50 tys. train rows,
128 MiB canonical train content, 1000 kategorii/cechę i 4096 output columns.

## Polecenia

Po [calendar-build 04.1](forecasting.md):

```bash
uv run --locked --extra snapshot retailops-ai-forecast features-build \
  --curated-dir data/generated/curated/<curated_dataset_id> \
  --calendar data/generated/forecast-calendars/<calendar_id>.json \
  --config contracts/forecast/v1/features.default.json
uv run --locked --extra snapshot retailops-ai-forecast features-verify \
  --feature-dir data/generated/feature-sets/<feature_set_id>
uv run --locked --extra snapshot retailops-ai-forecast split-build \
  --curated-dir data/generated/curated/<curated_dataset_id> \
  --feature-dir data/generated/feature-sets/<feature_set_id> \
  --config contracts/forecast/v1/split.temporal.default.json
uv run --locked --extra snapshot retailops-ai-forecast split-verify \
  --feature-dir data/generated/feature-sets/<feature_set_id> \
  --split-dir data/generated/forecast-splits/<split_id>
uv run --locked --extra snapshot retailops-ai-forecast preprocessing-fit \
  --feature-dir data/generated/feature-sets/<feature_set_id> \
  --split-dir data/generated/forecast-splits/<split_id> --fold development-v1
uv run --locked --extra snapshot retailops-ai-forecast preprocessing-verify \
  --feature-dir data/generated/feature-sets/<feature_set_id> \
  --split-dir data/generated/forecast-splits/<split_id> \
  --preprocessing-dir data/generated/forecast-preprocessing/<preprocessing_id>
make forecast-manifests-check
```

Config dat temporal smoke nie pasuje do dowolnego innego calendar range;
należy jawnie zamrozić własny config lub użyć opisanej wersjonowanej reguły
domyślnej przed oceną. Wszystkie komendy są offline, bez AWS i mutation API.

Weryfikacja sprawdza schematy, fizyczne i logiczne hashe, rodziców, unikalność,
cutoffy, pełne memberships i przelicza qualification/counts z historii
oraz osobnych etykiet. Odczyt inputs sprawdza pliki także przed i po iteracji.
Same hashe nie są podpisem ani niezależnym potwierdzeniem prawdziwości źródła.
Budowa korzysta ze zweryfikowanego curated/source history; nie ma DB RetailOps.

Typed label/membership tables mają bounded writes i dyskowy indeks:
256 rows/16 MiB buffer, 64 MiB Arrow batch, 1 MiB logical row,
10 mln rows/table, 2 GiB logical/physical output łącznie i 10 tys. plików.
Smoke nie kwalifikuje większych profili pod względem zasobów.

[Odbiór 04.3](evidence/04-03-manifests.md).
[Evaluator 04.4](forecast-baselines.md) korzysta z tych manifestów i wspólnych kluczy.
[Modele 04.5](forecast-models.md) zapisują pełne train-only pipeline'y RF/HGB.
[Backtesting 04.6](forecast-backtesting.md) wersjonuje chronologiczny plan foldów.
Kolejny zakres: **04.7 — metryki, niepewność i quality gates**.
