# AI 09 — trwały dziennik dostępu do wyników

Przyrost 09.7 zapisuje rezerwację **przed odczytem etykiet**, również przed
ich weryfikacją lub ponownym odtworzeniem. [Odbiór](evidence/09-07-outcome-access-journal.md)
obejmuje prawdziwe zapisy na dysku, równoczesne procesy, SIGKILL i odłączony
wheel. Cały AI 09 pozostaje **not_ready**.

## Co jest przypięte i zachowane

Dziennik korzysta z deklaracji historycznej przygotowanej w 09.6.
Sprawdza oryginalny ledger, receipt przygotowania oraz wszystkie pliki
`protocol.json`, bez przechodzenia do ścieżek datasetów znajdujących się
w tych dokumentach. Zachowuje 11 uruchomień, 10 protokołów, ich źródła,
snapshoty, seedy, foldy, sumy kontrolne oraz przerwane próby.

Dotychczasowy development holdout był już otwarty podczas weryfikacji
rodzica. Deklaracja retrospektywna nie jest dawnym zapisem przed odczytem
ani pełnym audytem wszystkich sesji. Dane niewymienione w historii mają
świeżość **unknown**, a nie automatycznie `unseen`.

Policy przypina kanoniczną ścieżkę dziennika, dokładne bajty deklaracji
historycznej, budżety, początkowy runtime oraz hash 16 jawnie wymienionych
plików audytora i jego kontraktów. Każdy nowy plan odczytu osobno przypina
**cały aktualny runtime**: wszystkie moduły Python, lock i wersję Pythona.
Dodanie nowego czytnika wymaga nowego planu, zachowując ten sam dziennik
i zużyte limity. Zmiana audytora, locka lub wersji Pythona blokuje nowe
rezerwacje; wymaga jawnej migracji zachowującej całą historię.

Binding zawiera źródło, snapshot, curated, features, seed danych, podział,
rolę, zakres origin, cutoff etykiet, hash całego zbioru kluczy i artefaktu
wyników. Osobno zapisuje seed inicjalizacji, cel odczytu, recipe, kandydata,
kalibrator i thresholdy. Wartości nieobecne są `null`; ich późniejsze
dodanie zmienia binding i wymaga uprzednio zarejestrowanego planu.

`forecast_population` przenosi te pola z poprawnego manifestu pięciu ról
bez odczytu etykiet. Nadal potrzebujemy osobnego czytnika potwierdzającego
fizyczne dane, dojrzałość, eligibility i zgodność pełnej populacji.

## Rezerwacja i błędy

Plan jest publikowany przed jego pierwszą rezerwacją. Role są związane
z celami: preprocessing/model fit z train, early stopping z jego rolą,
wybór recipe z tune, calibrator fit z calibration. Kalibracja wymaga
przypiętego dopasowanego kandydata. `verification` może dotyczyć każdej
z pięciu ról i również zużywa rezerwację. `purged` oraz portfolio final
test nie są obsługiwanymi rolami odczytu.

Każdy odczyt, również identyczny replay, dostaje nowe ID. Zakończenie tego
samego ID jest idempotentne i niezmienne. Błąd, KeyboardInterrupt, SIGKILL
lub brak końcowego zapisu nie zwracają budżetu; dane pozostają potencjalnie
ujawnione. Terminalny zapis nie zawiera treści wyjątków ani etykiet.

Domyślne limity to 16 planów, 64 nowe odczyty i 4 odczyty jednego bindingu.
Górne granice kontraktu wynoszą odpowiednio 32, 128 i 16; plik dziennika
ma limit 4 MiB. Zmiana protokołu nie resetuje limitu identycznego bindingu,
a zmiana recipe lub kandydata nie resetuje wspólnego limitu odczytów.
Nie da się podnieść zamrożonego budżetu przez ponowne `freeze`.

