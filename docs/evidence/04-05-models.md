# Odbiór AI 04.5 — modele RF i HGB

Data: 2026-09-29. Lokalny branch `ai/04-01-task-calendar`, baza main `9eb873a`.
[Protokół](../forecast-models.md) i
[zamrożona konfiguracja](../../contracts/forecast/v1/models.default.json)
opisują trening direct z horizon feature, train-only preprocessing,
wspólne eligible keys i diagnostyczny wybór na validation.

Odbiór korzysta z istniejących immutable rodziców AI 04.3, bez przebudowy
danych źródła: `ai-temporal-smoke`, seed 42, 102 dni, 60 originów, 14 dni
ogona etykiet. Nowy lockfile dodaje zależności ML; nowe model IDs wiążą ten
lock, a feature/split IDs zachowują oryginalne pochodzenie AI 04.3.

```bash
.venv/bin/python scripts/check_forecast_models.py \
  --feature-dir data/generated/feature-sets/features-sha256-e7992553d0d3b67f00b5f16e8e0ace55bb88fc3f3d023135bfcd70538e0fdbaf \
  --split-dir data/generated/forecast-splits/split-sha256-5e2eebec14ff8a4e6769a3fb5387b1d6fbc83d873168d3300263b9dd01a3584d \
  --output-root data/generated/forecast-models \
  --output docs/evidence/04-05-temporal.json
```

Trening ma 2710 eligible train; walidacja 3040, development holdout 2968.
Każda z pięciu metod zachowuje 20 076 memberships; całe porównanie ma
100 380 rekordów predykcji. Train cutoff: 12 czerwca, selection cutoff:
7 lipca, evaluation cutoff: 1 sierpnia 2026, zawsze EOD UTC.
Train predictions ML są jawnie in-sample; adapter odmawia użycia modelu
do prognozowania przed granicą wiedzy treningu.

[Raport temporalny](04-05-temporal.json) podaje wszystkie piny, receipts,
coverage, wybór i metryki. Comparison ID:
`forecast-model-comparison-sha256-b4dc6946a69971c7c8130bf400ed779eaa6b44b410d6ba7df70300d9f521fcab`.
Predictions SHA-256:
`7fe3f41402b67ee96ed9cc5d170e783728fc2fa5a079d3ffe5f74de92d38671d`.
Plik predykcji ma 53 276 244 B. Odbiór trwał **433,010 s**: świeży trening
i porównanie, pełny niezależny retraining/replay oraz immutable rerun.
Wszystkie model IDs, descriptor i prediction receipts były identyczne;
oryginalne bajty oraz checksums obu rodziców pozostały bez zmian.

| Metoda | Validation MAE | Validation WAPE | Development holdout MAE | Development holdout WAPE |
|---|---:|---:|---:|---:|
| Last observed | 2,825329 | 0,314305 | 2,298854 | 0,249224 |
| Moving average 7 | 1,712281 | 0,190483 | 1,736130 | 0,188218 |
| Seasonal naive7 | 1,512171 | 0,168222 | 1,563342 | 0,169485 |
| Random Forest | 1,411326 | 0,157003 | 1,531306 | 0,166012 |
| HistGradientBoosting | **1,410937** | **0,156960** | 1,528749 | 0,165735 |

Diagnostyczny wybór **HGB** wynika wyłącznie z validation: poprawa MAE
wobec seasonal naive7 wynosi **6,6946%**, ponad zamrożone minimum 5%.
Na development holdout poprawa wynosi **2,2128%**. Wynik nie kwalifikuje
wdrożenia ani komercyjnej jakości. Holdout nie zmienił parametrów lub wyboru.
[Kontrola ciągłości baseline'ów](04-05-baseline-continuity.json) potwierdza
identyczne bajty wszystkich 60 228 rekordów baseline'ów względem 04.4:
SHA-256 `b9ae5e0f0d61d9076ec328e3b34244150056321680faeb6a07d369d2522cd043`.

Pierwsze dopasowanie RF: 2,830 s wall, 2,161 s CPU, peak RSS 287 358 976 B;
HGB: 1,847 s wall, 1,714 s CPU, peak RSS 199 311 360 B. Oba procesy mają
jeden wątek i mieszczą się w limitach. Pipeline JSON: RF 3 082 952 B,
HGB 541 404 B, z pełnym preprocessingiem i wszystkimi drzewami.

Pełna regresja: **876 passed / 537,58 s**. Kontrola modeli, baseline'ów
i kontraktu CI: **41 passed / 33,64 s**. Testy obejmują natywną zgodność
portable drzew na niewidzianych wartościach/zerach, float32 RF, odrzucenie
cykli, limity czasu/CPU/RAM/macierz, brak losowego early stopping HGB,
zachowanie baseline'u przy remisie lub zbyt małej poprawie, wspólne klucze,
odmowę prognoz przed training cutoff oraz rehashed model forgery.
Spy treningu sprawdza dokładny target train przy każdym dopasowaniu;
validation i development holdout nie są używane do treningu.

Ruff/format: 235 plików; mypy strict: 142 pliki. Handoff, importer, curated,
calendar, features, manifests i baselines smoke przechodzą. Nowy
[component gate](04-05-components.json) sprawdza oba ograniczone procesy fit,
eksport/JSON i równość predykcji; jawnie pozostawia temporal qualification
`not_ready` dla samej macierzy komponentowej. Wszystkie contracts-check,
lokalne linki, wheel/sdist i Compose config przechodzą. Gitleaks git/dir
nie wykrywa sekretów; reguły skanera nie były zmieniane.

[Odłączony wheel](04-05-wheel.json) sprawdzono przez Python `-I` z
`/private/tmp`, po wypakowaniu pakietu i skopiowaniu immutable rodziców
poza checkout. Implementacja, w tym podproces treningu, pochodziła z wheel.
Pięć schemas i konfiguracja domyślna są w pakiecie. Świeży trening,
wspólny evaluator i niezależny retraining/replay mają **dokładnie ten sam
comparison ID, descriptor i predictions receipt** co checkout.
Wheel SHA-256:
`3f7976e0ee8f0064addbf72f40fdee7bba4f5f0f2658e668668992d28e3fbcd2`.

Pozostają AI 04.6–04.8: wielofoldowe backtesty, segmenty/bias/uncertainty
i quality gates oraz lifecycle/handoff. Nie ma wywołań AWS, zmian DB/API,
aktywacji modelu ani otwarcia portfolio final test. Zakres nie zastępuje
dotychczasowej decyzji odrzucającej RF w RetailOps. Commity pozostają lokalne;
zdalny Required CI nie został uruchomiony.
