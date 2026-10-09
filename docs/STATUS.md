# Aktualny status

**2026-10-09 — przygotowanie AI 09 capacity 1.10; etap nadal not_ready.**
[Receptura](reference/ai09-development-capacity-v1.10.json) przypina audytowane
poprawki CPU Source PR111. [09.65](evidence/09-65-source-cpu-capacity-preparation.json)
zachowuje dziewięć rzeczywistych porażek, pełny profil oraz limit 12 GiB
z rezerwą 1 GiB. 62 kontrole receptury przeszły, w tym odrzucenie startu
poniżej 13 GiB dostępnej pamięci. Nowej diagnostyki nie uruchomiono;
pełny odbiór dokładnych head i wynikowych main obu repozytoriów jest wymagany.

**2026-10-09 — AI 09: in_progress / not_ready.**
[Pełne historyczne cechy anomaly](ai09-native-anomaly-features.md) odtwarzają
wszystkie zadeklarowane dni przez natywne funkcje, z 28 dniami historii.
Pełny kontekst pozostaje zweryfikowany na dysku, a jedna grupa trafia do
natywnego doboru wersji. [09.63](evidence/09-63-native-anomaly-feature-preparation.json) zachowuje
dwie początkowe awarie własnego limitu oraz poprawkę zapisu pełnych Point.
Poprawiona wersja zaliczyła 98 kontroli natywnych i 64 zainstalowanego pakietu.
Pełne CI i publikacja
pozostają wymagane. Integracja prawdy offline i całej kampanii jest otwarta.

Pełny lokalny przebieg zaliczył 5038 testów z 49 udokumentowanymi pominięciami.
Kontrole TensorFlow wymagają większej dostępnej rezerwy pamięci; ich pięć odmów
startu pozostaje zapisanych. 43 kontrole obsługi nowego producenta i 43 kontrole
końcowej paczki przeszły. [Skan historii](evidence/09-64-historical-scanner-verification.json)
zachowuje dokładną klasyfikację fałszywych alarmów i kontrolę wykrywania nowych
poświadczeń. Chroniony odbiór pełnego CI nadal jest wymagany.

[Bramka dnia raw-DQ na dysku](ai09-native-anomaly-day-gate.md) łączy pełne
indeksy dni i zaakceptowanych faktów z niezmienionym `DayGate.point`.
Odtwarza globalną nieprzypisaną kwarantannę od rzeczywistej chwili odbioru.
Replay zachowuje odrzucone capture i sprawdza wyniki wobec hashy liczonych
podczas przetwarzania. [09.61](evidence/09-61-native-disk-day-gate.json)
zapisuje 60 kontroli, 60 z paczki i 43 regresje replay, bez pominięć.
Pełne CI i publikacja pozostają wymagane; integracja cech i prawda offline
i pełna kwalifikacja kampanii są nadal otwarte.

[Pełna projekcja dni anomaly](ai09-native-anomaly-days.md) odtwarza wszystkie
serie i okres reklamacji z całego publicznego parenta, przez natywne reguły.
Poprawiono awarię deklaracji serii bez sprzedaży, bez tworzenia pustej kohorty
reklamacji. [09.60](evidence/09-60-native-full-day-projection.json) zapisuje
96 kontroli z dotychczasową kwalifikacją dnia oraz końcowy odbiór retencji:
48 kontroli i 48 z paczki, bez pominięć. Pomocnicze indeksy wejściowe są
zwalniane po zachowaniu wszystkich dni. Pełne CI i publikacja są nadal
wymagane; projekcja nie zastępuje kwalifikacji raw-DQ, causal Point census
ani pełnej kampanii.

[Pełny publiczny parent anomaly](ai09-native-anomaly-parent.md) wykonuje
rzeczywisty replay całego snapshotu i curated, indeksuje wszystkie sales
i return claims oraz zachowuje natywne globalne UUID i klucze biznesowe.
Przywraca dokładną skalę kwot z typu Arrow przed generowaniem wire i UUID.
42 kontrole na publicznych fixture 1.1/1.2 i 42 kontrole z paczki przeszły;
[09.58](evidence/09-58-native-anomaly-public-parent.json) zachowuje także
12 początkowych błędów i ich poprawki. Pełne CI i publikacja pozostają
wymagane. Adapter nie zastępuje kwalifikacji
dnia, Point census, truth, rzeczywistego dziennika ani jakości modeli.

[Replay capture anomaly](ai09-native-anomaly-replay.md) przenosi globalne
receipts, tożsamość zdarzeń, fakty i rewizje na dysk, zachowując natywny
kernel zdarzeń AI 07. W pamięci zostaje jedna ograniczona grupa agregacji.
Do pełnej kampanii nadal potrzebne są powiązanie publicznych parentów
z dziennikiem, kwalifikacja dni, Point census i offline truth. Lokalny odbiór
opisuje [09.57](evidence/09-57-native-anomaly-disk-replay.json);
194 kontroli z regresjami i 43 z paczki przeszły bez pominięć. Publikacja
i pełne CI są nadal wymagane.

[Natywna ocena anomaly](ai09-native-anomaly-evaluation.md) podłącza pełny zadany
census do replay modelu i metryk observation/episode. 81 kontroli i 25 z
zainstalowanej paczki przeszło lokalnie. Projektowa integracja i kwalifikacja
całości nadal pozostają otwarte; poprawka czeka na publikację i pełne CI.

[Audyt kwalifikacji trzech zastosowań](ai09-native-selection-verification.md)
zamyka lukę między integralnością plików a dowodem jakości anomaly/stockout.
Poprawne hashe i zadeklarowane flagi nie otwierają final: do czasu pełnej
natywnej weryfikacji Project bramka jawnie odrzuca te dwa receipts.
Typowana weryfikacja forecast i zamknięte odbiory AI 07–08 pozostają zachowane.

