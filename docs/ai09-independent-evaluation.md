# AI 09 — niezależna ocena zamrożonego forecastingu

Kontrakty [v19](../contracts/evaluation/v19/frozen_forecast_configuration.schema.json)
wiążą pełną listę prób z konkretnymi fitami, hashami modeli i preprocessingu,
zakończonym wyborem Tune oraz osobną kalibracją. Typowany dokument sam nie
dowodzi ukończenia operacji w journalu i nie uprawnia do odczytu final test.
Publiczny audytowany runner musi osobno zweryfikować receipts, artefakty,
prerequisites i zamrożenie trzech zastosowań przed końcowymi etykietami.

`campaign_evaluation_configuration.bind_forecast_configuration` sprawdza
kompletne metadane każdego tripletu RF/HGB/TensorFlow na Tune i Calibration.
Obie role mają wspólną populację we wszystkich próbach; dokładne receipts,
source, dataset, runtime i lock muszą odpowiadać wyborowi i kalibratorowi.
Każdy fit ma ten sam zbiór treningowy i early stopping, a wybrane funkcjonały
wskazują właściwą próbę, model i pełny hash. Nie ma nowego wyboru architektury
lub dopasowania na niezależnej roli.

`campaign_forecast_inference` przyjmuje tylko covariates, historię i wspólną
eligibility. Zachowuje sześć istniejących wyników, częściowe okna 1–14,
oryginalne jednostki i wykluczenia; nie przyjmuje rzeczywistego targetu,
roli ani uprawnienia odczytu. Adapter Tune/Calibration zachowuje dotychczasowy
wire v16 i odrzuca niewłaściwą rolę przed inference. Nowe typy prognoz mają
jawnie `development_evaluation` albo `final_test`; final nie jest przedstawiany
jako dawna membership development.

`FrozenForecastComposer` sprawdza i hashuje konfigurację raz dla całego
strumienia. Dla każdego klucza wymaga kompletu prób w ustalonej kolejności,
identycznego example hash, eligibility i wyników bazowych. Średnia i mediana
mogą pochodzić z różnych wybranych prób. Przedział kandydata stosuje wyłącznie
zamrożony promień właściwego horyzontu wokół wybranej mediany.

Bazowe mean, median i interval wybiera Tune osobno. Typ
`CampaignForecastReference` przechowuje te funkcjonały oraz własny środek
bazowego przedziału. Osobno wybrana bazowa mediana może leżeć poza przedziałem
innego baseline’u: wartości pozostają bez przycinania. To referencje metryk,
nie deklaracja wspólnego rozkładu ani pakiet do wdrożenia.

`EvaluationSegment` zachowuje bounded statystyki wystarczające i stabilne sumy,
bez listy wszystkich etykiet lub kluczy w RAM. Wymaga rosnących kluczy,
jednego scope i konfiguracji, jawnego actual dla eligible oraz limitu wierszy.
Nieudany strumień nie może zwrócić pozornie poprawnego wyniku. Brak prognoz,
zbyt mała próba i zerowe mianowniki pozostają jawne; mierzalne porażki są
zachowane także obok `not_ready`. Nowa polityka ma te same progi liczbowe v2
i minimum 100 etykiet kalibracji na każdy horyzont. Jej scope wprost obejmuje
niezależną oraz końcową ocenę po zamrożeniu. Dotychczasowy evaluator v2 i
wcześniejsze schematy pozostają bajtowo bez zmian. Testy różnicowe porównują
obie implementacje na tych samych parach.

To przygotowanie komponentów. Pozostają audytowany adapter pełnych plików
development/final, fresh worker i supervised runner z pomiarem całego własnego
drzewa, complete segment inventory, block uncertainty, rzeczywista kampania
wszystkich trzech zastosowań, lifecycle i końcowy odbiór main. Projektowy journal
nie jest zainicjalizowany, nowe projektowe fity wynoszą zero, final test pozostaje
zamknięty, a AI 09 nadal jest `not_ready`.

[Evidence przygotowania](evidence/09-31-independent-forecast-components.json)
zachowuje 130 zaliczonych testów integracji w 23.90 s, kontrole typów
oraz zgodność 573 modułów i czterech schematów w wheel. Odrębny probe
zaimportował zainstalowany wheel bez TensorFlow/MLflow i potwierdził
`not_ready` pustej oceny. Te kontrole nie są wykonaniem projektu.
