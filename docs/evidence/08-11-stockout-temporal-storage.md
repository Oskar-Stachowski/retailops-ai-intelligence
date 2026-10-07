# AI 08.11 — odbiór temporalnego połączenia partycji

[Kontrakt](../reference/stockout-temporal-storage.md) dodaje osobny pakiet,
comparison/membership Parquet i rzeczywiste wejścia sześciu modeli z cech
2.2, upstream 2.0 oraz prywatnych etykiet 2.0. Stare pakiety/ID, AI 05
i finalne v12 pozostają zachowane. Cały AI 08 jest **not ready**.

## Dane, wejścia i modele

Odbiór ponawia istniejący `ai-temporal-smoke`, seed 42, 8 produktów,
2 fizyczne lokalizacje i 102 dni, producent `08639e9`, bez regeneracji.

| Kontrola | Wynik |
|---|---:|
| Comparison / membership | 1632 / 1632, wszystkie pola Parquet identyczne z v1 |
| Eligible train / tune / calibration | 340 / 135 / 130 |
| Rzeczywisty development z outcome vectors | 605, identyczne wiersze, cele, coverage i PIT lineage |
| Final test | 131 membership, bez wektora celów i metryk |
| Dopasowane modele | 6, te same model IDs, pipelines, wyniki i report |
| Części / pliki z manifestem | 7 / 15 |
| Największa porcja kluczy | 256 |
| Nowy bundle | 178 960 B |
| Dawne comparison + split JSON | 1 890 509 B |
| Wejścia i sześć rodziców v1 | 573 pliki / 63 417 718 B, bez zmian |

Zapis z manifestem jest o **90,5% mniejszy**. Pełne raporty i
logical hashes są identyczne z v1. Publiczny assembler odtworzył temporalny
bundle i dostarczył train/tune/calibration bez legacy bridge. Frozen
`build_development` dopasował modele bezpośrednio z tych nowych wejść.
Pełne JSON v1 służą wyłącznie porównaniu w helperze odbioru.

Temporal ID to `temporal-partitions-sha256-f638ac6f1ae44f11015dcae8eb919a402881746e7b6c237f9201a24eaeda9977`.
Nowy development ID to `development-sha256-d27ebd97d83958067b4b6517ff8efdc0e4b0b9bfd66e0e7669de391c99f3f809`; różni się od
v1 `development-sha256-18e71a00d92d1f5b22af4df603e01d8084155e61aa4e30102402db0d25a6f462`, bo wiąże rzeczywiste nowe bundle IDs.
Wybrany na tune HGB bez upstream pozostaje provisional; sigmoid ma
dotychczasowe diagnostyki in-sample. Końcowy test nie jest oceniony.

## Pakiet i kontrole

Odłączony wheel wykonał build/rebuild/verify i rzeczywisty trening.
Wszystkie bajty, manifest, temporal/development IDs oraz modele są
identyczne z native. Wszystkie 73 moduły
konsumenta pochodziły z instalacji, producent nie był importowalny.
Wejścia zachowały fingerprint; pliki bundle mają prawa 0600.

Native build trwał 252,12 s, assembler
236,48 s. Oba odbiory odbywały się przy własnych
równoległych kontrolach. To czasy etapów, bez porównania prędkości z v1,
pomiaru peak RSS ani pełnego scratch. Limity składników nie zaliczają
budżetu całego pipeline lub większego profilu.

Ukierunkowana regresja ma **153/153 zaliczeń**, w tym 27 nowych,
w 232,03 s. Obejmuje parity, granice purgingu i opóźnioną availability,
agregację klas, brakujące cechy, zakaz test i resealed test→train,
zmiany rodziców, limity, cleanup, immutable output, UTC oraz seal bazy.
Jedno ostrzeżenie joblib dotyczy blokowanego przez sandbox odczytu
fizycznych rdzeni przez `sysctl`; biblioteka używa rdzeni logicznych.
Limit wątków estimatora pozostaje 1, bez filtrowania ostrzeżeń.

Walidacja lokalna obejmuje **2037/2037 unikalnych przypadków**: 1903
zaliczone przypadki poza siedmioma modułami wymagającymi uprawnień oraz
całe 134 przypadki tych niezmienionych modułów, powtórzone z dostępem do
portu localhost i statystyk pamięci własnych procesów. Powtórzenia mają
113 + 17 + 2 + 2 zaliczenia; źródło i testy pozostają identyczne.
Pierwszy `make ci-local` ma exit 2: system blokował bind `127.0.0.1`
oraz wywołania `sysctl` przez psutil. Wszystkie 30 failures/errors miały
`Operation not permitted`. Receipt zachowuje pierwszy wynik: 2007 passed,
28 failed, 2 errors, 1 warning, 2416,98 s.
Nie przedstawia go jako pojedynczego udanego `make ci-local`.
Wszystkie 21 targetów są rozliczone; pozostałe targety Make, pakiet,
Compose config i oba skany sekretów przeszły z exit 0. Końcowe
utrwalenie samych dowodów ma ponowne kontrole dokumentacji i sekretów.
[Receipt JSON](08-11-stockout-temporal-storage.json) wiąże kod, rodziców,
seals, policy, limity i log hashes.

Poprzedni `077add2` ma zielone Required CI
[PR](https://github.com/Oskar-Stachowski/retailops-ai-intelligence/actions/runs/37201459907)
i [push](https://github.com/Oskar-Stachowski/retailops-ai-intelligence/actions/runs/37201457302).
Nowy commit wymaga własnego CI w draft PR #14.

## Pozostały zakres

Rzeczywisty trening jest podłączony do nowego adaptera. Nadal obowiązują
10 000 kluczy, development w pamięci / 16 MiB kanonicznej reprezentacji,
qualification JSON 4 MiB i globalny upstream 20 000 wierszy / 16 MiB.
Większy profil może przekroczyć również limity wejść i payload join.
Pozostają selekcja upstream po seriach z globalną walidacją, jawny
profil/config, preflight RSS/scratch całego pipeline, większy profil,
niezależna ocena, progi, karta z nowych rodziców i lifecycle/batch/read API.
Żaden nowy wyjątek jakości nie został dodany.
