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

`campaign_evaluation_data` indeksuje pełny wskazany plik roli jednym odczytem:
`development_evaluation.jsonl` albo rzeczywisty final wire `final_evaluation.jsonl`.
Sprawdza kolejność wszystkich kluczy, canonical bytes, kompletne checksums,
liczebności, eligibility, cenzurowanie, zera i powody wykluczeń. Final source
recipe musi odpowiadać planowi. Features i historia mają dokładne wiązania
hash; każde okno 1–14 jest sprawdzane także wtedy, gdy wszystkie jego obserwacje
są wykluczone. Brakujące lub powtórzone features/history blokują wykonanie.

Indeks covariates i osobny indeks actuals używają prywatnych baz SQLite.
Współdzielenie pliku, połączenia lub attached database jest odrzucane;
łączny rozmiar obu indeksów podlega jednemu limitowi. Proces predykcji ma
otrzymać wyłącznie pierwszy indeks: rekordy `EvaluationRecord` i wspólne
`InferenceRecord` nie zawierają targetu ani całego outcome. Actual wykluczonej
obserwacji pozostaje `None` w agregacji. Przygotowany indeks przypina dokładny
plan; inny plan albo niepełny strumień nie może go konsumować jako zakończonej
pełnej oceny. Ten wewnętrzny adapter nadal wymaga publicznego runnera,
który zweryfikuje journal, rodziców i dostęp przed otwarciem etykiet.

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

To przygotowanie komponentów. Publiczny runner opisany poniżej łączy pełne
pliki roli, świeże procesy i pomiar własnego drzewa. Pozostają complete segment
inventory, block uncertainty, rzeczywista kampania wszystkich trzech zastosowań,
lifecycle i końcowy odbiór main. Projektowy journal
nie jest zainicjalizowany, nowe projektowe fity wynoszą zero, final test pozostaje
zamknięty, a AI 09 nadal jest `not_ready`.

[Evidence przygotowania](evidence/09-31-independent-forecast-components.json)
zachowuje 130 zaliczonych testów integracji w 23.90 s, kontrole typów
oraz zgodność 573 modułów i czterech schematów w wheel. Odrębny probe
zaimportował zainstalowany wheel bez TensorFlow/MLflow i potwierdził
`not_ready` pustej oceny. Te kontrole nie są wykonaniem projektu.

Po dodaniu pełnego adaptera 116 testów integracji przeszło w 65.55 s,
w tym rzeczywisty eksport małego, wcześniej dostępnego source oraz pełny join
features/history final wire. Osobna kontrola po ostatnim strażniku attached
database ma 2 passed w 1.73 s. Wheel ma 574 moduły i cztery schematy v19,
wszystkie bajtowo zgodne ze źródłami; stary evaluator i 60 wcześniejszych
schematów pozostają bez zmian. Nowy probe zaimportował adapter z faktycznie
zainstalowanego wheel bez TensorFlow/MLflow. Mypy dla 695 plików, Ruff, format
1184 plików i schematy przeszły. Nie wykonano projektowych fitów, niezależnej
oceny projektu ani odczytu projektowego final test.

Wewnętrzny `campaign_evaluation_worker` ma osobne fazy przygotowania, predykcji,
konsumpcji jednej próby oraz końcowej agregacji. Request predykcji dopuszcza
wyłącznie określone pola i nie zawiera ścieżki do actuals lub całego datasetu.
Wiązania pełnych receiptów fitów i zamrożonej konfiguracji są sprawdzane przed
użyciem. Wszystkie próby zachowują pełne klucze; osobne wybrane mean i median
trafiają do bounded projekcji na dysku. Agregacja wymaga potwierdzenia każdej
zamrożonej próby, nawet gdy jej model nie został wybrany. Zmiana indeksu predykcji
blokuje odczyt etykiet. Wynik końcowy nadal jawnie nie kwalifikuje jakości:
critical segment inventory i block uncertainty są nieukończone.

105 testów integracji przeszło w 65.75 s, w tym 22 kontrole faz workera.
Kontrole używają deklarowanych rodziców i fake modeli; SDK MLflow jest osobno
mockowany. Dwa rzeczywiste świeże procesy core wykonały końcową agregację
kontrolnych development/final i zapisały własny czas/CPU/peak RSS. Nie jest to
native odbiór TF inference ani pomiar całego drzewa publicznego supervisora.
Mypy dla 697 plików, Ruff i format 1187 plików przeszły. Ten wcześniejszy
odbiór faz nie obejmował publicznej rezerwacji, dowodów ukończonych rodziców
ani trwałej publikacji wyniku; nowe kontrole tych elementów są opisane poniżej.