Prywatny katalog 0700, plik 0600, brak symlinków, `flock`, kanoniczny JSON,
hashowany łańcuch zdarzeń, atomowa podmiana i fsync chronią spójność
współpracujących procesów. Skopiowany katalog nie pasuje do ścieżki policy;
usunięcie pliku z istniejącego katalogu blokuje inicjalizację od zera.
To nie jest kontrola dostępu całego komputera ani ochrona przed właścicielem,
który świadomie przepisuje pliki. Ścieżkę i początkowy stan trzeba przypiąć
w przyszłym protokole treningowym.

## Polecenia

Historyczny dziennik z odbiorów 09.7–09.10 znajdował się w
`/private/tmp/ai09-development-outcome-journal`. Na 2026-10-07 ten katalog oraz
powiązane prywatne metadane nie istnieją. Ostatni wersjonowany receipt zachowuje
cztery plany i zero nowych rezerwacji projektu. Nie jest to odzyskany dziennik
ani kompletny audyt późniejszych sesji. Przed dalszymi odczytami trzeba
odtworzyć historię lub jawnie przenieść jej konserwatywne rozliczenie do nowego
protokołu, bez resetowania budżetu i bez uznania nieznanych danych za nietknięte.

Poniższe polecenia dokumentują wcześniejszy odbiór i nie są bieżącą procedurą
odtworzenia brakujących plików:

```bash
.venv/bin/python -m retailops_ai.evaluation_campaign.outcome_cli freeze \
  --journal /private/tmp/ai09-development-outcome-journal \
  --history /private/tmp/ai09-historical-outcome-access-inventory.json \
  --expected-history-sha256 2708341589f46341ee22abc6f0ae65b305e97a83ecb0ab19b85bb9c56e4de2e1

.venv/bin/python -m retailops_ai.evaluation_campaign.outcome_cli inspect \
  --journal /private/tmp/ai09-development-outcome-journal

.venv/bin/python -m retailops_ai.evaluation_campaign.outcome_cli verify-history \
  --journal /private/tmp/ai09-development-outcome-journal

.venv/bin/python -m retailops_ai.evaluation_campaign.outcome_cli preflight \
  --journal /private/tmp/ai09-development-outcome-journal
```

`freeze`, `inspect`, `verify-history`, `register-plan`, `reserve` i `finish`
zwracają 0 po poprawnym zapisie lub weryfikacji. `preflight` zwraca **3**
oraz `evaluation_status=not_ready`. Odrzucenie daje 2 i bezpieczny kod.
CLI nie przyjmuje ścieżki do etykiet i sam nie czyta datasetów.

Przyszły czytnik powinien używać `audited_access(journal, plan_sha256, binding)`.
Całe I/O oraz konsumpcja iteracji etykiet muszą odbywać się wewnątrz tego
kontekstu. Plan i binding są sprawdzane przed wejściem do jego ciała.
Nie wolno wynosić leniwej iteracji poza kontekst.

## Granica kwalifikacji

`independent_evaluation` jest odrzucane przed odczytem dziennika lub etykiet.
Żaden plan nie może nadać zgody na niezależną ocenę, final test ani promocję.
Related-data inventory zachowuje związki przez źródło, snapshot, seed oraz
artefakt wyników, niezależnie od nowej roli czy protokołu. Taki związek nie
oznacza, że każda obserwacja w nowym zakresie została faktycznie odczytana;
nie jest dowodem statystycznej niezależności.

Dotychczasowy comparator development i rejestr prób 09.5 nie zostały
przełączone na nowy dziennik i pozostają diagnostyczne. Po 09.7 wymagane są
role-scoped label reader, sprawdzenie dojrzałości i pełnych kluczy,
integracja z pięcioma rolami oraz budżetem fitów, kwalifikacja rzeczywiście
świeżych zakresów, kalibracja i niezależne wyniki. Końcowy protokół portfolio
oraz integracja AI 07/08 nadal są otwarte.

Dziennik nie przechwytuje automatycznie odczytów raw/curated ani historii
cech. Mogą one ujawniać te same obserwacje co etykiety; kompletny audyt
świeżości musi uwzględnić także takie wcześniejsze ujawnienie. Sam brak
nowego odczytu pliku etykiet nie stanowi potwierdzenia nietkniętego holdoutu.
