# AI 09 — optymalizacja eksportu bez zmiany danych

Zmiana usuwa powtarzaną pracę w trzech miejscach: schematy dekodowania są
odczytywane raz na tabelę i origin oraz raz na instancję indeksu wersji;
zbiór dozwolonych originów powstaje raz na weryfikację; walidacja canonical
JSON obsługuje zwykłe skalary i kontenery bez powtarzanych kontroli abstrakcyjnego
typu Mapping. Subklasy i niestandardowe Mapping zachowują wcześniejszą ścieżkę.
Każdy element kontenera i wszystkie liczby float nadal podlegają walidacji.

Wyniki pomiarów i porównania danych zawiera [receipt](ai09-performance.json).
Pełny przebieg nie potwierdził przyspieszenia: **238.83 → 246.89 s (+3.37%)**.
Próbkowany szczyt RSS drzewa procesów wyniósł **172.92 → 163.20 MiB**.
Zmienione funkcje mają niższy koszt CPU w naprzemiennym mikrobenchmarku:
`_finite` 1.83×, a `canonical_bytes` 1.36× (26.5% mniej czasu CPU).
Nie przenosimy tego wyniku na cały eksporter ani czas zakończenia AI09.
Pomiar obejmuje otwarcie i pełną weryfikację prywatnej kopii źródła, budowę
eksportu z jego wewnętrznymi kontrolami, zamknięcie źródła oraz dodatkową
niezależną weryfikację wynikowego eksportu.

## Metoda

Baseline pochodzi z `9bb10f084e465131b0d4f06c5dea452a37fb2481`. Oba przebiegi
używają tych samych istniejących danych diagnostycznych z
[09.13](09-13-physical-forecast-export.md): Source 1.1, ai-load 173/4/2/2,
seed 42, 29285 wierszy źródła i 14560 pełnych kluczy wynikowych.
Nie wykonano nowej generacji, fitu, inicjalizacji dziennika kampanii ani odczytu
końcowego testu. Dane wejściowe czytano bez zmian; kopie robocze i wyniki mają
oddzielne katalogi. Stare receipty i piny pozostają zachowane.

Przebiegi były sekwencyjne, z limitem jednego wątku bibliotek obliczeniowych
i `nice(10)`. Monitor sumował RSS procesu roboczego i jego potomków co 0.1 s.
Guard ograniczał to drzewo do 384 MiB, wymagał 1 GiB dostępnej pamięci systemowej,
50 GiB wolnego dysku przed startem i czasu poniżej 30 minut. Sąsiednich procesów
nie zatrzymywano. Cięższe testy naszej sesji i AI09 zakończyły się przed pomiarem po.
Pomiar RSS jest próbkowany, więc nie gwarantuje uchwycenia każdego krótkiego piku.

Osobny baseline z cProfile wskazał 94218 wywołań `columns_for` (41.96 s cumulative)
i 154437890 wywołań `_finite` (185.39 s cumulative). Te czasy są zagnieżdżone
i obciążone profilerem; nie należy ich dodawać ani traktować jako wall oszczędności.
Kontrole indeksu SQLite zajęły łącznie mniej niż sekundę. Nie zmieniono ich,
trwałości SQLite, pełnych weryfikacji wejść, budżetów ani reguł kwalifikacji.

Mikrobenchmark używał 16 rzeczywistych dokumentów features i 16 history ze
zweryfikowanego baseline. Pięć par pomiarów wykonywało oba warianty w jednym
procesie w naprzemiennej kolejności; porównano mediany `process_time()`.
Byte parity sprawdzono również na tym korpusie. Nie mierzy to narzutu I/O,
pozostałych walidacji ani całej populacji. Pełny przebieg miał różne koszty
otwarcia źródła i końcowej weryfikacji; pojedyncza para nie ustala przyczyny
tej zmienności. Nie odrzucano wolniejszego wyniku ani nie powtarzano pomiaru
całego eksportu w poszukiwaniu lepszego wyniku.

## Zgodność i odbiór

Porównanie obejmuje rzeczywiste SHA-256 sześciu plików JSONL, pełne liczniki,
populacje kluczy, eligibility, powody wyłączenia, inventory wersji oraz fizyczne
i logiczne tabele cech/historii. Nowy kod ma jawnie nowy runtime i identyfikatory
artefaktów zależne od implementacji; zgodność danych nie oznacza zgodności tych ID.

Nowe testy porównują canonical bytes i typ/treść błędów z zamrożoną wcześniejszą
implementacją. Obejmują Unicode, signed zero, liczby skończone i NaN/±inf na
różnych głębokościach, subklasy, niestandardowy Mapping i kolejność błędów kluczy.
Osobne testy odrzucają rzeczywiste wiersze features i history spoza poprawnie
przeliczonego, skróconego kalendarza. Szersza regresja obejmuje kontrakty, import
snapshot, curated, features, storage, source versions/replay i fizyczny eksporter.
**455 testów przeszło w 862.66 s**. Pełne ruff/format (1013 plików), mypy
(607 plików źródłowych) i kontrola dokumentacji przeszły. Wyniki kontroli
są zapisane w receipt.

Zmiany uzgodniono z sesją AI09 i przekazano jako oddzielne commity lokalnych
optymalizacji oraz wspólnej canonicalizacji. Pełne Required CI ma objąć końcowy
zintegrowany commit; lokalny benchmark go nie zastępuje.

To pojedyncza para pomiarów kontrolnego profilu, przy działających sąsiednich
sesjach. Nie ustala kosztu pełnego ai-training ani skrócenia całego AI09.
Wszystkie flagi kwalifikacji kampanii, jakości, promocji i gotowości etapu
pozostają false. Prywatne logi, profil i skrypt pomiarowy zachowano w
`.local/performance/` katalogu roboczego tej optymalizacji; receipt podaje ich skróty.