[Przygotowanie 09.54](evidence/09-54-ledger-query-capacity-preparation.json) i
[receptura 1.9](reference/ai09-development-capacity-v1.9.json) przypinają Source
`a7b850a0` z audytu indeksów zapytań, okresów fizycznych i jednostek produktów.
Niezależny verifier buduje własne koszyki dzienne. Source zaliczył 274 kontrole;
AI 106 i 22 z zainstalowanego wheela. Małe pary zachowują wszystkie porównane
hashe i mierzą także dodatkową pamięć indeksów. Receptura zachowuje 12 GiB,
pełne wymiary, pozostałe budżety i wszystkie osiem poprzednich porażek.
[Odbiór i start 09.59](evidence/09-59-query-capacity-acceptance-start.json)
wiąże pełne CI head i wynikowych main: Source PR110 `a412a912` (21 success
i 4 zadeklarowane scoped skips), AI PR62 `1f2d4f69` (17/17 success).
Jedna próba [37853501527](https://github.com/Oskar-Stachowski/retailops-ai-intelligence/actions/runs/37853501527)
zakończyła się `wall_limit`, bez ukończenia pierwszej fazy.
[Wynik 09.62](evidence/09-62-development-capacity-ninth-run.json) zapisuje
3600.53 s, RSS drzewa 9061416960 B i dolną granicę CPU workera 3599.51 s.
Nie uruchomiono treningów, dziennika Project ani świeżego final.
Pełna pojemność i kwalifikacja modeli nadal nie są potwierdzone.

[Receptura 1.8](reference/ai09-development-capacity-v1.8.json) ma na polecenie
użytkownika limit RSS drzewa **12 GiB** (12 884 901 888 B) i rezerwę 1 GiB.
PR60 scalono normalnie jako `4ce8a3cf`; pełne CI dokładnego head i tego main
zakończyły się **17/17 success**, przed pojedynczym uruchomieniem pomiaru.
[Wynik ósmej próby 09.53](evidence/09-53-development-capacity-eighth-run.json)
wiąże zweryfikowany artefakt [run 37824794411](https://github.com/Oskar-Stachowski/retailops-ai-intelligence/actions/runs/37824794411): **`wall_limit`**,
0/5 ukończonych faz, 3600.58 s.
Próbkowany peak drzewa wyniósł 9066307584 B, a dolna granica CPU workera 3599.61 s.
Profil 365 × 100 × 5 × 3, Source, walidacja i pozostałe budżety zachowano.
Zachowano siedem wcześniejszych porażek, receptury 1.0–1.7 i nieuruchomioną 1.6.
Nie wykonano automatycznego retry. Wynik nie kwalifikuje pełnego ai-training,
modeli ani poprzedniego limitu 8 GiB. Nowe projektowe fity i odczyty świeżego
final wynoszą zero; AI 09 pozostaje `not_ready`.

[Audyt przed kolejnym canonical](ai09-capacity-audit.md) wprowadza bezpieczne
poprawki pamięci i kosztu we wszystkich pięciu fazach. Pełny profil, limity,
wszystkie wcześniejsze porażki i dotychczasowe bramki pozostają zachowane.
Audyt przeszedł pełne CI dokładnych head i wynikowych main obu repozytoriów.
[Dalszy audyt retencji](evidence/09-49-projection-retention-capacity-preparation.json)
zwalnia pierwotne dane sprzedaży po rekonsyliacji, przed projekcją. Source zaliczył
trzy kontrole czasu życia i 67 regresji, a AI 95 kontroli oraz 41 z paczki.
Małe pomiary potwierdzają identyczność danych i niższą pamięć; wynik pełnego
pomiaru opisano poniżej. [Receptura 1.7](reference/ai09-development-capacity-v1.7.json)
przypina Source `4dacf040` z PR109 i zachowuje pełne wymiary oraz limity.
1.6 nie została uruchomiona i pozostaje niezmiennym zapisem przygotowania;
wszystkie sześć wcześniejszych porażek pozostaje zapisanych. Końcowy audyt jest
na Source main `c04ca954` (PR109) i AI main `89d6c8f2` (PR58), po pełnym
odbiorze head i main: Source 21 success + 4 scoped skips, AI 17/17 success.
[Dowód odbioru i startu 09.50](evidence/09-50-audit-acceptance-capacity-seventh-start.json)
zapisuje pojedynczą siódmą próbę [37807749014](https://github.com/Oskar-Stachowski/retailops-ai-intelligence/actions/runs/37807749014)
na niezmiennej referencji 1.7, z pełnym profilem i limitami.
[Wynik siódmej próby 09.51](evidence/09-51-development-capacity-seventh-run.json) wiąże odebrany i zweryfikowany artefakt [run 37807749014](https://github.com/Oskar-Stachowski/retailops-ai-intelligence/actions/runs/37807749014).
Pomiar zakończył się `tree_rss_limit`, z 0/5 ukończonych faz. Cały przebieg trwał 1702.79 s;
próbkowany peak drzewa wyniósł 8635432960 B, a dolna granica CPU workera 1701.93 s.
Pełna pojemność pozostaje niepotwierdzona. Zachowano koszt porażki; nie wykonano automatycznego retry.
Pełny profil 365 × 100 × 5 × 3, limity, rezerwy i wcześniejsze koszty zachowano.
Nowe projektowe fity i odczyty świeżego final wynoszą zero; AI 09 pozostaje `not_ready`.
Pełna kampania naukowa pozostaje otwarta.
[Przyrost 09.41](evidence/09-41-full-scenario-portfolio.json) dodaje
[pełne portfolio v25](ai09-full-scenario-portfolio.md): 12 pełnych źródeł
ordinary/demand/physical, jeden dziennik i wspólny freeze. Brak wariantu,
nierówny budżet, mniejszy profil, niepełna ocena development lub pominięty
końcowy seed blokują właściwą operację. Zaliczone kontrole protokołu/dziennika
i native bindings nie oznaczają wykonania kampanii.
[Przyrost 09.42](evidence/09-42-portfolio-forecast-evaluation.json) podłącza
publiczny forecast development evaluator do wszystkich trzech wariantów:
pełny protokół i receptura wiążą oddzielne źródła treningu i oceny w workerze,
trwałym artefakcie oraz publicznym verifierze. Starsze guardy i 81 wcześniejszych
plików JSON kontraktów zachowano. Zaliczone 14 nowych kontroli, 146 regresji
i dwa przebiegi z zainstalowanej paczki używają małych eksponowanych fixture
oraz kontrolnych modeli. Pełne dane, rzeczywiste evaluatory anomaly/stockout
i evidence wszystkich wariantów pozostają otwarte.
[Przyrost 09.43](evidence/09-43-portfolio-selection-evidence.json) sprawdza przed
publicznym dostępem do final wszystkie dziewięć ocen development, ich artefakty
i wspólne wybrane komponenty. Zaliczył 17 nowych kontroli, 118 regresji,
32 kontrole generacji oraz trzy kontrole z zainstalowanego wheela. Dotychczasowy
hash freeze i 84 pliki JSON kontraktów zachowano. To odbiór granicy na jawnych
kontrolach; nie zastępuje rzeczywistej naukowej kampanii.
[Przyrost 09.44](evidence/09-44-required-group-policy.json) przypina wymagane
grupy do wszystkich 12 receptur przed wynikami i wiąże politykę z publicznym
forecast evaluator, workerem, receiptem v27 i verifierem. Zachowuje całą
diagnostykę i blokadę przy niedostatecznej próbie krytycznej. Kontrolny odbiór
nie potwierdza efektów pełnych scenariuszy ani projektowego final.
[PR #48](https://github.com/Oskar-Stachowski/retailops-ai-intelligence/pull/48)
scala Source annotations, pełny context bundle i ocenę wszystkich surowych prób
na main `a59b2667` po 17/17 success dokładnego head `456dbfaf`.
[CI wynikowego main](https://github.com/Oskar-Stachowski/retailops-ai-intelligence/actions/runs/37734313726)
zakończyło się pełnym 17/17 success wraz z required-result; inventory odpowiada
wcześniejszemu main `ce81b8d5`, także 17/17 success.
[Przyrost 09.37](evidence/09-37-complete-forecast-robustness.json) podłącza
pełne segmenty wybranego, skalibrowanego forecastu i dwie analizy niepewności
do publicznego evaluatora oraz receipt v24.
[PR #49](https://github.com/Oskar-Stachowski/retailops-ai-intelligence/pull/49)
został chronioną ścieżką scalony jako main `bc507330` po pełnym 17/17 success
dokładnego head `f57f4c56` oraz zaakceptowanym poprzednim main `a59b2667`.
[CI wynikowego main](https://github.com/Oskar-Stachowski/retailops-ai-intelligence/actions/runs/37739064548)
zakończyło się pełnym 17/17 success wraz z required-result. Starszy PR46 zamknięto,
ponieważ jego kod jest już przodkiem zaakceptowanego main z PR48.

[Piąta próba pełnego ai-dev](evidence/09-38-development-capacity-fifth-run.json),
run `37731200719`, zakończyła się limitem RSS 8 GiB po 1836.06 s;
zero z pięciu faz ukończono. Zachowano artefakt, metadane stosów i wszystkie
wcześniejsze koszty. Nie zmniejszono profilu.
Pełny planowany Source i ai-training pozostają niezakwalifikowane.
[Receptura 1.5](reference/ai09-development-capacity-v1.5.json) i
[receipt 09.39](evidence/09-39-event-release-capacity-preparation.json)
przygotowują odrębny pomiar na Source `ab4d5690`, który zwalnia typowane
zdarzenia po ich przetworzeniu. Pierwotny Source head `ab4d5690` ma 33 zaliczone
native kontrole i pełny scoped Required CI: 21 success i 4 deklarowane skips.
Poprzedni Source main `cd5a9dbb` również ma 21 success i 4 scoped skips.
[Przyrost 09.40](evidence/09-40-planned-source-cache-integration.json) dodaje
cached execution oryginalnych planów demand i physical Source 2.8 oraz wybór
tego backendu według przypiętego producenta. Zachowuje komplet 58 tabel,
pełne scenariusze i zwykły niezależny replay. Dla już odsłoniętych kontroli
trzech seedów zaliczono 21 różnych kontroli Source w opisanych osobnych runach
oraz 76 testów AI; 6 istniejących warunkowych fixture annotations jest skipped.
Source PR107 integruje przyrost z nowszym main. Pierwszy head `345a7cf3` ma
nieudany pełny CI: dwa testy historycznych IDs pomijały dawny hash zależności
po zaakceptowanej aktualizacji main; pozostałe 1049 testów tego kroku przeszło.
Poprawka testowa `fe9d404d` zachowuje oba oryginalne golden IDs i komplet
hashów 58 tabel oraz sprawdza bieżący fingerprint zależności. Wszystkie 17
testów zmienionego modułu przeszło. Produkcja i aktualizacje zależności pozostają
zachowane.
[Source CI pierwszego head](https://github.com/Oskar-Stachowski/retailops-cloud-native-platform/actions/runs/37743193615)
jest zachowanym nieudanym runem;
[CI poprawki](https://github.com/Oskar-Stachowski/retailops-cloud-native-platform/actions/runs/37752460842)
zakończyło się 21 success i czterema deklarowanymi scoped skips. PR107 został
scalony chronioną ścieżką jako Source main `3d13a4dd`;
[CI wynikowego main](https://github.com/Oskar-Stachowski/retailops-cloud-native-platform/actions/runs/37759748828)
zakończyło się pełnym inventory: 21 success i cztery deklarowane scoped skips,
wraz z required-result. [PR #50](https://github.com/Oskar-Stachowski/retailops-ai-intelligence/pull/50)
scalono chronioną ścieżką jako `f0088e08` po pełnym 17/17 success dokładnego
head `daf8d605` i zaakceptowanego base `bc507330`.
[CI tego main](https://github.com/Oskar-Stachowski/retailops-ai-intelligence/actions/runs/37743593906)
zakończyło się pełnym 17/17 success. Publikacja receptury nie oznacza jej wykonania.
[PR #51](https://github.com/Oskar-Stachowski/retailops-ai-intelligence/pull/51)
scala wybór pinned cached Source 2.8 jako main `ab77bb1d`, po pełnym 17/17
success head `201ea8ed` i zaakceptowanego base `f0088e08`.
[CI nowego main](https://github.com/Oskar-Stachowski/retailops-ai-intelligence/actions/runs/37749318501)
zakończyło się pełnym 17/17 success.
[PR #52](https://github.com/Oskar-Stachowski/retailops-ai-intelligence/pull/52)
scala pełne portfolio jako main `656d7ddc`, po pełnym 17/17 success dokładnego
head `67324ab0` i zaakceptowanego base `ab77bb1d`.
[CI tego main](https://github.com/Oskar-Stachowski/retailops-ai-intelligence/actions/runs/37755648727)
zakończyło się pełnym 17/17 success wraz z required-result.
Adapter forecastu i kontrola 09.43 zostały wspólnie scalone przez
[PR #54](https://github.com/Oskar-Stachowski/retailops-ai-intelligence/pull/54)
jako main `ba377b58`, po pełnym 17/17 success dokładnego head `fa4ccd6f`
i zaakceptowanego base `656d7ddc`.
[CI wynikowego main](https://github.com/Oskar-Stachowski/retailops-ai-intelligence/actions/runs/37768482642)
zakończył się pełnym 17/17 success. PR53 został włączony przez PR54.
PR55 został normalnie scalony jako `fe830799`, po pełnym 17/17 success
dokładnego head i base; jego wynikowy main wymaga własnego pełnego CI.
[Próba 1.5](https://github.com/Oskar-Stachowski/retailops-ai-intelligence/actions/runs/37765329151)
została uruchomiona raz na osobnym runnerze, na niezmiennym AI `656d7ddc`
i oryginalnym Source `ab4d5690`, po pełnym odbiorze CI i ancestry.
Pomiar zakończył się szóstą porażką `tree_rss_limit`, po 2967.75 s,
z peak drzewa 8596701184 B i zero ukończonych faz.
[Receipt 09.45](evidence/09-45-development-capacity-sixth-run.json) zachowuje
artefakt i koszt. Wymagany po tej porażce audyt pięciu faz, bezpieczne poprawki,
zgodność danych i pełny odbiór CI obu repozytoriów zakończono przed próbą 1.7.
Nie zmniejszono danych ani nie zwiększono limitów.
Projektowy journal nie został zainicjalizowany, nowe projektowe fity wynoszą zero,
a świeży final test pozostaje zamknięty. Do zamknięcia pozostają pełna kampania
trzech zastosowań, pokrycie scenariuszy/seedów, raporty i lifecycle oraz odbiór main.
AI 07–08 i wcześniejsze etapy pozostają READY i zamknięte.

**2026-10-07 — AI 10: READY, pełny odbiór integracji.**
[Końcowy raport](evidence/ai10-bounded-acceptance.json) oraz
[mapa wszystkich siedmiu wymagań](evidence/ai10-final-ready-review.json) wiążą
snapshot/REST/stream, Source SQL/capture → ten sam TLS/SCRAM broker → AI SQL/ACK
z korektą 4 → 7 oraz oryginalne publishery SQL dla 40 stockout, 1232 anomaly
i 56 forecast. Wszystkie native payloady, lineage, projekcje i duplikaty
sprawdzono przez rzeczywiste Source SQL, authenticated TCP API i istniejący built UI.
[Pełny V12](evidence/ai10-native-v12-22de575-accepted.json), run `37665627162`,
zaliczył 273 boundary tests i rzeczywisty test Source bez failures/errors/skips:
56 oryginalnych ACK, dwie strony UI, development warning oraz live revocation.
Wszystkie sześć etapów oryginalnego odbioru i atomowy rollback passed.
Własne kontenery/volumes, sześć małych handoff objects i unikalny secret usunięto;
oryginalne S3, modele i inne sesje zachowano.

[AI main `b0e2de`](evidence/ai10-ai-b0-main-ci.json) ma 17/17 success,
[pełny Source baseline](evidence/ai10-source-code-main-ci.json) 30/30,
a [wcześniejszy scoped Source main](evidence/ai10-source-b723489-main-ci.json)
21 success i 4 celowe skipped. [Zgodność runtime](evidence/ai10-native-runtime-main-compatibility.json)
wiąże dokładne obiekty odebranych commitów z integracją.
[Integracja nowszego AI09 main `5ed0544`](evidence/ai10-ai09-5ed-main-integration.json)
zachowuje wszystkie 21 odebranych komponentów AI10. Powyższe receipts są
historycznymi baseline’ami; nowy zintegrowany head i wynikowy main wymagają własnego CI.
Końcowe rekordy publikuje [AI #38](https://github.com/Oskar-Stachowski/retailops-ai-intelligence/pull/38)
i [Source #104](https://github.com/Oskar-Stachowski/retailops-cloud-native-platform/pull/104)
przez normalne protected merges i Required CI dokładnych headów oraz main.
Dowód ostatniej publikacji stanowią aktualne checks i merge commity w GitHub;
nie dopisujemy do tego rekordu niewykonanego przyszłego CI.
V12 zachowuje oryginalne quality `not_ready` i wyłącznie zaakceptowany development
namespace. Sugestie są jawnym fixture AI10; rzeczywisty producent należy do AI12.
[Overlay istniejących projektów](ai10-acceptance.md#overlay-istniejących-projektów)
zachowuje prywatne bazy, jeden broker Source i jego external network.
AI09 pozostaje osobnym otwartym etapem. Wcześniejsze wpisy opisują historię.

**2026-10-07: AI 09 jest in_progress / not_ready; integracja po zamknięciu AI 07–08 jest na main.**
Runner generacji z PR #35 jest scalony jako `16a02ae` po pełnym Required CI
17/17 success na `4238b2f`. CI dokładnego main `37657724470` ma 17/17 success.
Source main `b7234899` ma pełny odbiór: 21 success i 4 przewidziane skipped.
Eksporter PR #36 jest scalony jako `b0e2de1` po 17/17 success na `f0fbbc9`;
CI dokładnego nowego main `37667669693` trwa. [Receipt publikacji](evidence/09-24-generation-publication.json)
zachowuje również porażkę przydziału runnerów w poprzednim CI treningowym.
Native kontrola generacji `37664228881` zakończyła pięć faz dla obu profili;
verify ujawnił błędną ścieżkę snapshotu. Poprawka ma 72 zaliczone testy,
w tym pełny replay rzeczywiście zaimportowanego publicznego smoke.
[Receipt 09.26](evidence/09-26-generation-snapshot-verification.json) zachowuje
koszty porażki. Nowy native run `37669667688` na `e3bc68e` zaliczył wszystkie
sześć faz dla obu profili. Scoring `e4d6371` ma pełne 17/17 success i 7 native
testów CPU w 144.78 s. Wspólny PR #39 do main obejmuje także trening PR #37
i poprawkę PR #40; nowy head integracji i jej main wymagają własnego pełnego CI.
AI 09 nadal wymaga projektowej kampanii i wyników końcowych.

[PR #33](https://github.com/Oskar-Stachowski/retailops-ai-intelligence/pull/33)
jest scalony jako `a128bb38` po pełnym zielonym CI jego head `be3b264`.
[Required CI dokładnego main](https://github.com/Oskar-Stachowski/retailops-ai-intelligence/actions/runs/37635615858)
jest zielony, z kompletem 15 wymaganych jobów. PR #34 zachował komplet bramek i zaliczył pełny odbiór swojego main
`89b64d2`; AI 07–08 pozostają zamknięte.
[PR #29](https://github.com/Oskar-Stachowski/retailops-ai-intelligence/pull/29)
ma komplet 14 zielonych jobów dla `4a0a6b5` i został scalony jako `5216e31`.
[CI dokładnego merge/main](https://github.com/Oskar-Stachowski/retailops-ai-intelligence/actions/runs/37592860960)
jest zielone, z kompletem 14 wymaganych jobów. [PR #30](https://github.com/Oskar-Stachowski/retailops-ai-intelligence/pull/30)
ma także 14 zielonych jobów na `9bb10f0` i jest scalony jako `3329a81`;
[CI dokładnego merge/main](https://github.com/Oskar-Stachowski/retailops-ai-intelligence/actions/runs/37604312440)
jest zielone, z kompletem 14 wymaganych jobów. Nowy [eksporter fizyczny](physical-forecast-export.md)
odtwarza cechy i etykiety wszystkich pięciu ról z jednego prywatnego replayu.
Jego kontrolny profil `ai-load` nie kwalifikuje pełnego `ai-training` ani kampanii.
[Przyrost 09.12](evidence/09-12-prospective-campaign-journal.md) zachowuje historię
11 dawnych prób bez odtwarzania utraconych budżetów i dodaje trwały dziennik
prospektywnej kampanii. 52 testy odłączonego wheela przeszły, w tym SIGKILL,
fsync, konkurujące klienty oraz blokada końcowych danych przed freeze. To odbiór
mechanizmu i kontrolowanych metadanych; rzeczywista kampania jeszcze nie ruszyła.
[Integracja 09.11](evidence/09-11-main-integration.md) ma 420 zaliczonych testów,
114 kontroli po zmianie SciPy i 3 rzeczywiste testy TensorFlow/MLflow/reload.
Required CI integracji i 09.12 jest zielony na dokładnym headzie PR #29;
odbiór dokładnego merge/main `5216e31` także ma komplet 14 zielonych jobów.
AI 07 i AI 08 są gotowe na `origin/main`. Gałąź AI 09 integruje ich kontrakty,
kontrole i pakowanie; nie otwiera ponownie odbiorów wcześniejszych etapów.
[Ostatni odbiór AI 09.10](evidence/09-10-forecast-source-versions.md) zachowuje
historyczny stan zasobów i CI. Jego commit `7f10f8e` ma już zielone
[Required CI214](https://github.com/Oskar-Stachowski/retailops-ai-intelligence/actions/runs/37274516620),
a kontrola miejsca 2026-10-07 wykazała około 82 GiB wolnego, ponad rezerwę 50 GiB.
Te fakty usuwają wcześniejsze przeszkody, lecz nie zastępują odbioru większego
profilu ani końcowej kampanii.

[Reader kampanii](evidence/09-14-audited-development-export.json) wiąże plan
eksportu v12 z zakończoną generacją i trwale
rezerwuje pełny parent read przed I/O. Kontrolowane testy powiązań i wcześniejszy
publiczny source-replay potwierdzają mechanizm; canonical przebieg tego API,
runner generacji/curation i reszta kampanii nadal nie są odebrane.
Po integracji [optymalizacji](evidence/ai09-performance.md) jest 272 native passed
oraz 198 testów wheela w dwóch częściach, z identycznymi bajtami 499 modułów
i schematów v12 w wheelu oraz kodzie źródłowym. Mniejszy RSS i CPU kanonicznego JSON nie są dowodem szybszego
całego eksportu: kontrolna para wall ma wzrost 3.37%. Reader i optymalizacje
mają pełne 14 zielonych jobów [PR #31](https://github.com/Oskar-Stachowski/retailops-ai-intelligence/pull/31)
na `db3aeb8` i są scalone jako `e1f864c`.
[CI dokładnego main](https://github.com/Oskar-Stachowski/retailops-ai-intelligence/actions/runs/37615123521)
jest zielone, z kompletem 14 wymaganych jobów na `e1f864c`.
To odbiór komponentów; pełna kampania nadal jest wymagana.

Do zamknięcia pozostają: pełny audytowany eksport rzeczywistych pięciu ról,
integracja fitów i kalibracji, ocena RF/HGB/baseline/TensorFlow na trzech seedach,
robustness/niepewność/koszty, wspólne raporty trzech zastosowań, decyzje lifecycle
oraz pełny odbiór dokładnego commita na `main`. Dawne prywatne katalogi AI 09
w `/private/tmp` nie istnieją. Kod i opublikowane dowody są w Git; ciągłość
budżetów oraz ekspozycji wymaga odtworzenia dzienników lub jawnego,
konserwatywnego rozliczenia brakującej historii przed nowymi eksperymentami.
09.12 przygotowuje takie rozliczenie z przypiętych receiptów i wymaga jawnego
powiązania go w nowym prospektywnym protokole; nie kwalifikuje świeżości danych.
Nowe CI zachowuje wszystkie kontrole AI 07–08 oraz osobny wymagany job TensorFlow.

[Pomiar pełnego development](ai09-development-capacity.md) ma przygotowany
osobny runner oraz [kontrolny odbiór](evidence/09-15-development-capacity-preparation.json):
14 testów supervisora, w tym rzeczywistych procesów i wszystkie pięć faz na małym `ai-load`.
[Pierwsza próba canonical `ai-dev`](evidence/09-16-development-capacity-first-run.json)
przekroczyła 4 GiB RSS po 127,875 s podczas generacji; nie ukończyła źródła.
Plan, porażka i koszt są zachowane bez retry; potrzebny jest osobny pomiar
większego prospektywnego budżetu albo ograniczenie pamięci producenta.
[Osobna receptura 1.1](reference/ai09-development-capacity-v1.1.json) planuje
próbę 8 GiB na podstawie zmierzonej dostępnej pamięci runnera, zachowując
pierwszy plan i porażkę oraz wszystkie stare limity i wymagania jakości.
Jej [wynik](evidence/09-18-development-capacity-second-run.json),
[run 37613368332](https://github.com/Oskar-Stachowski/retailops-ai-intelligence/actions/runs/37613368332)
na `d88d4a2`, zakończył się `wall_limit` po 3600.552 s podczas generacji.
Próbkowany peak własnego drzewa wyniósł 7679963136 B (7.15 GiB), poniżej 8 GiB;
nie ukończono żadnej fazy. Obie porażki i ich koszty są zachowane. Następny
pomiar wymaga osobnej receptury na zwalidowanym producencie.
[Source PR #101](https://github.com/Oskar-Stachowski/retailops-cloud-native-platform/pull/101)
na `5bec26f9` ogranicza kopie wejść i dzienny replay. Zaliczył 456 testów
regresji, osobne 16 kontroli parity kohort oraz 3 kontrole known plans na seedach
42/137/2026. Pełny [Required CI dokładnego head](https://github.com/Oskar-Stachowski/retailops-cloud-native-platform/actions/runs/37623701164)
jest zielony; zwykły chroniony merge opublikował `cbcac6eb` na source `main`.
[Required CI dokładnego source main](https://github.com/Oskar-Stachowski/retailops-cloud-native-platform/actions/runs/37629010997)
zakończył się pojedynczą porażką testowego odczytu koordynatora Kafka;
742 kontrole API zaliczono. [Source PR #103](https://github.com/Oskar-Stachowski/retailops-cloud-native-platform/pull/103)
na `a28101bf` dodaje ograniczoną poprawkę tego odczytu i rzeczywistą obsługę
source2.8 z known plans. Zaliczył cały [Required CI](https://github.com/Oskar-Stachowski/retailops-cloud-native-platform/actions/runs/37646475371):
21 success i 4 przewidziane skipped; chroniony merge opublikował `b7234899`.
[CI dokładnego Source main](https://github.com/Oskar-Stachowski/retailops-cloud-native-platform/actions/runs/37651259188)
jeszcze trwa i pozostaje wymagany przed nowym pomiarem canonical.
Pięć kontrolnych par daje −14.81% alokacji Python podczas kopii
i −89.62% CPU projekcji dziennych snapshotów, z identycznymi wynikami.
To pomiary komponentów; pełny RSS i koszt pipeline na nowym producencie nadal
wymagają odbioru. Wykorzystanie istniejącej szybkiej ścieżki AI08 sprawdzono
oddzielnie na małym native planned-forecast; nowy pomiar canonical nie ruszył.
Nie rozpoczęto projektowej kampanii ani generacji finalnych danych. Prawdziwa wersja planned
forecast to snapshot 1.1; kontrakt anomalii 1.2 pozostaje osobnym wymaganiem.

Szacunek zamknięcia na 2026-10-07: poprzednia lista prac to **24–40 godzin
aktywnej pracy**. Po przekroczeniu 4 GiB i osobnym limicie godziny podczas development
planuj **5–7 dni roboczych**, z **3–5 dniami** jako wariantem optymistycznym,
jeżeli odbiór zoptymalizowanego producenta szybko zakończy się sukcesem.
To szacunek implementacji i odbiorów; zakończony pełny profil i kampania
nadal nie mają zmierzonego całkowitego kosztu.
Największa niewiadoma to zasoby i czas canonical `ai-training`; kontrolne
14560 kluczy nie pozwala potwierdzić kosztu całego portfolio. Zakres czasu:

| Pozostała praca | Szacunek |
| --- | --- |
| Audytowany runner, eksport finalnej roli i pomiary pełnych profili | 6–10 h |
| Uczciwe strojenie/kalibracja i zamrożenie modeli/polityk | 4–7 h |
| Końcowa kampania trzech seedów dla trzech zastosowań | 6–10 h |
| Robustness, segmenty, niepewność i koszty | 3–5 h |
| MLflow/lifecycle oraz trzy karty i raporty | 3–5 h |
| Publikacja i odbiór dokładnego main z pełnym CI | 2–3 h |

[Audytowany runner generacji](ai09-audited-generation.md) wykonuje sześć faz
wewnątrz jednej zarezerwowanej próby, z pełnym replayem i osobnym lockiem
eksportera. Kod i kontrolowane testy nie stanowią odbioru canonical kampanii.
Lokalny native control został zatrzymany przez rezerwę RAM przed generation;
manualny odbiór na izolowanym runnerze ma zachowane pięć zaliczonych faz i
porażkę verify opisaną w receipt 09.26. Pełne sześć faz nadal wymaga odbioru.

[PR #35](https://github.com/Oskar-Stachowski/retailops-ai-intelligence/pull/35)
na `d81e78a` zaliczył pełny
[Required CI](https://github.com/Oskar-Stachowski/retailops-ai-intelligence/actions/runs/37638548322):
15/15 jobów success. Przed chronionym merge zintegrowano nowszy main `2dc0a5b`,
zachowując przyrosty AI 10. [CI integracji na `7ca2d56`](https://github.com/Oskar-Stachowski/retailops-ai-intelligence/actions/runs/37646472103)
ujawnił nieaktualne przypięcie jednego wspólnego pliku importera.
[Poprawka przypięcia](evidence/09-22-shared-importer-integration.json) zachowuje
poprzedni odebrany pin oraz osobno identyfikuje nową implementację z `d81e78a`.
64 kontrole zgodności, w tym rzeczywisty transfer/reuse bundle, oraz wszystkie
kontrole kontraktów przeszły. Nowy dokładny head wymaga własnego pełnego CI;
projektowa kampania, treningi i końcowa ocena pozostają niewykonane.

[Eksport końcowej oceny](ai09-final-export.md) dodaje format wyłącznie
`final_evaluation`, wiązanie selection freeze i generacji oraz trwały receipt.
[PR #36](https://github.com/Oskar-Stachowski/retailops-ai-intelligence/pull/36)
na `cd6106a` zaliczył cały [Required CI](https://github.com/Oskar-Stachowski/retailops-ai-intelligence/actions/runs/37642451429):
15/15 jobów success. Integracja z nowszym main i poprawką importera wymaga
własnego CI. Rzeczywista kampania, treningi i final test pozostają niewykonane.

[Audytowany trening forecastingu](ai09-campaign-fitting.md) ma osobny plan v15
i pełną populację wszystkich kwalifikujących się kluczy train/early_stopping,
bez zmiany starych limitów. RF ma mean, HGB oddzielne mean/median, a TF
kompaktowy Keras direct 14-horizon z train-only encoding i maskami.
Rezerwacja poprzedza I/O, a receipt następuje po mierzonym fit i reload w
świeżym procesie oraz sprawdzeniu wszystkich plików modelu. Zapis kosztów
MLflow i trzy nowe rzeczywiste kontrole CPU należą do wymaganego odbioru.
174 kontrole integracji i Mypy 669 plików przeszły. [Evidence](evidence/09-23-campaign-fitting-preparation.json)
rozdziela ten odbiór komponentów od niewykonanych nowych treningów projektu.
Head `ab122989` ma pełne 17/17 success CI `37659317202` oraz 6 rzeczywistych
kontroli CPU w 102.64 s, w tym odrzucenie uszkodzonego podpisu. PR #37 jest
skierowany na main; integracja ze scalonym eksporterem i poprawką snapshotu
wymaga własnego pełnego CI. 40 testów tej integracji przeszło w 37.37 s.
Scoring, kalibracja/wybór, pełne profile i końcowa ocena trzech zastosowań
nadal pozostają do wykonania.

[Wspólne prognozy development](ai09-campaign-scoring.md) mają nowy osobny plan v16,
pełne klucze jednej roli tune/calibration i sześć modeli na tych samych obserwacjach.
Audytowany runner rezerwuje próbę przed odczytem i zapisuje trwały receipt po
świeżej predykcji, hashach i pełnej kontroli populacji. Komponenty przeszły
166 testów integracji; późniejsze dodatkowe kontrole wymagają własnego zapisu
w [evidence](evidence/09-25-campaign-scoring-preparation.json).
Nowy rzeczywisty test wspólnej predykcji CPU przeszedł; cały CI `37664383535`
ma 17/17 success. To surowe prognozy
i diagnostyka, bez kalibracji, wyboru modelu, niezależnego development_evaluation
lub końcowego testu. Projektowe treningi i końcowe wyniki pozostają niewykonane.

**AI 07 — odbiór zaliczony, zakres `synthetic_ai_07_portfolio_v4`.**
Oba rzeczywiste modele v4 przechodzą 56/56 oryginalnych bramek jakości.
Świeży OCI/PostgreSQL 16/MLflow sprawdził komplet native publicznych rodziców,
gotowość API po migracjach, lifecycle, recovery, promocję, odrzucenie, rollback,
atomową publikację 1232 wyników, scoped HTTP i SIGKILL/restart.
[Końcowy odbiór](evidence/07-completion.md),
[manifest artefaktów](evidence/07-ready/capsules.json) oraz
[Required CI implementacji obu repozytoriów](evidence/07-ready/required-ci.json)
wiążą zakres, konkretne commity i zaliczone kontrole.
Publikacja została zatwierdzona i wykonana w
[AI PR #24](https://github.com/Oskar-Stachowski/retailops-ai-intelligence/pull/24)
oraz [source PR #97](https://github.com/Oskar-Stachowski/retailops-cloud-native-platform/pull/97).
**AI 07 jest `ready` na zaakceptowanych main.** Oba PR-y są scalone.
AI `18e771f9c2e89e91bf7afeb1744e0cd9112f50b5` ma zielony
[Required CI 37304852763](https://github.com/Oskar-Stachowski/retailops-ai-intelligence/actions/runs/37304852763),
a Source `1de462729012a1bad458a8da4ad317ec22dbc5b8` ma zielony
[Required CI 37305855911](https://github.com/Oskar-Stachowski/retailops-cloud-native-platform/actions/runs/37305855911).
[Zakres połączenia z main](evidence/07-main-integration.md) zachowuje
wspólne migracje i odbiór AI 05/08.

**2026-10-05: cały etap AI 08 jest READY.**
[Końcowe zamknięcie](evidence/08-28-final-ready.md) i
[receipt obu repo](evidence/08-28-final-ready.json) potwierdzają zaliczone dane,
jakość, kartę, rzeczywisty MLflow/lifecycle/batch/API, chroniony merge PR #14
oraz zielone Required CI jego HEAD i merge commitu na main.
9296 punktów ma 90 kontroli passed, 3 zaakceptowane ostrzeżenia i 0 blokad;
2543 testy pełnego CI, 206 focused tests, 12 review gates, 40 wyników i 15 attention.
Wymagane prace do zamknięcia AI 08: **0**. Wdrożenia produkcyjnego nie wykonano.

## Punkt wznowienia

Korzystaj z końcowego raportu i kwalifikowanej karty. AI 07 ma osobny odbiór;
AI 09/10 zachowują własne zależności. Wcześniejsze not ready nie otwierają ponownie AI 08.

## Historia wcześniejszych zakresów

Poniższe wpisy zachowują dawne wyniki, błędy i stan przyrostów w chwili odbioru.
Bieżący status AI 08 określa wyłącznie wpis READY i końcowy receipt powyżej.

**AI 08 — worker/HTTP/priority i pełny backup są zaliczone; final receipts/karta są przygotowane.**
[Przyrost 08.23](evidence/08-23-stockout-final-receipts-and-card.md) dodaje collector
sześciu światów, ponowną kontrolę bramek, kartę, qualifier i jawny review/import/lifecycle.
HEAD 549df93 ma zielony Required CI z 2478 testami. Nowy worker ma rzeczywisty odbiór
37257129973. Końcowe 9296 membership czeka na owner permission; nie oceniono outcomes.
Nowy zakres nadal wymaga własnego pełnego CI oraz rzeczywistych receipts final
orchestration/qualification. Niezależna jakość, osobna promocja, review/merge/main
są otwarte. **AI 08 pozostaje not ready.** Niżej jest historia wcześniejszych zakresów.

**AI 08 — real MLflow/SQL/backup są zaliczone; worker i priorytety czekają na pełny odbiór.**
[Przyrost 08.22](evidence/08-22-stockout-worker-and-priority.md) dodaje izolowane
obliczenia, fencing, widok priority przed projekcją scope i osobny current stockout.
132 focused tests przechodzą. Rzeczywiste odbiory 37255271458 i 37255965904 zaliczyły
SQL/backup oraz stockout MLflow; większy checker worker/HTTP/priority wymaga nowego CI.
Kampania 9296 punktów jest zamrożona i czeka na zgodę właściciela. Niezależna jakość,
kwalifikacja/karta, osobna promocja i końcowy CI pozostają otwarte.
**AI 08 pozostaje not ready.** Niżej zachowano historyczne zakresy.

**AI 08 — kampania 9296 punktów jest zamrożona; końcowa ocena czeka na zgodę.**
[Odbiór przyrostu](evidence/08-21-stockout-final-campaign-and-registry.md) zapisuje
sześć przygotowanych źródeł, evaluator z kontrolą zgody oraz byte verifier
i importer stockout MLflow. 118 focused tests przechodzi; rzeczywisty odbiór
nowego MLflow/SQL/backup jest jeszcze wymagany. Startup i pełne checks poprzedniego
CI są zielone, ale SQL 0021 wymagał poprawki kolejności operatorów JSON.
Worker, priorytety operacyjne, niezależna jakość, osobna promocja i końcowy odbiór
są otwarte. **AI 08 pozostaje not ready.** Niżej jest historia wcześniejszych zakresów.

**AI 08 — komplet sześciu źródeł jest przygotowany; poprawka CI czeka na odbiór.**
Trzy matching i trzy późniejsze kohorty 42/137/2026 zakończyły przygotowanie.
Końcowe etykiety nie były oceniane. Nowe CI wykryło błąd typu w generatorze
schematów i nieudany start loopback API; [poprawka](evidence/08-20-stockout-api-startup.md)
oddziela publiczne kontrakty od treningu/wykonania i sprawdza pełny zakres Mypy.
SQL 0021 wymaga jeszcze rzeczywistego odbioru. Poprzedni SQL 0020, pełny backup
oraz 2383 testy mają zielone CI. Zatwierdzenie polityki i kampanii, niezależna
jakość, MLflow, worker i końcowy odbiór są otwarte. **AI 08 pozostaje not ready.**
Niżej zachowano historyczny zakres wcześniejszych przyrostów.

**AI 08 — trwała kolejka i API są przetestowane; SQL 0021 czeka na CI.**
[Kontrakt](reference/stockout-jobs.md) i [odbiór](evidence/08-19-stockout-jobs.md)
opisują przypięte wersje, lease, retry, atomową publikację i fizyczny scope.
199 testów przechodzi. Poprzedni SQL 0020 i pełny backup/restore są zaliczone.
Trzy pierwotne kohorty są ukończone; późniejsze źródła 42/137 są przygotowane,
2026 jest w toku. Końcowa jakość, progi/capacity, MLflow, worker i końcowy
odbiór są otwarte. **AI 08 pozostaje not ready.** Niżej są wcześniejsze zakresy.

**AI 08 — podstawa lifecycle jest przetestowana; rzeczywisty SQL/registry oczekuje.**
[Kontrakt](reference/stockout-lifecycle.md) i [odbiór](evidence/08-18-stockout-lifecycle-foundation.md)
wiążą osobne stockout approval/binding/release ze wspólnym protokołem AI 05.
23 nowe i 114 istniejących testów regresji przechodzi. Nowa migracja wymaga
własnego rzeczywistego odbioru CI; lokalnej bazy źródła nie zmieniono.
CI producenta późniejszych źródeł jest zielone; dwie kohorty 1.8 liczą się.
Finalna jakość, zatwierdzona polityka, MLflow, durable batch/read i ready są otwarte.
**Cały AI 08 pozostaje not ready.**

**AI 08 — rzeczywisty nowy checkpoint ma zgodność native/wheel i zielone CI.**
[Odbiór 08.17](evidence/08-17-stockout-remote-checkpoints.md) potwierdza
wybrany model i wszystkie segmenty na danych wyboru. Matching seedy 42/137
są przygotowane; 2026 jest jeszcze w toku. [Późniejsze źródła](reference/stockout-future-sources.md)
mają osobne prospektywne profile/workflow, bez fitów lub otwarcia TEST.
Zgoda na politykę, rzeczywiste późniejsze źródła, niezależna jakość, lifecycle,
batch/read API i finalny odbiór pozostają otwarte. **Cały AI 08 pozostaje not ready.**

**AI 08 — późniejsza kalibracja i przenośny runtime mają osobny protokół development.**
[Kontrakt](reference/stockout-selection-runtime.md) i
[odbiór przyrostu](evidence/08-16-stockout-selection-runtime.md) dodają
pełny stan kalibratora, propozycję progów/capacity, publiczny replay wejść
i odrębne uprawnienia stockout. TRAIN fituje model, TUNE kalibrator,
CALIBRATION wybiera wariant; wyniki wyboru nie są niezależną oceną jakości.
Stare pakiety oraz ich niezaliczone bramki pozostają zachowane.
Trzy matching kohorty 30 × 2 × 102 na seedach 42/137/2026 mają przygotowany
osobny workflow GitHuba. Nowy rzeczywisty native/wheel i CI wymagają receipt.
Rezerwa lokalnego dysku pozostaje 50 GiB. Final test, zatwierdzone progi,
lifecycle/MLflow, trwały batch/read API i końcowy odbiór są jeszcze otwarte.
**Cały AI 08 pozostaje not ready.** Poniżej zachowano historyczny zakres.

**AI 08 — niezależna ocena i karta partycji działają; jakość pozostaje `not_ready`.**
[Receptura](reference/stockout-independent-qualification.md) i
[odbiór](evidence/08-15-stockout-independent-development.md) mają native/wheel
z identycznymi bajtami oraz pełnym replay rzeczywistych rodziców.
Nowy pilot 19 × 2 × 102 daje **3876 origin / 1473 development**, wobec
2856 / 1043. Cały pipeline trwa 1243,65 s, peak RSS 492,09 MiB,
allocated scratch 325,79 MiB i zachowuje rezerwę 50 GiB.
Model fituje 370 wcześniejszych punktów, sigmoid 232 późniejsze;
321 tune wybiera rodzinę, 313 kolejnych punktów ocenia ją niezależnie.
Wszystkie 8 kategorii mają obie klasy na tune. Wybrany LR without_upstream
ma niezależny AP 0,991449, ale sigmoid pogarsza Brier 0,035440→0,050294.
Dwie kategorie przekraczają proponowany ECE 0,15; constrained segment
ma tylko 3 negatywne wobec wymaganego minimum 5. Wyniki nie są uznane
za zaliczenie jakości. 100 focused/dependency testów przeszło bez ostrzeżeń.
Większa próba, robustness, zatwierdzone progi/capacity, finalna kampania,
lifecycle i batch/read API pozostają otwarte. Test końcowy nie jest oceniony,
model nie jest promowany; **cały AI 08 pozostaje not ready**.
Poniżej zachowano historyczny zakres wcześniejszych przyrostów.

**AI 08 — kompletny pipeline przeszedł odbiór większego pilota.**
[Pomiar](reference/stockout-resource-probe.md) i
[odbiór](evidence/08-14-stockout-resource-probe.md) obejmują producenta,
eksport/import, curated, rzeczywiste partycje, development i sześć modeli.
Jawny `ai-load` 14 × 2 × 102 ma **2856 punktów**, wobec 1632 smoke.
Train/tune/calibration: **598/227/218**, razem 1043 wobec 605.
Całość trwa 719,81 s, peak własnego drzewa RSS 622,23 MiB, allocated scratch
259,23 MiB; 50 GiB rezerwy jest zachowane. Przyjęte wejścia i modele są
zachowane oddzielnie do niezależnej oceny. Wariant 4480 odrzucono przed
startem przez estymację RAM. Pełne `ai-dev`/`ai-training` są nieodebrane.
Regeneracja smoke zachowuje sześć modeli/wyników, używając nowych prawdziwych
label/temporal IDs wynikających z nowych timestampów manifestu. Stare
573 wejścia/v1 rodzice i 302 pliki partycji są zachowane.
29 nowych kontroli jest zaliczonych; Ruff/mypy/docs oraz pełny rzeczywisty
smoke i pilot mają odbiór. Rodzic 4faaf4b ma zielone Required CI;
nowy commit wymaga swojego CI. Tune wybiera provisional LR without_upstream,
a kategorii not_evaluable jest 1/8 zamiast 5/8. To nie jest niezależny odbiór
jakości/kalibracji. Nowa karta, ta ocena, progi/capacity i lifecycle/batch/read
pozostają otwarte. Final test i promocja nie są wykonane;
**cały AI 08 pozostaje not ready**. AI 05/v12 i starsze pakiety są zachowane.

**AI 08 — upstream 2.1 jest podłączony do treningu bez nadmiarowego replay.**
[Kontrakt](reference/stockout-temporal-series.md) i
[odbiór](evidence/08-13-stockout-temporal-series.md) dodają osobny temporal 2.1.
1632 comparison/membership oraz sześć modeli zachowują wyniki; rzeczywiste
train/tune/calibration mają 340/135/130 punktów i nowe parent IDs.
Cechy/upstream/etykiety są odtwarzane raz w prywatnym kontekście; cała
weryfikacja kończy się przed zwróceniem development. Test to 131 membership,
bez wektora celów/metryk. Native i odłączony wheel/CLI zachowują bajty/ID;
79 modułów pochodzi z instalacji, producent jest nieimportowalny.
573 oryginalne wejścia/v1 rodzice i 287 plików starych partycji są zachowane.
Trzy pary assemblera mają medianę 329.81→177.06 s
(-46.3%), RSS 226.00→226.33 MiB;
RSS/scratch są podobne. To pomiar smoke/assemblera przy równoległym CI,
bez odbioru większego profilu lub całego pipeline.
Pełny `make ci-local` przeszedł: **2133/2133 testów**, 0 ostrzeżeń, 2849.97 s testów; 21 targetów, pakiet, Compose config i oba skany sekretów.
48 nowych przypadków obejmuje także kontrolę limitu 4096 output files przed
publikacją. Rodzic 92b8d5f ma zielone Required CI PR i push;
nowy commit wymaga własnego CI. Większy profil/preflight, niezależna ocena,
karta z nowych rodziców, progi i lifecycle/batch/read pozostają otwarte.
Stare pakiety/ID, AI 05 i v12 są zachowane; cały AI 08 pozostaje not ready.
Poniższe przyrosty zachowują historyczny zakres odbioru.

**AI 08 — upstream ma selekcję jednej fizycznej serii i globalną walidację.**
[Kontrakt 2.1](reference/stockout-upstream-series.md) i
[odbiór](evidence/08-12-stockout-upstream-series.md) zachowują wszystkie
1632 prognozy, comparison i sześć modeli obecnej próbki. Selekcja ma
najwyżej 198 zamiast 3625 rekordów
oraz 247,260 zamiast 4,166,485 B.
Ograniczony decoded cache ma 1024 rekordy / 1 MiB, schema wczytywane raz.
Globalne znane wersje i efektywne trasy są sprawdzane przed fizycznym filtrem.
Native i odłączony wheel zachowują identyczne bajty, 573 wejścia/rodzice
pozostały bez zmian. Regresja ukierunkowana ma 118/118 przypadków,
w tym 48 nowych. Pełny `make ci-local` przeszedł: 2085/2085 testów,
0 ostrzeżeń, wszystkie targety, pakiet, Compose config i skany sekretów.
Trzy pary buildów mają medianę 127.23 → 149.12 s,
peak RSS 127.17 → 125.83 MiB;
nie wykazano przyspieszenia lub istotnego zmniejszenia całego RSS na smoke.
To usunięcie globalnego panelu Python, bez odbioru większego profilu.
Rodzic `df61862` ma zielone Required CI PR i push; nowy commit wymaga własnego CI.
Temporalny trening 2.0 pozostaje odebrany z upstream 2.0. Podłączenie
upstream 2.1 i spójny przebieg bez nadmiarowego replay, pozostałe limity,
większy profil/RSS/scratch, niezależna ocena, progi, karta i lifecycle/batch/read
API pozostają otwarte. Stare pakiety/ID, AI 05 i v12 pozostają zachowane;
final test nie jest oceniony, cały AI 08 pozostaje not ready.
Poniższe przyrosty zachowują historyczny zakres odbioru.

**AI 08 — temporalne połączenie partycji jest podłączone do treningu.**
[Kontrakt](reference/stockout-temporal-storage.md) i
[odbiór](evidence/08-11-stockout-temporal-storage.md) dodają osobny pakiet
comparison/membership i assembler development. Wszystkie pola 1632
comparison i 1632 membership są identyczne z v1. Rzeczywiste wejścia
train/tune/calibration mają 340/135/130 punktów; końcowy test pozostaje
131 membership bez wektora celów i metryk. Sześć modeli zachowuje
pipelines, model IDs, wyniki i report; nowy development ID wiąże bundle
rodziców. Zapis ma 178 960 B zamiast 1 890 509 B,
o 90,5% mniej. Zainstalowany wheel odtworzył identyczne pliki,
manifest, development i modele; 573 wejścia/rodzice pozostały bez zmian.
Regresja ukierunkowana ma 153/153 zaliczeń, w tym 27 nowych. Ostrzeżenie
joblib dotyczy blokowanego odczytu fizycznych rdzeni CPU w sandboxie.
Walidacja lokalna ma 2037/2037 unikalnych przypadków: 1903 poza modułami
wymagającymi uprawnień i całe 134 przypadki tych niezmienionych modułów
powtórzone z dostępem do localhost/statystyk procesów. Pierwszy
`make ci-local` miał exit 2 przez bind i sysctl blokowane w sandboxie;
receipt jawnie zachowuje 28 failures / 2 errors i nie opisuje go jako exit 0.
Wszystkie targety, pakiet, Compose config i skany sekretów są zaliczone.
Końcowe dowody mają ponowne kontrole dokumentacji i sekretów. Poprzedni `077add2` ma zielone
Required CI PR i push; nowy commit wymaga własnego CI w draft PR #14.
Limity pozostają: 10 000 kluczy, kanoniczny development 16 MiB,
qualification JSON 4 MiB i globalny upstream 20 000 wierszy / 16 MiB.
Cały RSS/scratch i większy profil nie są odebrane. Pozostają selekcja
upstream po seriach, preflight i jawny większy profil, niezależna ocena,
progi, karta z nowych rodziców i lifecycle/batch/read API. Stare pakiety/ID,
AI 05 i v12 pozostają zachowane; cały AI 08 pozostaje not ready.
Poniższe przyrosty zachowują historyczny zakres odbioru.

**AI 08 — ograniczony upstream 2.0 zachowuje pełną próbkę.**
[Kontrakt](reference/stockout-upstream-storage.md) i
[odbiór](evidence/08-10-stockout-upstream-storage.md) dodają osobny pakiet,
pełny replay cech 2.2, jednorazowy panel faktów i chronologiczny Parquet.
Wszystkie pola 1632 prognoz, comparison i sześć modeli są identyczne z v1.
Bundle ma 1 103 923 B zamiast JSON 2 127 982 B, o 48,1% mniej.
Odłączony wheel odtworzył identyczny manifest, pliki i pełny iterator;
48 modułów konsumenta pochodziło z zainstalowanego pakietu.
Wszystkie 573 pliki wejść/rodziców pozostały bez zmian.
Testy ukierunkowane mają 80/80 zaliczeń, w tym 27 nowych, bez ostrzeżeń.
Trzy pary pełnego przygotowania, razem z replay rodzica cech, dały medianę
76,13 → 55,74 s i RSS 204,83 → 137,92 MiB. Pomiar łączy także korzyść
cech 2.2 i trwał przy równoległym CI; nie kwalifikuje większego profilu.
Lokalna walidacja ma 2010/2010 przypadków w dwóch częściach, bez ostrzeżeń,
wszystkie targety Makefile, pakiet, Compose config i czyste skany sekretów.
Pierwszy pełny przebieg ujawnił rzeczywisty timeout testu scope kolejki v12;
kontrolowany zegar i dwa osobne testy expiry/heartbeat dotyczą tylko testów.
Limit produkcyjny 120 s i predictor pozostają zachowane. Cały zmieniony
moduł oraz pozostały runtime ponownie przeszły; receipt jawnie zapisuje
nieudaną pierwszą próbę. Końcowe dowody mają ponowne kontrole dokumentacji.
Poprzedni `d083e0a` ma zielone Required CI PR i push.
Panel nadal ma globalny limit 20 000 wierszy / 16 MiB; cały RSS/scratch
większego pipeline nie jest odebrany. Comparison, split i trening pozostają
v1, a reader upstream nie wybiera ról development. Pozostają ich połączenie,
globalna walidacja z selekcją upstream po seriach, większy profil,
niezależna ocena, progi i lifecycle/batch/read API.
Stare pakiety/ID, AI 05 i v12 pozostają zachowane. Final test nie jest
oceniony; cały AI 08 pozostaje not ready. Poniższe przyrosty zachowują zakres.

**AI 08 — prywatne partycje etykiet 2.0 zachowują pełną próbkę.**
[Kontrakt](reference/stockout-label-partitions.md) i
[odbiór](evidence/08-09-stockout-label-partitions.md) dodają jednorazową bazę,
ledger jednej fizycznej serii oraz batch Parquet. Wszystkie pola 1632
etykiet i sześć wariantów modeli są identyczne z v1; nowe części mają
110 484 B zamiast JSON 666 623 B. Pełny iterator sprawdza całość przed
pierwszym punktem i ponawia kontrole przed konsumpcją części.
Testy ukierunkowane mają 98/98 zaliczeń, w tym 31 nowych, bez ostrzeżeń.
Końcowe trzy pary buildów po normalizacji UTC mają medianę RSS
112.73 → 102.48 MiB (-9.1%), wall
12.84 → 12.66 s. To mała próbka, bez odbioru całego RSS/scratch.
Odłączony wheel odtwarza identyczne pliki i iterator; 573 wejścia/rodzice
pozostają bez zmian. Pełny `make ci-local` przeszedł: 1981/1981 testów bez ostrzeżeń,
wszystkie targety, pakiet, Compose config i oba skany sekretów. Końcowe
dowody mają ponowne kontrole dokumentacji i sekretów; nowy commit wymaga
własnego Required CI w draft PR #14.
Stare pakiety/ID, AI 05 i v12 pozostają zachowane. Upstream, comparison,
split i trening nadal korzystają z v1. Pozostają ich czytniki i temporalne
połączenie, większy profil i odbiór całego pipeline, niezależna ocena,
progi i lifecycle/batch/read API. Cały AI 08 pozostaje not ready.
Poniższe przyrosty zachowują swój zakres.

**AI 08 — indeks historii 2.2 zachowuje pełną próbkę 102 dni.**
[Kontrakt](reference/stockout-history-index.md) i
[odbiór](evidence/08-08-stockout-history-index.md) obejmują sumy prefiksowe
ledgeru i dzienne grupowanie znanego popytu, bez zmiany pakietów v1/v2.0/v2.1.
Wszystkie 1632 punkty, Parquet i sześć wariantów modeli są identyczne;
native i wheel odtworzyły te same pliki, a 573 wejścia/rodzice zachowały hash.
Trzy pary świeżych buildów dały medianę 21,32 → 18,42 s, CPU 20,57 → 17,71 s;
mediana próbkowanego peak RSS wyniosła 131,75 → 131,98 MiB.
Sorty ledgeru spadły z 45 696 do 1632. Indeks powstaje na granicy wiedzy
każdego origin; późna korekta nie nadpisuje dawnych danych.
Przyrost ma 150/152 testy ukierunkowanych, w tym 37 nowych,
bez ostrzeżeń. Pełny `make ci-local` przeszedł:
1950/1952 testy bez ostrzeżeń, wszystkie targety, pakiet, Compose
config i oba skany sekretów. Końcowe receipt mają ponowne kontrole
dokumentacji i sekretów; nowy commit wymaga własnego CI w draft PR #14.
Poprzedni commit `858abcf` ma zielone Required CI PR i push.
Etykiety, upstream, split i trening nadal przyjmują v1. Pozostają ich
partycjonowanie, rzeczywisty większy profil i odbiór całego pipeline,
niezależna ocena, progi i lifecycle/batch/read API. Final test nie jest
oceniony, cały AI 08 pozostaje not ready. Poniższe przyrosty zachowują zakres.

**AI 08 — ograniczony odczyt faktów z dysku zachowuje wyniki małej próbki.**
[Kontrakt 2.1](reference/stockout-disk-facts.md) i
[odbiór](evidence/08-07-stockout-disk-facts.md) obejmują prywatny magazyn
SQLite oraz cache jednej fizycznej serii, bez zmiany kodu v1/v2.0.
Wszystkie 1632 punkty, pliki Parquet i sześć wariantów modeli development
są identyczne z wcześniejszą ścieżką. Native i zainstalowany wheel
odtworzyły te same pliki; 573 wejścia i rodzice pozostały bez zmian.
W osobnych świeżych procesach build miał 131,5 zamiast 175,0 MiB peak RSS
i trwał 15,44 zamiast 18,75 s. Pomiary obejmują replay curated i cechy,
bez kwalifikacji budżetu większego pipeline. Przyrost ma 113/113 testów
ukierunkowanych, w tym 30 nowych. Pełny `make ci-local` przeszedł:
1913/1913 testów bez ostrzeżeń, wszystkie targety, pakiet, Compose
config i oba skany sekretów. Końcowe receipt mają ponowne kontrole
dokumentacji i sekretów; nowy commit wymaga własnego CI w draft PR #14.
Poprzedni commit `ebe1f45` ma zielone Required CI PR i push.
Pozostają kumulacyjny ledger i ruchome okna, partycjonowanie pozostałych
rodziców/treningu, rzeczywisty większy profil oraz niezależna ocena,
progi i lifecycle/batch/read API. Final test nie jest oceniony;
cały AI 08 pozostaje not ready. Poniższe przyrosty zachowują swój zakres.

**AI 08 — partycje cech v2 zachowują całą próbkę 102 dni.**
[Kontrakt](reference/stockout-partitions.md) i
[odbiór](evidence/08-06-stockout-partitions.md) obejmują 1632 identyczne
punkty i identyczne modele development po rzeczywistym odczycie Parquet.
Zapis ma 1 546 444 B zamiast 10 249 881 B; indeks ogranicza skany obcych
serii i zachowuje granicę wiedzy oraz wszystkie stare wersje lineage.
Natywny build/rebuild/verify i zainstalowany wheel odtworzyły identyczne
ID/bajty. Wszystkie 567 odtworzonych wejściowych plików pozostały
niezmienione. Regresja przyrostu ma 83/83 testów, w tym 30 nowych.
Pełny `make ci-local` przeszedł: 1883/1883 testów bez ostrzeżeń,
wszystkie targety, pakiet, Compose config i oba skany sekretów.
Końcowe receipt mają ponowną kontrolę dokumentacji i sekretów;
nowy commit wymaga własnego CI w draft PR #14.
Wcześniejsza karta `ab22763` ma zielone Required CI PR i push.
Pozostają kumulacyjne indeksy i okna historii, ograniczony odczyt wejścia,
pozostałe rodzice i trening z partycji oraz odbiór większego profilu.
Final test nie jest oceniony, progi niezatwierdzone i model niepromowany;
cały AI 08 nie jest ready. Poniższe przyrosty zachowują historyczny zakres.

**AI 08 — karta i wyjaśnienia development mają odbiór natywny oraz wheel.**
[Kontrakt karty](reference/stockout-model-card.md) i
[pomiary](evidence/08-05-stockout-card.md) obejmują trzy tabele LR,
20 grup permutation importance wybranego HGB i 135 zestawów lokalnych
faktów PIT. Karta odtworzyła identyczne ID/bajty z odłączonego pakietu;
ponownie zahashowane zmiany karty i modelu zostały odrzucone. Wszystkie
278 wejściowych plików oraz identity przygotowania/treningu pozostały
niezmienione. Regresja przyrostu ma 49/49 testów, w tym 18 nowych.
Pełna regresja ma 1853/1853 testów. Wszystkie targety `check` i oba skany
sekretów są zaliczone; fałszywy alarm na polskim zdaniu usunięto korektą
dokumentacji i ponownym `make docs-check secrets`, bez zmiany skanera.
Nowy commit wymaga własnego CI w draft PR #14.
[Projekt większego profilu](reference/stockout-profile-resources.md) określa
partycje, indeksy PIT i pomiary; większa generacja nie została uruchomiona.
Pełny profil, niezależna ocena kalibracji, threshold policy i
lifecycle/batch/read API pozostają otwarte. Final test nie jest oceniony,
model niepromowany; cały AI 08 nie jest ready. Poniższe przyrosty AI 08
zachowują historyczny zakres pomiarów.

**AI 08 — LR/HGB mają pierwsze wyniki development na próbce 102 dni.**
[Kontrakt](reference/stockout-training.md) i
[odbiór](evidence/08-04-stockout-models.md) obejmują sześć porównywalnych
wariantów, 340 punktów train, 135 tune oraz osobny sigmoid na 130 punktach
calibration. Wybór provisional opiera się tylko na tune. Kalibracja po
dopasowaniu jest diagnostyką in-sample; final test nie jest oceniany.
Natywny build/rebuild/replay oraz zainstalowany wheel odtworzyły identyczne
modele; test ponownie zahashowanej zmiany wag został odrzucony. Regresja
ma 47/47 testów. Pełny `make ci-local` przeszedł: 1835/1835 testów,
wszystkie targety, pakiet i skany sekretów. Commit `4a7a76f` ma zielone
Required CI PR i push w draft PR #14. Pięć z ośmiu kategorii tune
ma ranking `not_evaluable` z powodu jednej klasy, bez wyjątku jakości.
Późniejsza karta ma osobny odbiór opisany powyżej. Pełny profil i niezależna
ocena, threshold policy oraz lifecycle/batch/read API pozostają otwarte;
cały AI 08 nie jest ready.

**AI 08 — historyczna prognoza bazowa ma odbiór natywnej próbki 102 dni.**
[Kontrakt](reference/stockout-upstream.md) i
[dowody](evidence/08-03-stockout-upstream.md) obejmują rekonstrukcję prognozy
z wiedzy każdego dnia oraz identyczne wejścia wariantów z prognozą i bez niej.
Prognoza jest dostępna dla 1015/1104 bazowych punktów `eligible` i wszystkich
736 punktów dopuszczonych przez split (340/135/130/131).
Regresja ma 102/102 testów; zainstalowany wheel odtworzył identyczne wyniki
z publicznych danych, bez etykiet i truth. Pełny `make ci-local` przeszedł:
1816/1816 testów, wszystkie targety, pakiet i skany sekretów. Commit
`aa6b953` ma zielone Required CI PR i push; draft PR #14 pozostaje otwarty.
Późniejsze wyniki porównania modeli i diagnostyki kalibracji mają odbiór 08.4;
final test pozostaje nieoceniony i cały AI 08 nie jest ready.

**AI 08 — wcześniejsze cechy PIT i podział w czasie mają pełny odbiór lokalny.**
[Kontrakt cech](reference/stockout-features.md) i
[dowody 102 dni](evidence/08-02-stockout-features.md) obejmują 1632 origin:
1104 kwalifikujące się cechy, 432 istniejące braki i 96 braków danych.
Podział dopuszcza 340/135/130/131 punktów train/tune/calibration/test;
279 okien wykluczono przy granicach okresów. Regresja ma 144/144 testów;
zainstalowany wheel odtworzył identyczne wyniki. Pełny `make ci-local` przeszedł,
w tym 1800/1800 testów, pakiet i skany sekretów.
Pierwszy zakres [etykiet](evidence/08-01-stockout-labels.md) ma zielone
Required CI PR i push na `172496b`. Nowszy przyrost w roboczym
[PR #14](https://github.com/Oskar-Stachowski/retailops-ai-intelligence/pull/14)
wymaga własnego CI; main nie zawiera jeszcze tego zakresu.
Trening LR/HGB i dopasowanie sigmoid opisuje późniejszy odbiór 08.4.
Niezależna ocena jakości i registry/batch/read API pozostają otwarte.
Historyczne raporty przyrostów zachowują swój zakres.

**AI 05 — ready po scaleniu PR-ów i zielonym Required CI obu mainów.**
[Końcowy raport publikacji](https://github.com/Oskar-Stachowski/retailops-cloud-native-platform/blob/main/docs/evidence/ai/05/final/README.md)
wiąże PR #8 AI, PR #81 źródła i końcową dokumentację PR #82.
Poniższe wcześniejsze sekcje AI 05 zachowują historyczny zakres prób;
ich informacje o otwartych blokadach zostały zastąpione końcowym odbiorem.

**2026-10-04 — AI 10, przyrost REST źródła.**
[Runbook](source-rest-v2.md) opisuje klienta wygenerowanego z rzeczywistego
OpenAPI chronionych `/integration/v2` odczytów, hard deadline, retry/breaker
i sprawdzenie scope/paginacji. To bounded live reads; immutable snapshot,
pełny ML grain i snapshot/log handoff nadal nie są obsługiwane.
AI 10 pozostaje `in_progress`; końcowy CI/cross-repo odbiór mają własne dowody.

**2026-10-03 — AI 10 rozpoczęty, pierwszy przyrost forecast v2.**
[Instrukcja](intelligence-integration-v2.md) i [dowód](evidence/10-forecast-integration.md)
obejmują atomowy outbox, schemat wyników ML oraz osobny projektor/read API
RetailOps. Head bazy tej gałęzi to `0020_intelligence_outbox`.
AI 10 pozostaje `in_progress`; trzech modeli, UI, snapshot/replay i całego E2E
jeszcze nie odebrano. Historyczne pomiary AI 05 poniżej zachowują swój zakres.

**AI 05 — lokalny przepływ finalnego v12 jest odebrany także na świeżym snapshocie.**
[Raport i pomiary](evidence/05-v12-real-serving.md) oraz
[wersjonowany zapis dowodów](evidence/05-v12-real-serving.json) obejmują cały
import 664 plików / około 29,8 GiB, kwalifikację oryginalnego predictora,
trzy działające wersje registry, trzy udane trwałe batch’e i 42 opublikowane wiersze.
Restart zachowuje zadanie i input; rollback nie przepina wcześniej przyjętego
runa; odrzucenie kandydata nie zmienia runtime. Wyniki obu historycznych wydań są identyczne.

Świeży source ma 56 dni historii do 2026-10-01 oraz 14 dni jawnych znanych
planów, bez przyszłych obserwacji. Pełny import i niezależna kontrola watermarku,
osobna source policy 1.1.0, wszystkie 10 raportów, wersja development 4 i worker
dały **14/14 `current`**, w tym 3 potwierdzone zamknięte dni jako `null`.
Batch z publikacją trwał 3,31 s, pierwsza strona API około 0,10 s.
Restart PostgreSQL/MLflow/API zachował job, dokładny hash i aktualny odczyt.
Kod producenta na aktualnym `main` przeszedł 772/772 testów danych.
Kod AI 05 z 556d349 ma zielony Required CI oraz 1730/1730 testów;
późniejszy commit raportu wymaga własnej kontroli publikacji.

To osobny [lokalny namespace developerski](forecast-v12-development.md).
Domyślne API nie pokazuje jego danych. Pełna ocena 100 produktów / 2 lokalizacji
zachowuje 221 passed, 3 failed i oryginalne `not_ready`; ograniczony viewer
nie widzi zbiorczego raportu. Origin 2026-09-16 pozostaje historyczny i API
zwraca `stale` dla wcześniejszych prognoz. Nie było refitu ani zmiany oryginalnych artefaktów AI 04.

Nowy odbiór [backup/restore](forecast-v12-backup.md) sprawdza cały stan obu baz
PostgreSQL i artefaktów na małych fixture z head `0019_v12_development`.
Nie jest niezależnym backupem rzeczywistej kampanii; klon APFS na tym samym
dysku także nim nie jest. Osobny trwały stos odbioru ma tę migrację;
dotychczasowego długotrwałego stosu nie zmieniono.

**Formalna publikacja AI 05 pozostaje otwarta:** wymagane są zielone Required CI
bieżących commitów i integracja [AI PR #8](https://github.com/Oskar-Stachowski/retailops-ai-intelligence/pull/8)
oraz [source PR #81](https://github.com/Oskar-Stachowski/retailops-cloud-native-platform/pull/81).
Lokalny świeży snapshot, kwalifikacja i odbiór batch/API są zakończone.
Produkcji nie dopuszczono. Historyczne raporty poszczególnych przyrostów
zachowują swoje wcześniejsze wyniki i granice.

Aktualizacja: **2026-10-02**. **Etap 11 — RAG jest odebrany lokalnie.**
[Instrukcja użytkowa](knowledge-semantic.md) opisuje rzeczywiste embeddings,
przygotowanie, kwalifikację, aktywację i rollback. [Końcowy odbiór](evidence/11-completion.md)
wiąże implementację z pomiarami i ograniczeniami. Zdalna publikacja przechodzi
przez chroniony `main` oraz Required CI; stan wykonania pokazuje
[workflow repozytorium](https://github.com/Oskar-Stachowski/retailops-ai-intelligence/actions/workflows/required-ci.yml).

**AI 04 — `ready`, finalna wersja v12, z jawną akceptacją trzech odstępstw.**
Właściciel projektu zakończył iterację developerską na v12 2026-10-01.
[Decyzja odbioru](evidence/04-v12-acceptance.md) obowiązuje po przyjęciu tego
commitu przez PR i zielonym Required CI chronionego `main`.
[Wersjonowany zapis decyzji](evidence/04-v12-acceptance.json) wiąże akceptację
z jednym konkretnym eksportem, pełnymi metrykami i dowodami odtworzenia.

Pełna kampania v12 obejmuje **64/64 kohorty i 27 396 096 wierszy prognoz**.
Oryginalny protokół jakości nadal daje **221 passed / 3 failed** oraz
`forecast_model_status=not_ready` i `quality_qualification_status=not_ready`.
Zaakceptowane odstępstwa MSE wynoszą **+0,204738%, +0,000619%, +0,043292%**.
Nie przepisano ich na zaliczone i nie zmieniono progów oceny.
`stage_status=ready` oznacza świadomy odbiór etapu przez właściciela,
nie nowy wynik statystyczny ani zgodę na wdrożenie produkcyjne.

[Finalne v12](forecast-functional-v12.md) ma zaliczony niezależny replay,
trwały eksport **663 plików / 31 994 594 655 B** i weryfikację rzeczywistego
runu z odłączonego wheel. Kontrola `make forecast-acceptance-check` sprawdza
oryginalne sumy SHA-256, komplet metryk i dokładny zakres trzech wyjątków.
Nie dopuszcza innego runu, v13 ani rozszerzenia decyzji na promocję modelu.

V13 jest **superseded**: [przygotowanie 36900199207](https://github.com/Oskar-Stachowski/retailops-ai-intelligence/actions/runs/36900199207)
zostało anulowane po wyborze v12. Lokalne oczekiwanie na ocenę i kolektor
zatrzymano przed oceną holdoutów v13. Zachowano istniejące pliki, rezerwacje
seedów, freeze i historię; automatyczne uruchamianie generacji po pushu wyłączono.

Odbiór kodu: [PR #7](https://github.com/Oskar-Stachowski/retailops-ai-intelligence/pull/7),
bez omijania ochrony `main` i Required CI. Źródło zostało przyjęte przez
[PR #77](https://github.com/Oskar-Stachowski/retailops-cloud-native-platform/pull/77).
Dalsze prace AI 05 korzystają z eksportu v12 i osobnego adaptera dwóch celów;
MLflow, promocja, batch i serving mają własny odbiór. Portfolio final test
pozostaje nietknięty. Historyczne wyniki [04.7](evidence/04-07-quality.md),
[04.8](evidence/04-08-handoff.md), [korekt](evidence/04-quality-remediation.md)
i [v11](forecast-functional-v2.md) zachowują pierwotne statusy.

**AI 05.1 — lokalny tracking i magazyn MLflow:** istniejący z AI 01
PostgreSQL, rola i trwały wolumen mają teraz [backup/restore i politykę
retencji](mlflow-store.md). [Odbiór](evidence/05-01-store.md) sprawdza
rzeczywiste przeniesienie eksperymentu, runu i artefaktu do pustego projektu
oraz odczyt po odtworzeniu. Kolejny zakres to import evidence 04.8 do
MLflow. Model pozostaje `not_ready`; registry, promocja, batch i API nie
są jeszcze częścią odbioru 05.1.

**AI 05.2 — historyczne evidence 04.8 w MLflow:** [importer i semantyka
runu](mlflow-evidence.md) zachowują oryginalne ID, czasy eksportu, lineage,
metryki wraz z ważnością i pełne archiwum z sumami kontrolnymi. [Odbiór
lokalny](evidence/05-02-import.md) potwierdza rzeczywisty import i powtórzenie
bez drugiego runu. Quality nadal ma 145 passed, 79 failed, 8 not_ready;
status modelu to `not_ready`. Registry, promocja, batch i API pozostają do
wykonania w kolejnych zakresach AI 05.

**AI 05.3a — kontrola registry przed wersją:** [review i odrzucenie](mlflow-registry.md)
sprawdziły import 04.8, zapisały audyt z rolą `promoter` oraz powtórzyły
decyzję bez duplikatu. [Odbiór](evidence/05-03-review.md) wskazuje runy i
backup. `retailops-demand-forecast` nie ma wersji ani aliasów; pełna
promocja/rollback na modelu AI 04 czekają na jego kwalifikację.

**AI 05.3b — Registry i recovery:** [mechanizmy lifecycle](mlflow-lifecycle.md)
utrwalają niezależny audyt w PostgreSQL, wersje, aliasy i niezmienne
release pins. [Odbiór](evidence/05-03-lifecycle.md) sprawdza dwie promocje,
rollback, odrzucenie trzeciej wersji, utracone odpowiedzi oraz SIGKILL/restart
w jednorazowym Registry `retailops-demand-forecast-mechanics`.
Nie zatwierdza jakości AI 04 ani działającego runtime; pointer oznacza
zatwierdzony release. Rzeczywista kwalifikacja modelu i późniejsze wpięcie
runtime nadal wymagają odbioru. Zmiany są lokalne, bez zdalnego Required CI.

**AI 05.3c — wspólny backup/restore:** [procedura](lifecycle-backup.md)
wiąże dane aplikacji AI, metadane MLflow i pełny wolumen artefaktów w jednym
pakiecie. [Odbiór](evidence/05-03-store.md) sprawdza blokadę zapisów ról
aplikacji, SIGKILL kontrolera i jawne wznowienie, checksumy każdej tabeli,
sekwencji i całego archiwum oraz recovery niedokończonej rejestracji po
odtworzeniu do nowego projektu. Cel z częściowym restore pozostaje offline;
istniejący cel nie jest nadpisywany. Testy używają wyłącznie izolowanych
modeli mechanicznych. **AI 05.3 czeka na kwalifikację rzeczywistego modelu
AI 04.**
Nie ma jeszcze serving prognoz ani zdalnego odbioru Required CI tych zmian.

**AI 05.4a — trwała kolejka i worker:** [runbook](forecast-worker.md) opisuje
POST202/GET runów, oddzielny grant `pipeline` + `forecast:run`, scope,
przypięte release/data/policy, leasing z heartbeat, automatyczny bounded retry
i prywatne anulowanie. [Odbiór](evidence/05-04-queue.md) potwierdza rzeczywiste
HTTP/PostgreSQL/MLflow, 9 runów, 12 prób i 2 kompletne wyniki fixture,
wyścig workerów, SIGKILL po częściowym obliczeniu, odrzucenie starego tokenu,
zmianę aliasu bez zmiany pinów oraz zachowanie pełnego stanu po restarcie bazy.
**Mechanika działa lokalnie na jawnych fixture; AI 05 pozostaje otwarte.**
Implementację integracji profili i atomowej publikacji opisuje 05.6 poniżej.
Odczyt prognoz opisuje 05.7a poniżej; pozostaje odbiór qualified release’u.
Nie ma zdalnego Required CI tych zmian. AI 04 jest rozwijane w osobnej sesji;
jego nowsza kwalifikacja nie została zaimportowana do tego worktree.

**AI 05.5a — pakiet wejścia i loader release’u:** [runbook](forecast-runtime.md)
opisuje zweryfikowane features/curated, pełny grain, historię, niezmienne
profile, image/lock/schema/config pins oraz inferencję RF/HGB/baseline bez
refit. [Odbiór](evidence/05-05-runtime.md) potwierdza po 14 wartości na
rzeczywistych archiwalnych artefaktach AI 04; RF/HGB zgadzają się z adapterem
diagnostycznym. Peak RSS całej próby: ~310 MiB. **Nie załadowano rzeczywistego
zakwalifikowanego release’u i nie opublikowano prognoz.** Archiwum pozostaje
`not_ready` i ma starszy lock features niż model/runtime, więc wymaga
spójnego pakietu z AI 04 przed servingiem. Nowsze zmiany AI 04 są w osobnej
sesji. Rejestr i supervisor mają odbiór 05.5b poniżej. Do wykonania:
odbiór zatwierdzonego, spójnego modelu. Implementację integracji
z kolejką i atomowym outputem opisuje 05.6, odczyt prognoz 05.7a poniżej; zdarzenia w AI 10.
Nowa bramka kontraktów należy do `make check`, bez zdalnego Required CI.

**AI 05.5b — trwałe wejścia i ograniczony preflight:** [runbook](forecast-input-store.md)
opisuje niezmienne profile PostgreSQL, idempotentny zapis, rozdzielenie
środowisk i limity pojemności. Supervisor loadera nie przekazuje childowi
poświadczeń i ogranicza czas, pamięć oraz IO. [Odbiór](evidence/05-05-input-store.md)
potwierdza rzeczywisty pakiet, wyścig zapisów, rollback limitu JSONB oraz
SIGKILL/restart bez zmiany treści. Worker odrzucił brak zatwierdzonego release’u;
nie utworzono prognoz ani runów. Po migracji ponownie odebrano kolejkę fixtures.
Do wykonania: odbiór zakwalifikowanego, spójnego pakietu AI 04.
Implementację runu, fencing i publikacji opisuje 05.6, a odczyt prognoz 05.7a poniżej.
Zdarzenia należą do AI 10.
Brak zdalnego Required CI; AI 05 pozostaje otwarte.

**AI 05.6 — atomowa publikacja i integracja workera:** [runbook](forecast-publication.md)
opisuje przyjęcie zarejestrowanych profili, filtrowany profil wykonania,
release pins, ograniczony supervisor z heartbeat oraz jedną transakcję
partycji/manifestu/runu/historii/pointera. [Stan weryfikacji](evidence/05-06-publication.md)
zawiera testy jednostkowe na synthetic inputs i SQL-only stub gates.
Odbiór PostgreSQL potwierdza rollback częściowego zapisu, odrzucenie starego
tokenu, wygaśnięcie lease podczas publikacji, zachowanie nowszego pointera
i identyczny pełny stan po restarcie. Ponownie przeszła regresja kolejki.
Nie zaimportowano qualified release’u AI 04;
pełny odbiór rzeczywistego batchu czeka na spójny zakwalifikowany handoff.
Odczyt prognoz z paginacją i oceną freshness opisuje 05.7a poniżej; zdarzenia AI 10.
Zdalny Required CI tego brancha pozostaje nieodebrany; AI 05 jest otwarte.

**AI 05.7a — odczyt prognoz:** [runbook](forecast-read.md) opisuje
`GET /api/v1/forecasts`, oddzielne `forecast:read`, SQL scope przed limitami,
stable sort, default/max limit 50/200 i hash widoku wymagany dla dalszych stron.
Każdy odczyt weryfikuje całe manifesty/partycje oraz piny udanych runów.
[Odbiór](evidence/05-07-read.md) rozdziela testy techniczne od jakości modelu.
Starszy origin i nowsza nieudana próba dają `stale`; brak osobnego source
watermark daje `unknown`, bez deklarowania `current` na podstawie replay.
[Watermark i freshness 05.7d](forecast-freshness.md) mają osobny odbiór poniżej.
Do wykonania: spójny qualified handoff AI 04 oraz rzeczywisty
batch na jego release’ie. Zdalny Required CI nadal nie jest odebrany;
outbox/zdarzenia pozostają w AI 10. **AI 05 pozostaje otwarte.**

**AI 05.7b — katalog modeli i wersji:** [runbook](model-catalog.md) opisuje
trzy endpointy `/models`, szczegół modelu i `/versions`. Katalog obejmuje
wersje z publikacją w autoryzowanym scope; filtr SQL poprzedza distinct/count
oraz limit 1000 wersji. Numeric sort, piny enrollment/release i hash paginacji
chronią odczyt. Nie udostępnia globalnych metryk ani URI plików.
Head spoza scope pozostaje ukryty, aliases/runtime/drift nie są potwierdzane,
freshness pozostaje `unknown`. [Odbiór](evidence/05-07-catalog.md) podaje
rzeczywisty HTTP/PostgreSQL, próby uszkodzenia i restartu oraz granice fixture.
Scoped historyczne evaluations opisuje 05.7c poniżej, watermark 05.7d.
Qualified batch AI 04 nadal pozostaje do wykonania.
Zdalny Required CI tego brancha nie jest odebrany. **AI 05 pozostaje otwarte.**

**AI 05.7c — historyczne oceny:** [runbook](evaluations.md) opisuje listę i szczegół
`/evaluations`, całkowite pokrycie scope raportu przez grant, filtr statusu,
immutable import i stabilne strony. Metryki MAE/WAPE zostały odtworzone z
zapisanych predykcji/etykiet kalkulatorem AI 04, bez treningu. Pierwotny status
jakości oraz stare code/lock pins są zachowane, bez nowej kwalifikacji modelu.
[Odbiór](evidence/05-07-evaluations.md) obejmuje HTTP/PostgreSQL, 37 synthetic
ocen i rzeczywisty historyczny export (12012 memberships), odmowę częściowego
scope, niezmienność i SIGKILL/restart. Tabela pochodzi z migracji
`0013_forecast_evaluations`; bieżący DB head to `0017_v12_outputs` opisane powyżej.
Trwałego stosu nie zmieniano. Pozostają qualified handoff AI 04,
batch na jego release'ie oraz zdalny Required CI. **AI 05 pozostaje otwarte.**

**AI 05.7d — watermark i świeżość:** [runbook](forecast-freshness.md) opisuje
przypiętą deklarację kompletności źródła, osobną dostępność obserwacji w cutoff,
wejścia/outputy 1.1 oraz odczyt `forecast-read-v2`. Origin ma maksimum 24 h,
watermark musi obejmować origin, a obserwacja może mieć lag do 1 dnia zgodnie
z dobowym close. Starsze artefakty 1.0 zachowują canonical IDs i nie otrzymują
domyślnego `current`. [Odbiór](evidence/05-07-freshness.md) obejmuje rzeczywisty
HTTP/PostgreSQL, mixed-version odczyt, atomową odmowę rehashed niezgodnego dowodu,
tamper/restore i SIGKILL/restart. Przygotowanie 1.1 ze zweryfikowanego archiwum
AI 04 zachowało parenty i stare locki; nie kwalifikowało modelu ani nie publikowało
prognoz z tego archiwum. Migracja freshness to `0014_forecast_freshness`;
**bieżący head to `0017_v12_outputs`** opisane powyżej;
trwałego stosu nie migrowano. Pozostają qualified handoff AI 04, rzeczywisty
batch na jego release'ie oraz zdalny Required CI. **AI 05 pozostaje otwarte.**

**AI 03.3 — kontrakt handoff odebrany lokalnie:** [snapshot źródła](source-snapshot-handoff.md)
ma wspólną wersję 1.0.0, pełny mały fixture oraz niezależną walidację schema,
identity i transportu bez generatora/DB.
**AI 03.4 — typed importer odebrany lokalnie:** [CLI i runbook](source-snapshot-import.md)
opisują pełną weryfikację Parquet, canonical hashes, gates, atomową publikację
i niezmienny reimport. [Evidence](evidence/03-04-importer.md) podaje testy obu
smoke, partycje, truth opt-in i odłączony wheel.
**AI 03.5 — curated:** [runbook](curated.md) opisuje jawne mappings,
normalizację, quarantine, immutable IDs i odczyt z pełnej historii wersji.
[Evidence](evidence/03-05-curated.md) podaje 743 testy i pomiary smoke/as-of.
[AI 03.6 — bramka cross-repo](evidence/03-06-cross-repo.md) wiąże oba repo
przez pełne smoke i przypięte rewizje. Końcowy odbiór RetailOps określa wejście
do 04/06 oraz odrębne readiness use cases.
AI 12 rozwija się w osobnym worktree; forecasting zaczyna się od main z AI 03.
Branch 03 jest opublikowany w [PR #5](https://github.com/Oskar-Stachowski/retailops-ai-intelligence/pull/5).
Przypięte wyniki Required CI, testów i rzeczywistego Compose/persistence
znajdują się w [końcowym evidence cross-repo](evidence/03-06-cross-repo.md).

## Etap 06 — inventory

**AI 06 ma [końcowy odbiór](evidence/06-inventory-complete.md).**
[Snapshot/import/curated 1.1](reference/inventory-snapshot-11.md) obsługuje
source 2.7, 43 facts/plans i oddzielne private evaluation truth. Ledger, historyczny
routing, sprzedaż/zwroty, orders/plans/receipts i snapshots są niezależnie uzgadniane.
Curated zachowuje causal availability, fizyczny grain i odczyty as-of bez future fallback.
Pełny pipeline obu profili dwukrotnie spełnia budżet 300 s / 1024 MiB.
Inventory readiness dotyczy danych; modele 04/05/08 wymagają własnej oceny.
AI 04 i AI 12 zachowują odrębne branche/worktrees.

## Etap 11

- Zatwierdzony korpus: 29 dokumentów, 451 fragmentów, przypięte źródła Git,
  statusy, klasy dostępu i dokładne cytaty.
- Amazon Titan Text Embeddings V2, 1024 wymiary, `eu-north-1`, kontekst
  nagłówków i wersjonowany cache. Wywołania AWS są jawne i ograniczone budżetem.
- Golden set: 44 pytania, w tym 9 krytycznych. Bez zmiany etykiet i progów:
  **Recall@5 0,852941 ≥ 0,80; MRR 0,661275 ≥ 0,60; cytaty i krytyczne 1,0**.
- Trwałe runy odtwarzają pomiar bez AWS. Niezaliczony próg zachowuje raport
  `failed/gate_failed`, bez outputu. Sukces nie aktywuje indeksu automatycznie.
- Użytkowa kwalifikacja wymaga udanego runa, zgód, raportu jakości i kompletnego
  przeglądu podobieństw. Aktywacja i rollback mają CAS, idempotencję i niezmienne piny.
- Właściwy indeks jest aktywny w lokalnej bazie w kanale `retrieval`.
  PostgreSQL odtworzył wszystkie 44 wyniki golden. Runtime wymaga jawnego
  `RAG_BEDROCK_ENABLED=true` i poświadczeń AWS procesu.
- 643 testy regresji; dodatkowa kontrola 77 testów po dopracowaniu current/report
  również przechodzi. Historyczny odbiór semantyczny obejmował migrację `0008_rag_semantic`,
  pgvector, HTTP, runy, SQL gates, aktywację/rollback, SIGKILL i trwałość danych.

Nie pozostały otwarte blokady implementacji lub jakości Etapu 11.
Fake pozostaje wyłącznie ścieżką testową i nigdy nie uprawnia do użytkowej aktywacji.
Szczegółowe wcześniejsze evidence opisuje historyczne, mniejsze zakresy odbioru;
nie stanowi bieżącej listy braków.

## Fundament i dalsza praca

Etap 01 ma odbiór lokalny i zdalny: pakiet/CLI, settings, HTTP/telemetry,
lokalne poświadczenia i scope, odrębne PostgreSQL AI/pgvector i MLflow,
wykonywalne kontrakty danych/run/tool, jawne migracje i Required CI.
[Uruchomienie](local-stack.md), [uprawnienia](access-control.md),
[kontrakty](data-contracts.md), [odbiór zdalny](evidence/01-remote-ci.md).

[Bieżący odbiór danych](evidence/03-06-cross-repo.md) jest wspólny z RetailOps.
Po pełnej bramce 03 można rozdzielić forecasting **04** w AI i ledger **06**
w RetailOps; nowe źródło po 06 wymaga ponownego importu i zależnych ocen. Równolegle można przygotować
interfejsy i test doubles **AI 12**. Pełne zamknięcie agenta wymaga **AI 10 i 11**;
11 jest gotowy, 10 nadal należy do późniejszego ciągu danych/ML/integracji.
[Pisemna mapa etapów i repozytoriów](https://github.com/Oskar-Stachowski/retailops-cloud-native-platform/blob/main/docs/plans/ai/kolejnosc-i-repozytoria.md).

## Granice

Odbiór dotyczy lokalnego retrieval na konkretnym zatwierdzonym snapshotcie.
Zmiana dokumentacji na `main` nie aktualizuje automatycznie korpusu. Kolejna
wersja wymaga nowego snapshotu, przeglądu i ewaluacji.
Nie ma jeszcze generowania odpowiedzi, ewaluacji groundedness ani wykonywania
narzędzi agenta — to AI 12. Pipeline danych, modele, integracja zdarzeń oraz
wdrożenie AWS/EKS mają dalsze bramki. Limit AWS na proces nie zastępuje wspólnego
budżetu wielu replik ani produkcyjnego IAM. Nie deklarujemy wdrożenia w chmurze.

## AI07 — pełny strumień operacyjny DQ

[Odbiór konsumenta v2](evidence/ai/07/07.5-full-dq-consumer/README.md) rozwija
wcześniejszy replay wybranych sprzedaży do wszystkich sprzedaży i natywnych
zgłoszeń zwrotów, z ogonem po historii sprzedaży. Brakujące fakty pozostają
jawne; odrzucone zwroty nie zwiększają refundowanych sztuk. Pokrycie konkretnego
źródła nie kwalifikuje kompletności dnia. [Kwalifikacja dni](reference/day-qualification.md)
weryfikuje jawne zamknięcie źródła i dostępne wtedy zaakceptowane fakty DQ.
[Cechy anomalii](reference/qualified-anomaly-inputs.md) korzystają z tej bramki
osobno dla historii przed ocenianym dniem i wyniku po jego zamknięciu.
[Detektory](reference/anomaly-detectors.md) dodają podział czasowy z cutoffami,
baseline/Isolation Forest i progi wyznaczane z walidacji. Artefakty odtwarzają
trening i wynik; nie kwalifikują jeszcze jakości modelu. AI07 wymaga oceny
obserwacji i epizodów na większych danych, porównania modeli oraz własnego lifecycle.

# AI10 — kolejny przyrost: immutable source bundles

**2026-10-04.** [Instrukcja pobrania i importu](source-bundles.md) opisuje
przesłanie istniejącego, kompletnego eksportu przez chronione API Source i
rzeczywisty typowany importer AI, także dla natywnego Snapshot 1.2 / Source 2.8.
Zachowano oryginalne identyfikatory, osobny grant na cały eksport oraz atomowy
import/reuse. Główny lock i kod modeli nie zmieniają się. Przypięte kopie
importera pochodzą z zatwierdzonego ownera, a profil transferu ma osobny lock.
Cały AI10 nadal jest `in_progress`; live SQL snapshot, snapshot/offset/replay,
kwalifikacja trzech modeli i pełny UI E2E wymagają własnego odbioru.
