# AI 09.7 — odbiór trwałego dziennika dostępu do wyników

[Kontrakt i polecenia](../outcome-access-journal.md) opisują nowy dziennik.
[Wersjonowany receipt](09-07-outcome-access-journal.json) wiąże wejścia,
implementację, wheel, testy i pomiary. Zakres: mechanika odczytu forecast
development, częściowa historia i kontrolne etykiety; cały AI 09 **not_ready**.

## Dane i zachowanie

Import zachował 11 istniejących uruchomień i 10 protokołów. Zweryfikowano
14 oryginalnych plików metadanych: deklarację, trial ledger, preparation
receipt oraz 11 plików `protocol.json`. Ich sumy przed/po są identyczne.
Ścieżki do danych w metadanych nie były używane jako wejścia czytnika.
Development holdout pozostaje jawnie wcześniej otwarty; niewymienione dane
nie otrzymują statusu świeżych.

Właściwy dziennik `/private/tmp/ai09-development-outcome-journal` zawiera
tę historię, zero nowych planów i zero nowych rezerwacji. Budżet nowych
odczytów wynosi 64. Nie wykonano nowych fitów ani odczytów target labels
projektu. Nie otwarto portfolio final testu.

Osobny fixture ma dwie kontrolne etykiety. Native zapisuje trzy rezerwacje:
jedną zakończoną, jedną nieudaną i jedną nierozliczoną. Wheel ładuje dokładnie
ten zapis, wykonuje czwarty kontrolny odczyt i blokuje piąty przed czytnikiem.
Końcowy stan to **4 reserved / 2 completed / 1 failed / 1 unresolved**.
Przy blokadzie piątej próby plik dziennika pozostaje identyczny.

Niezależna ocena jest odrzucana także z nieistniejącą ścieżką dziennika,
przed jakimkolwiek odczytem. Wszystkie zgody na ocenę niezależną, final test
i promocję pozostają false. Preflight zwraca 3.

## Pakiet i zasoby

Wheel uruchomiono poza checkoutem, z osobnej instalacji. Wszystkie załadowane
moduły konsumenta pochodzą z instalacji; **274 pliki Python** mają identyczne
sumy jak source. Wszystkie cztery nowe schematy v5 są w wheel i walidują
rzeczywiste policy, plan, binding i dziennik. TF i Keras nie zostały zaimportowane.

| Przebieg świeżego procesu | Czas samej operacji po importach | Peak RSS procesu |
|---|---:|---:|
| Native: freeze, plan, odczyty, preflight | 0.28 s | 77.34 MiB |
| Wheel: odtworzenie, odczyt, blokady i schematy | 0.31 s | 78.78 MiB |

RSS to `ru_maxrss` pojedynczego procesu, obejmujący również jego importy.
Czas tabeli nie obejmuje importów; całe polecenia trwały odpowiednio około
0.66 i 0.73 s. Native i wheel wykonują różne etapy tej samej próby, więc
tabela nie jest porównaniem szybkości dwóch implementacji. Końcowe pliki
kontrolnej próby mają 210 729 B; to rozmiar końcowy, nie zmierzony peak scratch.
Nie jest to kwalifikacja większego datasetu ani kosztu całego treningu.

## Kontrole

Regresja ukierunkowana: **123/123**, w tym **46 nowych** testów dziennika.
Obejmuje zapis przed rzeczywistym kontrolnym odczytem, każdy replay, błędy,
KeyboardInterrupt, SIGKILL, równoczesne procesy, atomic rename i fsync,
uszkodzone/resealed zdarzenia, sumy historii, prywatne pliki i symlinki,
zmiany bindingów, limity globalne, odtworzenie po zmianie kodu czytnika
oraz blokadę niezależnej oceny. Powiązanie wszystkich pięciu ról z manifestem
korzysta z pełnych kluczy bez etykiet.

Pełny lokalny `make ci-local` ma **exit 0**: **1960/1960 testów głównych**
w 2460.56 s oraz **3/3 rzeczywistych testów TensorFlow CPU** w 78.34 s,
bez pominięć. Lint, format, mypy, wszystkie bramki kontraktów i danych,
pakiet, Compose config oraz oba skany sekretów przeszły. Cały przebieg
trwał 2768.27 s (46 min 8 s), z priorytetem nice 15 i jednym wątkiem
numerycznym. Freeze 798 plików źródła/testów/kontraktów nie zmienił się
podczas przebiegu. Nowy commit wymaga własnego zdalnego Required CI.
Wcześniejszy własny pełny przebieg zatrzymano po 170.17 s przed zmianą
rozdzielającą kod audytora od runtime kolejnych planów. Zachowano jego
exit -15 i log; nie jest dowodem zaliczenia regresji końcowego kodu.

## Zależności i pozostały zakres

AI 07 odczytano na `8581230`, AI 08 na `92b8d5f`. AI 08.12 już ogranicza
selekcję prognoz do fizycznej serii; nie powielono jego storage ani czytników.
Nowy dziennik dotyczy forecast development i nie kwalifikuje anomalii lub
stockout. Innych worktree, branchy, działających usług i sesji nie zmieniano.

Pozostają fizyczny czytnik etykiet po rolach, dojrzałość/eligibility,
pięciorolowy trening z rejestrem budżetu, rzeczywiście świeże dane oceny,
kalibracja, większa skala, scenariusze i końcowy odbiór trzech zastosowań.
Zmiana audytora lub środowiska wymaga migracji zachowującej dotychczasowe
zdarzenia. Historia jest częściowa; brak wpisu nigdy nie stanowi zgody na
uznanie danych za nietknięty holdout.
Przyszły kompletny audyt musi uwzględnić również odczyty raw/curated i
historii cech, jeżeli ujawniają te same obserwacje co etykiety.
