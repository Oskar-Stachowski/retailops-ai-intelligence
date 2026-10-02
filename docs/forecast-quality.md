# Metryki, przedziały i bramki jakości AI 04.7

Ten dokument opisuje niezmienioną wersję 1. [Protokół 2.0](forecast-quality-v2.md)
rozdziela ocenę mediany i średniej oraz naprawia ocenę zer i przedziałów;
nie zmienia historycznych raportów ani wyników wersji 1.

Raport korzysta z niezmiennego [backtestu 04.6](forecast-backtesting.md)
i jego wspólnych kluczy, etykiet oraz predykcji. Rozszerza dotychczasowy
evaluator MAE/WAPE o dodatkowe statystyki; kontroluje zgodność jego wyników.
Nie trenuje nowych modeli, nie wybiera ponownie na holdoucie i nie zmienia
wcześniejszego odrzucenia RF w RetailOps.

## Metryki i przekroje

Raport obejmuje validation i development holdout osobno dla każdego folda
oraz pooled. Predykcje train pozostają poza oceną. Porównuje pięć metod,
`validation_selected` (zamrożony wybór per fold) i `validation_baseline`
(najlepszy baseline wybrany na validation danego folda).

Każda metoda ma wyniki globalne oraz według horyzontu 1–14, kategorii,
kanału i koszyka wolumenu. Kategoria pochodzi z cech znanych przy origin,
kanał z klucza prognozy. Wolumen to **znana w origin rolling mean 28 dni**:
zero, low `(0,5)`, medium `[5,20)`, high `[20,∞)`. Brak wartości ma koszyk
`unknown`; nie jest zerem. Podział nie korzysta z target actuals ani truth.
Segmenty inventory constrained/unconstrained wymagają zweryfikowanych
danych po AI 06 i nowej wersji oceny.

| Metryka | Definicja |
|---|---|
| MAE | suma `abs(forecast−actual)` / eligible rows |
| WAPE | ta sama suma / suma `abs(actual)`; zera pozostają w liczniku |
| RMSE | pierwiastek ze średniej kwadratów błędów |
| Bias | średnia `forecast−actual`; dodatnia oznacza przeszacowanie |
| Normalized bias | suma błędów ze znakiem / suma actuals |
| Under/overforecast | osobne sumy jednostek niedoszacowania/przeszacowania i liczby wierszy |
| MAPE | średnia `abs(error)/actual` tylko na dodatnich actuals, z liczebnością i udziałem próby |
| Zera | liczba actuals=0 oraz suma nadmiarowych prognoz na tych wierszach |
| Coverage | total/eligible/excluded/predicted rows, udziały oraz liczby przyczyn wyłączeń |

Pooled metryki wynikają z sum błędów i liczebności wszystkich foldów.
Nie uśredniamy WAPE ani procentów. Kilka przyczyn może dotyczyć jednego
wyłączonego wiersza, dlatego sumy exclusion counts nie muszą równać się
liczbie excluded rows. Brak choć jednej wymaganej predykcji wyłącza
wszystkie point metrics danej próbki; nie raportujemy korzystnego wyniku
tylko z dostępnej części. WAPE i normalized bias dla zerowego mianownika
to null. MAE, RMSE i nadmiarowe prognozy nadal pokazują błąd na zerach.

## Kalibracja i znaczenie przedziałów

Każdy fold, metoda i horyzont mają osobną kalibrację na eligible **validation**
residuals `abs(actual−forecast)`, dostępnych najpóźniej do selection cutoff.
Przy nominalnym coverage 90% wybieramy uporządkowaną resztę o indeksie
`ceil((n+1)×0.90)` (licząc od 1). Minimum to 50 reszt; indeks większy niż
n lub brak predykcji daje `not_ready`, bez sztucznego skrócenia indeksu.
Calibration ID wiąże klucze, predykcje, etykiety, cutoff, nominalny poziom
i wybraną resztę. Holdout actuals nie wpływają na ID ani szerokość.

Przedział to `[max(0,forecast−q), forecast+q]`, w jednostkach observed sales,
bez zaokrąglania. Każdy rekord wiąże calibration ID i cutoff. Dla holdoutu
kalibracja kończy się przed pierwszym origin. Przedziały na validation są
wyłącznie `in_sample_calibration_diagnostic`; nie są historycznie dostępną
prognozą wydaną przed tą walidacją. Holdout ma ocenę out-of-time development.
Raport pokazuje empirical coverage oraz średnią szerokość, także per segment.
Brak przedziału u choć jednej eligible prognozy daje incomplete/null.

