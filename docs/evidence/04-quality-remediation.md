# AI 04 — korekta blokad jakości

Data: 2026-09-30. Branch `ai/04-01-task-calendar`; implementacja `cdab752`,
kontrola próbek `6664da9`, niezmienność progów i test baseline'u `8e48300`,
zamrożona końcowa kampania z dwiema lokalizacjami `18bf9ec`,
konserwatywna kontrola źródła przed materializacją cech `1477cb6`.
[Receptura i polecenia](../forecast-remediation.md) opisują dopasowanie wyłącznie
na validation, niezmienne progi oraz niezależny replay.
[Wyniki maszynowe](04-quality-remediation.json) przypinają rodziców, kod,
checksumy, metryki i każdą niezaliczoną bramkę.

## Pomiar na dotychczasowych danych rozwojowych

Nowa receptura dzieli validation po originach: pierwszy blok dopasowuje
korektę wolumenu/kategorii, drugi wybiera metodę i kalibruje przedziały z reszt
ze znakiem. Zachowuje oryginalny baseline, gdy poprawa wyboru jest niewystarczająca.
Nie zmienia oryginalnych modeli, memberships, etykiet ani artefaktu 04.8.
Poniższe dane były już oglądane; wynik opisuje poprawę diagnostyczną, a nie
niezależne dopuszczenie modelu.

| Bramka / pomiar holdoutu | Przed korektą | Po korekcie | Wymaganie |
|---|---:|---:|---|
| Wszystkie bramki, obie role | 145 passed / 79 failed / 8 not_ready | 192 passed / 32 failed / 8 not_ready | wszystkie passed |
| Pooled MAE | 1,442420 | 1,358817 | >5% poprawy wobec zamrożonego baseline'u 1,558457 |
| Drugi fold, zmiana MAE względem baseline'u | +1,18% | −7,32% | poprawa >5% |
| Wysoki wolumen, pokrycie przedziałów | 69,37% | 92,62% | ≥80% |
| Niski wolumen, szerokość / średnie actuals | 3,514 | 1,024 | ≤2 |
| Niski wolumen, zmiana MAE względem baseline'u | +28,63% | +13,21% | ≤10% regresji |
| Historyczny wolumen zero | brak próbki | brak próbki | co najmniej 30 eligible rows |

Pooled holdout ma 5400 kluczy, WAPE 0,148378, normalized bias −2,61% i
coverage 89,87%. Globalne metryki nie zamykają krytycznych segmentów.
Pozostałe przyczyny failed: bias (17), szerokość przedziału (16), regresja
MAE segmentu (11), pokrycie (4); przyczyny mogą współwystępować w bramce.
Osiem `not_ready` wynika z braku koszyka zero. Nie poluzowano żadnego
oryginalnego progu. **Status jakości pozostaje `not_ready`.**

Artefakt:
`forecast-remediation-sha256-5467f0d1906c4c1083b6fe3d33e3a0f3e093736cdce618593dcd2467d62099f0`.
Odtworzenie z feature/backtest parentów potwierdziło ten sam wynik, a CLI
zwróciło exit 3, oznaczający kompletny raport z blokadami.

## Późniejsze dane

Pierwsze trzy kampanie przypinają producenta source 2.7 do
`d4ee7a4e4217a59871c654f204f16de74017d13b`.
Import pierwszego zestawu z 12 produktami przeszedł: 43 tabele, 133354 wiersze,
bez evaluation truth i quarantine, inventory source gotowe. Cechy nie miały
koszyków zero ani high. Uruchomiony wcześniej backtest został przerwany bez
publikacji końcowego artefaktu i bez obejrzenia metryk nowych holdoutów.

Dodana kontrola liczy origin-known cechy osobno dla każdego folda i roli,
bez czytania target labels. Brak wymaganej próby blokuje kolejne treningi.
[Kampania v2](../../contracts/forecast/v1/quality-remediation.campaign-v2.json)
zwiększa przekrój do 24 produktów, bez zmiany seeda. Rolling train 14 dni
zachowuje limity zasobów; validation i holdout mają po 14 dni, purge 15.
Holdouty: 29 lipca–11 sierpnia, 12–25 sierpnia, 26 sierpnia–8 września 2026.
Konfiguracja i receptura zostały zamrożone przed oceną ich wyników.

Wstępna kontrola source v2 wykazała 4133 origin-series low, 2169 medium i 298
high, bez zero. To historyczne obserwacje, bez oceny target outcomes, nie
eligible feature counts. Eksport zatrzymano przed publikacją snapshotu;
kopia manifestu i historii wraz z checksumą zachowuje tę diagnostykę.

