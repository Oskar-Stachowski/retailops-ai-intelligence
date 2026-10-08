# Pomiar pełnego development AI 09

[Prospektywna receptura 1.3](reference/ai09-development-capacity-v1.3.json)
przypina source `51827823`: cached ścieżka kopiuje tylko wejścia commerce,
których używa symulator, przez istniejącą funkcję `source_bridge._copy_commerce_inputs`.
Nie powtarza wdrożonych zmian AI08 dotyczących indeksów, cache i zwalniania build
state. Kontrolne native parity ma 23 passed; zmierzony spadek alokacji samej kopii
wynosi 11.87%, bez deklaracji poprawy całego RSS lub kwalifikacji pełnego profilu.

Worker generacji tej nowej próby zapisuje co 120 s stos własnych wątków,
bez lokalnych wartości. Obserwacja pomaga wskazać wykonywaną funkcję, ale nie jest
pomiarami alokacji ani ciągłym profilem pamięci. Zatrzymuje się także przy wyjątku;
log nadal obejmuje istniejący limit 16 MiB i cały scratch oraz zasoby workera.
28 testów supervisora, scope i obserwacji stosu przeszło w 1.27 s. Jeden kontrolny
proces rzeczywiście zapisuje okresowe stosy i sprawdza ich anulowanie oraz brak
lokalnych wartości w logu. Krótki interwał tego testu nie jest akceptowany przez
zamrożony plan pełnej próby. [Evidence](evidence/09-30-development-capacity-next-preparation.json)
opisuje zakres tych kontroli.

Receptura zachowuje bajty 1.0/1.1/1.2 i łańcuch wszystkich trzech porażek,
pełne wymiary i daty canonical, budżety RSS/scratch/czasu, rezerwy i parent caps.
Nie została uruchomiona. Wymaga chronionej publikacji producenta, pełnego odbioru
jego dokładnego head/main oraz publikacji i pełnego CI tej diagnostyki.
Pierwszy Required CI producenta `37683555355` zakończył się cancelled:
28/30 success, Docker cancelled i required-result failure. Instalacja Chromium
przekroczyła 35 minut przed rozpoczęciem testów aplikacji; data quality przeszło.
Oryginalny wynik jest zachowany, ponowiono tylko niezaliczone zadania CI.
Nie ponowiono canonical ani żadnej projektowej generacji. Aktualny source main
`39d56447` ma pełny odbiór 30/30 success. AI07/08 pozostają zamknięte.

