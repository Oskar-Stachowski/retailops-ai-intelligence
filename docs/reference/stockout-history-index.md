# Indeks historii stockout — zapis cech 2.2

Pakiet `stockout_history` przyspiesza liczenie 28-dniowej historii na
tych samych faktach znanych w origin. Używa ograniczonego magazynu
[2.1](stockout-disk-facts.md) i identycznego zapisu
[Parquet 2.0](stockout-partitions.md). Stare pakiety, czytniki oraz ID
pozostają bez zmian. Manifest 2.2 przypina własny kod indeksu i projekcji.
To przyrost przygotowania danych, bez odbioru większego profilu.

## Jeden indeks dla wiedzy jednego punktu

Po wykluczeniu wszystkich przyszłych lub niedostępnych faktów indeks
sortuje ledger raz po `(occurred_at, sequence)` i wylicza całkowitoliczbowe
sumy prefiksowe. Dla każdej daty wyszukuje binarnie początek i koniec
dnia. Zapas przed dniem pochodzi z prefiksu; tylko ruchy w tym dniu są
przeglądane do kontroli ujemnego bilansu, chwilowego zera i liczby
nowych braków. Zachowuje kolejność ruchów o identycznym czasie.

Opening stock, weryfikacja coverage i granice `[start, end)` mają
semantykę v1. Początkowy wiersz musi być `opening_stock`, a coverage
musi obejmować ten opening i cały oceniany dzień. Opening dokładnie
o północy dostarcza początkowy zapas; ruchy typu opening nie są liczone
ponownie w tym samym dniu. Chwilowe zero pozostaje ograniczeniem sprzedaży,
nawet jeśli towar zostanie natychmiast uzupełniony. Brak coverage pozostaje
stanem nieznanym, bez dopisywania zera i bez nowych wyjątków jakości.

Wersje popytu są grupowane po business date, bez usuwania rewizji.
Wybór najnowszej znanej wersji, efektywne trasy i assortment, komplet
kanałów, mapped stock oraz brakujące/zerowe obserwacje pozostają
w niezmienionych helperach v1. Historyczne lineage nadal obejmuje
wszystkie znane wiersze, także dawne wersje spoza okna.

Indeks powstaje ponownie dla każdego origin. Nie jest cache globalnej
najnowszej wersji ani incremental cache między origin. Późniejsza korekta
może zmienić dawny dzień dopiero od swojej granicy wiedzy. Gdy dzień
wypada z ruchomego okna 28 dni, znana korekta nadal należy do lineage,
ale nie do statystyk bieżącego okna. Pełne odtworzenie jest poprawne także
przy odwróconej kolejności origin i zmianie fizycznej serii.

## Wersje, limity i odtworzenie

Projekcja 2.2 zachowuje montaż pól v1, policy, statusy, preprocessing
`none`, matematykę statystyk, supply i lineage. Jest odrębnym plikiem,
aby nie zmieniać tożsamości zaakceptowanych artefaktów v1. Manifest
wiąże również stare implementacje przygotowania i magazynu oraz nową
sumę całego kodu `stockout_history`. Logiczny hash punktów i wszystkie
pliki Parquet pozostają identyczne; zmienia się ID bundle.

Indeks mieści się w ograniczonym widoku origin z magazynu 2.1. Nie
podnosi limitu wejścia 64 MiB / 500 000 wierszy, 10 000 origin, bazy
128 MiB, cache stron 8 MiB ani selekcji 20 000 wierszy / 16 MiB payload.
Nowe tablice prefiksu i mapy dni wymagają dodatkowej pamięci; te granice
składników nie są limitem całego RSS/scratch. Zapis nadal ma 16 MiB na
część, 64 MiB Parquet i 4 MiB manifestu.

Build publikuje kompletny prywatny bundle atomowo bez nadpisania.
Retry odtwarza całość, a reuse wymaga identycznego manifestu i plików.
Verify odbudowuje fakty, indeksy i każdą część oraz porównuje wszystkie
bajty i inwentarz. Ponowne zahashowanie zmiany indeksu, pliku lub zakresu
nie zastępuje replay. Wersje 2.1 i 2.2 mają osobne czytniki verify.

## Uruchomienie i odbiór

Rodzic musi być publicznym, odebranym curated 1.1. Katalog rodzica
wyjścia musi istnieć; wyjście nie może nakładać się na curated.

```sh
python -m retailops_ai.stockout_history.cli build \
  --curated data/generated/curated/curated-sha256-... \
  --output /private/tmp/stockout-features-v22
python -m retailops_ai.stockout_history.cli verify \
  --curated data/generated/curated/curated-sha256-... \
  --output /private/tmp/stockout-features-v22
```

[Odbiór](../evidence/08-08-stockout-history-index.md) obejmuje pełną
próbkę 102 dni, porównanie modeli i odłączony wheel. Etykiety, upstream,
split i trening nadal przyjmują v1; roundtrip smoke nie jest migracją
treningu. Incremental reuse historii między origin pozostaje możliwym
dalszym usprawnieniem. Większy profil, odbiór zasobów całego pipeline,
niezależna ocena, progi i lifecycle/batch/read API pozostają otwarte.
Cały AI 08 nie jest ready. Wycofanie odrębnego pakietu nie wymaga
migracji ani przeliczania dotychczasowych artefaktów v1/v2.0/v2.1.
