# Odbiór AI 04.7 — metryki, niepewność i bramki jakości

Data: 2026-09-29. Branch `ai/04-01-task-calendar`, rodzic kodu `e49fd52`.
[Protokół](../forecast-quality.md) oraz
[zamrożona konfiguracja](../../contracts/forecast/v1/quality.default.json)
określają przekroje, znany w origin wolumen, quantile reszt validation,
minimalne próby i progi. Konfiguracja powstała przed nowym przebiegiem,
ale snapshot był wcześniej oglądany: to development evidence, bez nowego
niezależnego final testu. Progi nie były dostrajane po wyniku tego odbioru.

```bash
.venv/bin/python scripts/check_forecast_quality.py \
  --feature-dir data/generated/feature-sets/features-sha256-e7992553d0d3b67f00b5f16e8e0ace55bb88fc3f3d023135bfcd70538e0fdbaf \
  --backtest-dir data/generated/forecast-backtests/forecast-backtest-sha256-0f8e578af20d95d6d6062e7d00f13b30b4522b30b1bb5ab68e9371483a5f3f22 \
  --output-root data/generated/forecast-quality \
  --output docs/evidence/04-07-temporal.json
```

Temporalny smoke z 04.6: 3 foldy, 8 produktów, 3 aktywne pary, 60 originów,
observed sales. Rodzice, modele RF/HGB i ich wybór **RF/HGB/RF** pozostają
identyczne. Nie wykonujemy nowego treningu ani drugiej selekcji.
Wszystkie pięć metod, strategia wybrana per fold i zamrożony baseline per
fold korzystają z tych samych memberships. Point metrics MAE/WAPE są
kontrolowane względem poprzedniego wspólnego evaluatora.

Quality ID:
`forecast-quality-sha256-66143185382f4f2f9353c0383b2661ac37e14293431959273878684014b6aab6`.
Raport zawiera **1624 segment metrics**, **294 kalibracje** i **76 104**
eligible interval predictions. W każdym foldzie/metodzie/horyzoncie
kalibracja ma **128–144** reszty; wszystkie przekraczają minimum 50.
Predykcje z przedziałami zajmują **55 463 755 B**, segment report 1 638 424 B.
Przedziały nominalne 90% korzystają wyłącznie z validation znanej przed
selection cutoff, nigdy z holdout outcomes. Walidacyjne coverage jest
diagnostyką in-sample kalibracji, a nie niezależną oceną jej skuteczności.

| Strategia / rola | Eligible rows | MAE | WAPE | RMSE | Bias jednostek | Normalized bias | Empirical coverage | Mean width |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Validation selected — validation | 5472 | 1,375073 | 0,153013 | 2,202055 | −0,171506 | −1,91% | 91,34% | 6,827543 |
| Validation baseline — validation | 5472 | 1,575292 | 0,175292 | 2,769985 | −0,271260 | −3,02% | 92,21% | 8,056652 |
| Validation selected — holdout | 5400 | 1,442420 | 0,157508 | 2,216533 | −0,400582 | −4,37% | 91,67% | 6,843928 |
| Validation baseline — holdout | 5400 | 1,558457 | 0,170178 | 2,736234 | −0,282037 | −3,08% | 91,89% | 8,042160 |

Wybrana strategia poprawia pooled holdout MAE o **7,45%** wobec strategii
baseline wybranej na validation każdego folda. Nie jest to porównanie
z nowym baseline wybranym po obejrzeniu wszystkich holdoutów.
Na holdoutach ma 2828 niedoszacowań (4976,104683 jednostki) i 2572
przeszacowania (2812,962171 jednostki). 702 actuals to zero; nadmiarowe
prognozy na nich sumują się do 348,696051 jednostki. MAPE dodatnich actuals
wynosi 0,292430 przy coverage 87%; nie zastępuje WAPE ani bramek.
Eligibility coverage to 90,54%, prediction i interval coverage 100%.
Wyłączone 564 memberships zachowują powód `closed_target`.

## Bieżące blokady jakości

**Quality status: `not_ready`.** Wynik globalny nie kwalifikuje modelu.
Łącznie: **145 passed, 79 failed, 8 not_ready** z 232 bramek, liczonych
per fold i pooled dla obu ról. Powody mogą współwystępować w jednej bramce.

