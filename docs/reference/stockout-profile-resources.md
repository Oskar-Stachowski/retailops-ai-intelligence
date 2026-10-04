# Większy profil stockout — ograniczenia i projekt wykonania

Aktualny [pomiar całego pipeline](stockout-resource-probe.md) i
[odbiór AI08.14](../evidence/08-14-stockout-resource-probe.md) kwalifikują
osobny pośredni `ai-load` 14 × 2 × 102, 2856 origin, 719,81 s,
622,23 MiB całego drzewa RSS i 259,23 MiB allocated scratch. Pilot ma jawnie
zamrożony budżet 2 GiB/1 GiB/1800 s i zachowuje 50 GiB wolnego miejsca.
Wariant 4480 odrzucono przed startem. Pełne `ai-dev`/`ai-training` i niezależna
jakość pozostają nieodebrane. Poniżej zachowano poprzedni projekt i jego
nieodebraną propozycję 5 GiB/600 s; nie stanowią wyniku tego pilota.

To projekt większego profilu, **nie wykonany odbiór większego profilu**.
[Partycje cech v2](stockout-partitions.md) realizują pierwszy przyrost:
osobny manifest, bounded Parquet i indeks serii/czasu wiedzy. Odbiór
ponawia małą próbkę 102 dni; nie uruchomiono większej generacji i nie
podniesiono istniejących limitów wejścia ani limitów ścieżek v1.
[Odczyt z dysku 2.1](stockout-disk-facts.md) dodaje jednorazowy magazyn
SQLite z ograniczoną selekcją i cache jednej serii. Także ten przyrost
ma odbiór małej próbki, bez kwalifikacji większego profilu.
[Indeks historii 2.2](stockout-history-index.md) wylicza prefiks ledgeru
raz na origin i ogranicza obliczenie każdego dnia do jego ruchów oraz
wersji popytu. Incremental reuse między origin i pozostałe rodzice
nadal wymagają osobnego przyrostu.
[Partycje etykiet 2.0](stockout-label-partitions.md) przenoszą prywatne
fakty do jednorazowej bazy i ograniczają replay do jednej fizycznej serii.
Mają batch Parquet i pełny iterator, ale kwalifikacja nadal jest JSON
4 MiB. Ten przyrost nie zalicza budżetu większego pipeline.
[Upstream 2.0](stockout-upstream-storage.md) dodaje pełny replay cech 2.2,
jednorazowy, ograniczony panel prognozy do ostatniego origin i chronologiczne
części Parquet. Każdy wcześniejszy origin zachowuje własną granicę wiedzy.
Panel nadal obejmuje wszystkie serie w limicie 20 000 wierszy / 16 MiB;
większy profil może być odrzucony. Ten przyrost również nie odbiera budżetu
większego pipeline.
[Temporalne połączenie 2.0](stockout-temporal-storage.md) dodaje osobny
comparison/membership Parquet i rzeczywiste wejścia treningu z cech 2.2,
upstream 2.0 i etykiet 2.0. Zachowuje v1, limit 10 000 kluczy i 16 MiB
kanonicznego development; cały wybrany development nadal mieści się
w pamięci. Większy profil pozostaje nieodebrany.
[Selekcja upstream 2.1](stockout-upstream-series.md) dodaje globalną
walidację znanych wersji/trasy w SQL i wybiera payload jednej fizycznej
serii. Usuwa wymaganie globalnego panelu Python; limity wejścia i bazy
pozostają, a temporalny adapter 2.0 nadal przyjmuje upstream 2.0.
[Temporal2.1](stockout-temporal-series.md) podłącza upstream2.1 do rzeczywistego
treningu i odtwarza każdego rodzica raz w prywatnym kontekście. Zachowuje
wcześniejsze pakiety i limity. Budżet większego pipeline pozostaje otwarty.
Obowiązują wymiary z
[planu źródła](https://github.com/Oskar-Stachowski/retailops-cloud-native-platform/blob/08639e9188badb352ed64686a088fe237badad41/docs/plans/ai/etapy/08-stockout-risk.md)
i [kontraktu profili](https://github.com/Oskar-Stachowski/retailops-cloud-native-platform/blob/08639e9188badb352ed64686a088fe237badad41/docs/plans/ai/kontrakty/profile-i-bramki.md)
w przypiętej rewizji producenta `08639e9`.

## Pomiary i szacunek

Odebrane temporal smoke ma 8 produktów, 2 fizyczne lokalizacje i 102 dni:
1632 bazowych punktów, nie 2448 wierszy selling location/channel.
Plik cech ma **10 249 881 bajtów**, upstream 2 127 982, comparison
1 407 074, etykiety 666 623, split 483 435 i modele 864 342.
W JSON każdego punktu powtarzają się 28 dni historii i pełne lineage.

| Profil | Górna liczba fizycznych origin przed eligibility | Cechy JSON przy obecnych bajtach/punkt |
|---|---:|---:|
| Odebrany temporal smoke | 1 632 | 9,78 MiB, pomiar |
| Przykładowe 32 produkty × 2 lokalizacje × 102 dni | 6 528 | 39,10 MiB, szacunek |
| `ai-dev`: 100 × 3 × 365 | 109 500 | około 656 MiB, szacunek |
| `ai-training`: 200 × 4 × 730 | 584 000 | około 3,42 GiB, szacunek |

To liniowy szacunek pliku cech, nie pamięci procesu, czasu ani sumy danych
generatora. Lifecycle i eligibility zmniejszają rzeczywiste liczby.
Transakcje/ledger mają inne mnożniki; nie oszacowano ich z liczby origin.
Rozkłady, liczba kategorii i długość historii też mogą zmienić bytes/point.

Ograniczenia ścieżek v1 nadal blokują prostą większą generację:

- `stockout.dataset.INPUT_LIMITS`: 500 000 wierszy i 64 MiB dla wejścia
  kwalifikowanego przez ścieżkę etykiet/cech; to limit konsumenta stockout,
  nie ogólne stwierdzenie o maksymalnej wielkości wszystkich snapshotów.
- Label policy: 10 000 okien oraz 100 000 wierszy ledgeru;
  etykiety v1 i split używają limitu JSON metadata 4 MiB. Nowe etykiety
  2.0 mają części Parquet, nadal te same limity wejścia/okien i bounded
  qualification JSON 4 MiB.
- Cechy: 10 000 punktów i 16 MiB. Upstream/comparison i modele mają również
  ograniczony JSON 16 MiB; trening przyjmuje do 10 000 wierszy.
- Ścieżki cech v1/v2.0 ładują pełne tabele faktów do list. Ścieżka 2.1
  strumieniuje je do prywatnej bazy i ogranicza dane jednej serii;
  etykiety 2.0 używają podobnego magazynu prywatnego rodzica, ale
  nowe upstream i temporalny join mają osobne ograniczone magazyny;
  starsze ścieżki pozostają dostępne. Projekcja v1
  przegląda tabele dla każdego origin i każdej z 28 dat. W 2.2 ledger
  ma jeden sort/prefiks na origin, a popyt dzienne grupowanie. Pełne
  lineage i stan wiedzy nadal są odtwarzane przy każdym origin;
  koszt większego pipeline nie musi rosnąć liniowo.

Sześć fitów modeli/kalibratorów z metrykami zajęło 0,466 s na smoke.
Pełne CLI około 99 s głównie odtwarza rodziców; nie jest prognozą czasu
większego datasetu. Najpierw trzeba zmienić przygotowanie danych.

## Projekt wykonania i zakres pozostający

Nowy zapis cech ma limit 16 MiB na część, 64 MiB na cały Parquet i
4 MiB na manifest. Zachowuje limit 10 000 origin oraz dotychczasowe
wejście. Indeks serii ogranicza skany obcych danych; w 2.2 dochodzą
prefiks ledgeru i dzienne indeksy dla okna 28 dni. Stan odtwarzany jest
osobno na granicy wiedzy każdego origin. W 2.1/2.2 główna baza ma limit
128 MiB, cache stron 8 MiB, a wybrana seria najwyżej 20 000 wierszy /
16 MiB kanonicznych payload. To granice składników, nie odbiór całego
scratch/RSS. Pełny reader v2.0 wykonuje dwa ograniczone replay;
2.1/2.2 mają build/verify. Temporalny adapter 2.0 odtwarza cechy 2.2 do
jednorazowego indeksu, czyta labels/upstream po ich pełnym replay i łączy
klucze partiami. Etykiety 2.0 mają osobny iterator labels-only, upstream
2.0 własny reader i globalny panel do ostatniego origin. Adapter wybiera
jedynie dojrzały development do istniejącego treningu; v1 pozostaje
zachowane. Główna baza join ma limit 128 MiB, payload 64 MiB, porcja
jednego rodzaju punktów 16 MiB. Wszystkie te limity są granicami
składników, bez odbioru całego RSS/scratch lub większego profilu.
[Odbiór upstream](../evidence/08-10-stockout-upstream-storage.md),
[partycji](../evidence/08-06-stockout-partitions.md)
i [magazynu](../evidence/08-07-stockout-disk-facts.md), a także
[indeksu historii](../evidence/08-08-stockout-history-index.md) oraz
[partycji etykiet](../evidence/08-09-stockout-label-partitions.md) nie zaliczają
budżetu większego pipeline ani nie zmieniają poniższych warunków.

1. Zachować obecny czytnik i artefakty jako wersję 1.0. Dodać odrębny
   manifest partycji z nowym schema/policy/implementation ID. Manifest
   wiąże rozłączne klucze, wymiary profilu, rodziców i checksums każdej
   partycji; verify wymaga wszystkich partycji bez braków i powtórzeń.
2. Zapis Parquet dzielić po fizycznej serii i czasie, początkowo batch
   256 origin, najwyżej 16 MiB na partycję. Historyczne wiersze i lineage
   przechowywać osobno z referencjami, bez kopiowania całych struktur dla
   każdego punktu. Jawnie weryfikować availability referencji względem origin.
3. Zbudować indeksy po product/stock/date oraz wersjach znanych w origin.
   Ledger liczyć kumulacyjnie; ostatnie 28 dni sprzedaży/coverage utrzymywać
   w ruchomym oknie. Zachować selekcję latest tylko spośród dostępnych
   wersji — globalna najnowsza wersja nie może przepisać historii.
4. Rodziców sprawdzać raz na spójny przebieg. Zweryfikowane referencje
   pozostają przypięte do checksums; zmiana pliku lub niekompletna partycja
   blokuje wynik. Trening pobiera wyłącznie dojrzałe role development,
   a final test nadal nie udostępnia outcomes do doboru modelu.
5. Najpierw porównać nową projekcję z każdym z 1632 starych punktów,
   licznikami i wynikami modeli. Odbiór obejmuje late correction,
   future plan, staleness, overlap splitu i restart częściowego zapisu.
   Sprawdzić brakujące, uszkodzone lub powtórzone partycje oraz powtarzalny replay.
6. Dopiero po zgodności wykonać większy development z prawdziwą nazwą
   profilu. Jeśli potrzeba próby pośredniej, dodać jawnie nowy profil
   producenta; nie nazywać 32 produktów pełnym `ai-training`.

## Proponowany budżet pilota

Przed uruchomieniem nowego większego **pilota**, zapisać w wersjonowanej
konfiguracji: maksymalnie 5 GiB nowego scratch, 1 GiB peak RSS całego drzewa
procesów i 10 minut na przygotowanie. Wymagać co najmniej 35 GiB wolnego
miejsca przed startem, co zachowuje 30 GiB rezerwy przy pełnym zużyciu scratch.
Aktualna potrzeba użytkownika to 50 GiB wolnego miejsca. Dla tej sesji
ma pierwszeństwo: przy budżecie 5 GiB start wymaga co najmniej 55 GiB,
aby po wykorzystaniu całego scratch pozostało 50 GiB. Ta rezerwa nie
kwalifikuje większego profilu ani nie zatwierdza jego generacji.
Limity dotyczą pilota, nie stanowią deklaracji wykonalności `ai-dev` ani
`ai-training`. Jeśli oszacowane wejście przekracza budżet, nie zaczynać runu.
Monitorować i przerwać własny izolowany proces przy przekroczeniu;
nie usuwać danych innej sesji w celu kontynuowania.

Raport musi mierzyć wall/CPU, jednoczesne peak RSS drzewa, bytes i liczbę
plików wejścia/wyjścia, rows/s, największą partycję oraz wolny dysk
przed/po. Importy i replay rodziców są częścią całego pipeline; sam fit
ma osobny timer. Brak pomiaru to `not_ready`, nie zaliczenie zasobów.
Pełny profil dostanie osobny budżet na podstawie pilota, przed generacją.

Większa próba ma rozliczyć obie klasy i liczebności kategorii/lokalizacji,
bez przyjmowania z góry, że więcej produktów automatycznie zaliczy bramki.
Scenariusze/seedy, split, metryki i końcowe progi odbioru należy zamrozić
przed final testem. Ten dokument nie zatwierdza promocji ani progów serving.
