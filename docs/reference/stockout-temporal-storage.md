# Comparison, split i trening z partycji — AI 08.11

Pakiet `retailops_ai.stockout_temporal_storage` łączy odebrane cechy 2.2,
upstream 2.0 i prywatne etykiety 2.0. Tworzy osobny immutable bundle 2.0
z porównywalnymi cechami obu wariantów oraz przynależnością czasową.
`assemble_partitioned_development` przekazuje dopuszczone wiersze do
dotychczasowego treningu sześciu wariantów LR/HGB. Pakiety v1, pozostałe
pakiety partycji, AI 05 i finalne v12 zachowują kod i identyfikatory.

## Rodzice i granica wiedzy

Każdy przebieg wymaga publicznego curated oraz prywatnego snapshotu
kwalifikacji, a także trzech pełnych bundle rodziców. Dostęp do prywatnych
etykiet wymaga jawnego `allow_evaluation_truth=True` lub flagi CLI.
Odmowa następuje przed załadowaniem prywatnych punktów.

Cechy są odtwarzane przez `HistoryPreparation`; wszystkie ich części,
manifest i inventory muszą się zgadzać. Upstream i etykiety korzystają
z istniejących readerów po pełnym replay. Tymczasowy magazyn przyjmuje
tylko odtworzone punkty, nie niezweryfikowany Parquet ani cache użytkownika.
Rodzice muszą mieć zgodne źródło, qualification i przypięty bundle cech.
Upstream ma dokładnie ten sam zbiór kluczy co cechy. Osobny brak cechy dla
etykiety pozostaje jawnym `feature_missing` w membership.

Klucz fizyczny normalizuje UTC do mikrosekund. Dwa zapisy tego samego
czasu nie tworzą dwóch origin. Nie zmienia się cutoff wiedzy ani dojrzałość
etykiety. Znana później kategoria produktu nie jest dostępna wcześniej;
niejednoznaczny katalog PIT blokuje wynik.

## Ograniczona pamięć i zapis

Jednorazowa baza SQLite w prywatnym katalogu 0700 ma plik 0600, cache stron
8 MiB, wyłączone mmap i limit głównej bazy 128 MiB. Dozwolone są najwyżej
10 000 punktów każdego rodzica, 10 000 kluczy ich unii oraz 10 000 wierszy
katalogu. Suma kanonicznych payload jest ograniczona do 64 MiB, selekcja
jednego rodzaju punktów w batchu do 16 MiB. Weryfikowane roots mają także
limit 4096 plików / 128 MiB na root. Limity można obniżyć, nie podnieść
poza wartości kontraktu.

Join pobiera najwyżej 256 kluczy naraz, w kolejności product/stock/UTC.
W każdej porcji frozen `build_comparison` i `build_split` podejmują te same
decyzje co v1. Liczniki ról, wykluczeń i klas development są sumowane;
readiness jest obliczana dopiero z całości, nie z gotowości pojedynczej
porcji. Każda część zawiera dwa typed Parquet: `comparison` i `membership`.
Membership zapisuje status dopuszczenia, rolę i dojrzałość, bez kolumny
`incident_stockout` lub informacji o pierwszym zdarzeniu.

Obie części batchu mają łącznie najwyżej 16 MiB, cały Parquet 64 MiB,
manifest 4 MiB. Zapis powstaje w staging, jest atomowy i nie nadpisuje
istniejącego katalogu. Identyczny wynik jest ponownie używany; inny jest
odrzucany. Scratch jest usuwany także po błędzie. Verify odtwarza całość
i porównuje rzeczywiste bajty, manifest, inventory oraz logical hashes.
Przypięty seal wszystkich pięciu roots wykrywa zmianę rodzica, a seal
prywatnej bazy wykrywa także usunięcie lub ponowne zahashowanie wiersza.

To limity składników. Pomocnicze bazy weryfikatorów i sorty SQLite nadal
wymagają pomiaru pełnego RSS/scratch przed większym profilem.

## Development i końcowy test

Publiczny assembler najpierw weryfikuje cały temporal bundle. Ponownie
odtwarza podział, sprawdza bieżące części i wybiera wyłącznie dopuszczone
klucze `train`, `tune` i `calibration`. Outcome window oraz availability
muszą spełniać dotychczasowe ścisłe granice purgingu. Niepełna lub nieznana
etykieta nie staje się zerem. Rola `test` jest odrzucana przez selektor;
nie powstaje jej wektor celów ani macierz modelu.

Weryfikacja prywatnego rodzica nadal odtwarza etykiety wszystkich okien;
to sprawdzenie spójności, bez metryk końcowego testu. Jego eligibility jest
raportowane jedynie jako liczba membership. Klasy i outcome vectors dotyczą
wyłącznie trzech ról development. Każda z nich musi mieć obie klasy.
Całe wybrane tablice nadal mieszczą się w pamięci: mają łącznie najwyżej
10 000 wierszy i 16 MiB kanonicznej reprezentacji batchów z targetami.
To nie trening sklearn o nieograniczonej liczbie wierszy.

Nowy `DevelopmentData.parents` wiąże rzeczywiste feature/upstream/label
bundle IDs i `temporal_bundle_id`, zamiast przypisywać im stare JSON IDs.
Dlatego nowy development ID różni się od v1 nawet przy identycznych wagach,
model IDs i metrykach. Przepis treningu, wybór na tune oraz dopasowanie
sigmoid na osobnym calibration pozostają frozen. Wynik jest provisional,
bez zmiany progów lub wpisu do registry.

## Polecenia offline

`POLICY.json` to jawny JSON `SplitPolicy`, zamrożony przed oceną.

```sh
python -m retailops_ai.stockout_temporal_storage.cli build \
  --curated CURATED --private PRIVATE \
  --features FEATURES_22 --upstream UPSTREAM_20 --labels LABELS_20 \
  --split-policy POLICY.json --output TEMPORAL --allow-evaluation-truth

python -m retailops_ai.stockout_temporal_storage.cli verify \
  --curated CURATED --private PRIVATE \
  --features FEATURES_22 --upstream UPSTREAM_20 --labels LABELS_20 \
  --output TEMPORAL --allow-evaluation-truth

python -m retailops_ai.stockout_temporal_storage.cli train \
  --curated CURATED --private PRIVATE \
  --features FEATURES_22 --upstream UPSTREAM_20 --labels LABELS_20 \
  --output TEMPORAL --allow-evaluation-truth
```

`train` wypisuje ograniczony development JSON na stdout. Nie publikuje
modelu, nie zmienia rejestru i nie ocenia końcowego testu. Błąd daje
ogólny komunikat i exit 1. Odłączony wheel oraz natywny wynik są objęte
[osobnym odbiorem](../evidence/08-11-stockout-temporal-storage.md).
Większy profil, pełne budżety, niezależna ocena, progi, karta z nowych
rodziców i lifecycle/batch/read API pozostają otwarte.
