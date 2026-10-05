# AI 09.10 — czasowa kwalifikacja jakości wersji źródłowych

Przyrost implementuje [czytnik wersji](../forecast-source-versions.md) wewnątrz
jednego audytowanego source replay. Pełny eksport do fizycznych kluczy ról,
integracja z fitami i końcowy odbiór AI 09 pozostają otwarte.

Kontrolny publiczny smoke snapshot 1.0 zawiera 25 tabel / 31 171 wierszy,
w tym 1612 obserwacji i 1612 wersji. Testy czytnika rezerwują wszystkie pięć
ról przed parent I/O, odtwarzają transformację raz i kwalifikują cały
inwentarz przed pierwszym udostępnionym wierszem. Ten smoke ma po jednej
wersji na obserwację; testy kontrolowanych korekt sprawdzają również wcześniejsze
wersje, późniejszy dowód jakości i brak przenoszenia kompletności wstecz.
Nie jest to rzeczywista kwalifikacja populacji pięciu ról: ich identyfikatory
w protokole smoke pozostają deklaracjami metadata z 09.9.

Testy negatywne obejmują brak jawnych flag, obcy klucz i observation ID,
lukę lub duplikat wersji, nieznaną politykę, cofnięcie availability,
wersję przed zamknięciem dnia, różnicę bieżącej obserwacji, za długą historię,
nieprawidłowy cutoff, podmianę indeksu, iterator po wyjściu i wyjątek klienta.
Błąd zapisu zakończenia audytu zachowuje cztery failed i jedną unresolved
rezerwację oraz nie publikuje receipt.

Końcowa regresja zaliczyła **278 testów, w tym 52 nowe**, bez skips,
w 408.93 s. Lint, format, Mypy (358 plików), linki dokumentacji oraz wszystkie
kontrakty przeszły. [Receipt](09-10-forecast-source-versions.json) zachowuje
hashe logów, source freeze i wcześniejsze nieudane próby. Pierwszy test ujawnił
potrzebę dokładnego zamknięcia dnia o następnej północy UTC; osobna próba
podmiany SQLite była blokowana przez aktywny kursor i została zastąpiona
kontrolą bezpośredniej podmiany prywatnych bajtów.

Zbudowany wheel ma 1 057 943 B; 283 pliki Python są identyczne z source.
Odłączony proces importuje 46 modułów wyłącznie z instalacji, sprawdza dwa
schematy v8 i nie importuje TensorFlow/Keras. To **statyczna kontrola pakietu**,
bez source replay native/wheel i bez deklaracji identycznego wyniku tych
dwóch ścieżek. Dynamiczne instancje obu schematów zaliczyły test native.

Końcowe skany sekretów Git i directory przeszły. Pierwszy pełny skan wspólnej
historii zgłosił cztery trafienia w dwóch commitach AI 08: dwa publiczne
SHA-256 membership train/calibration w `stockout-final-selected-recipe.json`.
Ich pola sprawdzono w opublikowanym metadata; nie otwierano plików outcomes.
W naszej konfiguracji dodano wyjątek wyłącznie w regule `generic-api-key`,
z jednoczesnym dopasowaniem dokładnej ścieżki i dwóch dokładnych wartości.
Kontrole negatywne nadal wykrywają inną wartość w tym samym pliku oraz oba
hashe w innym pliku. Nie wykluczono całego pliku. Pierwsza próbka kontrolna
zawierająca słowo `Synthetic` nie była rozpoznawana przez sam detektor;
zachowano tę próbę i zastąpiono ją wcześniej potwierdzoną niecredential
próbką. Receipt zawiera oba przebiegi. Konfiguracja sesji AI 08 pozostaje
bez zmian. Po regresji nie zmieniono żadnego bajtu kodu aplikacji.

Wspólny dziennik projektu pozostał byte-identical: cztery plany, zero nowych
rezerwacji i wszystkie 64 dostępne odczyty. Testy komponentu korzystają wyłącznie
ze znanego publicznego smoke i kontrolowanych danych syntetycznych.
Nowych etykiet projektu, fitów, final test ani promocji nie wykonano.

Przed pracą odczytano stan i istniejący mechanizm prywatnego replay/cache
AI 08.23 oraz kod AI 08.13. Nie uruchamiano ich pilota, nie zmieniano worktree
ani procesów innych sesji. Podobna zasada jednego replay jest zastosowana
w obrębie źródła forecast, które ma inny grain niż stockout.

Poprzedni commit `3b1ee7e1b11069400364d0497eb402c4687e0570` ma zielone
[Required CI178](https://github.com/Oskar-Stachowski/retailops-ai-intelligence/actions/runs/37233267515).
Nowy commit wymaga własnego Required CI. Pełnego lokalnego `make ci-local`
nie zastępujemy wynikiem mniejszych testów.

Wolne miejsce spadło do około 34 GiB, poniżej rezerwy 50 GiB użytej
w poprzednim pomiarze. Osobny native/wheel source replay z pomiarem całego
drzewa procesów nie jest teraz uruchamiany. Kontrola bajtów pakietu, importów
i schematów jest osobnym dowodem; nie stanowi pomiaru RSS/scratch ani
odbioru większego profilu. Historyczny pomiar 09.9 pozostaje przypisany
do jego poprzedniego runtime.
