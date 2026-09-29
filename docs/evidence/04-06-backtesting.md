# Odbiór AI 04.6 — chronologiczny backtesting

Data: 2026-09-29. Branch `ai/04-01-task-calendar`, baza main `9eb873a`.
[Protokół](../forecast-backtesting.md) oraz
[zamrożona konfiguracja](../../contracts/forecast/v1/backtest.default.json)
określają expanding folds, purge, cutoffy, train-only fitting i agregację.
Nie zmieniano parametrów RF/HGB, kryterium wyboru ani rodziców AI 04.3.

```bash
.venv/bin/python scripts/check_forecast_backtest.py \
  --feature-dir data/generated/feature-sets/features-sha256-e7992553d0d3b67f00b5f16e8e0ace55bb88fc3f3d023135bfcd70538e0fdbaf \
  --curated-dir data/generated/curated/curated-sha256-aeedd1a49f75eb27b687b328b92e23fee5b8cfa7c2520b2ff5219a8a74bd748a \
  --output-root data/generated/forecast-backtests \
  --output docs/evidence/04-06-temporal.json
```

Temporalny smoke: 102 dni, seed 42, 60 originów, 8 produktów, 3 ważne pary.
Trzy foldy zachowują 20 076 memberships każdy, **60 228 łącznie**.
Porównanie pięciu metod zawiera **301 140 rekordów predykcji**, z purged
i wszystkimi przyczynami wyłączeń. Model i preprocessing są osobne w każdym
foldzie; trzy walidacje i trzy holdouty mają rozłączne originy własnej roli.
Każdy fold wybiera metodę tylko na swojej walidacji, przed własnym holdoutem.

Backtest ID:
`forecast-backtest-sha256-0f8e578af20d95d6d6062e7d00f13b30b4522b30b1bb5ab68e9371483a5f3f22`.
Comparison ID:
`forecast-model-comparison-sha256-25ff34ce2996dde712e7183a4e3b2ce57585566afd68992353308e4cef6bb7c3`.
Predictions SHA-256:
`13709d6a745addd513e414776fed9ae1770f0a8fa6e6b51c2d1570cfb7bf8999`.
Plik predykcji ma **157 577 037 B**.

| Fold | Eligible train | Eligible validation | Eligible holdout | Najlepszy baseline na validation | Zamrożony wybór | MAE baseline validation | MAE wyboru validation | MAE wyboru holdout |
|---|---:|---:|---:|---|---|---:|---:|---:|
| expanding-01 | 1526 | 1824 | 1824 | Last observed | RF | 1,656798 | 1,445579 | 1,407470 |
| expanding-02 | 3302 | 1824 | 1821 | Seasonal naive7 | HGB | 1,514803 | 1,369586 | 1,443533 |
| expanding-03 | 5094 | 1824 | 1755 | Moving average 7 | RF | 1,554276 | 1,310054 | 1,477588 |

Łączna walidacja ma **5472 eligible keys**, holdouty **5400**; każda metoda
ma komplet predykcji na tej samej próbce. Najpóźniejsze etykiety train są
dostępne 8/14/20 czerwca o 00:00 UTC, przed cutoffami tych dni EOD.
Każdy audyt potwierdza 20 076 wspólnych kluczy, zero przyszłych etykiet
treningu i zero model-specific drops. Wyłączenia pozostają w osobnym coverage.

| Metoda | Pooled validation MAE | Pooled validation WAPE | Pooled holdout MAE | Pooled holdout WAPE |
|---|---:|---:|---:|---:|
| Last observed | 2,285636 | 0,254337 | 2,290370 | 0,250101 |
| Moving average 7 | 1,720577 | 0,191459 | 1,723241 | 0,188172 |
| Seasonal naive7 | 1,617507 | 0,179990 | 1,504630 | 0,164301 |
| RF | 1,377983 | 0,153336 | 1,437809 | 0,157004 |
| HGB | 1,418151 | 0,157806 | 1,459561 | 0,159379 |
| Wybór na validation osobno per fold | 1,375073 | 0,153013 | 1,442420 | 0,157508 |

