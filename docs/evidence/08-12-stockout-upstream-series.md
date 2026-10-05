# AI 08.12 — odbiór selekcji upstream po fizycznej serii

[Kontrakt](../reference/stockout-upstream-series.md) dodaje osobny pakiet
`stockout_upstream_series` i manifest 2.1. Pełna walidacja znanych wersji
oraz efektywnych tras odbywa się w SQL przed fizycznym filtrem. Do Python
trafiają payload jednej serii oraz osobny ograniczony cache dekodowania.
Stare pakiety/ID, AI 05 i finalne v12 pozostają zachowane.
Cały AI 08 pozostaje **not ready**.

## Zgodność i zakres

Ponowiono istniejący `ai-temporal-smoke`, seed 42, 8 produktów,
2 fizyczne lokalizacje i 102 dni, producent `08639e9`, bez regeneracji.
Wszystkie pola **1632 prognoz**, selling-series lineage, comparison
oraz pełny development sześciu modeli są identyczne z v1.
Report zachowuje 1380 available / 252 insufficient_data,
1104 base eligible / 1015 z prognozą.

Comparison i modele porównano przez jawny bridge v1 w helperze odbioru.
To dowód zgodności liczbowej, **nie** integracja treningu z nowym parent ID.
Temporalny adapter 2.0 nadal korzysta z upstream 2.0; jego odebrane
artefakty i rzeczywisty trening pozostają zachowane. Końcowy test nie jest
oceniony, progi nie są zatwierdzone, model nie jest promowany.

Nowy ID to `upstream-partitions-sha256-00706e367ea7f92dd81865e36fb426b8b7b44587cbe029f4fe5c4c0021c51ef5`.
Logical hash jest taki sam jak upstream 2.0 i v1 w porządku chronologicznym.
Native build/rebuild/verify i reader przeszły. Odłączony wheel wykonał
CLI build/rebuild/verify oraz reader; manifest i wszystkie pliki są
identyczne bajtowo z native. Wszystkie 50
modułów konsumenta pochodzi z instalacji, producent nie jest importowalny.
573 wejścia i rodzice v1 / 63 417 718 B zachowały fingerprint.

## Selekcja i pomiary małej próbki

| Wielkość | Globalny upstream 2.0 | Selekcja upstream 2.1 |
|---|---:|---:|
| Maksymalna liczba wybranych rekordów | 3625 | 198 |
| Maksymalny kanoniczny payload selekcji | 4,166,485 B | 247,260 B |
| Mediana pełnego build z replay cech | 127.23 s | 149.12 s |
| Mediana CPU świeżego procesu | 114.61 s | 127.63 s |
| Mediana peak RSS świeżego procesu | 127.17 MiB | 125.83 MiB |

Selekcja ma o **94.5% mniej rekordów** i **94.1% mniej
kanonicznych bajtów**. Osobny decoded cache osiągnął
957 rekordów / 1,048,576 B,
w granicach 1024 / 1 MiB; nie jest globalnym panelem ani cache decyzji
availability. Globalnie zweryfikowano 102 origin, wykonano 1632 selekcje.

Trzy pary korzystały z tych samych wejść, świeżych procesów i naprzemiennej
kolejności, przy równoległym własnym CI/odbiorze. Wszystkie wyniki mają
identyczny logical hash. Zmiana mediany czasu wynosi +17.2%.
Nie wykazano przyspieszenia ani istotnego zmniejszenia całego RSS na smoke.
Nowa ścieżka usuwa wymaganie globalnego panelu Python; nie kwalifikuje
większego profilu, czasu większej próby ani budżetu całego pipeline.
RSS jest `ru_maxrss` jednego procesu, bez sumowania drzewa procesów.
Własne wyjścia pomiarowe były usuwane po każdym przebiegu; dysk zachował
wymaganą rezerwę 50 GiB. Receipt zapisuje również wolny dysk przed/po.

Nowy bundle ma 1,104,817 B / 103 pliki,
102 części o najwyżej 16 punktach i 10,931 B.
Parquet ma identyczne bajty jak 2.0; dodatkowy seal i limity cache
zmieniają manifest/ID, bez zmiany wartości.

## Kontrole

Regresja ukierunkowana: **118/118**, w tym **48 nowych** przypadków.
Obejmuje obce remisy wszystkich dziewięciu tabel, remisy tras w kolejności
źródła, zmianę magazynu, dwukanałowe pooling, granice mikrosekund,
znane przyszłe kalendarze, brak danych, zachowanie błędów horyzontu 14,
cofnięcie origin po zbuforowaniu późniejszej rewizji, izolację słowników,
eviction i wyłączenie cache, jednorazowy odczyt schema, limity, cleanup,
immutable retry, resealed mutation i zmiany części/rodziców między porcjami.

Końcowy pełny `make ci-local` ma **exit 0: 2085/2085 testów**,
0 ostrzeżeń, 2339.55 s samych testów.
Wszystkie 21 targetów check, pakiet, Compose config i oba skany sekretów
przeszły. Kontrole portu localhost i własnych statystyk procesów otrzymały
wymagane uprawnienia; żaden test ani limit nie został pominięty lub osłabiony.
Dwa wcześniejsze pełne przebiegi prototypów przerwano przed publikacją
po pomiarach wydajności; nie stanowią odbioru obecnego kodu.
Końcowe dowody mają ponowne kontrole dokumentacji i sekretów.
Native helper zgłosił fallback joblib przy blokowanym odczycie fizycznych
rdzeni CPU w sandboxie; wątki estimatora pozostały 1, wyniki modeli identyczne.
[Receipt](08-12-stockout-upstream-series.json) wiąże kod, dane, policy i log hashes.

Rodzic `df61862` ma zielone Required CI
[PR](https://github.com/Oskar-Stachowski/retailops-ai-intelligence/actions/runs/37207588655)
i [push](https://github.com/Oskar-Stachowski/retailops-ai-intelligence/actions/runs/37207584813).
Nowy commit wymaga własnego CI w draft PR #14.

## Pozostały zakres

Pozostają podłączenie upstream 2.1 do rzeczywistego temporalnego treningu
z nowymi parent IDs oraz spójny przebieg ograniczający nadmiarowy replay.
Nadal obowiązują wejście 64 MiB / 500 000 wierszy, baza 128 MiB,
qualification JSON 4 MiB, 10 000 kluczy i 16 MiB development w pamięci.
Dalej wymagane są jawny większy profil, preflight/pomiary całego RSS/scratch,
karta z nowych rodziców, niezależna ocena jakości, zatwierdzone progi
oraz lifecycle/batch/read API. Żaden wyjątek jakości nie został dodany.
