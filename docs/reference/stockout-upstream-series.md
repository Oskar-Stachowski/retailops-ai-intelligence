# Historyczna prognoza pomocnicza z odczytem jednej fizycznej serii

Pakiet `stockout_upstream_series` publikuje osobny manifest
`stockout_upstream_partitions` **2.1.0**. Zachowuje odebrane pakiety,
manifesty i identyfikatory wcześniejszych wersji, AI 05 oraz finalne v12.
Nowe ID wiąże nowy kod selekcji, pełny seal wejścia i feature bundle 2.2.

```sh
python -m retailops_ai.stockout_upstream_series.cli build \
  --curated /absolute/curated --features /absolute/features-2.2 \
  --output /absolute/new-upstream-2.1
python -m retailops_ai.stockout_upstream_series.cli verify \
  --curated /absolute/curated --features /absolute/features-2.2 \
  --output /absolute/new-upstream-2.1
```

## Pełna walidacja, ograniczona selekcja

Każdy przebieg odtwarza wszystkie partycje cech 2.2 i weryfikuje pełny
publiczny curated. Zamrożony magazyn 2.0 streamuje allowlistowane tabele
do prywatnej SQLite. Nowy indeks powstaje jednorazowo z tych uszczelnionych
rekordów i zachowuje wszystkie wersje, granice dostępności, logiczne
klucze, wymiary i okresy. Nie przyjmuje cache przygotowanego przez wywołującego
i nie czyta etykiet ani truth.

Przed pierwszą selekcją w danym origin SQL sprawdza **cały znany zbiór**:

- Nie dopuszcza powtórzonej najwyższej znanej wersji żadnego logicznego
  klucza ośmiu tabel prognozy, także dla obcego produktu i poza oknem historii.
- Dla tras zachowuje kolejność oryginalnych rekordów i odrzucanie remisu
  bieżącej najwyższej wersji, zgodnie z zamrożonym `stockout.features.latest`.
  Późniejsza wyższa wersja nie ukrywa wcześniejszego takiego remisu.
- Sprawdza unikalność efektywnych tras selling location/channel **przed**
  filtrowaniem fizycznego magazynu.

SQL używa dyskowych indeksów i nie tworzy globalnego panelu obiektów Python.
Walidacja jest ponawiana przy zmianie origin. Jej lokalne zapamiętanie
obowiązuje tylko wewnątrz jednej prywatnej, uszczelnionej sesji bazy.

Następnie `known_series(product, stock, forecast_origin)` pobiera jedynie
rekordy potrzebne do bieżącej fizycznej serii. Wybiera trasę po ustaleniu
najnowszej **znanej** wersji; zmiana magazynu nie przywraca starszej trasy.
Zachowuje wszystkie znane wersje wybranych faktów, 28 dni historii,
14 dni kalendarza przyszłych celów, odpowiadającą kategorię i plany znane
już w origin. Pełne 14 dni jest wymagane, ponieważ zamrożony builder
tworzy 14 celów przed wyborem prognozy siedmiodniowej. Błędy także
nieużywanego później horyzontu pozostają widoczne.

`OriginFeatures` i `upstream_point` pozostają zamrożone. Kanały są
sumowane jednokrotnie do fizycznego zapasu. Historyczna granica wiedzy
pozostaje na sekundzie końca dnia i nie przekracza origin zapasu.
Znany przyszły plan lub kalendarz może być użyty; przyszła obserwowana
sprzedaż i podsekundowa późna korekta nie są użyte.

## Uszczelnienie, publikacja i limity

Wynik zachowuje jawne typy Parquet, UTC z mikrosekundami, nullable
prognozy i pełne selling-series lineage. Kolejność to `as_of/product/stock`.
Manifest wiąże także checksum indeksu oraz policy selekcji. Raport pokazuje
liczbę zapisanych faktów, maksymalną selekcję, liczbę selekcji fizycznych
serii i globalnie sprawdzonych origin oraz wykorzystanie cache dekodowania.
To pomiary składnika, bez odbioru
całego RSS/scratch pipeline.

Baza jest prywatna, jednorazowa i query-only. Każde użycie sprawdza
tożsamość, rozmiar i nanosekundowe czasy zmiany pliku oraz flagę query-only;
zmiana blokuje także ponowne użycie zapamiętanej walidacji origin.
Każdy wybrany payload zachowuje sprawdzanie checksum i zgodności indeksu.
Nie ma globalnego fallback `known()`.

Prywatny cache przechowuje co najwyżej 1024 zdekodowane rekordy / 1 MiB
kanonicznych payload. Przyspiesza wyłącznie dekodowanie. Selekcja
availability, checksum i zgodność indeksu są sprawdzane przy każdym
pobraniu, również przy trafieniu w cache. Klucz wiąże tabelę i pozycję
niezmiennego rekordu, bez zastępowania historii globalną najnowszą wersją.
Zwracane słowniki są kopią, a zamknięcie kontekstu usuwa cache.
Opis typów dziewięciu tabel jest wczytywany raz podczas budowy indeksu;
selekcja nie otwiera ponownie pliku kontraktu dla każdego punktu.
Manifest wiąże stałe granice cache; są to bajty kanoniczne, nie pomiar
rozmiaru obiektów Python ani całego RSS.

Nie podniesiono limitów. Nadal obowiązują: wejście 64 MiB / 500 000 wierszy,
baza 128 MiB i cache 8 MiB, maksymalnie 20 000 rekordów / 16 MiB kanonicznych
payload **jednej selekcji**, 10 000 origin z rodzica cech, porcja najwyżej
256 punktów, część 16 MiB, suma Parquet 64 MiB i manifest 4 MiB.
Pełny zbiór może przekroczyć limit wejścia lub bazy i zostać odrzucony.

Build publikuje kompletny staging atomowo bez nadpisania. Retry akceptuje
wyłącznie identyczny manifest, inventory i wszystkie bajty części.
Verify odtwarza wszystkie części. `iter_verified_upstream` wymaga pełnej
weryfikacji przed pierwszym punktem, potem odtwarza drugi uszczelniony
przebieg i sprawdza rodziców oraz części przed każdą porcją. Iterator
należy wyczerpać lub zamknąć. Niekompletny zapis, uszkodzone dane lub
przekroczony limit blokują wynik i usuwają własny scratch.

## Zakres odbioru

Odbiór ponawia istniejący `ai-temporal-smoke`, bez większej generacji.
Porównanie modeli przez bridge v1 w helperze jest dowodem zgodności,
nie integracją treningu z nowym parent ID. Odebrany temporalny adapter
2.0 nadal używa upstream 2.0; podłączenie upstream 2.1 wymaga osobnego
przyrostu zachowującego odebrane artefakty i tożsamości.

Pozostają spójny przebieg bez nadmiarowego replay rodziców, limity
pozostałych składników, większy jawny profil, preflight i pomiar całych
zasobów, karta z nowych rodziców, niezależna ocena jakości, zatwierdzone
progi oraz lifecycle/batch/read API. Final test nie jest oceniany,
model nie jest promowany. Cały AI 08 pozostaje **not ready**.