[V3](../../contracts/forecast/v1/quality-remediation.campaign-v3.json) użyła
standardowego przekroju 100 produktów `ai-dev` z jednym sklepem i tym samym
seedem 42. Nie oglądano metryk forecastingu. Weryfikacja źródła wielokrotnie
przeszukiwała pełną historię dla każdej pozycji, więc ten przebieg zatrzymano
przed publikacją i powtórzono identyczne parametry danych jako
[V4](../../contracts/forecast/v1/quality-remediation.campaign-v4.json).

Producent V4: branch `ai/04-quality-source-verification`, commit
`9115c1de2fde4dad6b37f1370f1b7a6d9c45601a`. Indeksy pozycji w reorder,
snapshotach i oknach diagnostycznych zachowują kolejność wejścia, wszystkie
cutoffy oraz oryginalne kontrole. Regresja źródła: **207 passed / 26,91 s**,
w tym ograniczenie liczby przeglądanych pozycji i zgodność z pełnym skanowaniem.
Na kontrolnym `ai-temporal-smoke` seed 41, 16 produktów, 60 dni, zakończenie
1 czerwca 2026, wszystkie 58 table identities i kontekst czasowy są identyczne.
Pomiar całego builda: 10,86 s przed i 8,05 s po zmianie. To kontrola parytetu
producenta, nie evidence jakości forecastingu.

Weryfikator snapshotu V4 i producent V5: branch `ai/04-quality-source-orders`,
commit `f0cfa92dc0c7f4748be70c4a20c39c0e0eb0d846`, zawierający poprzednią zmianę.
Indeks znanych planów i przyjęć w `orders_at` zachowuje cutoffy, kolejność oraz
statusy. Regresja źródła: **353 passed / 28,37 s**; identyczność 58 tabel i
kontekstu względem pierwotnego producenta potwierdzona ponownie. Zmiany
producenta pozostają lokalne.

V4 zachowała kompletny source z producenta `9115c1d`. Przy kopiowaniu sprawdzono
rozmiary i SHA-256 wszystkich 63 receipts. Pełna kwalifikacja i eksport z
weryfikatorem `f0cfa92` przeszły na tych samych danych, bez regeneracji.
Snapshot `75959d1…` i source `769ee09…` wiąże evidence JSON. Import i curated
przyjęły **315662 wiersze / 43 tabele, 0 quarantine**; `forecast_source`
przeszedł, `inventory_ready=true`.

Formalna kontrola cech V4 wykazała brak high we wszystkich foldach oraz zero
w validation foldów 1 i 2 i w holdoucie folda 3. Pooled zero miało 110
potencjalnie eligible rows validation i 190 holdoutu, więc pooled liczebność
nie rozwiązuje braków poszczególnych okien. **Exit 3, 0 model fits, bez oceny
metryk nowych holdoutów.**

[V5](../../contracts/forecast/v1/quality-remediation.campaign-v5.json) dodaje
drugą lokalizację przy tych samych 100 produktach, seedzie 42 i datach.
Teoretyczne maksimum 39200 train rows na fold pozostaje poniżej oryginalnego
limitu 50000. Nadal wymaga wszystkich krytycznych koszyków przed treningiem.
V5 i V6 zatrzymano przed publikacją source/snapshotu oraz oceną holdoutów:
każda dostawa ponownie wczytywała i normalizowała całą wcześniejszą księgę.
Parametry danych, seed, okna i receptura pozostają te same w
[V7](../../contracts/forecast/v1/quality-remediation.campaign-v7.json).

Końcowy producent: `ai/04-quality-source-quotes`, commit
`4ff2baef7b0262c91b29f2ee9511c1b7024b2db1` (zawiera wszystkie wcześniejsze
indeksy). `34ca6e1` indeksuje produkty, dostawców i oferty danego produktu.
`4ff2bae` weryfikuje pełną historię zamówienia bieżącej dostawy; zachowuje
globalną unikalność receipt ID i timestamp/sequence, cumulative quantity,
zgodność wymiarów, chronologię, pełną kontrolę nowego batcha zamówień oraz
końcową kontrolę całej księgi. Cache normalizacji ma najwyżej 32768
niezmiennych rekordów i nie pomija walidacji nowych wejść.

