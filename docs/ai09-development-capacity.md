# Pomiar pełnego development AI 09

Przygotowana [receptura 1.6](reference/ai09-development-capacity-v1.6.json)
przypina Source `a164cc6a` po [audycie pięciu faz](ai09-capacity-audit.md).
[Dowód przygotowania](evidence/09-48-audited-capacity-preparation.json) zachowuje
sześć poprzednich receptur i porażek, pełne wymiary i daty, limity, rezerwy,
locki konsumenta/eksportera oraz bezpieczny obserwator stosu. Source 2.2 ogranicza
retencję i kopiowanie, koszt wyszukiwania oraz alokacje przy walidacji i zapisie;
AI korzysta z odebranych lokalnie indeksów i ponownego użycia serializacji.
50 kontroli supervisora przeszło, w tym odmowa obcego producenta lub pinu zależności,
zmiany profilu, limitów i historii porażek oraz ograniczanie własnych procesów.

Pin zależności producenta zmienia się jawnie z `f55452e6` na `ea389b45`:
nowy Source zawiera wcześniejszą aktualizację `4805834` z jego `main`.
Pełny diff i obie sumy są zapisane w dowodzie; to pomiar nowej implementacji
i środowiska. Starsze receptury zachowują własne zależności. Aktualizacja nie
zmienia limitów ani wymaganych danych. 1.6 nie została uruchomiona: wymagane są
pełne CI audytu i jego wynikowych main w obu repozytoriach oraz osobny odbiór
publikacji diagnostyki przed pojedynczym ręcznym dispatch.

Poniżej zachowano opis wykonanej próby 1.5 i poprzednich pomiarów.