- **Zerowy historyczny wolumen:** brak memberships w czterech zakresach
  fold/pooled × dwóch rolach. Osiem wymaganych bramek ma `not_ready`;
  koszyk pozostał w raporcie. Potrzebna jest reprezentatywna próba,
  bez zastępowania historycznego wolumenu target actuals=0.
- **Drugi fold:** wybrany HGB ma holdout MAE 1,443533 wobec baseline
  1,426689, czyli pogorszenie 1,18%. Nie spełnia globalnej poprawy >5%.
- **Wysoki wolumen, pooled holdout:** empirical coverage **69,37%** wobec
  minimum 80%, mimo poprawy MAE. Część kategorii również ma niedostateczne
  pokrycie. Globalne 91,67% nie ukrywa tego problemu.
- **Niski wolumen, pooled holdout:** MAE gorsze od zamrożonego baseline
  o **28,63%** (limit 10%), średnia szerokość przedziału / średnie actuals
  **3,514** (limit 2). Część kategorii przekracza też absolutny bias 10%.

Te braki wymagają reprezentatywnych danych oraz osobnej, wersjonowanej
pracy nad modelem/kalibracją, z oceną na późniejszych danych. Nie zmieniano
progów ani wyboru na obecnych holdoutach, aby uzyskać `passed`.
AI 04.8 może zapisać diagnostyczny run i blokady do handoff; odbiór 04.7
nie nadaje gotowości do serving, promocji lub komercyjnego użycia.

## Weryfikacja

[Component gate](04-07-components.json) sprawdza błąd na zerach,
`null` WAPE, blokadę gate i indeks quantile; nie udaje temporalnej kwalifikacji.
Testy obejmują signed bias, under/overforecast, positive-only MAPE z coverage,
brak częściowych metryk, niewystarczającą próbę krytyczną, regresję/bias/coverage/
width, puste segmenty, cutoff i nieosiągalny indeks quantile. Zmiana holdout
actuals nie zmienia kalibracji; etykieta microsecond po selection cutoff
jest odrzucana. Pełny mały run dwóch foldów odtwarza raport, nie zmienia
rodziców, zachowuje immutable rerun, a także generuje poprawne przedziały
przy osobnej małej konfiguracji testowej. Rehashed, sfałszowany raport bias
nie przechodzi replay z rodziców. Ta konfiguracja testowa nie jest bramką
odbioru temporalnego powyżej.

[Końcowy odbiór temporalny](04-07-temporal.json) przeszedł w **262,854 s**.
Niezależny replay z rodziców dał identyczny descriptor, kalibracje, metryki,
bramki i przedziały. Ponowny pełny run zachował oryginalne bajty artefaktu;
checksumy features/backtestu również pozostały identyczne. W tym odbiorze
wykonano zero nowych fitów i zero AWS calls. SHA-256 pliku konfiguracji:
`34435b5c813bd3c5c854f5bcc246617247977a15b2748b749ab0de8097422dae`.

[Odłączony wheel](04-07-wheel.json) został uruchomiony przez Python `-I`
poza checkoutem, z pełnym skopiowanym raportem. Potwierdza import kodu
z wheel, integralność wszystkich receipts, typy segment metrics,
zgodność pinów kodu/lockfile, schemas/default config i komponenty zer/quantile.
Nie wykonuje nowego source replay ani treningu; pełny replay obejmuje
osobny odbiór temporalny powyżej. Wheel SHA-256:
`86dd8b7caac5a8130317415825223415cd29d59834b4245f859612aced01c067`.

Pełna regresja: **896 passed / 758,34 s**. Wcześniejszy kontrolowany zakres
quality/CI: 23 passed / 23,90 s. Ruff/format: 248 plików; mypy strict:
149 plików. Wszystkie bramki handoff/import/curated/calendar/features/
manifests/baselines/models/backtest/quality, kontrakty, lokalne linki,
wheel/sdist i Compose config przechodzą. Gitleaks git (76 commits)
i dir (343,72 MB) nie wykrywa sekretów. Nie wykonywano nowego smoke
usług Compose ani zdalnego Required CI w tym zakresie.

Brak AWS, nowych treningów, zmian DB/API i wdrożenia. Portfolio final test
nie był otwierany. Inventory/truth pozostają wyłączone, a wcześniejsze
odrzucenie RF w RetailOps bez zmian. Zdalny push/Required CI poza tym zakresem.
