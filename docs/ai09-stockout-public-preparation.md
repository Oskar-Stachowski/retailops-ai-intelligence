# Pełne przygotowanie publicznych danych stockout

`prepare_public_stockout` łączy [pełny odczyt cech](ai09-stockout-public-parent.md)
z retrospektywną prognozą upstream AI 08. Obejmuje wszystkie fizyczne obserwacje
produkt–miejsce zapasu–czas migawki. Zachowuje również brak prognozy i punkty
niekwalifikujące się do treningu. Nie dobiera próby ani korzystniejszych dat.

Każdy wynik używa faktów znanych w chwili obserwacji. Wspólny indeks na dysku
zachowuje wszystkie wersje, a istniejące funkcje AI 08 sprawdzają niejednoznaczne
wersje i trasy także dla innych produktów. Obliczenia przechodzą przez obserwacje
w kolejności dni: wspólna kontrola źródła wykonuje się raz dla danej daty.
Bufor dekodowania ma istniejące limity 1024 rekordów i 1 MiB. Nie trenuje się
tu globalnego modelu, którego późniejsza wiedza mogłaby trafić do wcześniejszych
obserwacji.

Artefakt zawiera kanoniczny plik JSONL oraz manifest z pochodzeniem, pełną
liczbą kluczy i hashami. Tymczasowe indeksy przechowują zwięzłe cechy i wyniki
na dysku. Wspólny budżet obejmuje indeks cech, indeks upstream i indeks wyników;
osobne limity obejmują cały artefakt oraz pojedynczy rekord. Publikacja następuje
atomowo po zakończeniu wszystkich kontroli źródeł. Przekroczenie limitu,
niepełna populacja, zmiana rodzica lub istniejący cel kończą operację odmową.

`verify_public_stockout` wymaga wcześniej znanego identyfikatora zbioru i planu.
Czyta cały plik, sprawdza typy, kanoniczną reprezentację, unikalność i kolejność
kluczy, liczby wierszy, rozmiary i hashe oraz stabilność plików podczas odczytu.
Ponowne zapieczętowanie zmienionych danych nie odpowiada oryginalnej tożsamości.
Ta kontrola artefaktu nie zastępuje rezerwacji operacji ani pełnej weryfikacji
rodzica przez nadrzędnego wykonawcę kampanii.

Dwa nowe kontrakty v44 opisują plan i wynikowy wiersz. Wcześniejsze kontrakty
oraz moduły AI 08 pozostają niezmienione. Testy porównują wszystkie obserwacje
istniejących fixtures Source 1.1, demand 1.2 i physical 1.2 z natywnymi cechami,
kategoriami i prognozami AI 08. Obejmują również uszkodzenia artefaktów, zmianę
indeksu, przekroczenie limitu oraz ochronę istniejącego celu.
**93 testy źródeł przeszły w 512,13 s**, bez pominięć. Mypy sprawdził 795
plików. [09.109](evidence/09-109-stockout-public-upstream.json) zachowuje pomiary
i zakres kontroli. 662 moduły i 39 kontraktów mają zgodne bajty w paczce
i instalacji. Te same **93 testy z paczki przeszły w 478.95 s**,
bez pominięć. Odbiór dokładnego PR i wynikowego main pozostaje wymagany.

Prywatne etykiety, pełna dojrzała populacja ról, rezerwacje treningu i kalibracji,
wybór oryginalnego modelu, progi i niezależna ocena pozostają do podłączenia.
Starszy odczyt kwalifikacji ma limit całego JSON 4 MiB, starsza selekcja limit
10 000 wpisów, a tworzenie wybranej propozycji ponownie uruchamia selekcję.
Nowe adaptery muszą zachować pełną populację i chronologię oraz zapobiec
niezaplanowanym dodatkowym treningom. Nie należy rozszerzać zamkniętych
kontraktów AI 08 ani osłabiać ich kontroli.

Ten przyrost nie odczytuje prywatnych etykiet i nie wykonuje treningu ani final
test. Nie dowodzi jakości modeli ani `ready` AI 09. Canonical pozostaje
nieuruchomiony, z limitem **12 GiB** i rezerwą systemową **1 GiB**.