Producent został następnie scalony przez chroniony
[Source PR #105](https://github.com/Oskar-Stachowski/retailops-cloud-native-platform/pull/105):
head `6bc96e2f` ma zakończony Required CI `37692294899`, 25 odczytanych z 25
jobs, 21 success, cztery zamierzone scoped skips i required-result success.
Merge `5c05445e` jest na Source/main; jego dokładny odbiór `37698252427`
przeszedł: 25 odczytanych z 25 jobs, 21 success, cztery zamierzone scoped
skips oraz required-result success. Przypięcie producenta `51827823` w recepturze nie zmienia się.
Pierwszy head diagnostyki `cf092d5e`, CI `37690635892`, zakończył się failure
z 15/17 success przez dotychczasowy limit manifestu 64 KiB. Zachowano
21 failed, 962 passed, 54 errors, 30 skipped i koszt 1483.69 s tego sharda.
Nowa gałąź zawiera poprawkę bounded metadata cap 128 KiB oraz diagnostykę
przerwanego backupu z kalibracji `b2e12a5d` i zaakceptowany main `92c2a3cd`.
48 testów diagnostyki, przerwania i limitu manifestu przeszło w 2.27 s;
Mypy dla 688 plików przeszło. Nowy dokładny head wymaga pełnego CI,
chronionego merge i odbioru AI/main przed dispatch. Pełna próba 1.3 nadal
nie została uruchomiona, a poprzednie plany, limity i porażki są zachowane.

[Prospektywna receptura 1.2](reference/ai09-development-capacity-v1.2.json)
została uruchomiona na `16d34887` z zaakceptowanego main `b0e2de16`.
[Trzeci pełny pomiar](evidence/09-28-development-capacity-third-run.json),
run `37676033214`, zakończył się `tree_rss_limit` po 1751.743 s.
Generacja osiągnęła próbkowane 8592396288 B, ponad limit 8 GiB;
kwalifikacja, eksport, import i curated nie rozpoczęły się. Minimalna dostępna
pamięć wyniosła 7162466304 B. Supervisor zatrzymał wyłącznie własnego workera;
nie wykonano retry, fitów ani final testu. Pełny koszt ukończonego profilu
pozostaje nieznany. Plan, artefakt i dwie wcześniejsze porażki są zachowane.
Wykorzystuje istniejącą ścieżkę AI 08 `source_cohort_batch_v2.run`, z indeksem
identyfikatorów, cached walidacją immutable rekordów i zwalnianiem build state
przed pełnym ponownym odczytem źródła. Zaktualizowane przypięcia zachowują
strażniki drift; native ordinary/cached parity ma 16 zaliczonych testów,
a trzy dodatkowe przypadki z 14 dniami planów forecast przechodzą na seedach
42/137/2026, z identycznymi 58 tabelami, CSV, kontekstem, source ID i bramkami.

Przed tym dispatch sprawdzono chronioną publikację producenta i pełny odbiór
source main `b7234899` (25 odczytanych z 25 jobs, 21 success i 4 zamierzone
skips) oraz AI main `b0e2de16` (17/17 success z required-result).
Ten pomiar jest wyłącznie ręczny;
push nie uruchamia automatycznego ponowienia. Rozmiar canonical, daty, locks,
8 GiB RSS/scratch, godzina wall, parent caps i wszystkie zakazy pozostają
takie jak w 1.1. Receptura przypina drugi wynik `wall_limit`, a wcześniejszy
plan 1.1 zachowuje pierwszą porażkę i plan 1.0. Oba stare plany pozostają
bajt w bajt. 21 testów supervisora i kontroli scope jest zaliczonych.
Wersja receptury 1.2 nie oznacza snapshotu anomalii 1.2: oczekiwany snapshot
tego diagnostycznego świata nadal ma wersję **1.1.0**.
[Odbiór połączeń](evidence/09-19-cached-development-capacity-preparation.json)
ukończył wszystkie pięć rzeczywistych faz na świeżym kontrolnym `ai-load`
45 × 2 × 1 × 1: source 2.7 ma 2800 wierszy i 58 tabel, snapshot/curated 1.1
mają 2124 operacyjne wiersze, 43 tabele i 0 odrzuconych. `forecast_source`
jest passed; modele, stockout i anomalie nie mają odbioru w tym małym świecie.
Nie mierzono pełnego drzewa RSS ani nie wykonano canonical lub kampanii.

[Plan 1.1](reference/ai09-development-capacity-v1.1.json) określa prawdziwe `ai-dev`:
365 dni, 100 produktów, 5 sklepów, 3 lokalizacje zapasu i seed 42. Generator
jest przypięty do `1de4627`, historia kończy się 2026-07-31, a znane plany mają
14 dni. To diagnostyka zasobów na wcześniej eksponowanych datach development.
Nie jest źródłem prospektywnej kampanii ani dowodem świeżości final test.

Workflow `AI09 canonical development capacity diagnostic` działa na osobnym
runnerze GitHuba. Kod odrzuca pełny przebieg na lokalnym komputerze. Generacja,
kwalifikacja, eksport snapshot 1.1 z deklarowanymi planami, import i curated są osobnymi procesami;
pozostała pamięć wcześniejszej fazy nie przechodzi do następnej. Wspólny limit
czasu obejmuje wszystkie fazy. Co 0,2 s supervisor mierzy własny RSS i całe
drzewo workera, logiczny i zaalokowany scratch, wolny dysk i dostępną pamięć.
Jest to próbkowanie, więc krótsze skoki całego drzewa mogą pozostać niewidoczne.
Każdy zakończony worker zapisuje też własny systemowy peak RSS; przekroczenie
limitu przez ten dodatkowy pomiar odrzuca fazę. Czas CPU jest
próbkowaną dolną granicą workera i nie obejmuje CPU supervisora.

Limity nowej próby: 8 GiB RSS, 8 GiB scratch, 3600 s, 6 GiB rezerwy dysku i 1 GiB
dostępnej pamięci. Przed startem wymagany jest dodatkowo wolny zapas równy
całemu budżetowi scratch/RSS. Przekroczenie limitu, awaria pomiaru lub błąd
workera zatrzymuje jego własną grupę procesów i zachowuje koszt oraz porażkę.
Nie zmienia limitów v11/v12, dawnych receiptów ani budżetów eksperymentów.

Artefakt GitHuba zachowuje plan z commitami i skrótami kodu/locków, pomiary
wszystkich rozpoczętych faz oraz ich ograniczone logi i metadane. Nie ma retry
generacji wewnątrz próby. Ewentualne kolejne uruchomienie jest odrębną, widoczną
próbą diagnostyczną. Nie wykonuje fitów, ocen modeli, końcowej generacji,
kalibracji lub promocji. Nie kwalifikuje `ai-training` ani całego AI 09.
Kontrakt 1.2 wymaga osobnego producenta z planem anomalii; samo włączenie
znanych planów forecast nie zmienia prawdziwej wersji snapshotu 1.1.

[Kontrolny odbiór](evidence/09-15-development-capacity-preparation.json) obejmuje
14 testów supervisora, w tym rzeczywistych procesów oraz native generację, kwalifikację,
eksport, import i curated małego `ai-load` 45 × 2 × 1 × 1. Otrzymano 2124
operacyjne wiersze snapshot/curated, 0 odrzuconych i `forecast_source: passed`.
To sprawdzenie połączeń API i obsługi zasobów.

[Pierwszy pełny pomiar](evidence/09-16-development-capacity-first-run.json)
na `cef4f08`, run `37611605538`, zakończył się `tree_rss_limit` po 127,875 s.
Generacja osiągnęła próbkowane 4295168000 B, ponad 4 GiB; nie ukończyła źródła,
więc kwalifikacja, eksport, import i curated nie rozpoczęły się. Supervisor
zatrzymał wyłącznie własnego workera (`exit_code: -9`). Plan i koszt są zachowane
w artefakcie GitHuba, bez automatycznego retry. To zmierzona dolna granica
potrzeb pełnego producenta; całkowity peak zakończonego profilu pozostaje nieznany.
Kolejny większy budżet wymaga osobnej prospektywnej receptury i własnego pomiaru,
albo ograniczenia pamięci producenta. Rozmiar canonical i stare limity pozostają
przypięte. Ten wynik nie kwalifikuje żadnej kampanii lub `ai-training`.

Osobna receptura 1.1 przypina tę porażkę i zachowuje [plan 1.0](reference/ai09-development-capacity.json)
bajt w bajt. Przed pierwszym startem runner miał 15532302336 B dostępnej pamięci;
to pozwala zaplanować próbę 8 GiB z dodatkową rezerwą 1 GiB. Wymagany preflight
i bieżące limity nadal działają. To nowy limit diagnostyczny, którego nie
uznajemy za zmierzony koszt ukończonego profilu. Kod producenta, pełny rozmiar
danych, historia, parent limits i zakazy fitów/final generation pozostają
przypięte. Pierwsza porażka pozostaje odrębnym
runem i nie jest nadpisywana.
Receptura 1.1 ma 17 zaliczonych testów supervisora i kontroli scope; obejmują
odrzucenie zmiany parent limits, rezerwy pamięci oraz skrótu wcześniejszego planu.

[Drugi pełny pomiar](evidence/09-18-development-capacity-second-run.json),
run `37613368332` na `d88d4a2`, zakończył się `wall_limit` po 3600.552 s.
Próbkowany peak własnego drzewa wyniósł 7679963136 B, poniżej limitu 8 GiB;
próbkowane CPU workera miało dolną granicę 3599.54 s. Generacja nie ukończyła
źródła; wszystkie późniejsze fazy pozostają nieuruchomione. Minimalna dostępna
pamięć wyniosła 8043921408 B. Artefakt i plan tej porażki są zachowane osobno.
Nie oznacza to, że ukończony profil mieści się w 8 GiB lub ma znany koszt.

[Source PR #101](https://github.com/Oskar-Stachowski/retailops-cloud-native-platform/pull/101)
proponuje ograniczenie kopii wejść commerce i jednokrotne stosowanie ruchów
przy dziennych snapshotach, z fallbackiem dla spóźnionych faktów.
456 native testów przechodzi. Pięć kontrolnych native par zachowuje kompletne
wyniki; alokacje Python podczas kopii maleją o 14.81%, CPU dziennych snapshotów
o 89.62%. To oddzielne pomiary komponentów. Przed nowym pełnym pomiarem potrzebne
są publikacja producenta, jego CI oraz osobny plan zachowujący obie porażki.

Odbiór source PR #101 na `5bec26f9` zakończył się pełnym zielonym Required CI
[37623701164](https://github.com/Oskar-Stachowski/retailops-cloud-native-platform/actions/runs/37623701164).
Chroniony merge opublikował `cbcac6eb` na source `main`;
[odbiór dokładnego main](https://github.com/Oskar-Stachowski/retailops-cloud-native-platform/actions/runs/37629010997)
był w toku w chwili wcześniejszej publikacji. Późniejszy source main
`b7234899` ma pełny odbiór, a receptura 1.2 i jej trzeci nieudany pomiar
są opisane powyżej. Zielone CI komponentów nie kwalifikuje pełnego profilu.

Core usprawnień CI z PR #34, main `89b64d2`, jest zintegrowany w tej gałęzi
bez zmiany `src` lub locków i bez przepięcia producenta AI07.
Przed chronionym merge tej publikacji wymagane są pełne CI dokładnego nowego
head oraz [main po PR #34](https://github.com/Oskar-Stachowski/retailops-ai-intelligence/actions/runs/37628897194).

Przed pierwszym dispatch przypięcie receptury zmieniono na source PR #103
`16d34887`, zawierający poprawkę odczytu Kafka i wsparcie planned anomaly.
Wynik tiny control w 09-19 pochodzi z poprzedniego `5bec26f` i pozostaje
historycznym odbiorem połączeń. Regresja cohort/forecast plans nowego source
ma 31 passed; pełne CI i odbiór jego main sprawdzono przed dispatch.

Ta próba już wykorzystuje ograniczenia pamięci wprowadzone przez AI08.
Nie należy wykonywać ich ponownie ani uznawać pomiarów małego świata za
dowód pełnej skali. Przed kolejną próbą trzeba zbadać pozostałe alokacje
producenta i zamrozić osobną wersję diagnostyki, zachowując wszystkie porażki,
canonical rozmiary, budżety kampanii i rezerwy. Ten cached forecast world
nie kwalifikuje pełnego planned-anomaly source 2.8 ani final `ai-training`.

Poprawiony head diagnostyki `0ac90bd0` ma zakończony pełny Required CI
`37698835119`, 17/17 success. Kalibracja i wybór Tune są już na AI/main
`78d853a6`, którego dokładny pełny odbiór pozostaje w toku. PR #43 diagnostyki
wymaga jeszcze chronionej publikacji na main i odbioru dokładnego merge przed
ręcznym dispatch. Source/main jest już w pełni zaakceptowany; przypięcie
producenta receptury, wszystkie stare plany, trzy porażki, limity i rezerwy
pozostają bez zmian. Pełna próba 1.3 nadal nie została uruchomiona.
