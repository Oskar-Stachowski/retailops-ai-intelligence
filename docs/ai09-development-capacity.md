# Pomiar pełnego development AI 09

[Przygotowywana receptura 1.11](reference/ai09-development-capacity-v1.11.json)
ma zatwierdzony przez użytkownika budżet **180 minut obliczeń** oraz **210 minut
na cały job Actions**. Limit RSS drzewa nadal wynosi **12 GiB**, scratch
8 GiB, rezerwa pamięci 1 GiB i dysku 6 GiB. Pełny profil i final portfolio
pozostają zachowane. 180 minut jest budżetem pomiaru, nie obietnicą ukończenia.

Nowe logi pokazują faktyczne etapy Source, zakończone zdarzenia, dni i wiersze
oraz oczekiwane liczby, kiedy są znane. Niezależny supervisor co 60 s wypisuje
heartbeat, upływ czasu, RAM i dolną granicę CPU; nie zwiększa liczników pracy.
Rekordy trafiają przez `flush()` do Actions oraz do prywatnego JSONL z `fsync()`.
Awaria telemetrii nie zasłania timeoutu ani kosztu nieudanego procesu.

Producent jest przypięty do [Source PR112](https://github.com/Oskar-Stachowski/retailops-cloud-native-platform/pull/112).
Mały workflow `AI09 real generation worker control`, tryb `live_progress`,
wykonuje rzeczywistą generację i osobne 130-sekundowe okno obserwacji logów.
Ten czas jest jawnie oznaczony jako oczekiwanie na obserwację, bez pracy Source.
Artefakty kontroli są przechowywane przez 90 dni; to nie jest archiwum trwałe.
Lokalne przejście testów ani końcowy log nie zastępują sprawdzenia widoczności
przyrostowych wpisów w UI podczas działania joba.

[Dowód live 09.73](evidence/09-73-actions-live-progress.json) potwierdza
kontrolę [run 37919491269](https://github.com/Oskar-Stachowski/retailops-ai-intelligence/actions/runs/37919491269)
na dokładnym AI `5914f8d2` i Source `ff2504a9`. W UI przy stanie
`currently running` były widoczne rzeczywiste liczniki tabel, operacje
walidacji i heartbeat po 60,2 s. Source skończył przed obserwacją UI;
job wciąż wykonywał jawne okno obserwacji. Końcowy screenshot powstał już
po zakończeniu i nie służy jako dowód wcześniejszej widoczności.
Zweryfikowany artefakt zawiera 391 zdarzeń Source i trzy heartbeat
w odstępach 60,08 oraz 60,20 s. Przebieg zakończył się sukcesem po 132,90 s,
w tym 130 s celowego oczekiwania; RSS drzewa wyniosło 139595776 B,
CPU workera co najmniej 2,34 s. Zachowano prywatną lokalną kopię artefaktu.
Nie wyciągamy z tej małej kontroli prognozy czasu pełnego profilu.

Source PR112 scalono jako `8479b5d3`. Dokładny head `ff2504a9` oraz
wynikowy main mają po 25 zakończonych jobs: 21 wymaganych dla tego zakresu
z sukcesem i cztery zamierzone pominięcia zgodne z polityką ścieżek Source.
Pominięcia nie są liczone jako sukces. Dowód obejmuje także `required-result`.

[Kontrola checkpointów 09.74](evidence/09-74-native-preparation-checkpoints.json)
potwierdza zapis i odtworzenie wszystkich pięciu ukończonych faz na małym,
rzeczywistym Source: 10 dni, 8 produktów, 2 sklepy, 1 magazyn, 58 tabel.
Po generacji i kwalifikacji odtworzono ich archiwa do nowego katalogu;
eksport, import i curation skorzystały z odtworzonych rodziców. Generator
uruchomiono raz. Przechwycono pomyślny powrót rzeczywistego walidatora każdej
fazy; pełne inventory danych odpowiada zachowanemu świadectwu walidacji.
Cała kontrola zajęła 8,31 s, w tym 7,34 s pracy pięciu procesów,
0,37 s zapisu archiwów i 0,39 s późniejszego odtworzenia wszystkich faz.
Koszty odtworzenia i pierwotnego przygotowania pozostają osobne.

Nowy komponent używa istniejącego formatu archiwów AI04 bez zmiany jego
kontraktu ani mechanizmu AI08. Wymaga osobno zachowanego, zaufanego powiązania
z wynikiem workera, kodem, konfiguracją, seedem, zależnościami i walidatorem.
Sam poprawny hash archiwum nie wystarcza. Eksport wiąże zarówno Source,
jak i kwalifikację; błąd dowolnego rodzica blokuje publikację całego prefiksu.
Nowe pliki mają prawa `0600`, katalogi `0700`, niezależnie od `umask`.
173 kontrole komponentów, wcześniejszego archiwum, telemetrii, capacity i CI
przeszły. Kontrolę wykonano w lokalnym środowisku testowym z zapisanymi
wersjami pakietów; nie kwalifikuje ona pełnej skali ani środowiska Actions.

Pozostały odbiór checkpointów i wznowienie między przebiegami Actions:
zaufane powiązania poza archiwami, pełna historia nieudanych prób
oraz wspólny budżet wznowienia. Kontrola komponentu nie
otwiera dostępu Project ani final i nie zamyka tych wymagań.

Nowy `run_ai09_preparation.py` rozdziela każdą fazę na dwa procesy pod
nadzorem: natywne przygotowanie i zapis zweryfikowanego checkpointu.
Oba zużywają wspólny budżet obliczeń; rezerwy RAM/dysku obejmują także
kompresję. Dziennik zapisuje i synchronizuje zamiar przed startem procesu,
a następnie rzeczywisty pomiar, także przy porażce. Brak końcowego pomiaru
oznacza nieznany koszt i blokuje dalsze uruchomienia z tej sesji.
Blokada pliku nie pozwala uruchomić drugiego kontrolera równocześnie.
Inspekcja środowiska ma osobny pomiar preflight, poza budżetem przygotowania,
ale wewnątrz limitu całego joba. Wersje pakietów i ich metadata RECORD są
przypięte; nie jest to ponowny pełny audyt wszystkich zainstalowanych plików.

Workflow przekazuje każdą zakończoną lub nieudaną fazę do osobnego artefaktu
przed przejściem dalej, zachowując checkpoint, niezależne powiązanie workera,
dziennik, logi i koszty. Retencja wynosi 90 dni i wymaga późniejszego odbioru
do trwałego archiwum. [Dowód 09.76](evidence/09-76-phased-preparation-controller.json)
obejmuje 197 zaliczonych kontroli oraz mały rzeczywisty przebieg pięciu faz
nowego kontrolera na `a9f18aa`. Dziesięć osobno nadzorowanych procesów
pozostawiło 20 trwałych rekordów. Naliczono 10,22 s obliczeń,
osobno 0,38 s preflight; próbki RSS drzewa nie przekroczyły 132300800 B.
Wszystkie pięć checkpointów odtworzono bez ponownej generacji.
Późniejszą blokadę ścieżek względnych i symlinków sprawdzają dwie kontrole
przed jakimkolwiek dostępem do Source; kod wykonania faz pozostał ten sam.
Zdalny upload, transport i wznowienie wymagają rzeczywistej kontroli Actions.
Publiczne wejścia kontrolera oraz workera respektują
`dispatch_enabled=false` przed rozpoczęciem jakiejkolwiek pracy Source.

Tryb `checkpoint_resume` małego workflow wykonuje wyłącznie zamrożony profil
10 dni × 8 produktów × 2 sklepy × 1 magazyn. Zatrzymuje się po kwalifikacji,
wysyła checkpointy do artefaktu, a następnie pobiera wybrany artefakt przez
API GitHub. Sprawdza identyfikator, run, dokładny commit, nazwę, hash ZIP,
pełną zawartość i niezależnie zachowane zakończenie natywnych faz.
Używa istniejącego transportu HTTP AI04, który usuwa autoryzację przy
przejściu na host storage; kontrakty AI04 i AI08 pozostają bez zmian.
Koszt pobrania i odtworzenia pomniejsza pozostały budżet przygotowania.
Wznowione eksport/import/curation korzystają z odtworzonych danych po
przeniesieniu oryginałów do osobnego zachowanego katalogu kontrolnego.
Pierwotne aktywne ścieżki nie mogą więc przypadkowo obsłużyć wznowienia.

Ten mechanizm przyjmuje celowo zatrzymany, w pełni zmierzony prefiks.
Nie pozwala zastąpić historii nieudanej lub przerwanej próby wcześniejszym
udanym checkpointem. Rozliczenie takiej historii pozostaje wymaganiem
przed wznowieniem pełnej diagnostyki. [Mała kontrola Actions 09.77](evidence/09-77-actions-checkpoint-resume.json)
ukończyła 5/5 faz z pojedynczą generacją. Zweryfikowano oba artefakty GitHub,
wszystkie archiwa faz i zachowanie wcześniejszych ośmiu rekordów kosztów.
Łączny koszt wyniósł 17,14 s, w tym 1,26 s pobrania i odtworzenia.
Kontrola wykonała transport przez GitHub w obrębie jednego joba; wznowienie
z innego przebiegu pozostaje do sprawdzenia. Osobne 130,09 s okno obserwacji
pozostawiło heartbeat po 60,11 i 120,22 s, ale nie obejrzano wtedy logów
nowego kontrolera w UI. Ta bramka pozostaje otwarta; nie powtarzamy
niezmienionego przebiegu tylko z powodu utraconego okna obserwacji.

Poprawka kolejnego wznowienia zachowuje też wcześniejsze archiwa faz.
Poprzednia implementacja przenosiła wyniki i koszty, lecz nie archiwa, więc
kolejny `bundle_prefix` nie miał pełnej zawartości. Kopiowanie, weryfikacja
natywnego powiązania i fsync odbywają się teraz wewnątrz nadzorowanego procesu
odtwarzania i zużywają jego budżet. Kontroler przenosi gotowy prywatny katalog
atomowo, bez drugiego niezmierzonego skanowania payloadów. Sprawdza tożsamość
plików przy przekazaniu własnego workera; późniejszy bundle ponownie sprawdza
pełne hashe i powiązania. Oryginalne archiwa pozostają zachowane.

[Dowód 09.79](evidence/09-79-actions-repeated-checkpoint-resume.json) obejmuje
dwa kolejne rzeczywiste uploady i pobrania przez GitHub na `8f63b56`.
Po drugim odtworzeniu kontrola ukończyła 5/5 faz, używając drugiego zestawu
odtworzonych danych. Generator uruchomiono raz. Trzy artefakty, oba prefiksy,
pięć archiwów faz, osiem pierwotnych i 20 końcowych rekordów zweryfikowano
i zachowano lokalnie. Pierwsze 6,26 s pracy pozostało w budżecie; oba wznowienia
dodały łącznie 2,49 s, a pełny naliczony koszt wyniósł 18,80 s.
224 kontrole komponentów przeszły. Nadal jest to wznowienie w jednym jobie;
nie kwalifikuje innego przebiegu ani historii awarii. Heartbeat w końcowym
artefakcie nie jest dowodem obserwacji w UI podczas działania kontrolera.

Kontrole komponentów obejmują dwa wznowienia, sumę wcześniejszych kosztów
oraz odmowę przy zmianie, braku i symlinku archiwum. Wynik Actions odebrano
i sprawdzono; rozliczenie nieudanych prób oraz wznowienie pomiędzy
oddzielnymi przebiegami Actions pozostają otwarte.

[Komponent rozliczenia prób 09.80](evidence/09-80-terminal-attempt-settlement.json)
dodaje do wznowienia pełny końcowy zapis nieudanej lub przerwanej próby.
Zachowuje wcześniejsze rekordy, identyczne wejścia i koszty oraz cały zapis
pracy po ostatnim ukończonym checkpointcie. Znany koszt porażki jest doliczany
przed nową pracą. Dla niedokończonej operacji rezerwuje dodatkowo czas całego
zakończonego joba GitHub z dwusekundowym marginesem rozdzielczości timestampów.
To celowo ostrożna rezerwa; nie zastępuje jej fikcyjnym pomiarem ani zerem.
Pola `unmeasured_wall_cost_present`, `reserved_unknown_wall_seconds` oraz
`unsettled_wall_cost_present` rozróżniają nieznany pomiar i rozliczony budżet.
Nieznane CPU i rezerwa przetrwają kolejne wznowienia. Wyczerpany budżet blokuje
publikację sesji, a brak pełnego zapisu końcowej próby blokuje jej wznowienie.

Czytnik używa stałych endpointów GitHub dla wskazanego runu i joba oraz
wcześniejszego transportu artefaktów AI04. Sprawdza końcowy status, nazwę joba,
workflow, repozytorium, commit i najnowszy numer próby. Nazwa i hash artefaktu
wiążą końcowy zapis z tą próbą; run jest ponownie sprawdzany po pobraniu.
Checkpoint z innego runu nie może być wznowiony bez końcowej historii;
identyfikator runu w tej historii musi odpowiadać pobranemu checkpointowi.
Sam dostarczony JSON z hashem nie stanowi upoważnienia: dowód musi pochodzić
z tego czytnika i uwierzytelnionego serwera GitHub. Funkcja nie dispatchuje
ani nie ponawia jobów. 283 kontroli obejmuje wszystkie cztery granice
prefiksu, błędy natywnego procesu i archiwizacji, przerwania, późniejsze
wznowienie oraz odrzucenie zmienionej lub niepełnej historii.
API w tych kontrolach jest zastąpione kontrolowanymi odpowiedziami;
publikacja snapshotu po zakończeniu pracy i rzeczywisty test między dwoma
jobami pozostają otwarte. Całe pobranie i walidacja muszą być objęte nadzorem
zasobów w kontrolerze wznowienia. Nie jest to jeszcze odbiór tej ścieżki Actions.

**1.11 nie została uruchomiona i nie pozwala jeszcze uruchomić pełnej próby**:
`dispatch_enabled=false` blokuje supervisor i bezpośrednie wejście workera.
Pozostały odbiór chronionych head/main konsumenta, powiązanie dowodu live
z końcowym kodem, integracja checkpointów/wznowienia w Actions
i reprezentatywna ścieżka dane→raport.
Kolejny spójny przyrost musi dostarczyć te dowody przed otwarciem bramki.
Zachowano wszystkie wcześniejsze receptury i dziewięć rzeczywistych porażek.

Nieuruchomiona [receptura 1.10](reference/ai09-development-capacity-v1.10.json) przypina
audyt CPU z [Source PR111](https://github.com/Oskar-Stachowski/retailops-cloud-native-platform/pull/111):
ograniczony cache konwersji Arrow oraz istniejący indeks ledger uruchamiany
dopiero po pełnej walidacji. Trzy małe pary zachowują 58 tabel, kontekst, CSV
i raporty; mediana całego CPU spadła o 3,46%. To nie jest pomiar pełnego profilu.
[Przygotowanie 09.65](evidence/09-65-source-cpu-capacity-preparation.json)
zachowuje dziewięć rzeczywistych porażek, wszystkie wcześniejsze receptury
i nieuruchomioną 1.6. Limit nadal wynosi **12 GiB** z rezerwą **1 GiB**;
start wymaga co najmniej **13 GiB dostępnej pamięci**. Pełny profil,
pięć faz, limit 3600 s, scratch 8 GiB i pozostałe bramki pozostają zachowane.
Kontrole receptury zaliczyły 62 testy. Diagnostyki 1.10 jeszcze nie uruchomiono:
wymaga pełnego odbioru dokładnego head oraz wynikowego main obu repozytoriów.

[Wynik dziewiątej próby 09.62](evidence/09-62-development-capacity-ninth-run.json)
wiąże zweryfikowany artefakt [run 37853501527](https://github.com/Oskar-Stachowski/retailops-ai-intelligence/actions/runs/37853501527).
Receptura 1.9 zakończyła się `wall_limit`: **0/5 faz, 3600.53 s**,
próbkowane RSS drzewa **9061416960 B**, dolna granica CPU workera **3599.51 s**.
Pełny profil, limit 12 GiB, wszystkie bramki i dziewięć rzeczywistych porażek
pozostają zachowane. Końcowa próbka stosu wskazuje niezależną rekonsyliację
movementów podczas budowania raportu Source; nie dowodzi dominującego kosztu.
Nie wykonano retry, fitów Project ani odczytu świeżego final. Pełna pojemność
oraz kwalifikacja modeli pozostają niepotwierdzone.

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

Poniżej zachowano odbiór audytu i wynik receptury 1.7 z limitem 8 GiB.

[Receptura 1.7](reference/ai09-development-capacity-v1.7.json) przypina Source
`4dacf040` z [PR109](https://github.com/Oskar-Stachowski/retailops-cloud-native-platform/pull/109).
[Dowód przygotowania 09.49](evidence/09-49-projection-retention-capacity-preparation.json)
obejmuje audyt pięciu faz oraz dodatkowe zwalnianie pierwotnych danych sprzedaży
po rekonsyliacji i przed projekcją. Cztery prywatne tabele pozostają w wyniku.
Trzy native kontrole czasu życia, 67 regresji Source, 95 kontroli AI i 41 z paczki
przeszły. Mała para nie zastępuje pełnego pomiaru zasobów.

Zachowano dokładne bajty receptur 1.0–1.6. 1.6 **nie została uruchomiona**;
`previous_preparation` wiąże ją z receiptem 09.48, a `previous_attempt` nadal
wskazuje rzeczywistą szóstą porażkę 1.5. Kontrole odrzucają zmianę tych faktów,
profilu, limitów, rezerw, producenta i jego zależności. Pełne wymiary, daty,
seedy, locki i obserwator stosów pozostają bez zmian względem przygotowania 1.6.

Pin zależności producenta `ea389b45` pochodzi z wcześniejszej aktualizacji Source
`4805834`. [Pierwotny dowód 09.48](evidence/09-48-audited-capacity-preparation.json)
zachowuje pełny diff względem starszego `f55452e6`. To pomiar nowej implementacji
i środowiska; nie przypisujemy całej zmiany kosztu wyłącznie kodowi.

**1.7 została uruchomiona raz**, jako [run 37807749014](https://github.com/Oskar-Stachowski/retailops-ai-intelligence/actions/runs/37807749014),
po pełnym CI dokładnych head, standardowych scaleniach i pełnym CI wynikowych
main obu repozytoriów. [Dowód 09.50](evidence/09-50-audit-acceptance-capacity-seventh-start.json)
wiąże Source `4dacf040`, AI `89d6c8f2`, niezmienną referencję i dokładne bajty
receptur, skryptu, workflow oraz locków. Zachowano pełny profil 365 × 100 × 5 × 3,
limity i sześć wcześniejszych porażek.
[Wynik siódmej próby 09.51](evidence/09-51-development-capacity-seventh-run.json) wiąże odebrany i zweryfikowany artefakt [run 37807749014](https://github.com/Oskar-Stachowski/retailops-ai-intelligence/actions/runs/37807749014).
Pomiar zakończył się `tree_rss_limit`, z 0/5 ukończonych faz. Cały przebieg trwał 1702.79 s;
próbkowany peak drzewa wyniósł 8635432960 B, a dolna granica CPU workera 1701.93 s.
Pełna pojemność pozostaje niepotwierdzona. Zachowano koszt porażki; nie wykonano automatycznego retry.
Pełny profil 365 × 100 × 5 × 3, limity, rezerwy i wcześniejsze koszty zachowano.
Nowe projektowe fity i odczyty świeżego final wynoszą zero; AI 09 pozostaje `not_ready`.

| Faza | Wynik | Czas (s) | RSS drzewa (B) | CPU dolna granica (s) | Scratch zaalokowany (B) |
|---|---|---:|---:|---:|---:|
| generation | tree_rss_limit | 1702.62 | 8635432960 | 1701.93 | 49152 |

Próbkowane RSS nie jest ciągłym maksimum. CPU nie obejmuje supervisora ani
niezaobserwowanych dzieci. Nieukończonej próby nie porównuje się jako kosztu
ukończonego profilu. Lokalizacje ramek nie dowodzą przyczyny alokacji.

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
parametry oraz ograniczenia obserwacji stosów. Wymagany po tej porażce audyt
pięciu faz i odbiór bezpiecznych optymalizacji zakończono przed próbą 1.7.
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

Ostatni z 14 ograniczonych zapisów stosu obejmuje `InventoryLedger.from_payload`
podczas niezależnej `reconcile_simulation` / `reconcile_source_commerce`.
To lokalizacja wykonywania, nie dowód właściciela alokacji ani dokładny stos chwili
zatrzymania. Przed kolejnym canonical trzeba zmierzyć tę część na odsłoniętych
kontrolach, zachować wszystkie walidatory i odebrać ewentualne poprawki oraz pełne CI.
