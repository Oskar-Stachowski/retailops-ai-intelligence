# AI 08.8 — odbiór indeksu historii

Przyrost zastępuje wielokrotne sortowanie ledgeru indeksem prefiksu oraz
grupuje znane wersje popytu po dniu. [Kontrakt](../reference/stockout-history-index.md)
wiąże odrębny pakiet z manifestem 2.2; stare pakiety i identyfikatory
pozostają zachowane. Cały AI 08 pozostaje **not ready**.

## Próbka i zgodność

Użyto istniejącego `ai-temporal-smoke`, seed 42, 102 dni, 8 produktów
i 2 fizycznych lokalizacji. Producent pozostaje przypięty do `08639e9`;
źródła nie regenerowano. Odbiór czyta rzeczywisty Parquet, rozwiązuje
referencje historii/lineage i porównuje wszystkie pola 1632 punktów.

| Kontrola | Wynik |
|---|---:|
| Punkty | 1 632, identyczne z v1 |
| Eligible / istniejący brak / brak danych | 1 104 / 432 / 96 |
| Parquet | dokładne bajty identyczne z 2.1 |
| Części / pliki | 16 / 49 |
| Bundle 2.2 z manifestem | 1 553 889 B |
| Sześć modeli i wszystkie wyniki development | identyczne z v1 |
| Wejścia i sześć rodziców v1 | 573 pliki / 63 417 718 B, bez zmian |

Logiczny hash punktów nadal wynosi
`4a55767d14681273301dbe910262cac93b8a685d0aaeba45f4de91a167ce8b40`.
Zachowany development ID to
`development-sha256-18e71a00d92d1f5b22af4df603e01d8084155e61aa4e30102402db0d25a6f462`;
wybór na tune pozostaje provisional HGB bez upstream. Modele są
odtwarzane z rzeczywistych punktów po roundtrip oraz w pełnym dawnym
CLI verify rodziców. Nie jest to nowy reader treningowy i nie obejmuje
oceny outcomes final testu. Stare native ID v1 i bundle 2.1 są zachowane;
nowy bundle 2.2 przypina osobny kod indeksu i montażu punktu.

## Profilowanie i pomiar świeżych procesów

Dawna projekcja wywoływała `inventory_day` i sortowanie ledgeru
45 696 razy. Nowa wywołuje zapytania dzienne tyle samo razy, ale tworzy
1632 indeksy: jeden sort/prefiks i jedną mapę dni na origin. Weryfikacja
liczników używa cProfile; jej czas przy równoległym CI nie służy do
porównania wydajności.

Zmierzono trzy buildy każdej wersji, każdy w świeżym procesie, w
kolejności 2.1/2.2, 2.2/2.1, 2.1/2.2. Sampler psutil co 50 ms sumuje
jednoczesny RSS drzewa potomnego, bez zewnętrznego samplera. Pełne lokalne
CI rozpoczęto po wszystkich pomiarach. Aktywność innych sesji komputera
nie była kontrolowana; świeży proces nie oznacza zimnego cache systemu.

| Mediana trzech buildów | 2.1 | 2.2 |
|---|---:|---:|
| Wall operacji | 21,317 s | 18,416 s |
| CPU operacji | 20,574 s | 17,714 s |
| Próbkowany peak RSS drzewa | 131,75 MiB | 131,98 MiB |

Mediana wall spadła o **13,6%**, CPU o **13,9%**, a RSS wzrosła
o około **0,23 MiB**. Wall poszczególnych buildów wynosił 20,063–21,340 s
dla 2.1 i 17,508–18,530 s dla 2.2. Najwyższy próbkowany build peak
wynosił odpowiednio 131,88 i 133,75 MiB. To wynik tej samej małej próbki,
nie prognoza większego profilu ani gwarancja limitu RAM.

Operacja obejmuje pełną weryfikację curated, pieczętowanie faktów,
bazę/indeksy i przygotowanie części. Timer operacji pomija start Pythona;
receipt podaje również wall ze startem i CPU całego procesu. Osobny
rebuild 2.2 dał reuse w 17,407 s, verify w 17,901 s, oba z identycznym ID.
Nie obejmuje generacji/importu, etykiet, upstream, splitu, treningu ani
osobnego dawnego bridge porównania modeli. Nie zalicza budżetu całego
większego pipeline. Indeks mieści się w dotychczasowym ograniczonym
widoku origin; bazowy magazyn nadal ma 13 942 wiersze, 25 575 424 B
i jest usuwany po kontekście. Limity komponentów nie zostały podniesione.

## Pakiet i kontrole

Odłączony wheel wykonał build/rebuild/verify w 21,770 / 21,955 / 21,779 s
przy równoległym CI. Manifesty, ID i bajty wszystkich plików są identyczne
z native, pliki mają 0600. Wszystkie 33 moduły konsumenta pochodziły
z zainstalowanego pakietu; producent nie był importowalny. Wejścia oraz
sześć rodziców pozostały bez zmian także po odbiorze wheel.

37 nowych przypadków obejmuje 80 deterministycznych ledgerów, każdy
porównany z v1 dla 31 dat, oraz opening/coverage i granice północy.
Testy kontrolują kolejność identycznych czasów, chwilowe zera/onsets,
ujemny bilans, brak coverage, rewizje popytu, brakujące/zerowe obserwacje,
mapped stock i niejednoznaczność wersji. Spóźniona korekta jest sprawdzana
w obu przypadkach: dzień zostaje w oknie lub wypada z niego po zmianie
origin. Zachowane są wszystkie granice wiedzy, pełne lineage, policy,
natywne punkty w odwrotnej kolejności i stare ID. Odbiór części odrzuca
resealing, braki/dodatki/powtórzenia, zmianę pieczęci indeksu, przekroczenie
limitów, przerwany zapis i immutable konflikt; retry jest kompletny.

Testy ukierunkowane mają **150/150 zaliczeń w 121,95 s**, w tym 37 nowych,
bez ostrzeżeń. Pełne `make ci-local` zakończyło się z exit 0:
**1950/1950 testów w 1798.03 s**, bez ostrzeżeń, Ruff/format
599 plików, mypy 358 modułów, wszystkie 21 targetów `check`, pakiet,
Compose config i oba skany sekretów. Końcowa aktualizacja samych dowodów
ma ponowne kontrole dokumentacji i sekretów; testowany kod pozostał
niezmieniony. [Receipt JSON](08-08-stockout-history-index.json)
wiąże pomiary, fingerprint, policy, wersje i granice odbioru.
Nowy commit wymaga własnego Required CI w draft PR #14.
Poprzedni `858abcf` ma zielone Required CI PR i push, odpowiednio
[37184775977](https://github.com/Oskar-Stachowski/retailops-ai-intelligence/actions/runs/37184775977)
i [37184773597](https://github.com/Oskar-Stachowski/retailops-ai-intelligence/actions/runs/37184773597).

## Zakres pozostający

Indeks powstaje osobno na granicy wiedzy każdego origin. Nie jest
cache globalnej najnowszej rewizji ani incremental cache między origin.
Etykiety, upstream, split i trening nadal przyjmują v1. Większej
generacji nie uruchomiono, final test nie jest oceniony, progi
niezatwierdzone i model niepromowany. Nie zmieniono AI 05/v12.
Pozostają partycjonowanie pozostałych rodziców i treningu, freeze
rzeczywistego profilu z preflight zasobów oraz niezależna ocena,
progi i lifecycle/batch/read API. Incremental reuse historii między
origin jest możliwym dalszym usprawnieniem, z własnym odbiorem PIT.
