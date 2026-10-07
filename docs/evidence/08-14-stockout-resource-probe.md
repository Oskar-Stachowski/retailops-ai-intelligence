# AI 08.14 — cały pipeline i większy jawny pilot

**Odebrano zasoby kompletnego smoke i pilota `ai-load` 2856 origin.**
[Kontrakt](../reference/stockout-resource-probe.md),
[konfiguracja 1.1](../reference/stockout-resource-pilot-1.1.json) oraz
[receipt](08-14-stockout-resource-probe.json) zachowują rzeczywiste rodzice
i osobną granicę jakości. Konsument jest przypięty do `4faaf4b`, producent
do `08639e9`. Kod wcześniejszych pakietów, kontrakty i lockfile są niezmienione.
Oba Required CI rodzica, PR 37221609490 i push 37221607064, są zielone.

## Pełny odbiór zasobów

Każdy przebieg tworzy osobny lokalny klon producenta i nowe źródła, oba
eksporty/importy, curated, cechy 2.2, etykiety 2.0, upstream 2.1 i temporal
2.1. Następnie odtwarza prawdziwy development i dopasowuje sześć modeli.
Konsument nie może importować korzeni producenta. Monitoring co 0,2 s obejmuje
całe własne drzewo, RSS rodzica, logiczny/zaalokowany scratch i wolny dysk.
RSS jest próbkowanym maksimum. Instalacja zależności jest poza pomiarem;
checkout, importy i replay są w nim. Oba zaakceptowane przebiegi mają exit 0.

| Pomiar | Smoke 8 × 2 × 102 | Pilot 14 × 2 × 102 |
|---|---:|---:|
| Wszystkie physical origin / comparison / membership | 1632 | 2856 |
| Train / tune / calibration | 340 / 135 / 130 | 598 / 227 / 218 |
| Development razem | 605 | 1043 |
| Test: wyłącznie eligible membership | 131 | 226 |
| Cały przebieg | 500,74 s | 719,81 s |
| Szczyt RSS całego własnego drzewa | 402,19 MiB | 622,23 MiB |
| Szczyt zaalokowanego scratch | 165,78 MiB | 259,23 MiB |
| Fit sześciu modeli w pilocie | — | 0,778 s |
| Najmniej wolnego dysku | 52,87 GiB | 53,00 GiB |

Smoke ma limit 512 MiB scratch / 1 GiB RSS / 600 s. Pilot ma przed startem
zamrożony limit 2 GiB / 1 GiB / 1800 s i co najmniej 50 GiB wolnego miejsca
przez cały pomiar. Start wymaga 52 GiB. To jawna rewizja nieodebranej propozycji
5 GiB/600 s; nie zmienia limitów konsumenta ani wymaganej rezerwy użytkownika.

Wariant 4480 origin (16 × 2 × 140) odrzucono **przed generacją**: preflight
z 20% zapasem szacuje 1,294 GiB RSS przy istniejącym limicie 1 GiB. Pilot
2856 ma estymację około 845 MiB i przechodzi preflight; rzeczywisty pomiar
potwierdza budżety. Estymacja jest heurystyką, nie gwarantowanym upper bound.

Wejście prywatne pilota ma 148 911 wierszy / 8 792 250 B wobec limitu
500 000 / 64 MiB. Qualification JSON ma 1 605 404 B wobec 4 MiB,
ledger 11 649 wobec 100 000. Maksymalne Parquet części features/labels/
upstream/temporal mają odpowiednio 47 186 / 6731 / 12 450 / 20 948 B.
Origin, limity baz i kanoniczne development arrays pozostają egzekwowane
przez niezmienione pakiety. Pilot nie jest pełnym `ai-dev` ani `ai-training`.
Producent `ai-load` uczciwie ma warmup/origin/tail metadata 0;
frozen feature/split/availability reguły kwalifikują rzeczywiste role.

## Zgodność i zachowanie wejść

Regeneracja zmienia `generated_at` eksportu i źródłowego manifestu.
Pełny prywatny manifest SHA, label bundle, temporal bundle i development
ID odpowiednio się zmieniają. Smoke porównuje wszystkie modele/pipelines,
wyniki, report, klucze/cel, polityki, label content seals i temporal indexed
payload/rows. Wszystkie są identyczne; nowe rodzice są rzeczywiście przypięte
i mają poprawne wyliczone IDs. Surowe checksums/timestamps pozostają zachowane.
Weryfikacja każdego nowego źródła i pakietu nadal jest pełna.

573 stare wejścia i v1 rodzice mają niezmienione 63 417 718 B oraz SHA
`8a7575f4ce9acebde85bd07ccb65c271a24654bfb59cae01a3008c58585ee6b9`.
302 pliki wcześniejszych partycji zachowuje IDs i poprawne checksums;
ich aktualne rozmiary razem mają 4 235 262 B.

Przyjęty pilot pozostaje w osobnym katalogu wskazanym przez receipt jako
`retained_root`. Końcowy logical scratch ma około 123 MiB, wraz z lokalnym
checkout i wszystkimi input/output. `development-inputs.json` ma 1 261 676 B
i tylko train/tune/calibration, bez test outcomes. Dojrzałe klasy to:
train 350 negatywnych / 248 pozytywnych, tune 132 / 95, calibration 134 / 84.
Historyczny forecast jest dostępny dla wszystkich 1043 wybranych punktów.

## Zakres jakości i weryfikacji

Sześć wariantów jest porównanych, a tune wybiera provisional
`logistic_regression:without_upstream`. Tune kategorie zmieniają się z
3 passed / 5 not_evaluable do 7 / 1. To liczebność/evaluability metryk,
bez deklaracji niezależnego odbioru jakości. Sigmoid raportuje wyłącznie
fit diagnostics na calibration; generalizacja nie jest jeszcze oceniona.
Progi/capacity nie są zatwierdzone, registry jest niezmienione, promocji
ani final test scoring nie wykonano. Cały AI 08 pozostaje not ready.

29 nowych kontroli ma 0 failures/0 warnings. Obejmują odmowę startu bez
rezerwy, każdy live limit, brak receipt mimo exit 0, cleanup tylko własnego
katalogu, zatrzymanie potomka ignorującego SIGTERM, zmiany checksum/etykiet/
modeli/metryk/pinów mimo poprawnych nowych IDs oraz odmowę większej generacji
po nieodebranym smoke. Regresja równoczesnego usuwania katalogów zachowuje
pomiar; PermissionError nie jest ukrywany jako pusty scratch.
Ruff/format 641 plików, mypy 382 modułów i documentation check są zaliczone.
Poprzedni kod ma 2133 testy i zielone CI; nie powtarzano go lokalnie dla
zmiany scripts/docs. Nowy commit wymaga swojego CI.

Receipt zachowuje skróty wcześniejszych nieodebranych prób: blokadę sysctl
w sandboxie, wymaganie eksportera dotyczące `data/generated`, cache ścieżki
przed checkout, błędne wymaganie starego development ID oraz race monitora
z usuniętym katalogiem. Wszystkie są jawnie superseded; odbiór opiera się
na dwóch późniejszych pełnych przebiegach z exit 0.

Pozostają niezależna ocena kalibracji i jakości/scenariuszy/seedów, brakująca
kategoria, karta nowych rodziców, zatwierdzone progi/capacity i lifecycle,
batch/read API oraz finalny odbiór CI. AI 05 i v12 pozostają zachowane.
