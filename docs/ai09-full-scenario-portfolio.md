# Pełne portfolio scenariuszy AI 09

Dotychczasowy v10 dopuszcza cztery źródła: jeden development i trzy końcowe
seedy. Generator Source 2.8 przyjmuje jeden plan demand albo physical na
generację. Jedna receptura nie pokrywa więc obu rodzajów zaplanowanej zmiany.
[Rozszerzenie v25](../contracts/evaluation/v25/full_scenario_portfolio_protocol.schema.json)
rejestruje cały wymagany zakres w jednym dzienniku, z jednym wspólnym freeze.

| Faza i seed | Warianty | Rozmiar każdego źródła |
| --- | --- | --- |
| Development 42 | ordinary, demand, physical | 365 dni, 100 produktów, 5 par sprzedaży, 3 lokalizacje zapasu |
| Final 42 | ordinary, demand, physical | 730 dni, 200 produktów, 10 par sprzedaży, 4 lokalizacje zapasu |
| Final 137 | ordinary, demand, physical | 730 dni, 200 produktów, 10 par sprzedaży, 4 lokalizacje zapasu |
| Final 2026 | ordinary, demand, physical | 730 dni, 200 produktów, 10 par sprzedaży, 4 lokalizacje zapasu |

To 12 pełnych źródeł. Ordinary zachowuje naturalny normal i znane promocje,
demand dostarcza zaplanowane demand shocks, a physical musi zawierać
`inventory_censored_episode`. Sam plan return spike nie zastępuje scenariusza
ograniczonego zapasu. Te deklaracje wymagają później rzeczywistych liczebności,
pokrycia i efektów w raportach; nazwa wariantu nie dowodzi wykonania scenariusza.

Warianty tej samej fazy i seeda mają identyczne pełne parametry bazowe oraz
osobne, przypięte hashe oryginalnych planów. Wszystkie źródła używają jednego
producenta i jego locków. Publiczne `CampaignGenerationPlan.bind` sprawdza
rodzaj planu i jego dokładny hash przed wejściem do istniejącego runnera.
Normalny Source writer, reader i niezależny replay pozostają obowiązkowe.

Protokół wskazuje osobne, zamrożone źródło treningu dla każdego zastosowania.
Forecast RF/HGB/TensorFlow zachowuje wspólny zakres i równe budżety prób oraz
seedów inicjalizacji. Zależność między wariantami development może przenosić
wyłącznie zadeklarowany artefakt tego samego zastosowania; odczyt własnego
rodzica nadal wymaga własnej zakończonej generacji. Final nie pożycza mutowalnej
gałęzi development. Źródła i wszystkie odczyty pozostają jawnie rozliczone.

Przed wspólnym freeze muszą zakończyć się odczyty całego development oraz
ocena wszystkich trzech zastosowań w każdym z trzech wariantów. Każdy
zadeklarowany fit musi mieć próbę w dzienniku; awaria pozostaje rozliczona,
a niewykonana próba nie może zniknąć za wybranym zwycięzcą. Dotychczasowe guardy
wymagają również zakończonego fitu każdej rodziny forecastu i rozstrzygnięcia
wszystkich rezerwacji. Końcowe zamknięcie obejmuje wszystkie trzy zastosowania
na każdym z dziewięciu źródeł final. Generacja każdego źródła ma tylko jeden slot.

V10 i jego schemy pozostają niezmienione. V25 ma jawne
`portfolio_version=ai09-full-scenario-portfolio-1.0.0`, a `version` zachowuje
wersję protokołu bazowego. Czytnik dziennika wybiera właściwy ścisły kontrakt
i zachowuje całą rozszerzoną strukturę przy reload, rezerwacji i publikacji.

[Receipt 09.41](evidence/09-41-full-scenario-portfolio.json) oddziela testy
metadata i trwałego dziennika od wykonania naukowej kampanii. Przykładowe
68 operacji/78 slotów w testach nie są zamrożonym protokołem projektu.
Native control sprawdza oryginalne kontrakty planów oraz 12 publicznych bindings
na pełnych metadanych, z jawnymi kontrolnymi IDs i wcześniej eksponowanymi
oknami. Nie generuje datasetów ani nie kwalifikuje aktywności serii, efektów
fizycznych, zasobów lub świeżości final.

[Przyrost 09.42](evidence/09-42-portfolio-forecast-evaluation.json) podłącza
publiczny evaluator forecast do wariantów development. Nowe receipt v26
zapisują pełny, wcześniej zamrożony protokół, oryginalną recepturę operacji oraz
osobne dataset IDs treningu i oceny. Źródło treningu musi odpowiadać deklaracji
`training_source_recipe_sha256["forecast"]`. Własny zakończony export ocenianego
wariantu jest wymagany przed odczytem jego danych. Inny wariant wymaga innego
dataset ID; ponowna ocena tego samego źródła zachowuje jego ID.

Wszystkie wcześniej zamrożone próby korzystają ze swoich oryginalnych modeli,
encodingów i kalibracji. Ich ponowny fit lub wybór architektury podczas oceny
jest zabroniony. Powiązanie wariantu sprawdzają runner, core consume/finalize,
receipt i publiczny verifier. Jest zapisane w obowiązkowym `portfolio.json`
z checksum; proces inferencji nadal otrzymuje wyłącznie dozwolone dane wejściowe.
Dotychczasowe receipt i evaluator bazowego v10 zachowują wymóg tego samego
źródła development i dataset ID. Dotychczasowa ścieżka final pozostaje związana
z trwałym freeze i niezależnym development evidence.

