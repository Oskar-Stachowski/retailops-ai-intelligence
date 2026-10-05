# AI 08.10 — odbiór ograniczonego upstream

[Kontrakt](../reference/stockout-upstream-storage.md) dodaje osobny pakiet,
panel faktów na dysku i chronologiczne części Parquet 2.0, przypięte do
pełnego replay cech 2.2. Stare pakiety i identyfikatory, AI 05 oraz v12
pozostają zachowane. Cały AI 08 jest **not ready**.

## Ta sama próbka, wszystkie wartości

Odbiór ponawia istniejący `ai-temporal-smoke`, seed 42, 8 produktów,
2 fizyczne magazyny i 102 dni, producent `08639e9`. Nie regenerowano źródła.
Rzeczywisty Parquet oraz dwupassowy iterator zachowują wszystkie pola
1632 prognoz po porównaniu po kluczu fizycznym, w tym selling-series
lineage, codzienne wartości i nullable powody braku danych.

| Kontrola | Wynik |
|---|---:|
| Wszystkie punkty upstream | 1 632, identyczne z v1 |
| Prognoza dostępna / niewystarczające dane | 1 380 / 252 |
| Eligible z prognozą / wszystkie eligible | 1 015 / 1 104 |
| Części / pliki z manifestem | 102 / 103 |
| Największa część | 16 wierszy / 10 931 B |
| Nowy bundle z manifestem | 1 103 923 B |
| Dawny JSON v1 | 2 127 982 B |
| Wejścia i sześć rodziców v1 | 573 pliki / 63 417 718 B, bez zmian |
| Comparison i sześć modeli development | pełne artefakty identyczne z v1 |

Zapis jest o **48,1% mniejszy**. Nowy logical hash odnosi się do kolejności
chronologicznej; v1 pozostaje w kolejności product/stock/origin.
Hash wszystkich punktów po sortowaniu v1 nadal wynosi
`fab2ef43890e4ee270fd5d4f7e954c4387ec1b1f8e134535cb58ec1dac84bd3e`.
Zachowany ID v1 to
`upstream-sha256-a56a4fb27af6d68f6f9bd77142733a5d6c89e483596faa5c98bd19f6e8c2cd17`.
ID cech 2.2 to
`feature-partitions-sha256-4f16903728474a85b4eada9efe990bc0c87c6154829369fd9b047652c90981af`.

Mały bridge po rzeczywistym roundtrip odbudował dokładnie comparison
i cały development ID
`development-sha256-18e71a00d92d1f5b22af4df603e01d8084155e61aa4e30102402db0d25a6f462`.
Wybrany na tune model nadal jest provisional HGB bez upstream. To dowód
zgodności tej próbki, bez podłączenia nowego readera do treningu lub oceny
final testu. Poniższe pomiary nie zmieniają jakości prognozy.

## Pomiary, pakiet i kontrole

Trzy pary końcowych buildów w świeżych procesach, w kolejności
v1/2.0, 2.0/v1, v1/2.0, dały medianę wall **76,135 → 55,739 s**,
CPU **75,848 → 55,486 s** i próbkowanego peak RSS
**204,83 → 137,92 MiB**. To -26,8% czasu, -26,8% CPU i -32,7% RSS.
Obie ścieżki obejmują pełny replay rodzica cech, weryfikację curated,
przygotowanie prognoz i zapis. V1 odtwarza stare cechy, nowa ścieżka cechy
2.2; wynik zawiera korzyść wcześniejszej optymalizacji cech i nie jest
izolowanym pomiarem nowego algorytmu upstream.

Sampler co 50 ms sumuje jednoczesny RSS drzewa potomnego bez samplera.
Obie implementacje worker importuje przed timerem. Pełne CI trwało przez
wszystkie pary; pierwsza para nakładała się także na odbiór wheel.
Pozostała aktywność komputera i cache systemu nie były kontrolowane.
Świeży proces nie oznacza zimnego cache. Pomiar nie obejmuje generacji,
importu ani treningu i nie kwalifikuje większego profilu.
Wszystkie sześć zmierzonych wyjść ma bajty identyczne z odebranym
JSON v1 lub native bundle; 573 wejścia zachowały fingerprint również
po pomiarach. Główna baza miała 3646 wierszy / 6 983 680 B; największy
odczyt panelu 3625 wierszy / 4 166 485 B payload. Scratch usunięto.