[Receptura 1.5](reference/ai09-development-capacity-v1.5.json) przypina Source
`ab4d5690` z [PR #107](https://github.com/Oskar-Stachowski/retailops-cloud-native-platform/pull/107).
Cached simulator po pełnej walidacji i inicjalizacji kolejki zwalnia dodatkowe
referencje do typowanych list scenariusza. Każde zdarzenie nadal pozostaje
w kolejce do wykonania; pełny scenariusz rodzica i wszystkie wyniki są zachowane.
33 native testy oraz trzy pary kontroli alokacji potwierdzają zgodność
58 tabel/CSV, zwalnianie obiektów podczas wykonania i brak mutacji wejść.
Spadek peak alokacji Python małego komponentu o 15.50% nie dowodzi zmniejszenia
pełnego RSS ani czasu całego generatora. [Receipt przygotowania](evidence/09-39-event-release-capacity-preparation.json)
wiąże exact producer, recepturę, niezmienione limity i pięć wcześniejszych prób.

[Przyrost 09.40](evidence/09-40-planned-source-cache-integration.json) dodaje
osobną ścieżkę cached execution planów demand/physical Source 2.8. Na trzech
już odsłoniętych seedach zachowuje komplet danych, wszystkie 38 kontroli jakości,
pełny fizyczny scenariusz i niezależny ordinary replay. Zamrożona receptura 1.5
nadal przypina wcześniejszy commit `ab4d5690`; nie zmieniono jej scope ani locks.
PR50 publikuje ją na main `f0088e08` po pełnym head CI. Source PR107 został
scalony jako `3d13a4dd` po poprawce testowej `fe9d404d`; dokładny head i wynikowy
main mają pełne 25-job inventory: 21 success i cztery deklarowane scoped skips.
Nieudany CI wcześniejszego head `345a7cf3` pozostaje zachowany.

Pełna próba 1.4 została wykonana i zakończyła się `tree_rss_limit` po 1836.06 s,
z peak drzewa 8590368768 B i zero ukończonych faz.
[Wynik 09.38](evidence/09-38-development-capacity-fifth-run.json) zachowuje
artefakt, koszt oraz niepotwierdzoną przyczynę SIGSEGV starszej próby 1.3.
1.5 zachowuje bajty receptur 1.0–1.4, cały łańcuch pięciu porażek, wymiary
365 × 100 × 5 × 3, daty, 14 dni planów, locks, RSS/scratch po 8 GiB,
3600 s, rezerwy i parent caps. Zachowuje też bezpieczny obserwator ramek 1.4.
**Próba 1.5 została uruchomiona raz** jako
[run 37765329151](https://github.com/Oskar-Stachowski/retailops-ai-intelligence/actions/runs/37765329151),
na niezmiennym AI `656d7ddc` i oryginalnym Source `ab4d5690`, po świeżym pełnym
odbiorze dokładnych CI obu repozytoriów, ancestry i niezmienionych receptur/locków.
Próba zakończyła się `tree_rss_limit`: 2967.75 s, peak drzewa 8596701184 B,
zero ukończonych faz. [Szósta porażka](evidence/09-45-development-capacity-sixth-run.json)
zachowuje uwierzytelniony i zweryfikowany artefakt, pełny koszt, niezmienione
parametry oraz ograniczenia obserwacji stosów. Przed kolejnym canonical trwa
szczegółowy audyt wszystkich pięciu faz i odbiór bezpiecznych optymalizacji.
Nie wykonujemy automatycznego retry ani zmiany profilu lub limitów.
Plan nie uprawnia do projektowych fitów ani final testu.
Pomiar nadal dotyczy Source 2.7/snapshot 1.1; planowany Source 2.8,
pełny ai-training i kampania wszystkich trzech zastosowań wymagają osobnego
odbioru. AI07/08 pozostają zamknięte.

Poniżej zachowano historię wcześniejszych przygotowań i pomiarów.

[Prospektywna receptura 1.4](reference/ai09-development-capacity-v1.4.json)
zastępuje natywny timer dumpujący stosy ograniczoną obserwacją ramek przez
`sys._current_frames()` w osobnym wątku własnego workera. Odczytuje wyłącznie
nazwę pliku, funkcję i numer linii. Referencje do ramek są zwalniane przed
zapisem logu; nie odczytuje lokalnych wartości, globali, argumentów ani linii
kodu. Co 120 s może zapisać najwyżej 64 wątki po 64 ramki, do 64 KiB;
skrócenie głębokości, liczby wątków, nazw lub bajtów jest jawne. Zatrzymanie
ustawia event i łączy wątek z limitem 5 s. Błąd obserwacji lub brak zakończenia
wątku uniemożliwia zaliczenie fazy. RSS/CPU, scratch i istniejący limit logu
16 MiB obejmują diagnostykę. Obserwacja może być opóźniona, gdy kod natywny
nie zwalnia GIL; nie jest profilem alokacji, ciągłym stosem ani natywnym
backtrace awarii. Tę ograniczoną przydatność odczytu ramek opisuje także
[dokumentacja Python 3.11](https://docs.python.org/3.11/library/sys.html#sys._current_frames).

[Kontrole 1.4](evidence/09-33-development-capacity-safe-trace-preparation.json)
mają 47 passed w 1.81 s. Obejmują rzeczywisty okresowy zapis i anulowanie,
zmieniające się obiekty kodu i wątki, głęboki stos, zwolnienie payloadu po
zakończeniu ramki, granice bajtów i unicode, awarie obserwacji oraz zachowanie
łańcucha wszystkich czterech porażek. To małe kontrole stdlib, bez generacji
Source, fitów i final test. Kontrole Linux nowego dokładnego head, pełne
Required CI, chroniona publikacja i odbiór main są nadal wymagane przed
odrębnym ręcznym dispatch. Na etapie tych kontroli próba 1.4 jeszcze nie była
uruchomiona; jej późniejszy rzeczywisty wynik opisuje receipt 09.38 powyżej.
Producent, locks, canonical, daty, limity i rezerwy są identyczne z 1.3;
bajty receptur 1.0–1.3 pozostają zachowane. Hipoteza o przyczynie poprzedniego
SIGSEGV pozostaje niepotwierdzona. Zmiana obserwatora nie dowodzi zmniejszenia
pamięci ani ukończenia pełnego profilu.


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
Nowa próba została uruchomiona 2026-10-08 po chronionej publikacji producenta
i diagnostyki oraz pełnym odbiorze ich dokładnych head/main.
[Run 37714051649](https://github.com/Oskar-Stachowski/retailops-ai-intelligence/actions/runs/37714051649)
wykonano na osobnym runnerze z AI/main `0990905b`; pełny Required CI tego main
`37711535728` ma 17/17 success. Source/main `5c05445e` ma 25 odczytanych
z 25 jobs, 21 success i cztery zamierzone scoped skips. Producent `51827823`
pozostaje przypięty i ma pełny odbiór attempt2: 30/30 success. Sprawdzono
jego ancestry oraz dokładne bajty receptury i workflow na zaakceptowanym main.
[Czwarty pełny pomiar](evidence/09-32-development-capacity-fourth-run.json)
zakończył się `worker_exit`, `exit_code: -11` (`SIGSEGV`), po 883.575 s;
generacja trwała 882.775 s i nie ukończyła żadnej z pięciu faz. Próbkowany
peak drzewa wyniósł 7562665984 B, poniżej limitu 8 GiB; próbkowane CPU miało
dolną granicę 847.72 s. Ten wynik nie dowodzi przekroczenia RSS i nie jest
kosztem ukończonego profilu. Minimalna dostępna pamięć miała 8177016832 B.
Artefakt 11523680984 zawiera plan, receipt i log; brak ukończonego worker receipt.
Zachowano wszystkie cztery porażki i koszty. Nie wykonano retry ani fitów,
nie otwarto final test; profile i AI09 nadal nie mają kwalifikacji.

Log urywa się podczas okresowego dumpu po `price_resolver.py:60`, z niedokończonym
prefiksem `File`. Przyczyna awarii pozostaje niepotwierdzona: nie ma core ani
natywnego backtrace. Podobne awarie diagnostyki opisują zgłoszenia
[CPython 116008](https://github.com/python/cpython/issues/116008)
(3.11.4, sygnał SIGUSR1) i
[CPython 158200](https://github.com/python/cpython/issues/158200)
(timer, pydebug main/3.16). Dotyczą innych wyzwalaczy lub wersji i są tylko
podstawą hipotezy, że awarię mógł wywołać watchdog obserwacji.
[Przypięty CPython 3.11.15](https://github.com/python/cpython/blob/v3.11.15/Modules/faulthandler.c)
używa natywnego watchdogu do odczytu stosów. Przed odrębną kolejną próbą
potrzebna jest kontrola bezpieczniejszej, ograniczonej obserwacji Pythonowych
ramek i nowa prospektywna receptura, zachowująca plany 1.0–1.3, pełny scope,
budżety, rezerwy i wszystkie porażki. Samo usunięcie diagnostyki nie dowodzi
spadku RSS, ukończenia źródła ani kwalifikacji pełnych profili.
Pierwszy Required CI producenta `37683555355` zakończył się cancelled:
28/30 success, Docker cancelled i required-result failure. Instalacja Chromium
przekroczyła 35 minut przed rozpoczęciem testów aplikacji; data quality przeszło.
Oryginalny wynik jest zachowany, ponowiono tylko niezaliczone zadania CI.
Nie ponowiono canonical ani żadnej projektowej generacji. W tamtej obserwacji source main
`39d56447` miał pełny odbiór 30/30 success. AI07/08 pozostają zamknięte.

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
Mypy dla 688 plików przeszło. Poprawiony head przeszedł pełne CI, chroniony merge i odbiór AI/main.
Pełna próba 1.3 została następnie uruchomiona po pełnym odbiorze dokładnego
head `a93893b8`, chronionym merge PR43 i odbiorze main `0990905b`. Poprzednie
plany, limity i porażki pozostają zachowane.

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

Poprawiony head diagnostyki `0ac90bd0` ma zakończony Required CI
`37698835119`, 17/17 success. Po integracji zaakceptowanego main `78d853a6`
nowy head `a93893b8` miał identyczne drzewo plików i pełny odbiór
`37707162141`, 17/17 success. Chroniony merge PR #43 opublikował `0990905b`;
jego odbiór `37711535728` przeszedł 17/17 przed ręcznym dispatch.
Te wcześniejsze warunki publikacji są spełnione. Terminalny wynik próby 1.3
jest opisany powyżej i w 09-32. Pozostaje potrzeba korekty diagnostyki oraz
rzeczywistej kwalifikacji pełnego źródła, snapshotu i curated; zielone CI
komponentów nie zastępuje tych wyników.
