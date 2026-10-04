# AI 08.9 — odbiór prywatnych partycji etykiet

[Kontrakt](../reference/stockout-label-partitions.md) dodaje odrębny
manifest etykiet 2.0, prywatną bazę i batch Parquet. Stary JSON v1,
algorytm etykiet, identyfikatory oraz AI 05/v12 pozostają zachowane.
Cały AI 08 pozostaje **not ready**.

## Zgodność tej samej próbki

Użyto istniejącego `ai-temporal-smoke`, seed 42, 8 produktów,
2 fizycznych magazynów i 102 dni, producent `08639e9`.
Źródła nie regenerowano. Odbiór dekoduje rzeczywisty Parquet i porównuje
wszystkie pola z pełnym v1 verify oraz wyniki dwupassowego czytnika.

| Kontrola | Wynik |
|---|---:|
| Wszystkie etykiety | 1 632, identyczne z v1 |
| Evaluable / istniejący brak / nieocenialne | 1 083 / 432 / 117 |
| Dodatnie / ujemne dojrzałe etykiety | 472 / 611 |
| Części / pliki z manifestem | 16 / 17 |
| Największa część | 102 wiersze / 6 708 B |
| Bundle 2.0 z manifestem | 110 484 B |
| Dawny JSON v1 | 666 623 B |
| Wejścia i sześć rodziców v1 | 573 pliki / 63 417 718 B, bez zmian |
| Sześć modeli development i wszystkie wyniki | identyczne z v1 |

Nowy zapis jest o **83,4% mniejszy** od JSON tej próbki. Logical point
hash pozostaje `ea1d4a9b6065cae105f75e4a34216b688cd14cc5d5a276d0996bd46fcc87bcd4`.
Zachowany label ID v1 to
`labels-sha256-a0e221a244cc31809487cf9f09c3e865798bce469883977fa84e7cbdc153ebb0`.
Sześć modeli porównano przez mały istniejący bridge v1 po roundtrip;
pełny development ID pozostaje
`development-sha256-18e71a00d92d1f5b22af4df603e01d8084155e61aa4e30102402db0d25a6f462`.
Wybrany model na tune to nadal provisional HGB bez upstream.
To nie jest migracja treningu do nowego readera i nie ocenia final testu.

## Pomiar i pakiet

Końcowy kod zmierzono w trzech parach buildów w świeżych procesach,
w kolejności v1/2.0, 2.0/v1, v1/2.0. Mediana wall wyniosła
**12.841 → 12.658 s**, CPU **12.721 → 12.435 s**,
a próbkowany peak RSS **112.73 → 102.48 MiB**.
RSS spadł o 9.1%, wall zmienił się o -1.4%, CPU o -2.2%.
Czas na tej małej próbce jest zbliżony; nie jest prognozą większego profilu.

Sampler co 50 ms sumuje jednoczesny RSS drzewa potomnego, bez samplera.
Obie wersje worker importuje przed timerem. Operacja obejmuje pełną
weryfikację prywatnego snapshotu, przygotowanie i zapis; nie obejmuje
generacji/importu ani treningu. Wszystkie końcowe pary wykonano po
normalizacji UTC, przed restartem pełnego lokalnego CI, po zakończeniu
innych własnych odbiorów. Pozostała aktywność komputera i cache systemu
nie były kontrolowane. Świeży proces nie oznacza zimnego cache systemu.
To pomiar próbki, nie kwalifikacja większego profilu lub gwarancja RAM.
Główna baza miała 13 783 040 B i 10 318 wierszy faktów; największa seria
ledgeru 1547 wierszy / 804 487 B. Scratch był usuwany po kontekście.

Odłączony wheel końcowego kodu UTC wykonał build/rebuild/verify w
20,864 / 16,571 / 16,860 s przy równoległym CI. Manifest, ID i bajty
wszystkich plików były identyczne z native, pliki miały 0600. Dwupassowy
iterator zwrócił identyczne etykiety. Wszystkie 19 modułów konsumenta
pochodziły z zainstalowanego pakietu; producent nie był importowalny.
Wejścia i sześć dawnych rodziców pozostały bez zmian. Czasy wheel
nie służą do porównania wydajności.

## Kontrole i granice

Przyrost ma **31 nowych przypadków**: pełny roundtrip wszystkich pól,
stare ID, private opt-in/public rejection, limity wejścia i wybranej serii,
ścisłą policy, prywatne prawa i sprzątanie scratch, resealed label/range/seal/
report/version, missing/extra/duplicate parts przed pierwszym punktem,
zmianę payload/index, zmianę faktów i kwalifikacji podczas odczytu,
limity wyjścia, przerwany zapis/retry, immutable konflikt i zmianę między
verify a iteracją oraz UTC `Z`/`+00:00` i granice mikrosekund. Reguły label_window pozostają objęte testami v1.
Ukierunkowane label/split/training mają **98/98 zaliczeń w 33,01 s**,
bez ostrzeżeń. Pełny `make ci-local` zakończył się z exit 0: **1981/1981 testów
w 1668.92 s**, bez ostrzeżeń, Ruff/format 607 plików, mypy
363 modułów, wszystkie 21 targetów check, pakiet, Compose config
i oba skany sekretów. Końcowa aktualizacja samych dowodów ma ponowne
kontrole dokumentacji i sekretów; testowany kod pozostał niezmieniony.

[Receipt JSON](08-09-stockout-label-partitions.json) wiąże rodziców,
policy, implementację, limity, pomiary i wyniki odbioru.
Poprzedni `b74fa9a` ma zielone Required CI PR i push, odpowiednio
[37187866403](https://github.com/Oskar-Stachowski/retailops-ai-intelligence/actions/runs/37187866403)
i [37187864436](https://github.com/Oskar-Stachowski/retailops-ai-intelligence/actions/runs/37187864436). Nowy commit również wymaga własnego CI
w draft PR #14.

Kwalifikacja pozostaje bounded JSON 4 MiB. Weryfikator rodzica i pomocnicze
bazy sum logicznych zachowują własne scratch; limit głównej bazy nie
zalicza całego RSS/scratch. Odtworzenie okna nadal używa algorytmu v1.
Nowy iterator udostępnia jawnie prywatne etykiety, bez wyboru ról treningu.
Upstream, comparison, split i trening nadal korzystają z v1; potrzebują
odrębnych ograniczonych czytników oraz odbioru temporalnego połączenia.
Większej generacji nie uruchomiono. Profil, pełny budżet zasobów,
niezależna ocena, progi i lifecycle/batch/read API pozostają otwarte.
