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
  Gdy suma prognoz wynosi zero, kandydatem jest przesunięcie o średni popyt.
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
