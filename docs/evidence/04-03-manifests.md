# AI 04.3 — formalne manifests i kwalifikacja

Data: 2026-09-29. Repo: `retailops-ai-intelligence`.
Branch: `ai/04-01-task-calendar`, kontynuacja 04.2 `947de2f`.
Zakres odpowiada punktowi 04.3
[planu](https://github.com/Oskar-Stachowski/retailops-cloud-native-platform/blob/main/docs/plans/ai/etapy/04-forecasting.md).
[Runbook](../forecast-manifests.md) podaje znaczenie konfiguracji i polecenia.

## Wynik funkcjonalny

Formalny feature set wiąże rzeczywistą treść 04.2 z parent dataset,
allowlistą/typami, preprocessing recipe, polityką minimum/freshness/cold start
oraz fingerprintem kodu i lock/runtime. Label dataset jest osobny od features;
wybiera quantity versions według cutoffu train/selection/evaluation.
Split ID obejmuje faktyczne okna, etykiety, wszystkie memberships, qualification
i coverage. Manifest nie tworzy ID tylko z dat/profilu/seeda.

Fixed-origin development protocol ma 15-dniowe purge, train przed validation
oraz validation przed development holdout. Portfolio final test pozostaje
poza tym protokołem. Nie oglądano żadnych model-quality metrics.
Cold/closed/missing/purged są jawne. Stale history lub zbyt mała próbka
blokują fitting; not-ready artifacts zachowują diagnozę i CLI exit 3.

Preprocessing dopasowuje mediany/mody/vocabulary tylko na eligible train
jednego folda. Missing indicators i unknown categories pozostają jawne.
Stan zapisuje pełne parametry, recipe i train content hash. Weryfikacja
ponownie dopasowuje je z pinned inputs/split, zamiast ufać samemu hashowi.
Publikacja feature set/split/state jest atomowa i nie nadpisuje bajtów.

## Odbiór

**850 testów przeszło w 1242,87 s**, w tym 25 przypadków nowego zakresu.
Regresja działała równolegle z odbiorem danych i pozostałymi bramkami.
Ruff/format, strict mypy (133 pliki), wszystkie schemas, docs,
handoff, dwukrotny import/curated, calendar, draft features, wheel/sdist,
Compose config i skany Gitleaks katalogu oraz historii przeszły.
Job checks ma limit 30 minut zamiast 20, uwzględniający rozbudowaną regresję
i nowy obowiązkowy `forecast-manifests-check`. Pozostałe bramki są zachowane.
15 testów kontraktu CI przeszło również po zmianie tego limitu.

[Mały smoke](04-03-smoke.json) utworzył powtarzalny i niezmienny formalny
feature set: 2 originy i 1 518 rows. Poprawnie odmówił kwalifikacji splitu
z powodu zbyt krótkiego kalendarza; nie dopasował preprocessingu.
[Odłączony wheel](04-03-wheel.json), poza checkoutem i w Python isolated mode,
odtworzył te same feature/input IDs. Zawiera wszystkie siedem nowych schemas
oraz obie zamrożone konfiguracje; krótki split również pozostaje not-ready.
[Pełny temporal smoke](04-03-temporal.json) używa zaakceptowanego curated AI 03
z [odbioru 04.2](04-02-temporal.json), bez generowania nowego źródła
i bez zmian w równolegle rozwijanym AI 06. Zweryfikowano pełny parent,
rzeczywistą historię quantity versions i wszystkie 60 originów.

| Część | Wiersze w coverage | Eligible |
|---|---:|---:|
| Train 2026-05-19–2026-05-28 | 3 360 | 2 710 |
| Validation 2026-06-13–2026-06-22 | 3 360 | 3 040 |
| Development holdout 2026-07-08–2026-07-17 | 3 276 | 2 968 |
| Dwa purge po 15 dni | 10 080 | 0 |

Łącznie 20 076 memberships. 252 mają insufficient history, a 1 060 known
closed target w przypisanych rolach; powody mogą się nakładać. Wszystkie
etykiety przypisanych ról są dojrzałe przy ich cutoffach. Nie ma stale history.
Kwalifikacja wykonania splitu przeszła, przy modelu nadal `not_ready`.
Preprocessing dopasowano na 2 710 train rows, do 99 kolumn model input.
Train row hash i wszystkie feature/label/split/preprocessing IDs są w receipt.

Powtórzono pełny build/verify cech, splitu oraz fitting. IDs i wcześniejsze
bajty pozostały identyczne; source/curated/feature inputs nie zmieniły się.
Cały odbiór trwał **1690,751 s**, równolegle z regresją i innymi bramkami.
Ten czas obejmuje wielokrotne pełne weryfikacje oraz dwukrotne budowy/fitting,
nie jest czasem pojedynczego treningu ani benchmarkiem serving.
Nie kwalifikuje pełnego ai-dev/ai-training pod względem zasobów.
AWS calls: 0. Portfolio final test nie znajduje się w tym protokole.

Testy kontrolowanego timeline sprawdzają wszystkie role, mature labels,
microsecond cutoff, late correction, incomplete label, pełne coverage,
train-only preprocessing, cold/stale/censored, changed identity, corrupt
artefacts i zmienione counts/statystyki po ponownym obliczeniu hashy.
Kontrolowany fixture izoluje jedynie upstream source index; rzeczywiste
feature/label/split writers, readers, manifests i fitting pozostają uruchomione.
Odbiór obu rzeczywistych źródeł sprawdza pełny zweryfikowany curated AI 03.

## Wykonane komendy

```bash
.venv/bin/python -m pytest -q
.venv/bin/python scripts/check_forecast_manifests.py --output docs/evidence/04-03-smoke.json
.venv/bin/python scripts/check_forecast_manifests.py \
  --curated-dir data/generated/curated/curated-sha256-aeedd1a49f75eb27b687b328b92e23fee5b8cfa7c2520b2ff5219a8a74bd748a \
  --output docs/evidence/04-03-temporal.json
make lint type-check docs-check handoff-check snapshot-import-check curated-check \
  forecast-calendar-check forecast-features-check contracts-check package compose-config \
  UV=/Users/oskarstachowski/retailops-ai-intelligence/.tools/bin/uv
make secrets GITLEAKS=/opt/homebrew/bin/gitleaks
```

Nową bramkę `forecast-manifests-check` wykonano przez jej skrypt, z jawnymi
ścieżkami do evidence. Wszystkie bramki `make check` i `make secrets` przeszły.
Odłączony wheel był rozpakowany w temp i uruchomiony przez `.venv/bin/python -I`
poza checkoutem; receipt wiąże checksum paczki z identycznymi content IDs.

## Granice i dalsza praca

Nie ma jeszcze wspólnego evaluatora/baselines (04.4), RF/HGB (04.5),
expanding/rolling backtestu (04.6), model-quality gates/intervals (04.7)
ani model lifecycle handoff (04.8). Minimum jednej eligible row/rolę
jest tylko bramką wykonania kontraktu; nie bramką jakości portfolio.
Model pozostaje `not_ready`. Inventory/truth są wyłączone.
Nie uruchamiano AWS ani nowego Compose/persistence; zakres nie zmienia DB.
Nie ma push, PR, zdalnego Required CI ani merge tego zakresu na main.
AI 12 i jego budżet pozostały osobno.

Kolejny zakres: **04.4 — wspólny evaluator i baseline'y**.
