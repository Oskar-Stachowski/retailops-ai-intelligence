# AI 09 — wybór prognoz na Tune

`evaluation_campaign.campaign_tune.select_campaign_forecast` realizuje osobny,
zamrożony plan [v17](../contracts/evaluation/v17/campaign_forecast_tune_plan.schema.json).
Operacja obejmuje wszystkie prerejestrowane triplets RF/HGB/TensorFlow.
Każdy fit musi wystąpić dokładnie raz; nie można po wynikach pominąć gorszej
próby lub seeda inicjalizacji. Każdy rodzic raw scoring musi być ukończony
w tym samym dzienniku, na tym samym źródle, eksporcie, runtime i pełnej
populacji Tune. Budżet prób i seedów rodzin nadal sprawdza protokół v10.

Rezerwacja selekcji poprzedza kontrolę rodziców oraz odczyt etykiet. Świeży
proces wykonuje jeden pełny, strumieniowy odczyt Tune i predykcji na próbę.
Sprawdza kolejność, pełne klucze, checksum każdego example, eligibility,
wykluczenia, całe pliki i ich liczebności. Zachowuje censored oraz pozostałe
wykluczenia. Populacja i digest prognoz baseline muszą być identyczne między
próbami; nie ma próbkowania ani cichego poolingu różnych kluczy.

Średnia, mediana i bazowy przedział są wybierane osobno. Średnia minimalizuje
MSE; learned candidate ma guard bezwzględnego normalized bias 10%. Mediana
minimalizuje MAE i wymaga poprawy ściśle większej niż 5% względem najlepszego
baseline. Bazowy przedział minimalizuje proper central interval score przy
coverage nominalnym 90%, zamiast wybierać go na podstawie point error.
Przy remisie pozostaje baseline, potem kolejność modeli i zamrożona kolejność
prób. RF ma tylko średnią. WAPE i normalized bias są niezdefiniowane przy
zerowej sumie actual, bez sztucznego mianownika. Minimum to 100 eligible
obserwacji i 80% eligibility coverage; brak danych lub pełnej funkcji baseline
daje `not_ready`, bez udawanej idealnej metryki.

Wybór learned head przypina operation ID jego fitu i scoringu oraz rzeczywisty
digest model bundle. Średnia i mediana mogą pochodzić z różnych rodzin lub
prób. Centrum przyszłego przedziału candidate odpowiada wybranej medianie;
sam przedział trzeba dopasować osobno na roli calibration. Ten przyrost nie
dopasowuje modeli, preprocessingu ani kalibratora. Nie czyta independent
development lub final testu. `selected_for_independent_evaluation` oznacza
wybór do dalszej oceny, a nie zaliczoną jakość lub zgodę na promocję.

MLflow zapisuje plan, pełne parent bindings, metryki i selection. Supervisor
mierzy cały czas i własne drzewo procesów, scratch oraz rezerwy. Worker zapisuje
własny systemowy peak RSS i CPU także po publikacji artefaktów do MLflow.
Nieudana próba pozostaje rozliczona, bez automatycznego retry. Zweryfikowany
prywatny receipt zostaje trwale opublikowany przed journal completion.
`verify_campaign_forecast_selection` kontroluje ukończony receipt, hashe
oraz odtworzony wybór z metryk bez ponownego otwierania etykiet.

Kontrole danych badają rzeczywiste pliki ról, parowanie i matematykę. Kontrole
ordering używają jawnie mocked workerów; kontrola pełnego `select` ma mocked
gate wersji pakietów. Required test CPU rozszerza rzeczywisty trening/reload
i sześć prognoz o świeży proces Tune oraz MLflow, nadal na małych kontrolowanych
danych i mocked przygotowaniu features. Jego wykonanie na nowym headzie
pozostaje wymagane; lokalnie sprawdzono wyłącznie collection, aby zachować
rezerwę RAM otwartych sesji.

[Receipt przygotowania](evidence/09-27-campaign-selection-preparation.json)
oddziela te kontrole od niewykonanej projektowej kampanii. Journal projektu
pozostaje niezainicjalizowany, nowe projektowe fity wynoszą zero i final test
pozostaje zamknięty. Do AI09 ready wymagane są pełne dane, kalibracja,
niezależna ocena trzech zastosowań, końcowe seedy 42/137/2026, scenariusze,
segmenty, niepewność i koszty, lifecycle, trzy karty/raporty oraz publikacja
i pełne CI końcowego main.