Strategia wyboru zachowuje **RF/HGB/RF**. Niższe pooled holdout MAE samego
RF nie zmienia wyboru w drugim foldzie; nie wybieramy ponownie na holdoucie.
To agregaty sum błędów i actuals, bez średniej WAPE i bez metryk train.
Wynik protokołu nie nadaje gotowości modelu lub championa.

Wszystkie sześć procesów fit mieści się w limitach. Wall **1,767–3,277 s**,
CPU **1,700–2,969 s**, peak RSS maksymalnie **339 460 096 B**, po jednym
wątku. Pipeline'y RF mają 2 240 463 / 3 551 291 / **4 415 272 B**, HGB
543 198 / 541 751 / 541 375 B. Limity i parametry modeli nie były zwiększane.

Pełna regresja poprawionej implementacji: **888 passed / 718,14 s**.
Kontrola modeli/backtestu/CI po poprawce decoder: **36 passed / 54,71 s**.
Wcześniejsza kontrola całego forecastingu/CI: 76 passed / 250,89 s.
Testy potwierdzają expanding i rolling, rozłączne role, odrzucenie krótkiego
kalendarza bez skracania okien, dojrzałość korekt per cutoff, poprawne
agregaty/zera, blokadę brakującego folda i brak outputu dla błędnej konfiguracji.
Pełny kontrolowany run dwóch foldów buduje źródłowe etykiety, osobne pipeline'y,
przechodzi niezależny rebuild/training i zachowuje immutable rerun.
Spy fit potwierdza 14/28 próbek train osobno; zmienione i rehashed metryki
są odrzucane przez replay z artefaktów podrzędnych.

Ruff/format: 241 plików; mypy strict: 145 plików. Wszystkie smoke danych,
baseline/model/backtest gates, kontrakty, lokalne linki, wheel/sdist oraz
Compose config przechodzą. [Component gate](04-06-components.json) jawnie
nie udaje temporalnej kwalifikacji. Gitleaks git/dir nie wykrywa sekretów.

[Odłączony wheel](04-06-wheel.json) uruchomiono przez Python `-I` poza
checkoutem, po skopiowaniu kompletnego backtestu. Sprawdza przypięty kod,
pakowanie obu nowych schemas/config, wszystkie model/prediction/label receipts,
ponowny audyt foldów i pooled metrics oraz identyczny descriptor/ID.
Ta próba pakowania nie wykonuje nowego treningu; niezależne source rebuild
i pełny trening są częścią osobnego odbioru temporalnego.
Wheel SHA-256:
`f521151f431e554e8b22550d85c2bd8005d215fcc882c3a76807680ba7d7855a`.

[Końcowy odbiór temporalny](04-06-temporal.json) przeszedł w **1489,662 s**.
Niezależna odbudowa splitu/etykiet z curated i ponowny trening wszystkich
sześciu modeli potwierdziły identyczny backtest descriptor i ID.
Kolejny pełny run zachował wszystkie opublikowane pliki bez zmian;
checksumy rodziców również pozostały identyczne. Raport potwierdza
`independent_source_rebuild_and_retraining`, `repeatable_and_immutable`
oraz `parents_unchanged`, każde z wartością `true`.

Odczyt większych modeli JSON korzysta z własnego limitu 128 MiB;
zwykłe metadata nadal mają limit 4 MiB. Kontrola większego RF ujawniła
kolizję tych dwóch budżetów w odczycie procesu fit, której nie pokazywał
mały model z 04.5. Test regresji potwierdza akceptację model data >4 MiB,
odrzucenie zwykłych metadata tej wielkości oraz duplicate keys, NaN/Infinity
i przekroczenia limitu modelu. Strict decoding obejmuje też zapisane pipeline'y.

To development evidence na wcześniej używanym syntetycznym snapshotcie,
bez twierdzenia o jakości komercyjnej lub nietkniętym final teście.
Portfolio final test nie był otwierany. Brak AWS, zmian DB/API, aktywacji
lub zmiany dotychczasowego odrzucenia RF w RetailOps. Pozostają AI 04.7–04.8.
Zdalna publikacja i Required CI nie były wykonywane w tym zakresie.
