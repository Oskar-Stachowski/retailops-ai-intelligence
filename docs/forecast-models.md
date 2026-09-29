# Modele forecastingu AI 04.5

Zakres dodaje RandomForestRegressor i HistGradientBoostingRegressor do
[wspólnego evaluatora](forecast-baselines.md), na zweryfikowanych
[features, etykietach i splicie](forecast-manifests.md). Wyniki są diagnostyczne.
`forecast_model_status` i status wdrożenia pozostają `not_ready` do odbioru
backtestingu, bramek jakości i lifecycle w AI 04.6–04.8.

## Trening i granica wiedzy

Strategia `direct_global_with_horizon_feature` uczy jeden model każdej rodziny
na fold, wspólny dla produktów, lokalizacji, kanałów i horyzontów 1–14.
Każda próbka ma features znane przy origin, jawny `horizon_days` oraz dojrzałą
etykietę `observed_sales_units` dla target date. Prognoza nie używa przyszłych
actuals ani rekurencyjnego podawania przewidywań jako historii.

Preprocessing i model dopasowują się wyłącznie na eligible train danego folda.
Etykieta musi być dostępna przed training cutoff. Validation i development
holdout nie wchodzą do imputacji, kategorii ani dopasowania drzew.
Portfolio final test pozostaje poza protokołem. Inventory i simulation truth
nie są cechami; observed sales może być ograniczona zapasem i nie oznacza
nieograniczonego popytu.

`ForecastAdapter.forecast` zwraca typowane wartości z pełnym grainem,
horyzontem, typem targetu i model ID. Wymaga kwalifikowanych inputs związanych
z feature set, po granicy wiedzy treningu. Odrzuca origin wcześniejszy lub
równy training cutoff. Predykcje train dostępne są osobno przez
`diagnose_train`, oznaczone jako `in_sample_diagnostic`. Nie stanowią backtestu
ani historycznych sygnałów do polityki decyzji.

## Zamrożona konfiguracja i zasoby

[Wersjonowana konfiguracja](../contracts/forecast/v1/models.default.json)
ma jeden wariant na rodzinę, seed 42, bez przeszukiwania parametrów:

| Rodzina | Ustawienia |
|---|---|
| Random Forest | 64 drzewa, głębokość ≤12, min. 2 próbki w liściu, bootstrap, `n_jobs=1` |
| HistGradientBoosting | 100 iteracji, ≤31 liści, min. 20 próbek w liściu, learning rate 0,1 |

