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
nie może autoryzować końcowej oceny projektu. Wspólny verifier tego dowodu jest teraz wywoływany również przed final
generation/export. Pełni evaluatorzy pozostałych zastosowań i kompletna ocena
jakości nadal wymagają implementacji przed zainicjalizowaniem kampanii.

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
Nie wykonuje dodatkowego treningu. Odbiór tej ścieżki na head `b41d1f26`, native CPU job `113081286526`
w Required CI `37706155765`, zaliczył cały zestaw: **8 passed w 147.23 s**.
Pełny Required CI tego head zakończył się sukcesem: 17 odczytanych z 17 jobs,
wszystkie success wraz z required-result. Nie uruchomiono lokalnych fitów
ani nowej kampanii.
Przygotowanie i metadane kalibracji w tej kontroli są deklarowane, więc nawet
po jej zaliczeniu nie będzie to pełna kampania lub uprawnienie final test.

`campaign_selection_evidence.verify_completed_campaign_selection` jest wspólną
kontrolą przed publiczną final generation, final export i final evaluation.
Wymaga wcześniejszych ukończonych ocen development wszystkich trzech zastosowań,
pełnej roli, zamrożonej receptury i źródła, prywatnych canonical receiptów,
zgodnych selection components, zmierzonych kosztów oraz opublikowanych bundles.
Sprawdza typowany forecast receipt i jego bundle; wersjonowane receipty
i artefakty anomaly/stockout nadal wymagają swoich pełnych evaluatorów.
Nie otwiera pierwotnych etykiet. Sama metadata freeze lub trzy ścieżki
bez dowodów nie uruchomią source workera ani nie otworzą końcowego rodzica.

Regresja generation/final-export/evaluation/worker ma 78 passed w 113.31 s.
Sześć nowych kontroli blokuje brakujący dowód przed producer/parent I/O.
Dodatkowe trzy przypadki brakującego, malformed lub nieprywatnego receipt
mają 3 passed w 6.58 s i nie zmieniają journalu ani nie wykonują nowej fazy.
Testy sukcesu starej publikacji final mają jawnie mocked quality proof
(wraz z wcześniej mockowanym replay), aby osobno sprawdzać kolejność,
trwałość i awarie wyjścia. Nie są pełnym dowodem końcowego dostępu projektu.
Mypy dla 700 plików, Ruff i format 1191 plików przeszły. Zmiana pozostaje lokalna.
Po pełnym odbiorze `b41d1f26` kolejną publikację poprzedzi integracja aktualnej
zaakceptowanej bazy; nie ponowiono żadnego live head ani pełnego profilu.

Wheel wspólnej kontroli ma 579 modułów i sześć zgodnych schematów v19/v20;
64 wcześniejsze pliki evaluation i quality_v2 są niezmienione. Zainstalowany
pakiet importuje generation jako pierwszy bez cyklu i potwierdza użycie
tego samego verifiera przez wszystkie trzy publiczne granice final.
Nie importuje TensorFlow/MLflow ani nie uruchamia kampanii.

`PairedForecastUncertainty` jest osobnym kontrolowanym komponentem statystycznym.
Nie jest jeszcze podłączony do faz pełnej kampanii. Kontrakty
[v21](../contracts/evaluation/v21/campaign_forecast_uncertainty_policy.schema.json)
przypinają dwie oddzielne analizy wrażliwości: pełne rozłączne bloki dni origin
(domyślnie 28 dni, stała kotwica 2000-01-03, wszystkie serie razem) oraz całe
serie product/location/channel (wszystkie originy i horyzonty razem).
Nie jest to wspólny bootstrap wielowymiarowy ani gwarancja nominalnego pokrycia.
W każdym losowaniu candidate, reference i actual używają tych samych grup
i krotności. Seed danych nie jest seedem losowania; zakres, metoda i zamrożona
polityka określają osobny seed PCG64 oraz hash wszystkich losowań.

Prywatny SQLite zachowuje wystarczające statystyki grup i stabilne rozwinięcia
sum. Nie ma listy wszystkich etykiet lub residuals w RAM. Nierówne liczebności
grup zmieniają mianowniki każdej próby; nie uśredniamy samych metryk grup.
Wymagane są zewnętrzne liczebności i hashe pełnego segmentu, więc ucięty
strumień nie daje raportu. Limit wierszy, komórek, pliku indeksu i łącznej
liczby odwiedzin grup jest jawny; publiczny worker nadal musi objąć ten
komponent pomiarem całego drzewa RSS/scratch/czasu.

