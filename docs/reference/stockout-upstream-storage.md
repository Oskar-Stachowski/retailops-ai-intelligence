# Ograniczone przygotowanie historycznej prognozy pomocniczej

Pakiet `stockout_upstream_storage` dodaje osobną ścieżkę i manifest
`stockout_upstream_partitions` 2.0. Korzysta z publicznego curated i
odebranego bundle cech 2.2. Nie zmienia pakietów wcześniejszych wersji,
algorytmu AI 04, przyjętej v12 ani identyfikatorów v1.

```sh
python -m retailops_ai.stockout_upstream_storage.cli build \
  --curated /absolute/curated --features /absolute/features-2.2 \
  --output /absolute/new-upstream
python -m retailops_ai.stockout_upstream_storage.cli verify \
  --curated /absolute/curated --features /absolute/features-2.2 \
  --output /absolute/new-upstream
```

## Przygotowanie i granica wiedzy

Każdy przebieg odtwarza wszystkie cechy 2.2 oraz porównuje manifest,
inventory i dokładne bajty ich części. Z punktów zachowuje jedynie
klucze fizyczne, origin i flagę eligibility, najwyżej 10 000 elementów.
Nie materializuje całego JSON cech ani całej historii wszystkich punktów.

Następnie jednorazowo weryfikuje pełny curated, streamuje dziewięć
allowlistowanych tabel prognozy do prywatnej, tymczasowej bazy SQLite
i sprawdza sumy logiczne oraz ponownie inventory i hash wszystkich
plików rodzica. Nie odczytuje prywatnych etykiet lub truth.
Magazyn ma tylko odczyt po uszczelnieniu; każda pobrana pozycja musi
zgadzać się z własnym payload checksum i indeksem dostępności.

Panel faktów jest odczytany raz do ostatniego origin. Zachowuje wszystkie
znane wersje, zamiast globalnej najnowszej rewizji. Zamrożone
`OriginFeatures` i `upstream_point` niezależnie ograniczają wiedzę do
historycznego origin każdego dnia. Zachowane są 28 dni historii,
plany i kalendarze przyszłych dni znane już w origin, rzeczywiste selling
location/channel i jednokrotne sumowanie ich do fizycznego magazynu.
Nieznany kalendarz pozostaje brakiem; znany zamknięty dzień ma zero.
Origin AI 04 kończy się na sekundzie danego dnia i nigdy nie przesuwa się
po origin zapasu. Późne i podsekundowe rewizje nie przepisują historii.

Panel obejmuje wszystkie serie mieszczące się w limicie. Dzięki temu
projekcja nadal odrzuca niejednoznaczne trasy i znane wersje także poza
wybranym magazynem. Nie ma jeszcze indeksu ograniczającego ten panel do
jednej serii z osobną globalną walidacją; większy profil może przekroczyć
ten limit i zostać odrzucony.

## Zapis, czytnik i limity

Parquet ma jawne typy, UTC z mikrosekundami, nullable float64 i pełne
selling-series lineage z siedmioma dziennymi prognozami. Części są
chronologiczne, po dniu i batch najwyżej 256 punktów. Logical hash jest
liczony w kolejności `as_of/product/stock`, podczas gdy v1 sortuje
`product/stock/as_of`; zgodność wartości porównuje pełne pola po kluczu.
Nowy bundle ID wiąże także feature bundle ID, jego logical point hash,
input seal, policy, kod projekcji/przygotowania i wersje Arrow/SQLite.

Granice pozostają: wejście 64 MiB / 500 000 wierszy, główna baza
128 MiB, cache stron 8 MiB, panel najwyżej 20 000 wierszy / 16 MiB
kanonicznych payload, część 16 MiB, suma Parquet 64 MiB, manifest 4 MiB.
To limity składników, nie gwarancja całego RSS lub scratch. Weryfikatory
rodziców i pomocnicze bazy sum logicznych mają własny scratch.

Każdy build publikuje kompletny prywatny staging atomowo bez nadpisania.
Retry akceptuje tylko identyczny manifest, inventory i wszystkie części.
Przekroczenie zasobów lub błąd odrzuca wynik i usuwa własny scratch.
Zmiana cech wejściowych jest sprawdzana ponownie przed publikacją.

`iter_verified_upstream` weryfikuje całość przed pierwszym punktem,
w drugim przebiegu ponownie uszczelnia curated i odtwarza cechy.
Przy każdej części sprawdza rodzica cech, bieżący manifest i dokładne bajty.
Zwraca wartości odtworzone przez projekcję; nie dekoduje niesprawdzonego
Parquet. Konsument musi domknąć lub wyczerpać iterator, aby zamknąć jego
kontekst. Ten reader nie wybiera ról temporalnego splitu i nie jest
jeszcze adapterem treningu.

Odbiór obejmuje istniejącą próbkę 102 dni. Comparison, split i trening
w produkcyjnej ścieżce nadal przyjmują v1; mały bridge zgodności w odbiorze
nie zmienia tego stanu. Większy profil, pełny budżet, niezależna ocena,
progi i lifecycle/batch/read API pozostają otwarte. Cały AI 08 nie jest ready.
