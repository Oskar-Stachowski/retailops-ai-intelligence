# Końcowa kontrola plików Source

Czytniki publicznych danych anomaly i stockout sprawdzały przy zamykaniu
te same cztery zbiory dwukrotnie: oryginalny snapshot i curated oraz ich
prywatne kopie. Pierwszy przebieg wykonywał czytnik, a drugi właściciel
Source podczas zamykania swojego kontekstu. Kontrola pomiarowa przed zmianą
potwierdziła osiem pełnych sprawdzeń w każdym z sześciu przypadków:
dwa czytniki razy trzy istniejące wersje źródła kontrolnego.

Czytnik pozostawia końcową kontrolę właścicielowi Source, który zamyka się
jako ostatni. Odbiór nadal staje się dostępny dopiero po pomyślnym zamknięciu
całego kontekstu. Pozostają pełne hashe oryginałów i kopii, sprawdzenie
metadanych i runtime oraz kontrole prywatnych indeksów i pełnego odczytu.
Nie zmieniono kontroli wykonywanych wewnątrz pracy na pożyczonym kontekście.

Nowa regresja sprawdza liczbę rzeczywistych końcowych odczytów oraz uszkodzenie
każdego z czterech zbiorów po zakończeniu konsumpcji. Każde uszkodzenie musi
odrzucić odbiór, a pliki robocze muszą zostać posprzątane. Kontrole obejmują
oba czytniki i istniejące źródła inventory, demand i physical.

Zmiana usuwa jeden zbędny przebieg w końcowej walidacji. Nie oznacza to
dwukrotnego przyspieszenia całego przygotowania danych; ten koszt wymaga
pomiaru pełnego profilu. [09.121](evidence/09-121-parent-final-seals.json)
potwierdza **481 testów kodu i te same 481 testów z zainstalowanej paczki**,
bez pominięć, oraz Mypy dla **808 plików**. Wszystkie **675 modułów i 55
schematów** mają zgodne bajty w paczce i instalacji. Wszystkie 129 modułów
zamkniętego AI 08 i wcześniejsze kontrakty pozostają niezmienione.
Pełny CI dokładnego PR i wynikowego main pozostaje wymagany.
Canonical nadal wyłączony, limit **12 GiB**, rezerwa **1 GiB**; AI 09
pozostaje **`not_ready`**.