Częściowe bloki graniczne i grupy zawierające tylko wykluczenia pozostają
w populacji. Brak predykcji, zerowy mianownik i niewystarczająca liczba grup
są jawne. Jeżeli jakiekolwiek losowanie ma nieokreśloną metrykę, raport
zachowuje liczbę takich prób i estymatę punktową, lecz nie publikuje
przedziału liczonego tylko z pozostałych prób. Domyślnie wymagane jest osiem
kwalifikujących się bloków czasu i 30 serii; mniejsze minima w kontrolach
nie zmieniają polityki pełnego projektu.

Zasada wspólnego losowania odpowiada opisanemu w
[dokumentacji SciPy paired bootstrap](https://docs.scipy.org/doc/scipy/reference/generated/scipy.stats.bootstrap.html).
Konieczność uwzględniania zależności czasowej omawia przegląd autorów
[Bootstrap Technology and Applications](https://www.stat.cmu.edu/technometrics/90-00/vol-34-04/v3404378.pdf).
Nasze stałe rozłączne bloki są jawnym wyborem polityki, nie implementacją
opisanego w tym artykule moving-block bootstrap. Założenia o słabej zależności
czasu oraz niezależności między seriami są oddzielnie zapisane i wymagają
ostrożnej interpretacji; dwie analizy nie dowodzą ich spełnienia.

Kontrole porównują statystyki na dysku z bezpośrednim losowaniem całych grup
i równaniami siedmiu metryk na małych jawnych danych. Sprawdzają też pełne
klucze, nierówne grupy, braki, wszystkie zera, wykluczone grupy, limity,
determinism, prywatny indeks oraz rozdzielenie seedów i ról. Nowy komponent
nie otwiera projektowego final testu i nie kwalifikuje AI 09. Pozostają
źródłowy kontekst pełnych segmentów, konsumpcja wszystkich surowych prób przed
usunięciem ich indeksów, podłączenie raportu do publicznego workera i receipt,
oceny pozostałych zastosowań oraz pełna kampania i lifecycle. Wcześniejsze
receipty v20 nadal jawnie oznaczają block uncertainty jako nieukończone.
32 kontrole nowego komponentu wraz z istniejącymi kontrolami metryk i
konfiguracji mają 74 passed w 1.19 s. Mypy dla 702 plików, Ruff, format
1194 plików, schematy i kontrola dokumentacji przeszły. Wheel zawiera
581 zgodnych modułów oraz osiem schematów v19/v20/v21; 66 wcześniejszych
plików evaluation i oba pliki quality_v2 pozostają bez zmian. Zainstalowany
wheel zwrócił jawnie nieokreśloną pustą ocenę, bez importu TensorFlow/MLflow.

Nowe kontrakty [v22](../contracts/evaluation/v22/campaign_forecast_segment_census.schema.json)
obejmują pełny inwentarz kontekstu: global, 14 horyzontów, wszystkie kategorie
zweryfikowanego katalogu, kanały, wolumen, cztery scenariusze, historię,
dostępność danych, zapas, lead time, intermittency i zaplanowane anomalie.
Puste grupy oraz wykluczone klucze pozostają w raporcie. Dostępność ma osobne
nakładające się flagi; pozostałe osie są partycjami i mają wspólne liczebności,
powody wykluczeń oraz hashe pełnych i kwalifikujących się kluczy. Wymagany
zewnętrzny census blokuje ucięcie strumienia, zmianę scope lub kategorii.

`CampaignContextFacts` ma osobny prospektywny storage policy AI09 dla curated
1.1/1.2. Nie zmienia zamkniętych wire, limitów lub kodu AI08. Sprawdza pełnego
rodzica, indeksuje wszystkie wersje jedenastu istniejących tabel AI08 na
prywatnym SQLite i ponownie sprawdza cały indeks oraz wszystkie pliki rodzica,
w tym nieużywane tabele. Query zachowuje oryginalną kolejność i klipuje wiedzę
przed niezmienioną projekcją AI08. Routing pochodzi z rzeczywistego źródła,
zapas z origin-known available quantity, a lead time z ówczesnej oferty dostawcy.
Brakujący deklarowany route nie może przesunąć znanego zapasu do grupy unknown.
Cache obejmuje tylko jeden rzeczywisty punkt product/stock/origin, ponownie
użyty przez horyzonty oraz jeden niezmieniony `FactIndex` AI08 dla fizycznej
serii. Zachowuje wszystkie wersje do jawnego bound pełnego rodzica, a następnie
klipuje je ponownie przy każdym origin; nie jest cache'em global latest. Limity parent, indeksu,
wierszy i bajtów pojedynczego odczytu są jawne, bez mniejszej populacji po błędzie.

`RawTrialCriticalSegments` zachowuje bounded statystyki wszystkich sześciu
prognoz w każdej wymaganej grupie. Wymaga dokładnego join klucza, example hash,
eligibility i exclusion reasons z kontekstem oraz całego census i context trace.
RF nadal ma wyuczony mean, bez udawanej mediany; surowe learned intervals nie
są przedstawiane jako skalibrowane. Matematyczne kontrole porównują wszystkie
mierzalne wyniki z równaniami na jawnych wierszach. Oś anomaly dodano także do
komponentu metryk i prospektywnego v21 przed jego pierwszą publikacją; wire
v1–v20 i zamknięte komponenty AI08 pozostają niezmienione.

To nadal komponenty przygotowania. Scope kontrolny i annotation są deklarowane:
nie dowodzą audytowanego odczytu projektu ani źródłowego planu scenariuszy.
Pozostają trwały pełny context bundle ze zweryfikowanego rodzica i planu,
podłączenie wszystkich raw trial segments przed usunięciem indeksów, podłączenie
uncertainty do publicznego workera i wersjonowanego receipt oraz rzeczywiste
oceny anomaly/stockout i pełna kampania. Żaden nowy typ nie autoryzuje final
testu, jakości lub promocji. Najnowsze wyniki kontroli i publikacji są w
[evidence 09-31](evidence/09-31-independent-forecast-components.json).

Zestaw metryk, uncertainty, census i rzeczywistego odczytu publicznych fixture
ma 138 passed w 97.65 s. Po ponownym użyciu zamkniętego `FactIndex` AI08
73 kontrole źródła i census przeszły w 96.23 s. Mypy dla 706 plików, Ruff,
format 1201 plików, schematy i kontrola dokumentacji przeszły. Wheel ma 585
zgodnych modułów i 15 schematów v19–v22. Wszystkie 66 wcześniejszych plików
evaluation v1–v20 i 28 plików zamkniętych projekcji/storage/preparation AI08
pozostają bajtowo bez zmian. Faktycznie zainstalowany wheel zaimportował nowe
komponenty i schemat v22 oraz zwrócił 56 pustych grup kontrolnych, bez importu
TensorFlow/MLflow lub uruchomienia source/model workera. Integracja z pełnym
zaakceptowanym main `0990905b` zmieniła tylko ancestry, bez zmiany drzewa.
Nowa publikacja i jej dokładne pełne CI pozostają wymagane.


`SourceAnnotations` wiąże plan z publicznym, zweryfikowanym snapshotem,
Source ID, snapshot ID, seedem, requested/resolved parameters i deklaracją
zamrożonej receptury generacji. Używa niezmienionych schematów Source 1.2.
Normalizuje kolejność injections/controls według ID tak jak producent i wymaga
zgodnego plan/schema SHA z rzeczywistym manifestem Source 2.8. Obsługuje oba
zamknięte rodzaje planu: demand oraz physical. Okna są ograniczone, unikalne
i nie zachodzą na siebie w grain; etykieta dotyczy target date, z zachowaniem
originu i horyzontu. Znana promocja pochodzi z origin-known input; sam control
promotion lub przyszły efekt nie tworzy dostępnej promocji. Interwencja demand
ma pierwszeństwo przed tą etykietą, a physical inventory ma własny segment.
Return spike pozostaje osobną osią anomaly. Nie deklaruje niezależności efektów
fizycznych, nie otwiera prywatnego Source artifactu effects i nie zmienia features.

Komponent wymaga od wywołującego dowodu ukończonej generacji i rezerwacji całego
odczytu Source. Sam obiekt Snapshot, generation plan albo metadata nie dowodzą
uprawnienia, czasu prerejestracji czy kwalifikacji canonical. 39 kontroli mają
rzeczywiste publiczne fixture 1.1/1.2 i kontrolowane features. Plan odzyskano
z wcześniej eksponowanego prywatnego fixture tylko do porównania jego hash;
nie jest to dowód prerejestracji projektu. Sześć pominięć dotyczy wyłącznie
braku planu anomalii w 1.1. Nie wykonano Source generacji, fitów ani final read
projektu. [Evidence 09-34](evidence/09-34-source-annotation-binding.json) opisuje
zakres kontroli. Nadal wymagane są publiczny, audytowany context bundle,
pełny role join i pokrycie wszystkich wymaganych scenariuszy na każdym seedzie.
Pojedynczy rodzaj planu nie jest dowodem całego portfolio scenariuszy.

Pakiet tego komponentu ma 586 zgodnych modułów i 15 schematów v19–v22.
66 wcześniejszych kontraktów v1–v20 i 129 plików zamkniętych komponentów stockout
pozostają zgodne z bazą. Import faktycznie zainstalowanego wheel i obu
zamkniętych schematów Source plan przeszedł bez importu TensorFlow/MLflow
lub uruchomienia workera. Publikacja nowego dokładnego head i pełny odbiór CI
są nadal wymagane przed użyciem w audytowanym context bundle.

Nowy [v23](../contracts/evaluation/v23/campaign_context_bundle_recipe.schema.json)
dodaje pełny Source context bundle i jego publiczny runner. Receptura zamraża
operacje generacji/eksportu, politykę segmentów i limity przed poznaniem wynikowych
IDs. Runner rezerwuje cały odczyt Source przed metadata lub workerem, wymaga
trwałego ukończenia obu rodziców i zgodnej receptury generacji. Dla final najpierw
sprawdza rzeczywiste evidence wszystkich trzech zastosowań; sam nagłówek freeze
nie wystarcza. Oddzielny worker pod nadzorem sprawdza pełny snapshot/curated,
odtwarza transformację, sprawdza producenta/lock/runtime i wiąże plan z publicznym
Source. Nie otwiera prywatnego artifactu effects.

Kontekst pochodzi z rzeczywistych features/history oraz niezmienionej projekcji
AI08. Łączy każdy klucz ocenianej roli, także wykluczony. Bounded SQLite sortuje
klucze kanonicznie przed census; logiczna kolejność horyzontów w window nie jest
porządkiem leksykograficznym tych kluczy. Bundle zachowuje contexts, pełny census,
scope, population i seale rodziców. Końcowa kontrola obejmuje także wszystkie
nieużywane pliki oryginalnego eksportu. Indeksy inputs/actuals/context są prywatne
i po sukcesie usuwane; actuals nie trafiają do context rows. Output nie może
obejmować rodziców ani znajdować się wewnątrz nich. Awaria pozostawia obciążoną
operację i koszt, bez zaakceptowanego receipt lub automatycznego retry.

Koszt odczytu jest jawny: pełny walidator eksportu parsuje jego pliki ról
(jeden final albo sześć development), a indeks parsuje ocenianą rolę ponownie.
Nowy receipt zapisuje zatem dwa parsowania ocenianej roli. Instrumentacja native
kontroli potwierdza oba odczyty. Wcześniejsze v19/v20 nadal mają własną niezmienioną
semantykę jednego odczytu w swoim prepare; nowy kontekst jest osobną operacją
whole-parent. Sam typed receipt nie dowodzi ukończenia: verifier wymaga zgodnego
trwałego eventu, stored receipt, wszystkich artifactów i ponownie pełnego census
z każdego context row oraz ukończonych rodziców.

117 kontroli nowego odczytu i dotychczasowego data/worker/runner forecast przeszło
w 214.81 s; dalsze 14 kontroli granic/odmowy final przeszło w 5.21 s. Native
kontrole korzystają z wcześniej eksponowanych Source demand/physical 1.2,
z dwoma kontrolnymi originami i wszystkimi 14 horyzontami. Odzyskanie ich planu
z dawnego prywatnego fixture jest wyłącznie kontrolą istniejącego hash, nie
dowodem czasu prerejestracji projektu. Pozytywnego publicznego runnera na pełnym
canonical Source jeszcze nie wykonano. Nie ma nowych Source generacji, Project
fitów ani świeżego final read. 75 wcześniejszych wire oraz wszystkie 129 plików
zamkniętych komponentów stockout pozostają bez zmian. Szczegóły i wcześniejsze
nieudane kontrole zachowano w [evidence 09-35](evidence/09-35-source-context-bundle.json).
Pozostają pełna kwalifikacja canonical, publiczne raw critical/uncertainty
receipty, rzeczywiste anomaly/stockout i kompletna kampania z lifecycle.

Wheel zawiera 590 zgodnych modułów i 17 schematów v19–v23. Faktycznie
zainstalowany pakiet zaimportował publiczny context runner, oba schematy v23
i nowy reviewed role plan bez TensorFlow/MLflow, Source workera lub Project
fitów/final read. Mypy dla 711 plików, Ruff i format 1208 plików przeszły.
Nowy dokładny head oraz pełne CI/publikacja na main nadal pozostają wymagane.

Publiczny evaluator forecast przyjmuje teraz opcjonalny, ukończony Source context
bundle do zebrania surowych metryk każdej zamrożonej próby przed usunięciem jej
indeksu predykcji. Operacja kontekstu musi być prerekwizytem zamrożonym w protokole;
sam nagłówek receipt nie wystarcza. Runner sprawdza trwałe ukończenie, pełny bundle,
eksport, Source, runtime, selection, rolę i politykę segmentów. Po prepare porównuje
pełne liczby oraz hashe kluczy/populacji z niezależnie utworzonym indeksem.

Kontekst i census trafiają wyłącznie do core consume, po zakończeniu procesu modelu.
Predict nadal odrzuca payload kontekstu i nie otrzymuje datasetu ani actuals.
Consume łączy kanonicznie posortowany context z każdym raw prediction i actual,
weryfikuje example/eligibility/exclusions, ponownie odtwarza pełny census i sprawdza
seale plików po odczycie. Brak, duplikat, inna kolejność lub zmieniony scope
przerywa operację. Wszystkie sześć modeli i wszystkie deklarowane segmenty,
w tym puste i wykluczone, pozostają w `trials/trial-NNN.json`. Usunięcie surowego
indeksu następuje dopiero po potwierdzonym raporcie consume i fsync artifactu.

`parents.json` zachowuje receipt i pełny census kontekstu. Verifier wymaga zgodnego
raportu każdej próby, kompletu modeli/grup, poprawnych counts i skończonych metryk.
Metryki bez obserwacji lub poprawnego mianownika pozostają `null`; wartości zero
nie mogą zastąpić nieokreślonego wyniku. Odczyt ukończonego receipt kontekstu jest
kontrolą lineage wcześniejszego obliczenia i nie przyznaje nowego dostępu Source
lub final. Output evaluatora nie może obejmować ani zmieniać wejściowego bundle.

Kontrole używają wcześniej eksponowanych Source1.2 demand/physical, wszystkich
kluczy dwóch originów i 14 horyzontów oraz dwóch zadeklarowanych prób z kontrolnymi
forecastami. Krótkie fixture nie zapewniają historii i dojrzałych etykiet dla
tego zakresu: eligible rows wynosi zero. Te kontrole dowodzą pełnego join i
zachowania wykluczeń, a nie kwalifikacji jakości. Równania z eligible rows oraz
odmowę brakujących/nieskończonych metryk sprawdzają osobne jawne kontrole.
Pozytywnej publicznej kampanii na pełnym canonical Source jeszcze nie wykonano.
Nowe kontrole granicy API potwierdzają rezerwację i koszt odmowy przed Source
context lub role workerem. Szczegóły, także nieudanych kontroli, zapisano w
[evidence 09-36](evidence/09-36-raw-context-consumption.json).

Wire v1–v23 i zamknięte komponenty AI08 pozostają bez zmian. Nowe dane są
opcjonalnymi artifactami diagnostycznymi w istniejącym receipt v20; jego
`critical_segment_inventory_complete`, `block_uncertainty_complete`,
`quality_qualified` i `stage_ready` nadal są false. Pełne segmenty wybranego,
skalibrowanego forecastu, paired uncertainty, rzeczywiste anomaly/stockout,
kwalifikacja canonical i kompletna kampania/lifecycle nadal wymagają wykonania.

Zaktualizowany wheel zawiera 591 zgodnych modułów i 17 schematów v19–v23.
Faktycznie zainstalowany pakiet importuje publiczny evaluator z parametrami
kontekstu i nowe helpery bez TensorFlow/MLflow, generacji Source lub Project fitów.
77 istniejących plików wire i 129 plików źródeł/wire stockout pozostają byte-for-byte
bez zmian. Mypy712 plików, Ruff, format1210 plików, schematy i dokumentacja
przeszły. Kolekcja zmienionych modułów testowych zawiera129 unikalnych kontroli;
opisane wykonania częściowo się pokrywają i ich liczby nie sumują się.
Dokładny head, pełne CI i chroniona publikacja na main pozostają wymagane.

W przyroście 09.37 publiczny evaluator przyjmuje również rzeczywistą,
zamrożoną `uncertainty_policy`. Jej hash i nominal coverage muszą odpowiadać
prerejestrowanej recepturze, a pełny context bundle musi mieć zakończoną
operację będącą prerequisite oceny. Polityka ani kontekst nie trafiają do
workera predykcji. W fazie finalize ten sam jeden odczyt indeksu actuals
łączy ostateczne, skalibrowane mean/median/interval i zamrożone referencje
z każdym kluczem pełnego strumienia kontekstu.

Każda zadeklarowana grupa zachowuje liczebności, hashe, porównanie funkcjonalne
i oddzielne raporty resamplingu bloków czasu oraz całych serii. Puste grupy,
wykluczenia, za mało klastrów, brakujące predykcje i nieokreślone mianowniki
pozostają jawne. Żadnej grupy nie usuwa się dla zmieszczenia w budżecie:
wspólny limit SQLite obejmuje przygotowane wejścia, actuals, projection i wszystkie
indeksy klastrów. Nadzorca zachowuje RSS całego drzewa, scratch i czas fazy.
Te analizy wrażliwości nie gwarantują nominalnego pokrycia ani niezależności
obu osi; ich zakres i ograniczenia pozostają przypięte do zamrożonej polityki.

Nowy receipt `ai09-campaign-forecast-evaluation-receipt-2.0.0` w v24 wymaga
pełnego kontekstu, jego census/trace oraz polityki niepewności. Kompletność obu
inwentarzy jest true dopiero po całym przebiegu i weryfikacji artefaktu.
Verifier odtwarza numerical gates z agregatów, sprawdza wszystkie grupy,
obie metody, scope, seeds i zgodność punktowych delt z porównaniami.
`quality_qualified` wynika z wymaganych bramek; `promotion_allowed` i `stage_ready`
pozostają false. Receipt v20/1 nadal ma dawny niekompletny zakres i nie może
zostać podniesiony zmianą flag. Wspólny strażnik przed final dodatkowo wymaga
`quality_qualified=true` oraz trwałego context completion i pełnego bundle.

Kontrola publicznego API używa zadeklarowanych parent/model/monitor controls,
zamrożonych przed utworzeniem receiptów. Niezależna kontrola finalize ponownie
wykorzystała wcześniej odczytane Source1.2 i gotowy kontrolny projection:
645 kluczy, wszystkie 63 grupy, obie metody na grupę, jeden actual-index pass,
bez zmiany źródłowego bundle, generacji ani fitów. Eligible rows nadal wynosi zero;
pełny inwentarz raportów nie stanowi kwalifikacji jakości. Nie powtórzono parsowania
roli przy poprawce brakującego runtime w prywatnym żądaniu; koszt pierwszej
nieudanej kontroli zachowano z nieznanym czasem, a nie zerem.

97 wcześniejszych testów przeszło w 47.40 s; kombinacja nowej ścieżki i kontroli
evaluation/generation/final-export ma 77 passed w 161.19 s, a późniejsze kontrole
resource/seal/underpowered selection 11 passed w 26.37 s. Te liczby zawierają
powtarzające się testy. Mypy 715 plików, Ruff, format i schema snapshots przeszły;
wheel ma identyczne bajty 594 modułów i 75 schematów evaluation, w tym v24.
Faktycznie zainstalowany package importuje publiczny evaluator i v24 bez
TensorFlow/MLflow. Szczegóły, porażki kontrolne i dalszy zakres zachowuje
[evidence 09.37](evidence/09-37-complete-forecast-robustness.json).
Pełna kampania canonical, anomaly/stockout, final 42/137/2026 oraz MLflow/cards/
lifecycle nadal pozostają wymagane. Nowy head i wynikowy main wymagają własnego CI.