Regresja końcowego źródła: **559 passed / 49,34 s**. Testy obejmują odrzucenie
duplikatów między zamówieniami i over-receipt bez mutacji stanu oraz brak
ponownego wczytywania niezwiązanych zamówień dla każdej dostawy. Wszystkie
58 table identities i kontekst kontrolnego eksportu są identyczne z
`d4ee7a4`. Czas kontrolny: 6,65 s; nie jest to wynik jakości prognozy.
V7 wygenerowała pełny source
`source-sha256-15bd29634ccb9c1333f08107ed130584defec98db351a492940c4c57a1c29ef9`.
Zachowaną kopię przypina
[V8](../../contracts/forecast/v1/quality-remediation.campaign-v8.json); sprawdzono
rozmiary i SHA-256 wszystkich 63 plików, bez regeneracji ani oceny holdoutów.
Weryfikator snapshotu: `ai/04-quality-source-projection`, commit
`c09aa58d51aa0e4c8337152bac2348c28dbea9dd`, zawierający wcześniejsze zmiany.
Indeksy pozycji ruchów, pierwszej jednostki produktu i strat dla epizodu
zachowują kolejność oraz wszystkie oryginalne kontrole cutoffu, odtworzenia
i agregacji. **560 passed / 61,92 s**, parytet wszystkich 58 tabel i kontekstu
kontrolnego eksportu. Pełna niezależna kwalifikacja kopii przeszła po 700,07 s;
snapshot opublikowano lokalnie po 1094,34 s całego przebiegu. Snapshot
`snapshot-sha256-4c9187afe1a52f21618119a9b693dc1b46ee74cf0ed084f809b7173647c8bffe`
ma **626238 wierszy / 43 tabele**, bez evaluation truth. Import potwierdził
typed canonical parity. Zastąpiony eksport V7 zatrzymano dopiero po zachowaniu
63 receipts i pełnym niezależnym eksporcie/importcie tego samego source.
Curated przyjęło wszystkie **626238 wierszy, 0 quarantine**;
`forecast_source=passed`, `inventory_ready=true`.
Kalendarz `forecast-calendar-sha256-267133ee0227ce364f9c2f638dcb49488b745a90682fb703dd8575d0456d1369`
wiąże późniejsze okna z tym źródłem.

**V8 zakończyła się exit 3 / `not_ready` przed materializacją cech i treningiem.**
Konserwatywna kontrola używa tego samego as-of wyboru historii co builder cech,
ale uwzględnia wszystkie 14 możliwych horyzontów, również potencjalnie
niekwalifikowane. Nawet taka górna granica daje:

| Koszyk zero | Okno originów | Maksymalna możliwa próba | Wymaganie |
|---|---|---:|---:|
| Validation fold 1 | 30 czerwca–13 lipca 2026 | 0 | ≥30 |
| Validation fold 2 | 14–27 lipca 2026 | 0 | ≥30 |
| Holdout fold 3 | 26 sierpnia–8 września 2026 | 0 | ≥30 |

Pooled zero ma górną granicę 140 validation i 238 holdoutu; te sumy nie
rozwiązują pustych okien. High nie został odrzucony przez górną granicę
liczebności (672–896 validation i 518–840 holdout na fold), ale nie wykonano
pełnej oceny eligibility ani jego jakości. **0 model fits, brak etykiet
target i metryk nowych holdoutów.** Weryfikacja źródła/indeksu przegląda
całe fakty; do liczenia historii dobiera wyłącznie dane znane w origin.
Raport V8 i checksumy zapisuje evidence JSON.

Do dalszej kwalifikacji potrzeba reprezentatywnego źródła z koszykiem zero
w każdym wymaganym oknie. Dopiero na nim można ocenić zamrożoną korektę poza
wcześniej obserwowanym development. Kolejne zmiany po obejrzeniu jego metryk
wymagałyby nowej, jawnie wersjonowanej kampanii. Nie deklarujemy zamknięcia
32 pozostałych failed ani 8 not_ready dotychczasowego pomiaru.

Regresja AI 04: **940 passed / 729,27 s** oraz późniejsze testy korekty/CI:
**22 passed / 62,98 s**, w tym dokładne zachowanie bezbłędnego baseline'u oraz
test konserwatywnej górnej granicy liczebności z historii, bez target outcomes.
Ruff/format, mypy, kontrakty, dokumentacja, pakiet wheel/sdist, odłączony wheel
oraz skan katalogu i historii bieżącego HEAD repozytorium AI przechodzą.
Skan wszystkich lokalnych branchy AI zgłasza checksumę SHA-256 `Dockerfile.api`
w historycznym raporcie AI 05 z commita `8b19acc`, poza historią tego HEAD;
wartość potwierdzono jako checksumę, a nie credential. Wheel SHA-256 zapisuje
evidence JSON. Skan zmian producenta od `d4ee7a4` przechodzi; pełna historia producenta
ma dwa znane false positives: checksumy SHA-256 workflow w starym evidence,
a nie znalezione credentials. V8 nie kwalifikuje modelu.

## Granice

Pomiar dotyczy syntetycznej obserwowanej sprzedaży. Inventory v2 features oraz
segmenty constrained/unconstrained pozostają osobną pracą po ponownej ocenie
nowego źródła. Wybór i kalibracja współdzielą drugi blok validation; nakładające
się horyzonty i zależność czasowa wykluczają gwarancję pokrycia.
Portfolio final test nie był otwierany. Brak AWS calls, publikacji, zmian
registry, promocji i serving. Lokalna poprawa kodu nie zmienia wcześniejszego
odrzucenia modelu w AI 05.
