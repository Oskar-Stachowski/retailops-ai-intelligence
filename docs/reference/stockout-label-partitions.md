# Prywatne partycje etykiet stockout — wersja 2.0

Pakiet `stockout_label_partitions` przygotowuje etykiety z jednego
zweryfikowanego prywatnego snapshotu inventory 1.1. Zapisuje je w małych
częściach Parquet i ma odrębny manifest 2.0. [Etykiety v1](stockout-labels.md),
ich kod, CLI, JSON i identyfikatory pozostają bez zmian. Nowy format
nie jest jeszcze wejściem istniejącego treningu ani splitu.

## Reguły i prywatna granica

Build, verify i iterator wymagają jawnego `allow_evaluation_truth=True`.
Publiczny snapshot faktów nie może zastąpić prywatnego rodzica.
Algorytm `label_window` v1 pozostaje niezmieniony: fizyczna jednostka to
produkt × magazyn × origin, nowe zero należy do `(t, t + 7 dni]`, także
przy natychmiastowym uzupełnieniu w tym samym czasie. Nieznany lub
niedostępny stan, spóźniony ruch, staleness, coverage i dojrzałość wyniku
zachowują dotychczasowe znaczenie. Nieocenialny wynik ma `null`, nie zero.

Kwalifikacja lifecycle, assortment, routing i sprzedaży pozostaje
przypiętym dowodem producenta AI 06. Konsument odtwarza ledger i porównuje
status, etykietę oraz dostępność wyniku z kwalifikacją. Zmiana magazynu,
niezgodność ze stanem początkowym lub kwalifikacją zatrzymuje budowę.
Nie dodano nowych wyjątków jakości i nie oceniono final testu.

## Magazyn i limity

Jeden przebieg weryfikuje całego rodzica, następnie strumieniuje ledger,
stany dzienne i coverage do prywatnej, jednorazowej bazy SQLite. Logiczne
sumy i liczby wierszy odczytanych tabel są sprawdzane ponownie. Po odczycie
weryfikowane są manifest, wszystkie pliki snapshotu i ich inwentarz.
Manifest wyniku wiąże pełnego rodzica i pieczęć wybranych tabel.

Kwalifikacja pozostaje ograniczonym JSON do 4 MiB / 10 000 okien;
po umieszczeniu jej w bazie lista jest zwalniana. Okna są czytane kursorem
w kolejności fizycznej serii i origin. Klucz czasu normalizuje UTC do
mikrosekund: zapisy `Z` i `+00:00` mają ten sam porządek chronologiczny;
dwa zapisy tego samego fizycznego origin nie tworzą osobnych okien. W pamięci jest ledger jednej serii,
batch do 256 okien i punktów oraz pojedynczy stan i coverage. Lookup stanu używa
dokładnego UTC `snapshot_at`; źródłowy grain dzienny pozostaje zachowany.
Hash payload i zgodność indeksu z zawartością są kontrolowane przy odczycie.

| Składnik | Maksimum |
|---|---:|
| Prywatny snapshot | 64 MiB / 500 000 wierszy |
| Cały ledger / kwalifikowane okna | 100 000 / 10 000 |
| Ledger wybranej fizycznej serii | 20 000 wierszy / 16 MiB payload |
| Główna baza / cache stron | 128 MiB / 8 MiB |
| Batch wyników | 256 origin jednej serii |
| Jedna część / wszystkie części Parquet | 16 MiB / 64 MiB |
| Manifest / JSON kwalifikacji | po 4 MiB |

Limity wejścia i dawnej label policy nie zostały podniesione. Główny limit
bazy nie obejmuje pomocniczych baz weryfikatora rodzica i sortowania sum
logicznych. Odtworzenie v1 nadal sortuje wybrany ledger dla każdego okna.
Te granice składników nie są limitem całego RSS/scratch ani odbiorem
większego profilu. Preflight pełnego pipeline pozostaje do wykonania.

## Zapis, odtworzenie i czytnik

Jawny schemat Arrow zachowuje UTC w mikrosekundach, int64 oraz nullable
outcome/onset/availability. Każda część ma jedną fizyczną serię i rozłączny
zakres origin. Manifest wiąże wszystkie zakresy, liczniki, logical point
hash, label, partition i storage policy, rodziców, checksums i wersję Arrow. Tożsamość obejmuje
cały nowy pakiet oraz dotychczasowy kod replay, lock i Python.

Build zapisuje prywatny staging, synchronizuje go i publikuje atomowo
kompletny katalog. Pliki mają 0600, katalog 0700. Retry tworzy całość
od nowa; reuse wymaga identycznego manifestu, inwentarza i każdego bajtu.
Inny artefakt nie jest nadpisywany. Symlinki, `..` i nakładanie się
wyjścia lub scratch na źródło są odrzucane.

Verify ponownie odtwarza wszystkie punkty i dokładne bajty Parquet;
nie dekoduje niezweryfikowanego pliku wyjściowego. Resealing zmienionej
części, zakresu, reportu lub pieczęci nie zastępuje replay. Brak,
powtórzenie i dodatkowy plik blokują wynik.

`iter_verified_labels` najpierw sprawdza cały bundle, przed pierwszym
punktem. W drugim ograniczonym przebiegu sprawdza ponownie rodzica,
manifest i aktualną część, po czym zwraca odpowiadające jej odtworzone
`LabelPoint`. Wykrywa zmianę między przebiegami i przed konsumpcją części.
To czytnik jawnie prywatnych etykiet; nie filtruje ról development/test.
Trening musi osobno weryfikować temporalny split, dojrzałość i role.

## CLI i zakres odbioru

```sh
python -m retailops_ai.stockout_label_partitions.cli build \
  --source /path/to/private-inventory-snapshot \
  --output /private/tmp/stockout-label-parts --allow-evaluation-truth
python -m retailops_ai.stockout_label_partitions.cli verify \
  --source /path/to/private-inventory-snapshot \
  --output /private/tmp/stockout-label-parts --allow-evaluation-truth
```

Rodzic wyjścia musi istnieć. [Odbiór](../evidence/08-09-stockout-label-partitions.md)
porównuje wszystkie pola małej próbki 102 dni, modele przez istniejący
bridge v1 oraz odłączony wheel. Większej generacji nie uruchomiono.
Pozostają upstream, comparison, split, integracja treningu, profil i
budżet całego pipeline, niezależna ocena, progi i lifecycle/batch/read API.
Cały AI 08 pozostaje not ready; AI 05/v12 nie został zmieniony.
