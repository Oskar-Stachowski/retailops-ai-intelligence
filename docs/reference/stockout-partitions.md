# Partycje cech stockout — wersja 2.0

Nowy zapis ogranicza rozmiar pojedynczej części i koszt wyszukiwania faktów
dla fizycznej serii. Pakiet `stockout_preparation` ma własną tożsamość kodu;
nie zmienia projekcji, identyfikatorów ani czytników artefaktów v1.
To pierwszy przyrost [projektu większego profilu](stockout-profile-resources.md),
nie odbiór `ai-dev` lub `ai-training`.

## Zapis i granice

Manifest `2.0.0` wiąże źródło, snapshot, qualification, curated, policy cech,
policy partycji, kod oraz wersję Arrow. ID ma prefiks
`feature-partitions-sha256-`. Logiczna suma `points_sha256` pozostaje sumą
tej samej uporządkowanej tablicy punktów co w v1. Format zapisu ma osobne ID.

Każda część obejmuje jedną serię produkt × fizyczna lokalizacja i najwyżej
256 kolejnych origin. Trzy pliki typed Parquet przechowują:

- `points`: klucz, origin, status, dostępność, wartości liczbowe oraz referencje;
- `history`: rozłączne, zahashowane dzienne wiersze historii;
- `lineage`: rozłączne, zahashowane wiersze pochodzenia danych.

Referencje są lokalne dla części. Identyczne wiersze historii mogą być
współdzielone pomiędzy nakładającymi się oknami. Różne wersje treści mają
różne referencje; nie ma globalnego nadpisywania dawnych danych.
Schematy Arrow są jawne, również dla całkowicie pustych wartości nullable.

Łączny Parquet jednej części ma limit 16 MiB; cały Parquet bundle 64 MiB,
a manifest 4 MiB. Policy może zmniejszyć batch lub limit części, ale nie
zwiększyć tych granic. Pozostają dotychczasowe limity wejścia 64 MiB /
500 000 wierszy oraz 10 000 punktów. Zapis v1 nadal ma własny limit 16 MiB.
Partycjonowanie nie oznacza automatycznego dopuszczenia większego profilu.

## Wiedza w dniu predykcji

`FactIndex` przechowuje własną kopię skalarnych wierszy. Indeksuje product,
stock i najpóźniejszy czas wymagany do użycia wiersza: curated availability,
occurred/snapshot/ordered/received/known oraz początek business date dla
popytu. Wyszukanie używa tylko prefiksu znanego w origin i zachowuje
oryginalną kolejność wierszy. Popyt obejmuje wszystkie kanały produktu;
routing pozostaje globalny, aby późniejsza zmiana trasy nie usuwała
wcześniejszych wersji potrzebnych do rekonstrukcji.

Selekcja efektywnych dat, najnowszych znanych wersji i liczenie wszystkich
wartości pozostają w projekcji v1. Zachowane są wszystkie znane wiersze
lineage, także stare wersje i fakty sprzed okna 28 dni. Nie stosujemy cache
globalnej najnowszej wersji. Brak dostępności, przyszłe fakty i przyszły
business date pozostają wykluczone. Kontrole staleness, coverage, istniejącego
braku i niejednoznacznej wersji pozostają aktywne.

Indeks ogranicza przeglądanie obcych serii. Kumulacyjne liczenie ledgeru,
ruchome okna oraz odczyt wejścia z dysku w częściach wymagają kolejnego
przyrostu; bieżący etap nadal ładuje ograniczone tabele do pamięci.

## Odtworzenie i publikacja

Build sprawdza rodzica i ponownie pieczętuje odczytane tabele raz na przebieg.
W pamięci pozostają zweryfikowane fakty i najwyżej batch punktów. Zapis
odbywa się w prywatnym katalogu staging, pliki mają tryb 0600, katalogi 0700.
Cały bundle jest publikowany atomowo bez nadpisania istniejącego celu.
Przerwany zapis nie publikuje manifestu ani częściowego celu. Ponowienie
odtwarza całość; nie wznawia niezweryfikowanego staging. Reuse wymaga
identycznego manifestu i wszystkich plików; konflikt blokuje zapis.

Verify odtwarza fakty i każdą część, porównuje dokładne bajty wygenerowane
przez przypiętą wersję Arrow oraz cały manifest. Nie odczytuje dowolnego
niezaufanego Parquet; samo ponowne zahashowanie uszkodzonych plików nie
zalicza kontroli. Inwentarz odrzuca brakujące, dodatkowe i specjalne pliki.
Manifest zawiera również rozłączne zakresy kluczy, liczniki i sumy części.

`iter_verified_points` najpierw weryfikuje cały bundle, następnie wykonuje
drugi ograniczony replay i ponownie sprawdza każdą część przed udostępnieniem
punktów. Sprawdza tożsamość rodzica i manifestu. Dwa przebiegi kosztują czas,
ale zapobiegają rozpoczęciu konsumpcji bundle z nieprawidłową późniejszą
częścią. To czytnik do weryfikacji; nie jest jeszcze szybką ścieżką treningu.

## Uruchomienie

Rodzic musi być publicznym, odebranym curated 1.1; katalog rodzica wyjścia
musi istnieć. Wyjście wewnątrz wejściowego curated jest odrzucane.

```sh
python -m retailops_ai.stockout_preparation.cli build \
  --curated data/generated/curated/curated-sha256-... \
  --output /private/tmp/stockout-features-v2
python -m retailops_ai.stockout_preparation.cli verify \
  --curated data/generated/curated/curated-sha256-... \
  --output /private/tmp/stockout-features-v2
```

Odbiór [102 dni](../evidence/08-06-stockout-partitions.md) porównuje wszystkie
1632 punkty i modele development przez ograniczony roundtrip do v1.
Trening, etykiety, upstream i split nadal przyjmują v1. Nie oceniono final
testu, nie dopasowano progów serving i nie promowano modelu.
Wycofanie usuwa odrębny pakiet i dokumentację; nie wymaga migracji v1.