Validation była również używana do wyboru metody. Horyzonty, originy
i sprzedaż są zależne czasowo. **Nie deklarujemy gwarancji coverage**
opartej na niezależności lub exchangeability. To prosta kalibracja diagnostyczna,
której rzeczywiste pokrycie trzeba sprawdzić na późniejszych danych.

## Zamrożone bramki rozwojowe

[Konfiguracja jakości](../contracts/forecast/v1/quality.default.json) została
zapisana przed uruchomieniem nowej oceny. Dane z 04.6 były już widziane;
ta konfiguracja nie jest planem niezależnego final testu ani kwalifikacją
produkcji. Przed kampanią AI 09 należy osobno zamrozić jej dataset i protokół.
Nie dostrajamy progów po wyniku bieżącego przebiegu.

| Warunek | Domyślny próg |
|---|---|
| Primary metric | MAE, poprawa **>5%** globalnie względem validation baseline |
| Baseline fallback | zachowuje baseline bez wymagania poprawy wobec samego siebie |
| Absolutny normalized bias | ≤10% |
| Regresja MAE w krytycznym segmencie | ≤10% wobec zamrożonego baseline |
| Minimalna próba | 100 globalnie; 30 w każdym krytycznym segmencie |
| Prediction coverage | 100% eligible keys |
| Eligibility coverage | ≥80% memberships danego segmentu |
| Kalibracja | ≥50 reszt per fold/metoda/horyzont |
| Empirical interval coverage | ≥80%, nominalny poziom 90% |
| Średnia szerokość / średnie actuals | ≤2 |

Bramki dotyczą wyboru per fold i pooled, na obu rolach. Wszystkie horyzonty,
kategorie i kanały obecne w planie oraz cztery wymagane koszyki wolumenu
pozostają w raporcie, także puste. Brak minimalnej próby, zerowy mianownik
lub niekompletna kalibracja daje `not_ready`. Przekroczenie mierzalnego progu
daje `failed`. Raport zachowuje obie listy powodów. Całość jest `not_ready`,
jeżeli choć jedna wymagana bramka nie daje się ocenić; inaczej `failed`, gdy
choć jedna przekracza próg. MAPE nie jest główną bramką.

## Polecenia i artefakty

```bash
uv run --locked --extra snapshot --extra forecast retailops-ai-forecast quality-evaluate \
  --feature-dir data/generated/feature-sets/<feature_set_id> \
  --backtest-dir data/generated/forecast-backtests/<backtest_id> \
  --config contracts/forecast/v1/quality.default.json
uv run --locked --extra snapshot --extra forecast retailops-ai-forecast quality-verify \
  --quality-dir data/generated/forecast-quality/<quality_id> \
  --feature-dir data/generated/feature-sets/<feature_set_id> \
  --backtest-dir data/generated/forecast-backtests/<backtest_id>
make forecast-quality-check
```

Exit 0 oznacza przejście bramek rozwojowych, 3 — poprawnie zapisany raport
`failed/not_ready`, 2 — błąd wejścia/konfiguracji. Nawet exit 0 nie promuje
modelu. Domyślna bramka CI sprawdza komponenty, nie kwalifikuje temporalnego
modelu; checker z jawnie wskazanymi rodzicami wykonuje pełny raport,
niezależny replay oraz immutable rerun.

Artefakt zawiera manifest, config, segment metrics, calibration, gates,
model card i eligible interval predictions. ID wiąże rodzica, konfigurację,
kod/runtime oraz checksumy wszystkich raportów. Rodzice pozostają bez zmian.
`load_quality` bada integralność i typy; `quality-verify` niezależnie
odtwarza statystyki, kalibrację i bramki z rodziców. Samo podmienienie raportu
i przeliczenie jego hashy nie wystarczy. Replay nie wykonuje ponownego
treningu — odbiór treningu i etykiet źródłowych pozostaje w 04.6.
Indeks SQLite ma limit 2 GiB, grupy 4000, przedziały 3 mln rekordów/2 GiB,
metadata 4 MiB; czytniki i bufory zachowują ograniczenia poprzednich etapów.

[Evidence 04.7](evidence/04-07-quality.md) podaje wyniki i aktualne blokady.
[Eksport AI 04.8](forecast-run.md) utrwala je wraz z niezmiennym backtestem
do późniejszego importu w AI 05; nie zmienia statusu jakości.
