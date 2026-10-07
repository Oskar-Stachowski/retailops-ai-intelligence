# AI 08.6 — odbiór partycji cech

Zakres obejmuje odrębny zapis v2 i konserwatywny indeks faktów PIT.
[Kontrakt](../reference/stockout-partitions.md) zachowuje projekcję v1.
Większego profilu nie uruchomiono; cały AI 08 pozostaje **not ready**.

Odtworzono odrębne środowisko po usunięciu wcześniejszych katalogów tmp.
Producent jest przypięty do `08639e9188badb352ed64686a088fe237badad41`;
próbka nadal ma prawdziwą nazwę `ai-temporal-smoke`, seed 42, 102 dni,
8 produktów i 2 fizyczne lokalizacje. Source, qualification i curated
odtworzyły wcześniejsze ID. Pliki odtworzonych wejść mają własny świeży
fingerprint; nie przypisujemy im dawnych fingerprintów usuniętych katalogów.

## Zapis i zgodność

| Pomiar | Wynik |
|---|---:|
| Wszystkie punkty | 1 632 |
| Eligible / już brak / niepełne dane | 1 104 / 432 / 96 |
| JSON cech v1 | 10 249 881 B |
| Bundle v2, razem z manifestem | 1 546 444 B |
| Części fizycznych serii | 16 |
| Pliki, razem z manifestem | 49 |
| Największa część, trzy pliki Parquet | 101 249 B |

Bundle zajmuje około 85% mniej miejsca. Odbiór odczytuje rzeczywiste
Parquet, rozwiązuje referencje historii i lineage, waliduje kontrakt i
porównuje każde pole wszystkich 1632 punktów. Logiczny hash punktów jest
identyczny z v1. Build/rebuild/verify natywny i odłączony zainstalowany wheel
odtworzyły identyczne ID oraz wszystkie bajty. Pliki mają tryb 0600,
katalogi zapisu 0700. Publiczna ścieżka nie importuje producenta ani truth.

## Kontrole

30 nowych testów obejmuje pełny roundtrip, odwróconą kolejność origin,
spóźnione korekty, każdą granicę czasu wiedzy, przyszły business date,
inne fizyczne serie, stare snapshoty, przyszłe plany, ambiguity oraz
niezmienność indeksu. Brakujące, dodatkowe, uszkodzone i ponownie
zahashowane części, powtórzony zakres i nieistniejąca referencja są
odrzucane. Limity punktów, części, bundle i manifestu blokują publikację.
Przerwany staging nie publikuje wyniku, ponowienie daje pełny wynik,
konflikt nie nadpisuje wcześniejszego zapisu. Plik zmieniony po verify
jest ponownie sprawdzany przed udostępnieniem pierwszego punktu.

Regresja cech, splitu i treningu ma 83/83 testów. Jeden warning z sandboxa
dotyczył odczytu liczby fizycznych CPU przez joblib; nie dotyczył wyników.
Pełna regresja ma **1883/1883 testów w 1799,02 s**, bez ostrzeżeń.
Ruff/format sprawdził 582 pliki, mypy 349 modułów. Pełny `make ci-local`
zakończył się z exit 0: wszystkie 21 targetów `check`, pakiet, Compose
config oraz oba skany sekretów przeszły. Końcowa aktualizacja samych
receipt/dokumentacji ma ponowne kontrole dokumentacji i sekretów;
testowany kod pozostał niezmieniony. Nowy commit wymaga własnego Required
CI w draft PR #14; zielone CI poprzedniego SHA nie zastępuje tej kontroli.

## Modele i zasoby małej próby

Wszystkie sześć modeli i wyników development odtworzonych po rzeczywistym
roundtrip jest identyczne ze ścieżką v1. Zachowany development ID to
`development-sha256-18e71a00d92d1f5b22af4df603e01d8084155e61aa4e30102402db0d25a6f462`.
Nie oceniono outcomes final testu. Etykiety, upstream i split zbudowano
dotychczasową ścieżką; nie są uznane za nową ścieżkę partycjonowaną.

| Operacja | Wall | CPU | Próbkowany peak RSS drzewa |
|---|---:|---:|---:|
| Build v1 z replay curated | 35,640 s | 35,647 s | 272,4 MiB |
| Build bundle v2 z replay curated | 30,280 s | 30,197 s | 280,0 MiB |
| Rebuild v2, reuse | 30,122 s | 30,037 s | 278,0 MiB |
| Verify v2 | 36,524 s | 32,063 s | 278,3 MiB |
| Projekcja v1 na tych samych sealed faktach | 20,800 s | 20,801 s | 313,0 MiB |
| Projekcja indeksowana, razem z budową indeksu | 13,434 s | 13,436 s | 323,1 MiB |

Samo liczenie cech było około 35% krótsze, pełny build około 15% krótszy.
Pomiar wykonywano przy równoległej regresji i z samplerem psutil co 25 ms;
nie jest prognozą czasu innego profilu. Proces zachowywał wcześniejsze
wyniki do porównania, więc liczby RSS nie są niezależnym porównaniem
pamięci dwóch implementacji. Maksimum próbkowanego drzewa całego odbioru
wyniosło 371,2 MiB; nie obejmuje wcześniejszej regeneracji/importu ani
niezależnego procesu CI. Pierwszy pomiar RSS w sandboxie był niedostępny;
zerowe odczyty odrzucono i powtórzono pomiar poza tym ograniczeniem.

Historia w częściach ma 3694 wiersze zamiast 45 696 wystąpień w v1;
lineage 8424 zamiast 17 952. Wyjście wheel jest identyczne, a jego
build/rebuild/verify trwały 31,503 / 32,008 / 27,092 s. Wszystkie
567 wejściowych plików, 47 618 381 B, zachowały fingerprint
`befa58608dd1785f6d124063be5f5dc58f6815a7afea07aff871b162c9c63d93`.
Źródło i import tej samej małej próby odtworzono w 172,090 s;
to osobny timer bez odbioru budżetu całego pipeline. Wolny dysk przy
odbiorze wynosił około 64,6 GiB przed i po. Szczegółowy receipt
znajduje się w [JSON](08-06-stockout-partitions.json).

## Dalszy zakres

Ten przyrost ogranicza rozmiar zapisu i skany obcych serii. Wejściowe
tabele nadal są ładowane w całości w dotychczasowym limicie; ledger i
28 dni historii nadal liczy projekcja v1. Czytnik pełnej weryfikacji ma
dwa przebiegi. Etykiety, split, upstream i trening nadal mają ograniczony
format v1; roundtrip smoke nie jest migracją tych ścieżek.

Pozostają kumulacyjne indeksy ledgeru/ruchome okna, odczyt wejścia w
częściach, przygotowanie pozostałych rodziców i trening z partycji.
Przed większą generacją trzeba przypiąć profil i budżet oraz sprawdzić
resource preflight. Budżet całego większego pipeline nie jest zaliczony
pomiarem małej próbki. Final test pozostaje nieoceniony, model niepromowany.
