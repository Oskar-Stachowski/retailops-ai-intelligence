# Odczyt pełnej kwalifikacji stockout

`CampaignStockoutQualification` czyta oryginalny prywatny wynik kwalifikacji
Source związany z publicznym snapshotem. Obecne generowanie kampanii zapisuje
kwalifikację osobno, a publiczny snapshot nie zawiera jej okien. Adapter nie
odtwarza utraconych wyników i nie uruchamia ponownie generatora.

Nowy czytnik odczytuje kanoniczną tablicę JSON porcjami po 8192 bajty. Zachowuje
oryginalną reprezentację i hash całej tablicy, zamiast ładować ją jako jeden
dokument metadanych. Nowa polityka v45 dopuszcza do miliona okien, osobno
ogranicza cały odczyt i pojedynczy rekord. Dotychczasowy limit metadanych
Source **4 MiB** i kontrakty AI 08 pozostają niezmienione.

Przed odczytem potrzebne są jawna zgoda wewnętrznego wywołującego na prywatne
dane, zweryfikowany snapshot oraz zarezerwowana operacja kampanii. Sam czytnik
sprawdza dokładny zbiór trzech plików, oryginalny descriptor kwalifikacji,
tożsamość Source, schemat, pochodzenie kodu i zależności oraz hashe artefaktów.
Każde okno przechodzi walidację oryginalnego schematu, kolejności fizycznych
kluczy, czasu UTC, horyzontu siedmiu dni i dojrzałości dostępnych etykiet.
Różne zapisy tego samego czasu UTC nie tworzą dwóch różnych obserwacji.

Cały odczyt musi zgadzać się z oryginalną liczbą okien, hashem tablicy
i podsumowaniem klas, statusów oraz przyczyn niekwalifikowania. Przerwanie
strumienia, ponowne użycie, zmiana plików lub metadanych w pamięci uniemożliwia
odbiór. Odbiór jest dostępny dopiero po pełnym przejściu i pomyślnym zamknięciu
kontekstu. Zmniejszenie limitu nie daje krótszej, pozornie poprawnej populacji.

**61 testów źródeł przeszło w 1,42 s**, bez pominięć; Mypy sprawdził 793 pliki.
Kontrole wykorzystują oryginalne, wcześniej ujawnione fixtures inventory 1.1
oraz demand i physical 1.2, uprzednio sprawdzone natywną kontrolą kwalifikacji.
Osobny test strumieniuje 10 001 rekordów i plik przekraczający 4 MiB, wymuszając
małe odczyty. To kontrola mechanizmu odczytu, a nie dowód pełnej kampanii Source.
Te same **61 testów z paczki przeszło w 1.42 s**,
bez pominięć. 660 modułów i 38 kontraktów mają identyczne bajty w źródłach,
paczce i instalacji. [09.110](evidence/09-110-stockout-qualification-intake.json)
zachowuje pełny zakres i pomiary. Odbiór dokładnego PR i main pozostaje wymagany.

Następny krok to niezależne odtworzenie etykiet z pełnego ledgeru i certyfikatów
pokrycia oraz dokładne połączenie z wszystkimi publicznymi obserwacjami.
Trzeba zachować nieznane i spóźnione wyniki, rozdział ról czasowych i komplet
kwalifikujących się przykładów. Sam zgodny plik kwalifikacji nie dowodzi
odtworzenia ledgeru, pochodzenia generacji ani uprawnienia dziennika do odczytu.
Odbiór jawnie oznacza te nieukończone kontrole i `stage_ready` jako fałszywe.

Nie uruchomiono danych ani treningu Project i nie otwarto final test. Canonical
pozostaje wyłączony, z limitem **12 GiB** i rezerwą **1 GiB** dla systemu.