HGB ma `early_stopping=false`, bez losowo wydzielanej walidacji i bez
automatycznych kategorii. Kod eksportu jest przypięty do scikit-learn 1.9.1.
Dokumentacja parametrów: [RF](https://scikit-learn.org/stable/modules/generated/sklearn.ensemble.RandomForestRegressor.html),
[HGB](https://scikit-learn.org/stable/modules/generated/sklearn.ensemble.HistGradientBoostingRegressor.html).

Każde dopasowanie działa w osobnym procesie z jednym wątkiem obliczeniowym.
Domyślne limity: **120 s wall, 90 s CPU, 1 GiB RSS**, obejmujące również
uruchomienie/importy procesu. Monitor kontroluje czas, CPU i RSS co 50 ms;
przekroczenie zabija proces i blokuje publikację. Receipt zawiera pomiar.
Train ma limit 50 000 wierszy; macierz i target łącznie ≤64 MiB, surowe
próbki ≤128 MiB. Model JSON ≤128 MiB, z ograniczonymi drzewami i liczbą węzłów.
To limity dopasowania, a nie termin wykonania całego importu/evaluatora.
Rodzice oraz strumieniowy evaluator zachowują odrębne limity danych.

## Wspólne porównanie

Wszystkie pięć metod zachowuje rekord dla każdego membership, także purged
i wykluczonego. Metryki mają dokładnie te same eligible keys i definicje
MAE/WAPE co baseline'y. Brak wymaganej predykcji blokuje wybór i częściowe
metryki. Ujemne wyniki ML są obcinane do zera, bez zaokrąglania.

Na validation wybieramy najlepszy baseline. Model ML zastępuje go tylko,
jeśli uzyska **więcej niż 5% względnej poprawy MAE**. Remis pozostawia baseline;
przy remisie ML pierwszeństwo ma RF. Konfiguracja i kryterium są zamrożone
przed pomiarem. Selection hash wiąże politykę, rodziców, fold, wspólny grain
i metryki validation, zanim zostaną policzone wyniki development holdout.
Holdout raportuje ten wybór i nie służy dostrajaniu.

Wybór jest diagnostyczny. Wcześniejsze odrzucenie RF w RetailOps nadal
obowiązuje; nowe porównanie nie zmienia batch forecastingu ani decyzji serving.
Pełne wielofoldowe backtesty, przekroje, bias/uncertainty i bramki modelu są
następnymi zakresami.

## Polecenia i artefakty offline

```bash
uv sync --locked --extra snapshot --extra forecast
uv run --locked --extra snapshot --extra forecast retailops-ai-forecast models-evaluate \
  --feature-dir data/generated/feature-sets/<feature_set_id> \
  --split-dir data/generated/forecast-splits/<split_id> \
  --config contracts/forecast/v1/models.default.json
uv run --locked --extra snapshot --extra forecast retailops-ai-forecast models-verify \
  --comparison-dir data/generated/forecast-models/<comparison_id> \
  --feature-dir data/generated/feature-sets/<feature_set_id> \
  --split-dir data/generated/forecast-splits/<split_id>
make forecast-models-check
```

Exit 0 oznacza poprawne wykonanie porównania, 3 — zapisany `not_ready`,
2 — odrzucone wejście/konfigurację. Polecenia nie wymagają AWS ani DB/API.
Domyślny CI gate sprawdza komponenty treningu/eksportu/JSON na małej macierzy;
nie kwalifikuje temporalnie modelu. Checker z `--feature-dir` i `--split-dir`
wykonuje rzeczywisty trening, pełne niezależne retraining/replay i immutable rerun.

Katalog `forecast-model-comparison-sha256-…` zawiera manifest, model card,
kanoniczny `predictions.jsonl` i pełne pipeline'y JSON w `models/`.
Każdy pipeline utrwala train-only preprocessing, fills/vocabularies, kolejność
kolumn, horizon feature, parametry, wszystkie drzewa, train labels hash,
rodziców i kod/lock/runtime. Ładowanie nie wykonuje pickle. Eksport drzew
porównuje predykcje z natywnym scikit-learn podczas treningu; testy obejmują
również niewidziane próbki i granice float32 RF.

ID zależy od treści, bez ścieżek i timestampów. Pomiar czasu/CPU/RSS jest
receiptem wykonania, bez wpływu na tożsamość modelu. Atomowa publikacja nie
nadpisuje istniejącego wyniku; rerun zachowuje oryginalne bajty.
`models-verify` ponownie dopasowuje oba modele ze zweryfikowanych rodziców
i odtwarza wszystkie predykcje, wybór oraz metryki. Zmienione drzewa z
przeliczonymi hashami nie przechodzą takiej weryfikacji.
Pełne verify wymaga przypiętego kodu, lockfile i runtime z manifestu.
Historyczne evidence dotyczy zapisanej wersji; zmiana implementacji tworzy
nowe model IDs i wymaga nowego pomiaru. Zwykły odczyt starego artefaktu
sprawdza jego treść i bindings bez deklarowania nowej kwalifikacji.

[Odbiór 04.5](evidence/04-05-models.md) podaje konkretny pomiar i ograniczenia.
[AI 04.6 — chronologiczny backtesting wielu foldów](forecast-backtesting.md)
korzysta z tych samych pipeline'ów i evaluatora. Następny zakres:
**AI 04.7 — przekroje, bias, niepewność i quality gates**.
