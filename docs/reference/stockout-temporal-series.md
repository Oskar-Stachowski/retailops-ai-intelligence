# Trening z upstream 2.1 i jednym replay — AI 08.13

`retailops_ai.stockout_temporal_series` tworzy osobny temporal bundle 2.1
z odebranych cech 2.2, upstream 2.1 i prywatnych etykiet 2.0. Trening otrzymuje
rzeczywiste identyfikatory tych rodziców. Dotychczasowy temporal 2.0 nadal
korzysta z upstream 2.0; żaden wcześniejszy pakiet ani identyfikator nie zmienia się.

## Spójny przebieg

Jednorazowy, prywatny kontekst odtwarza każde wejście dokładnie raz:

1. Odtwarza cechy i sprawdza wszystkie części, manifest i inventory.
2. Z zapisanych prywatnie punktów wyprowadza klucze upstream. `SeriesFacts`
   sprawdza globalne znane wersje i efektywne trasy przed wyborem jednej
   fizycznej serii. Wszystkie części upstream muszą odpowiadać replay.
3. Odtwarza etykiety z prywatnego snapshotu, sprawdza wszystkie części,
   manifest i inventory. Wymagana jest jawna zgoda na evaluation truth.
4. Sprawdza zgodność source, qualification, curated i feature pins, identyczne
   klucze cech/upstream oraz katalog kategorii dostępny w czasie prognozy.
5. Kończy zapis prywatnej bazy, sprawdza pełne seals rodziców i blokuje zapis.

Nie przyjmuje flagi „już zweryfikowane” ani cache od wywołującego.
Następne wywołanie tworzy nowy kontekst i ponownie sprawdza rodziców.
Odtworzone punkty są niedostępne do końca pełnej inicjalizacji; błąd usuwa scratch.
Dodatkowe hashowanie plików i odczyt katalogu nie są pomijane przez ten kontrakt.

Porcje porównania i membership korzystają z niezmienionych decyzji temporal 2.0
oraz tego samego kodowania Parquet. Manifest 2.1 wiąże nową implementację,
implementację frozen temporal/training, rzeczywiste parent IDs i politykę
`single-private-parent-replay-1.0.0`. Polityki rozmiaru batchu i bazy pozostają
wcześniejszymi kontraktami; ich wersja nie oznacza liczby replay.

## Trening i kontrole zmian

Assembler w jednym replay temporal wypełnia prywatne tablice `train`, `tune`
i `calibration`. Zwraca je dopiero po sprawdzeniu **wszystkich** części,
manifestu, inventory, niezmienności całego temporal pakietu, rodziców i bazy.
Nie przekazuje częściowych danych do fittera. Uszkodzona ostatnia część lub
ponownie zahashowane przeniesienie punktu z test do train blokuje wynik.
Zmiana wcześniej przeczytanej części jest także wykrywana na końcu.

Baza jest prywatna, query-only. Każdy odczyt sprawdza dev/inode/rozmiar/mtime/ctime,
brak symlink i flagę query-only. Payload nadal ma checksum i zgodność z indeksem.
Zmiana bazy lub jej zastąpienie blokuje następny odczyt, także po przywróceniu mtime.
Pełny checksum bazy powstaje raz; nie jest liczony ponownie dla każdego produktu.

Etykiety test są odtwarzane przy sprawdzaniu prywatnego rodzica. Nie tworzy się
wektora celów lub macierzy modelu test ani jego metryk; raport podaje tylko
liczbę dopuszczonych membership. Dostępność i purging oraz PIT kategorii
pozostają niezmienione. Każda rola development musi mieć obie klasy.

## Limity

Pozostają 10 000 kluczy, 128 MiB głównej bazy, 64 MiB payload, 8 MiB cache
SQLite, najwyżej 256 kluczy i 16 MiB payload na batch. Rodzice i sprawdzany
pakiet temporal mają limit 4096 plików / 128 MiB na root. Parquet ma limit
64 MiB, para części 16 MiB, manifest 4 MiB. Tablice development z targetami
**oraz wpisami lineage** mają łącznie do 10 000 wierszy / 16 MiB kanonicznych
bajtów; nadal są w pamięci. Limitów nie wolno podnosić przez opcje wywołania.

Helpery curated i etykiet nadal mają własny scratch i bazy weryfikacji.
Odbiór małej próbki nie kwalifikuje całego RSS, scratch ani większego profilu.

## Polecenia offline

Jawny `POLICY.json` zawiera `SplitPolicy`. `TEMPORAL_21` to osobny, immutable
katalog; identyczny retry jest używany ponownie, inny wynik nie nadpisuje go.

```sh
python -m retailops_ai.stockout_temporal_series.cli build \
  --curated CURATED --private PRIVATE \
  --features FEATURES_22 --upstream UPSTREAM_21 --labels LABELS_20 \
  --split-policy POLICY.json --output TEMPORAL_21 --allow-evaluation-truth

python -m retailops_ai.stockout_temporal_series.cli verify \
  --curated CURATED --private PRIVATE \
  --features FEATURES_22 --upstream UPSTREAM_21 --labels LABELS_20 \
  --output TEMPORAL_21 --allow-evaluation-truth

python -m retailops_ai.stockout_temporal_series.cli train \
  --curated CURATED --private PRIVATE \
  --features FEATURES_22 --upstream UPSTREAM_21 --labels LABELS_20 \
  --output TEMPORAL_21 --allow-evaluation-truth
```

`train` zwraca development JSON na stdout. Nie publikuje modelu ani nie zmienia
progów. Pełny AI 08 wymaga jeszcze większego, jawnego profilu i budżetów,
niezależnej oceny/calibration, karty z nowych rodziców, zaakceptowanych progów
oraz lifecycle/batch/read API. [Odbiór](../evidence/08-13-stockout-temporal-series.md)
rozdziela ten przyrost od gotowości całego etapu.