Odłączony wheel wykonał build/rebuild/verify w 56,09 / 63,00 / 62,23 s
przy równoległych własnych kontrolach. Wszystkie pliki, manifest i ID są
identyczne z native, mają prawa 0600; pełny iterator jest zgodny z v1.
Wszystkie 48 modułów konsumenta pochodziło z zainstalowanego pakietu,
producent nie był importowalny, a wejścia zachowały fingerprint.
Te czasy wheel nie służą do porównania wydajności.

Pełna walidacja lokalna jest zaliczona w dwóch częściach: 1949 przypadków niezmienionych modułów przed kolejką oraz wszystkie 61 przypadków zmienionego modułu kolejki i pozostałego runtime, razem **2010/2010**, bez ostrzeżeń. Wszystkie 21 wymaganych targetów Makefile są rozliczone, pakiet i Compose config przeszły, oba skany sekretów są czyste.

Pierwszy `make ci-local` nie miał exit 0: po 1959 zaliczeniach test maksymalnego scope kolejki v12 wyczerpał własny rzeczywisty budżet 120 s i zwrócił `lease_lost`. Zatrzymano wyłącznie własny nieudany przebieg. Test podziału wyniku dostał kontrolowany zegar workera; dodano osobne przypadki wygaśnięcia czasu podczas predykcji i odnawiania fence między częściami. Zmieniono tylko testy, bez podnoszenia produkcyjnego limitu 120 s lub limitów rzeczywistego predictora. Pełny moduł kolejki i cała pozostała końcówka regresji przeszły w 1312,67 s. Nie powtarzano zaliczonych, niezmienionych modułów. Receipt zachowuje błąd pierwszej próby i oddzielne logi; nie przedstawia tego jako pojedynczego udanego `make ci-local`.

Końcowa aktualizacja samych dowodów ma ponowne kontrole dokumentacji i sekretów. Końcowy kod jest zamrożony; wcześniejsza wersja
odczytująca panel dla każdego dnia oraz jej przerwane próby nie są
dowodem odbioru.

Ukierunkowane testy mają **80/80 zaliczeń w 56,34 s**, w tym 27 nowych
przypadków, bez ostrzeżeń. Obejmują pełną zgodność i lineage, wszystkie
tabele na dokładnej granicy wiedzy, późne korekty i znane przyszłe plany,
globalną niejednoznaczność tras, limity bazy/panelu/wyjścia, prywatny
scratch i jego sprzątanie, payload/index mutation, zmiany wejścia,
missing/extra/resealed output i zmianę rodzica lub części pomiędzy replay
a iteracją. Dawne reguły baseline pozostają objęte testami v1.

[Receipt JSON](08-10-stockout-upstream-storage.json) wiąże rodziców,
policy, kod, limity i bieżące wyniki odbioru. Poprzedni `d083e0a` ma
zielone Required CI PR i push:
[37191974760](https://github.com/Oskar-Stachowski/retailops-ai-intelligence/actions/runs/37191974760)
oraz [37191972365](https://github.com/Oskar-Stachowski/retailops-ai-intelligence/actions/runs/37191972365).
Nowy commit również wymaga własnego CI w draft PR #14.

## Pozostały zakres

Panel jest ograniczony do 20 000 wierszy / 16 MiB i zachowuje wszystkie
serie, aby nie ukryć obcej niejednoznaczności. Większy profil może nadal
przekroczyć ten limit. Pełny RSS/scratch nie jest odebrany przez limit
głównej bazy; weryfikatory rodziców używają własnych pomocniczych baz.
Comparison, split i trening nadal przyjmują v1, a reader upstream nie
wybiera ról development. Pozostają ich ograniczone czytniki i temporalne
połączenie, selekcja upstream po seriach z globalną walidacją, większy
profil i budżet całego pipeline, niezależna ocena, progi oraz
lifecycle/batch/read API. Żaden nowy wyjątek jakości nie został dodany.
