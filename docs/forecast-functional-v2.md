# AI 04 — kampania v11 z osobną medianą i średnią

Kampania korzysta z [zamrożonego protokołu jakości 2.0](forecast-quality-v2.md).
Wyniki starej kampanii, snapshot `ai-intermittent-v1` i raport zatrzymanej v10
pozostają odrębnymi artefaktami. Przygotowanie v11 nie generuje źródła ponownie.
Stan końcowego odbioru jest podany w [STATUS](STATUS.md).

## Prognozy i wybór na walidacji

Każdy klucz otrzymuje osobną medianę, średnią oraz centralny przedział 90%.
RF i HGB z kwadratową funkcją straty uczą średnią. Trzy dalsze HGB uczą
kwantyle 0,5, 0,05 i 0,95. Jest to jawne użycie
[funkcji quantile w HGB](https://scikit-learn.org/stable/modules/generated/sklearn.ensemble.HistGradientBoostingRegressor.html).
Każdy fold ma pięć fitów, seed 42, jeden wątek, dotychczasowe hiperparametry
i limity: 120 s czasu ściennego, 90 s CPU, 1 GiB RSS, 50 tys. wierszy,
64 MiB macierzy. Zagnieżdżony dawny ModelPolicy dostarcza hiperparametry
i budżety; wyborem celów kieruje nowy FunctionalPolicy.

Baseline’y korzystają tylko z historii znanej w chwili prognozy: ostatnich
7 dni, 28 dni albo tego samego dnia tygodnia w 28 dniach. Dla każdej próbki
wyliczane są osobno mediana, średnia i empiryczne kwantyle 0,05/0,95.
Brak obserwacji nie jest zastępowany zerem. Baseline mediany wybiera MAE,
baseline średniej MSE, a baseline przedziału interval score na walidacji.

Pierwsza połowa dni walidacji dostarcza korekt i kalibracji; druga połowa
wybiera konfigurację. Wyboru dokonuje się według historycznego wolumenu
i kategorii, z globalną konfiguracją dla nieobserwowanej grupy.

- Kandydaci mediany obejmują wartości bez korekty oraz przesunięcie o medianę
  reszt z pierwszej połowy. W obu połowach działa strażnik MAE względem baseline’u.
  Brak wymaganej globalnej poprawy w drugiej połowie pozostawia dokładny baseline.
- Kandydaci średniej obejmują wartości bez korekty oraz mnożnik obliczony
  w pierwszej połowie, ograniczony z góry ustaloną regułą do 0,5–2,0.
  Gdy suma prognoz wynosi zero, kandydatem jest przesunięcie o średnią sprzedaż.
  Wybrany kandydat musi przejść MSE i bias w obu połowach; inaczej zostaje baseline.
  Samo pozostawienie baseline’u nie zwalnia z testu bias na holdoucie.
- Kalibracja granic używa kwantyli reszt końców przedziału i co najmniej
  50 próbek. Kolejność fallbacku: wolumen × kategoria, wolumen, globalnie.
  Wybór przedziału w drugiej połowie wymaga coverage i braku regresji interval score.
  Brak kalibracji pozostawia `null` i stan `not_ready`.

Walidacja jest raportem diagnostycznym z danych użytych do kalibracji i wyboru.
Nie jest niezależnym testem. Właściwa ocena dotyczy rozwojowych holdoutów;
portfolio final test pozostaje nieotwarty. Wszystkie receptury foldów są zapisane
przed pierwszą oceną holdoutów. Każda predykcja wskazuje recepturę, czas
dostępności parametrów i komórkę kalibracyjną. Po ekspozycji holdoutu nie wolno
ponownie kwalifikować zmienionego modelu na tym snapshotcie jako na nowych danych.
CLI odmawia takiego ponownego treningu; odtworzenie zapisanych modeli pozostaje możliwe.

## Próbka, zasoby i zachowanie dowodów

Kalendarz, okna, purge i dojrzałość etykiet pozostają zgodne z v10.
Ocena obejmuje każdy fold i pooled, oba role, globalnie oraz wszystkie
horyzonty 1–14, kategorie, kanały i koszyki wolumenu zero/low/medium/high.
Puste obowiązkowe koszyki pozostają w raporcie. Brak predykcji, brak próbki
i mierzalne pogorszenie nie są usuwane ani zamieniane w zaliczenie.

Zapis wejść `forecast-inputs-1.2.0` kompresuje indeks SQLite zarówno podczas
budowy, jak i weryfikacji. Limit fizyczny i indeksu wynosi nadal 2 GiB.
Limit logiczny 5 GiB wynika z oszacowania pełnego panelu 4 290 442 055 B
z 10% zapasem i zaokrągleniem; nie jest zmianą progu jakości. Starsze wersje
nadal egzekwują własne limity 2/4 GiB. Przed startem wymagane jest 16 GiB
wolnego miejsca w workspace i katalogu tymczasowym. Nie jest to gwarantowane
maksimum wszystkich historycznych, nieograniczonych indeksów.

Nowa kampania używa jednego sekwencyjnego procesu oraz macierzy w pamięci.
Pełne rekordy cech są przetwarzane strumieniowo. Nie usuwa się wierszy,
żeby zmieścić model lub uzyskać lepsze metryki. Awaria zostawia częściowy
artefakt `failed-functional-*`, etap ekspozycji testu, konfigurację i błąd.

## Zamrożenie, uruchomienie i odtworzenie

`scripts/run_forecast_functional_campaign.py freeze` wymaga gotowego splitu,
zaliczonych raportów pytest, zachowania dawnych sum kontrolnych i miejsca.
Zapisuje pełną politykę, kod, środowisko, rodziców, okna i referencje do
freeze protokołu oraz wejść. `run` weryfikuje te powiązania przed treningiem.
Raporty i freeze tworzy się pod nowymi nazwami; wcześniejszych się nie nadpisuje.

```sh
.venv/bin/python scripts/run_forecast_functional_campaign.py freeze \
  --prepared reports/ai04-quality-v2-split-preparation.json \
  --freeze contracts/forecast/v2/campaign-v11.freeze.json \
  --report reports/ai04-campaign-v11-freeze.json \
  --test-report reports/ai04-functional-full-regression.xml \
  --test-report reports/ai04-functional-loopback-verification.xml \
  --test-report reports/ai04-functional-freeze-verification.xml

.venv/bin/python scripts/run_forecast_functional_campaign.py run \
  --prepared reports/ai04-quality-v2-split-preparation.json \
  --freeze contracts/forecast/v2/campaign-v11.freeze.json \
  --report reports/ai04-campaign-v11.json
```

Kod zakończenia 3 po pełnym eksporcie oznacza zachowane blokady jakości.
Nie oznacza zgody na strojenie do wyników holdoutu. Biblioteczna weryfikacja
`functional_campaign.verify_campaign` odtwarza preprocessing, baseline’y,
predykcje z drzew, kalibrację, wybór i raporty bez fitów. Wszystkie identyfikatory
i sumy muszą się zgadzać.

Pełny replay egzekwuje także wersje bibliotek, system i architekturę zapisane
we freeze. Dla wyeksportowanego runu używa się jego kompletu rodziców:

```sh
.venv/bin/python -m retailops_ai.forecasting.functional_campaign verify \
  --features "$FORECAST_V11_RUN/features" \
  --split "$FORECAST_V11_RUN/split" \
  --output "$FORECAST_V11_RUN/campaign"
```

`FORECAST_V11_RUN` wskazuje katalog z `run_manifest.json`. To odtworzenie
zapisanych modeli, a nie nowa kwalifikacja lub ponowny trening.

Eksport `functional_run` kopiuje source, curated, features, split i campaign,
weryfikuje kopie oraz zapisuje model card i handoff. Zachowuje `not_ready`, jeśli
jakakolwiek obowiązkowa bramka nie przeszła. Format v2 wymaga importera dla dwóch
celów; stary importer pojedynczej prognozy nie może go przemianować na v1.
Nie ma automatycznej promocji modelu ani nadania aliasu champion.

## Testy

Kontrolowane testy sprawdzają rozdzielenie średniej 2 i mediany 0 dla próbki
z 80% zer, przenośne drzewa kwantylowe, zgodność strumieniowego preprocessingu,
odmowę użycia etykiet holdoutu lub zbyt późnych etykiet do kalibracji, jawny
brak przedziałów przy małej próbce, komplet koszyków, dwa foldy i replay bez
treningu. Test eksportu wykrywa naruszenie sum i zachowuje blokady. Osobne testy
zamrożonego protokołu nadal sprawdzają zero sprzedaży, mały mianownik oraz
zgodność celów MAE i bias.

## Wynik kampanii v11 — 2026-09-30

Ocena została zakończona na zachowanym snapshotcie, po zamrożeniu protokołu
i receptury. Trzy foldy obejmują 15 fitów oraz 214 032 predykcje walidacyjne
i testowe, w tym 90 454 eligible klucze development holdout. Wszystkie
32 wcześniejsze kontrole próbki przeszły; każdy obowiązkowy segment ma
komplet predykcji i wystarczającą próbkę. Model pozostaje **`not_ready`**.
[Końcowe evidence](evidence/04-functional-campaign-v11.json) potwierdza
niezależne odtworzenie bez fitów, eksport i sprawdzenie sum kontrolnych.
Archiwum `functional-run-sha256-137af4ca4ed80004340e12e1037a234ba3fd9f135598b7d7737fcd241fa48f70`
ma 5621 plików i 494 382 216 B, łącznie z manifestem. Kampania ma ID
`forecast-functional-sha256-0131e7295648e6e3a2cd1a98fac9cd686c7587820775f6f065a5f6798a683c26`.
Końcowa kontrola potwierdziła niezmienność wszystkich 3070 zachowanych plików
oraz 11 plików freeze protokołu. Źródła nie generowano ponownie.

| Zakres | Passed | Failed | Not ready z powodu braków |
|---|---:|---:|---:|
| Validation — diagnostyka dopasowania i wyboru | 98 | 14 | 0 |
| Development holdout — ocena zamrożonej receptury | 67 | 45 | 0 |
| Łącznie | 165 | 59 | 0 |

Liczby obejmują przekroje każdego folda oraz pooled, więc nie oznaczają tylu
niezależnych zbiorów. Nie są bezpośrednim porównaniem ze starymi kampaniami:
zmieniły się dane, cele i protokół. Dawne błędy pozostają w dawnych raportach.

| Pooled holdout | Wynik | Wymaganie / baseline |
|---|---:|---:|
| MAE mediany | 2,552934; poprawa 4,7797% | baseline 2,681081; poprawa >5% |
| MSE średniej | 31,333540 | nie więcej niż 32,497176 |
| Normalized bias średniej | −1,7127% | wartość bezwzględna ≤10% |
| Coverage przedziału | 95,4131% | ≥80% |
| Interval score | 9,935083 | nie więcej niż 10,159534 |

Wynik globalny nie spełnia wymogu poprawy MAE. Pozostałe globalne miary
nie usuwają błędów szczegółowych:

- Koszyk `zero` oznacza zerową **historię rolling 28**, a nie same zerowe
  przyszłe etykiety. Jego 4841 kluczy ma łącznie 213 jednostek actuals;
  prognoza średniej daje około 7,63, czyli bias −96,42%. To rzeczywiste
  niedoszacowanie sprzedaży w ocenianych kluczach. Diagnostyczny iloraz szerokości
  przedziału przez średnie actuals wynosi 5,70 i sam nie jest powodem odrzucenia.
- Pooled `high` ma MSE 487,814174 wobec 478,790794 baseline’u, czyli regresję
  około 1,88%. Pooled `low` i `medium` przechodzą wszystkie reguły.
- `Home Improvement` w pierwszym foldzie ma regresję MAE mediany 20,66%
  przy limicie 10%, a także regresję MSE średniej.
- W holdoutach 23 oceny mają regresję interval score, 18 regresję MSE,
  7 nadmierny bias, 2 niewystarczającą globalną poprawę MAE i 1 regresję
  MAE krytycznego segmentu. Jedna ocena może mieć kilka przyczyn odrzucenia.

Żaden próg nie został zmieniony po obejrzeniu tych wyników. Dalsza receptura
może powstawać na danych rozwojowych; jej kwalifikacja wymaga osobnych,
dotąd niewykorzystanych danych, zamrożenia przed oceną i ponownego oszacowania
miejsca. Ponowne strojenie i zaliczenie na holdoutach v11 nie zamknie AI 04.
Portfolio final test pozostaje poza kampanią; model nie został promowany.

[Osobny wheel v11](evidence/04-functional-v11-wheel.json) zawiera zapis freeze
kampanii. Odczyt kampanii z rozpakowanego wheela poza repozytorium potwierdził
zgodność 24 plików kodu, środowiska i identyfikatora kampanii oraz zachowanie
statusu `not_ready`. Sumy trzech wcześniejszych pakietów pozostały bez zmian.
