# Odczyt faktów stockout z dysku — zapis cech 2.1

Pakiet `stockout_storage` zastępuje ładowanie wszystkich tabel faktów do
list prywatnym, jednorazowym magazynem SQLite i cache jednej fizycznej
serii. Zachowuje projekcję i Parquet z [wersji 2.0](stockout-partitions.md).
Kod v1, v2.0 i treningu pozostaje bez zmian; nowe manifesty mają schema
`2.1.0` i własne ID. To kolejny przyrost
[przygotowania większego profilu](stockout-profile-resources.md).

## Wejście i granice zasobów

Każdy kontekst najpierw weryfikuje kompletny publiczny curated 1.1,
następnie strumieniuje 11 tabel potrzebnych do cech. Domyślny batch Arrow
ma 256 wierszy, najwyżej 512. Wiersze trafiają do SQLite z kluczem tabeli,
kolejnością, produktem, fizycznym stock, granicą wiedzy i sumą payload.
Przy odczycie ponownie sprawdzane są semantyczne sumy tabel. Przed
udostępnieniem magazynu wymagane są również identyczne bajty manifestu,
hash i rozmiar każdego pliku oraz pełny inwentarz rodzica, także tabel
nieużywanych do cech. Zmiana rodzica blokuje wynik.

| Granica `StoragePolicy` | Wartość |
|---|---:|
| Główna baza SQLite, `max_page_count` | 128 MiB |
| Cache stron SQLite | 8 MiB |
| Wybrane wiersze, łącznie ze wszystkich tabel | 20 000 |
| Kanoniczne bajty wybranych wierszy, przed dekodowaniem | 16 MiB |
| Batch wejściowy | 256, najwyżej 512 wierszy |

Policy może zmniejszyć limity, ale nie zwiększyć górnych granic.
Przekroczenie limitu bazy, selekcji lub zapisu blokuje publikację i usuwa
własny katalog tymczasowy. Nie są to limity peak RSS ani całego scratch:
pełny verifier curated i pieczętowanie tabel mają własne pliki robocze,
a Python/Arrow i projekcja potrzebują dodatkowej pamięci. Budżet całego
większego pipeline nadal wymaga osobnego odbioru. Dotychczasowe granice
wejścia 64 MiB / 500 000 wierszy, 10 000 origin i limity Parquet/manifestu
pozostają aktywne.

Magazyn ma prywatny katalog 0700 i plik 0600. Wyłącza mmap, używa
dyskowego temp store i po pieczętowaniu przechodzi w `query_only`.
Jest usuwany po sukcesie, wyjątku lub przerwanym zapisie. Nie przyjmujemy
wcześniej zapisanej bazy jako zweryfikowanego cache; każdy przebieg
odtwarza ją z rodzica. Symlinki i scratch wewnątrz curated są odrzucane.

## Wiedza i cache jednej serii

SQL korzysta z indeksu `(table, product, stock, ready, position)`,
parametryzowanego prefiksu `ready <= origin` i limitu kursora. `ready`
obejmuje curated availability oraz wszystkie czasy zdarzenia wymagane
przez projekcję v1. Routing i popyt zachowują swoje globalne zakresy.
Brak dostępności jest wykluczony. Suma payload i odtworzone klucze
indeksu muszą pasować do wybranego wiersza; kolejność wejściowa jest
przywracana przed przekazaniem faktów projekcji.

Cache przechowuje najwyżej jedną fizyczną serię. Ładuje wszystkie jej
wersje znane w ostatnim przygotowanym origin, następnie istniejący
`FactIndex` ponownie ogranicza wiedzę przy każdym wcześniejszym origin.
Późniejsza korekta nie nadpisuje dawnego widoku. Nie redukujemy danych
do globalnej najnowszej wersji; zachowujemy historyczne lineage i fakty
sprzed 28-dniowego okna. Zmiana serii usuwa poprzedni indeks przed
odczytem następnej. Origin poza przygotowanym zakresem jest odrzucany.

## Manifest i odtworzenie

Manifest 2.1 wiąże dotychczasową policy/projekcję/Arrow oraz osobno policy
magazynu, sumę kodu `stockout_storage`, wersję SQLite, sumy odczytanych
tabel i uporządkowaną sumę payload. Logiczne punkty i każdy plik Parquet
mają tę samą treść co v2.0. ID bundle zmienia się, ponieważ przypina inną
implementację wejścia. Stare czytniki i stare identyfikatory pozostają
ważne w swoich wersjach.

Build publikuje kompletny prywatny bundle atomowo bez nadpisania celu.
Reuse wymaga ponownego przygotowania oraz identycznego manifestu i
wszystkich plików. Verify odbudowuje magazyn, każdą część i cały manifest;
porównuje dokładne bajty oraz inwentarz. Zmiana części lub pieczęci,
nawet ponownie zahashowana, nie zastępuje replay. Części brakujące,
powtórzone i dodatkowe pliki blokują odbiór.

## Uruchomienie i dalszy zakres

Katalog rodzica wyjścia musi istnieć. Wyjście nie może nakładać się
na curated. Dane truth nie są wejściem tego CLI.

```sh
python -m retailops_ai.stockout_storage.cli build \
  --curated data/generated/curated/curated-sha256-... \
  --output /private/tmp/stockout-features-v21
python -m retailops_ai.stockout_storage.cli verify \
  --curated data/generated/curated/curated-sha256-... \
  --output /private/tmp/stockout-features-v21
```

[Odbiór 102 dni](../evidence/08-07-stockout-disk-facts.md) obejmuje pełny
roundtrip 1632 punktów, zgodność modeli i odłączony zainstalowany wheel.
Kumulacyjny ledger i ruchome okna nadal wymagają kolejnego przyrostu.
Etykiety, upstream, split i trening korzystają z v1; 2.1 nie ma jeszcze
szybkiego czytnika treningowego. Większy profil, niezależna ocena,
progi i lifecycle/batch/read API pozostają otwarte. Cały AI 08 nie jest
ready. Wycofanie tego odrębnego pakietu nie wymaga migracji v1/v2.0.