Publiczny `evaluate_campaign_forecast` rezerwuje operację przed weryfikacją
plików rodziców i etykiet. Sprawdza ukończone export, wszystkie fity, wszystkie
score na Tune/Calibration, wybór Tune i kalibrację wraz z prywatnymi receiptami.
Następnie uruchamia osobne świeże procesy przygotowania, predykcji każdej próby,
konsumpcji i końcowej agregacji. Predykcja otrzymuje wyłącznie indeks covariates
i zamrożone rodzice; nie otrzymuje actuals ani ścieżki całego datasetu. Każda
próba zachowuje wszystkie klucze, metryki, trace hash i koszty. Tylko bieżący
surowy indeks predykcji jest usuwany po sprawdzonej, trwałej konsumpcji.

Nowe kontrakty [v20](../contracts/evaluation/v20/campaign_forecast_evaluation_recipe.schema.json)
oddzielają prospektywną recepturę od rozwiązanego planu v19. Receptura zamraża
identyfikatory operacji rodziców, pełny inwentarz prób, polityki i zasoby przed
wynikami. Dopiero po ukończeniu rodziców plan wiąże faktyczne hashe konfiguracji
i receiptów. Zapobiega to cyklowi: protocol hash → configuration result hash →
fit/calibration receipts → protocol hash. Kontrola używa rzeczywistego journalu
z protokołem zamrożonym przed zadeklarowanymi wynikami; wcześniejsze v1–v19
pozostają bajtowo bez zmian.

Receipt v20 przypina recepturę, plan, konfigurację, wszystkie artefakty,
populację i koszty. Jest zapisywany prywatnie i trwale przed zakończeniem
operacji w journalu. Awaria dowolnej fazy lub publikacji zachowuje budżet,
znany peak i znany rozmiar artefaktu. Ponowna próba nie refunduje budżetu.
Read-only verifier odczytuje opublikowany bundle i receipt, bez ponownego
otwierania pierwotnych etykiet. Tożsamości selection components dla modeli
i preprocessingu są hashami pełnego kompatybilnego inwentarza wybranych
funkcjonałów, w tym faktycznych model hashes; nie oznaczają pojedynczego pliku
wag dla złożonej prognozy mean/median.

Dostęp final evaluation wymaga dowodów ukończonej niezależnej oceny development
wszystkich trzech zastosowań, zgodnych pakietów i pełnych segmentów oraz
niepewności. Sama deklaracja `selection_frozen` nie wystarcza. Obecny forecast
component ma jawnie nieukończone critical segments i block uncertainty, więc
nie może autoryzować końcowej oceny projektu. Integracja tego dowodu przed
final generation/export, pełni evaluatorzy pozostałych zastosowań i kompletna
ocena jakości nadal wymagają implementacji przed zainicjalizowaniem kampanii.

Najnowszy odbiór ma 176 passed w 118.81 s, z realnym kontrolnym journalem,
ukończonymi prywatnymi receiptami zadeklarowanych rodziców i fake modelami.
Weryfikatory ukończenia Tune/Cal/Fit/Score są rzeczywiste; zawartość model
bundles w tej kontroli jest mockowana. Dwa świeże core procesy i mały wcześniej
eksponowany eksport source wchodzą w tę integrację. Mypy dla 699 plików, Ruff,
format 1190 plików i schematy przeszły. Wheel zawiera 578 zgodnych modułów,
cztery schematy v19 i dwa v20; 64 wcześniejsze pliki evaluation i quality_v2
pozostają bez zmian. Rzeczywiście zainstalowany wheel importuje runner bez
TensorFlow/MLflow.

Osobna kontrola native CPU dodaje świeżą predykcję niezależnej roli do
istniejącego odbioru RF/HGB/TF, ponownie używając jego rzeczywistych fitów.
Nie wykonuje dodatkowego treningu. Odbiór tej nowej ścieżki jest jeszcze
**pending w Required CI**; nie uruchomiono lokalnych fitów ani nowej kampanii.
Przygotowanie i metadane kalibracji w tej kontroli są deklarowane, więc nawet
po jej zaliczeniu nie będzie to pełna kampania lub uprawnienie final test.
