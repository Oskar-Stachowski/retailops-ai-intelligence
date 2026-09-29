# Odbiór AI 04.4 — baseline'y i evaluator

Data: 2026-09-29. Lokalny branch `ai/04-01-task-calendar`, baza main `9eb873a`.
[Protokół użytkowy](../forecast-baselines.md) oraz
[zamrożona konfiguracja](../../contracts/forecast/v1/baselines.default.json)
opisują fixed-origin, MA7, seasonal naive7, wymagane wspólne coverage i wybór
według validation MAE. Zakres nie obejmuje treningu RF/HGB, końcowych bramek
jakości lub aktywacji modelu.

Odbiór temporalny korzysta ze źródła AI 03, seed 42, profilu
`ai-temporal-smoke`, 102 dni, 28 warmup, 60 originów i 14 dni ogona etykiet.
Nie zmieniano źródła ani rodziców AI 04.3. Wyłączenia pozostają w coverage;
każdy z trzech baseline'ów ma rekord dla każdego membership, w tym purged.

```bash
.venv/bin/python scripts/check_forecast_baselines.py \
  --feature-dir data/generated/feature-sets/features-sha256-e7992553d0d3b67f00b5f16e8e0ace55bb88fc3f3d023135bfcd70538e0fdbaf \
  --split-dir data/generated/forecast-splits/split-sha256-5e2eebec14ff8a4e6769a3fb5387b1d6fbc83d873168d3300263b9dd01a3584d \
  --output-root data/generated/forecast-evaluations \
  --output docs/evidence/04-04-temporal.json
```

[Raport temporalny](04-04-temporal.json) zapisuje pełne receipts, piny,
coverage i metryki. Evaluation ID:
`forecast-evaluation-sha256-848df301d82db5fed75197dc9cf7992da2ba2259955e151dc8aaea4155fceea6`.
Predictions SHA-256: `b9ae5e0f0d61d9076ec328e3b34244150056321680faeb6a07d369d2522cd043`.
Plik ma **60 228 rekordów / 27 358 275 bajtów**: 20 076 memberships × 3 modele,
w tym 10 080 purged. Każdy model przewiduje wszystkie **2710 train,
3040 validation i 2968 development holdout eligible keys**.
Wyłączenia train: 252 insufficient history i 432 closed target; 34 wiersze
mają obie przyczyny, więc nie sumujemy tych kategorii jako rozłącznych.
Validation wyklucza 320 closed targets, holdout — 308.

| Baseline | Validation MAE | Validation WAPE | Development holdout MAE | Development holdout WAPE |
|---|---:|---:|---:|---:|
| Last observed | 2,825329 | 0,314305 | 2,298854 | 0,249224 |
| Moving average 7 | 1,712281 | 0,190483 | 1,736130 | 0,188218 |
| Seasonal naive7 | **1,512171** | **0,168222** | 1,563342 | 0,169485 |

Wybrano **seasonal naive7 wyłącznie na validation**; ustawienia nie były
dostrajane na holdoucie. WAPE jest proporcją. Odbiór trwał **321,162 s** i objął
świeżą budowę predykcji, niezależne przeliczenie z rodziców oraz ponowną budowę
z porównaniem niezmiennych bajtów. Parents checksums pozostały identyczne.
To wynik tego fixture, bez twierdzenia o produkcyjnym progu jakości.

Kontrole testowe
obejmują analityczne wartości baseline'ów, fixed-origin h=8–14, przyszłe
actuals, closed-zero, brakujące daty, tygodniowe cofnięcie przy brakach,
globalny WAPE i null przy zerowym mianowniku, brak częściowych metryk,
remisy wyboru, identyczne klucze, CLI oraz odrzucenie rehashed forged predictions.

Pełna regresja: **866 passed / 579,96 s**. Po doprecyzowaniu nazwy pola hasha
grainu walidacji: **31 passed / 14,79 s** (baseline'y + kontrakt CI).
Ruff: 227 plików sformatowanych, mypy strict: 137 plików, kontrakty i lokalne
linki przechodzą. Handoff, importer, curated, calendar, features i manifests
smoke przechodzą; nowy short-source gate sprawdził **4554** predykcje kandydatów
i zachował `not_ready` dla zbyt krótkiego splitu. Wheel/sdist i Compose config
przechodzą. Gitleaks git/dir nie wykrywa sekretów; skaner nie został osłabiony.

[Odbiór odłączonego wheel](04-04-wheel.json) wykonano z `/private/tmp`,
Python `-I`, po wypakowaniu wheel i skopiowaniu obu immutable rodziców
poza checkout. Weryfikacja potwierdza ładowanie implementacji z pakietu,
trzy nowe schemas i domyślną konfigurację. Świeży evaluator i replay mają
**dokładnie ten sam evaluation ID, descriptor i predictions receipt** co checkout.
Wheel SHA-256: `7fb7c4c31cbf49f32b291553cf553214969dc751702406886bd0749df526fb2a`.

Zakres jest offline: bez wywołań AWS, DB/API, aktywacji lub otwarcia portfolio
final test. Wyniki dotyczą małego temporalnego fixture i poprawności protokołu,
nie jakości produkcyjnej. Pozostały AI 04.5–04.8 oraz dalsze bramki wdrożenia.
Commity pozostają lokalne; zdalny Required CI nie został uruchomiony.