Zaliczono 14 nowych kontroli oraz 146 testów regresji. Publiczne przebiegi dla
ordinary/demand/physical obejmują wszystkie klucze małych, już eksponowanych
kontroli i wszystkie zadeklarowane próby. Również pełne raporty segmentów
i niepewności zachowują odrzucenie przy niedostatecznej próbie. Dwa przebiegi
przeszły z rzeczywiście zainstalowanej paczki. Rodzice Source i modele są w tych
testach jawnie kontrolne; nie dowodzą efektów pełnego scenariusza ani wyników
projektowej kampanii.

[Przyrost 09.43](evidence/09-43-portfolio-selection-evidence.json) rozszerza
wspólną kontrolę przed dostępem do final. Samo ukończenie operacji w dzienniku
nie wystarcza: sprawdzane są prywatne receipty i rzeczywiste artefakty każdej
zadeklarowanej oceny development, także wariantów innych niż trzy receipty
wskazane bezpośrednio przez wybór. Wszystkie warianty muszą dotyczyć tych samych
wybranych komponentów, a forecast również tej samej zamrożonej konfiguracji.

Mapa `development_selection_bundles` zawiera trzy wybrane aliasy `forecast`,
`anomaly`, `stockout` oraz ścieżkę artefaktu pod każdym zadeklarowanym operation ID
oceny development. Dla standardowego portfolio są to łącznie 12 kluczy.
Brak dowodu, słaby wynik, inne komponenty, uszkodzenie artefaktu lub kilka
udanych zakończeń jednej operacji blokują final. Osobny powtórzony pomiar wymaga
własnej prerejestrowanej operacji; nie wybieramy najlepszego zakończenia po wynikach.
Nieudane próby i ich koszty pozostają w dzienniku.

Dotychczasowy digest wyboru pozostaje niezmieniony: zawarty w nim
`development_journal_head_sha256` wiąże już całą wcześniejszą historię i hashe
dowodów. Nie dodano wersji wire. Bazowy v10 nadal przyjmuje trzy aliasy.
Zaliczono 17 kontroli nowej granicy, 118 regresji, 32 kontrole generacji i trzy
kontrole z zainstalowanej paczki. Nowe kontrole używają prawdziwego trwałego
journal i prywatnych plików, lecz jawnie zastępują naukową weryfikację forecastu.
Produkcja nadal używa jej istniejącego typed parsera/publicznego verifiera.
Pełne typed evaluatory anomaly/stockout i ich rzeczywista kwalifikacja są otwarte.

[Przyrost 09.44](evidence/09-44-required-group-policy.json) dodaje jawną
[politykę v27](../contracts/evaluation/v27/portfolio_required_group_policy.schema.json).
Jej digest jest zamrożony jako `selection_policy_sha256` protokołu przed
generacją i wynikami. Każde z 12 źródeł ma własny uporządkowany, jednoznaczny
zestaw wymaganych grup, przypięty do oryginalnej receptury. Globalna ocena,
wszystkie 14 horyzontów, katalog kategorii i oba kanały są wymagane na każdym
źródle. Ordinary musi ocenić normal i promotion, demand — demand shock,
a physical — inventory constraint. Komplet pozostałych krytycznych grup musi
mieć zadeklarowanych właścicieli osobno dla development i każdego końcowego
seeda. Liczebności zaobserwowane po wynikach nie wybierają właściciela.

Niektóre grupy opisują strukturalnie nieznane wejścia, np. brak kategorii
czy nieznany lead time. Zachowują wszystkie liczebności, metryki, statusy
i niepewność, także przy zerowej próbie. Nie muszą wystąpić w każdym źródle.
Zero/intermittent sales, cold start, brakująca i spóźniona historia, znane
poziomy zapasu i lead time oraz planowane anomalie pozostają obowiązkowe
w pełnym portfolio. Niedostateczna próba wymaganej grupy nadal blokuje
kwalifikację; nie zmieniono progów jakości ani sposobu liczenia niepewności.

Publiczny evaluator, finalize worker i niezależny verifier stosują tę samą
zamrożoną politykę. Nowy receipt zapisuje pełny protokół i politykę oraz wymaga
`required-groups.json` i `portfolio-protocol.json` w prywatnym artefakcie.
Development zachowuje także istniejący `portfolio.json`. Polityka nie trafia
do procesu inferencji. Starsze receipty i 84 pliki kontraktów pozostają
niezmienione i zachowują wcześniejsze ścisłe reguły.

Kontrole 09.44 używają rzeczywistych strumieni metryk, plików, SQLite i bootstrapu
na małych jawnych danych, ale kontrolnych rodziców Source i modeli. To odbiór
mechanizmu. Nie potwierdza wykonania natywnych interwencji, zachowania encodera
przy nieznanych kategoriach, pełnego canonical profilu ani projektowego final.

Do rzeczywistego wykonania pozostają odbiór pełnej pojemności, kompletne
produkcyjne receptury i budżety, rzeczywiste evaluatory anomaly/stockout,
kwalifikacja rzeczywistego pokrycia segmentów oraz pełny wybór na evidence
wszystkich wariantów. Wagi wymagają prerejestracji, aby powtarzające się normalne klucze
nie były liczone wielokrotnie. Przed inicjalizacją Project trzeba zweryfikować
nieeksponowane końcowe okna. Samo zamknięcie lokalnego dziennika nadal nie
nadaje jakości, świeżości ani statusu AI 09 ready.
