# Baseline'y i evaluator AI 04.4

Evaluator ocenia obserwowaną sprzedaż na zweryfikowanych
[features i split](forecast-manifests.md). Każdy baseline zachowuje wszystkie
klucze membership: train, validation, development holdout oraz purged.
Braki historii, zamknięte/nieznane target days i cenzurowane etykiety pozostają
w coverage z przyczyną. Do metryk trafia dokładnie wspólny zbiór eligible keys.
Nie usuwamy trudnych wierszy osobno dla poszczególnych modeli.

## Zamrożony protokół

[Konfiguracja](../contracts/forecast/v1/baselines.default.json) ma trzy kandydaty:

| Baseline | Prognoza dla każdego target date |
|---|---|
| `last_observed` | Ostatnia kompletna, znana obserwacja z historii origin |
| `moving_average` | Średnia znanych obserwacji z kalendarzowego okna D−6…D |
| `seasonal_naive7` | Ostatnia znana obserwacja tego samego dnia tygodnia, w historii D−27…D |

Historia zamyka się w **23:59:59 UTC dnia D**. Każdy horyzont 1–14 używa
tej samej zamrożonej historii. Dla h=8…14 seasonal naive powtarza odpowiedni
znany dzień tygodnia sprzed origin; nie odczytuje rzeczywistej sprzedaży
z przyszłych h=1…7. „Ten sam dzień tygodnia w poprzednim tygodniu” jest
tym samym baseline'em, a nie czwartym modelem. Protokół nie jest rekurencyjny.

Znane zero i potwierdzony closed-zero należą do historii. Brak nie jest zerem.
Średnia dzieli przez liczbę znanych obserwacji w jawnym oknie kalendarzowym,
domyślnie wymaga co najmniej jednej. D zazwyczaj nie jest jeszcze dostępny,
więc pełna historia często daje sześć znanych dni w oknie siedmiodniowym.
Konfiguracja może jawnie wskazać okno 14/28 oraz większe minimum; zmienia to ID.
Nie wydłużamy okna, aby zastąpić brakujące dni starszymi obserwacjami.
Seasonal naive przy brakującej najnowszej analogicznej dacie szuka wcześniejszego
znanego dnia tego samego tygodnia, najwyżej w 28-dniowej historii.
Bez takiej obserwacji zapisuje `no_known_same_weekday`, bez zastępczej prognozy.

Eligibility, minimum historii i freshness pochodzą ze splitu. Split `not_ready`,
w tym stale history, blokuje wykonanie evaluatora. Brak wymaganej predykcji
pozostawia artefakt `not_ready`: nie ma wyboru baseline'u ani metryki liczonej
na pomniejszonym zbiorze. Pokrycie predykcji wymagane do wyboru wynosi 100%.

## Wybór i metryki

W każdym foldzie wybieramy najniższe **MAE na validation**. Przy dokładnym remisie
obowiązuje kolejność z konfiguracji: last observed, moving average, seasonal naive7.
Okno, minimum, kryterium i tie-break są zapisane przed pomiarem. Trzy modele
nie dopasowują parametrów na train; [RF/HGB](forecast-models.md) używają train-only preprocessing.

Evaluator zapisuje hash wyboru związany z konfiguracją, rodzicami, foldem,
kluczami i metrykami validation, zanim policzy wyniki development holdout.
Holdout służy do raportowania zamrożonego wyboru; nie wolno dostrajać na nim
okna ani kryterium. Portfolio final test nie należy do tego protokołu.
Jeżeli validation nie umożliwia wyboru, metryki holdoutu są odroczone
z `selection_not_ready`; raport zachowuje wyłącznie liczbę dostępnych predykcji.

`MAE = sum(abs(y − yhat)) / liczba_eligible_rows`.
`WAPE = sum(abs(y − yhat)) / sum(abs(y))`, jako proporcja, nie procent.
To globalny iloraz sum, nie średnia błędów procentowych. Wiersze z y=0
wchodzą do licznika. Dla y=[0], yhat=[100] MAE=100, WAPE=null z
`zero_denominator`; brak mianownika nie oznacza idealnego wyniku.
Brak predykcji daje `incomplete` i null dla obu metryk; pusty zbiór daje
`not_evaluable`. Raport podaje eligible/predicted counts i sumy kontrolne.

## Polecenia offline

```bash
uv run --locked --extra snapshot retailops-ai-forecast baselines-evaluate \
  --feature-dir data/generated/feature-sets/<feature_set_id> \
  --split-dir data/generated/forecast-splits/<split_id> \
  --config contracts/forecast/v1/baselines.default.json
uv run --locked --extra snapshot retailops-ai-forecast evaluation-verify \
  --evaluation-dir data/generated/forecast-evaluations/<evaluation_id> \
  --feature-dir data/generated/feature-sets/<feature_set_id> \
  --split-dir data/generated/forecast-splits/<split_id>
make forecast-baselines-check
```

Exit 0 oznacza poprawne wykonanie baseline'ów, exit 3 — zapisany raport
`not_ready`, exit 2 — odrzucone wejście/konfigurację. Żaden z tych wyników
nie nadaje gotowości operacyjnej modelowi. CLI nie wymaga DB/API ani AWS.

Artefakt `forecast-evaluation-sha256-…` zawiera immutable manifest oraz
kanoniczny `predictions.jsonl` z typowanymi rekordami, wyłączeniami i datami
historii wykorzystanej przez baseline. Manifest wiąże features/split/labels,
kod, lockfile, Python/PyArrow, politykę, coverage, wybór i metryki.
ID nie zależy od ścieżki ani `generated_at`. Publikacja jest atomowa,
bez nadpisania; ponowne wykonanie zachowuje pierwotne bajty.
`evaluation-verify` odtwarza predykcje i metryki ze zweryfikowanych rodziców,
więc zmiana wartości i przeliczenie hashy nie wystarczają do pozytywnej weryfikacji.

Odczyt/budowa są strumieniowe, z indeksem SQLite na dysku i ograniczonym cache
historii. Limity tego protokołu: 3 mln rekordów predykcji, 2 GiB pliku predykcji
i osobno 2 GiB indeksu SQLite, 64 KiB na rekord. Rodzice zachowują własne limity.
Pełna weryfikacja rodziców i replay wymagają czasu; nie pomijamy ich dla szybszego CLI.

Krótki CI fixture sprawdza predykcje as-of i odrzucenie zbyt krótkiego splitu.
Nie udaje kwalifikacji temporalnej. Checker z `--feature-dir` i `--split-dir`
wykonuje rzeczywisty evaluator, niezależny replay i immutable rerun.
[Evidence 04.4](evidence/04-04-baselines.md) podaje konkretny temporalny odbiór.

**[AI 04.5 — RandomForestRegressor i HistGradientBoostingRegressor](forecast-models.md)**
korzystają z tego samego splitu i train-only preprocessing. Wielofoldowy backtesting,
pełne przekroje/intervals/quality gates oraz model lifecycle należą do 04.6–04.8.
`forecast_model_status` pozostaje `not_ready`.
