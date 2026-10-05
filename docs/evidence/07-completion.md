# AI 07 — końcowy odbiór

Stan na 2026-10-05: **odbiór kwalifikacji zaliczony** w zakresie
`synthetic_ai_07_portfolio_v4`. Pełne zamknięcie `ready` na `main` ma jawny
warunek: oba PR-y #24/#97 scalone, Required CI ich aktualnych HEAD i obu
merge commitów zaliczony. Dokładne SHA i runy są publikowane w tych PR-ach.
[Integracja z main](07-main-integration.md) zachowuje istniejący AI 05/08.
Pełny odbiór lokalny i Required CI implementacji obu repozytoriów są zaliczone.
Publikacja została zatwierdzona i wykonana w PR-ach
[consumer #24](https://github.com/Oskar-Stachowski/retailops-ai-intelligence/pull/24)
oraz [producer #97](https://github.com/Oskar-Stachowski/retailops-cloud-native-platform/pull/97).
[Pierwszy run konsumenta i reprodukcja](07-ci-remediation.json) ujawniły
nieaktualną oczekiwaną rewizję migracji w `/ready` oraz brak importu `data`
przy bezpośrednim uruchomieniu skryptu producenta. Poprawka wymaga aktualnej
rewizji `0022_anomaly_evaluations`, uruchamia przygotowanie jako moduł i
dodatkowo sprawdza gotowość rzeczywistego serwera API w OCI bez portów hosta.
[Dowody Required CI](07-ready/required-ci.json) wiążą poprawioną implementację
`eb15ac94398761ff2b2c10c4fe1a1073634d3b1e` i publikację producenta
`8747074adceea2fe6bbeccdf9068dcee6dd81d02` z faktycznie zaliczonymi runami.
Końcowy commit odbioru przechodzi pełny Required CI z równoległymi grupami;
jego runy i dokładny HEAD są zapisane w PR #24.
[Poprzedni przebieg PR](07-ready/required-ci-timeout.json) zaliczył wszystkie
testy i bramki `make check`, ale limit 125 minut przerwał pakowanie cache
w kroku końcowym. [Podział CI](07-ready/parallel-ci.json) uruchamia cztery
kompletne grupy pytest i cztery grupy odbioru na osobnych runnerach. W poprzednim odbiorze wszystkie
2151 testów były przypisane dokładnie raz. Po integracji kolekcja obejmuje
również wszystkie testy AI 08 z aktualnego `main`. Wszystkie kontrole, progi
jakości i limity pojedynczych procesów pozostają bez zmian.

## Zamrożona jakość

[Deklaracja v4](07-v4-experiment-declaration.json) poprzedza generację i ocenę
nowego finalnego okna. Dziewięć rzeczywistych dopasowań i porównanie obu rodzin
używają wyłącznie seed-42 train/validation. Dwie selekcje zamrożono przed
otwarciem sześciu finalnych przypadków: seedy 42, 137, 2026 × demand/physical.
Historia to 128 dni, 2025-01-01–2025-05-08; zakres ma 12 produktów,
dwie pary sprzedaży i dwie fizyczne lokalizacje zapasu.

[Wynik finalny](07-v4-final-qualified.json) zachowuje rzeczywiste modele,
oryginalne czasy fitu, konfiguracje i hashe wszystkich wejść oraz raportów.
Obie wersje przechodzą **56/56** pierwotnych bramek, bez odstępstw.

| Metryka | Seasonal residual — primary | Native Isolation Forest — reference |
|---|---:|---:|
| Precision | 0,964286 | 0,956044 |
| Recall obserwacji | 0,900000 | 0,725000 |
| Fałszywe alarmy / 1000 clean | 0,962232 | 0,962232 |
| Precision high severity | 1,000000 | 1,000000 |
| Recall epizodów | 32/33 | 33/33 |
| Evaluable coverage | 4277/7392 | 4277/7392 |

Obie wersje mają cztery false positives na 4157 ocenialnych clean observations.
Unknown truth 2232 i insufficient inputs 883 pozostają jawne. Zaliczono bramki
każdego typu, segmentu, seeda/scenariusza i minimalnej liczebności.
[Nieudany final v2](07-v2-final-not-ready.json) i
[nieudany final v3](07-v3-final-not-ready.json) zachowują statusy i wyniki.
Powtarzane syntetyczne eksperymenty są skorelowane; kwalifikacja dotyczy tylko
`synthetic_ai_07_portfolio_v4`.

## Rzeczywisty lifecycle, batch i odczyt

[Świeży raport OCI po poprawkach CI](07-ready/ci-fix-oci-acceptance.json) dotyczy
konsumenta `eb15ac94398761ff2b2c10c4fe1a1073634d3b1e` i zamrożonego źródła
`48439ebd9515dc1c7adc633609bbe33d1657c3df`. Rzeczywisty obraz aplikacji ma digest
`sha256:f5b5162fa1d49d411aed11bc15cc8ecb4f65c9350275e6b428eddebb3fd96efc`.
Test odtworzył źródło i wszystkich publicznych rodziców z czystego przypiętego
checkoutu, zbudował obrazy, uruchomił własne PostgreSQL 16/MLflow, wszystkie
migracje i rzeczywistą usługę API; `/ready` zwróciło HTTP 200 wewnątrz kontenera.
Nie używał atrap registry, persistence ani HTTP i nie publikował portów hosta.
[Pierwotny raport OCI](07-ready/oci-acceptance.json) dla wcześniejszego commitu
`5be04b1` pozostaje niezmienionym dowodem historycznym.

Siedemnaście kontroli obejmuje prywatne uwierzytelnione CLI, rejestrację,
promocję primary i reference, awarie po utworzeniu wersji i zapisie aliasu,
rzeczywistą rywalizację blokady, recovery bez duplikatu, odrzucenie trzeciej
wersji, blokadę promocji odrzuconej wersji oraz rollback dokładnego modelu i obrazu.
Zmiana zewnętrznego aliasu blokuje nowe decyzje i nie przepina przyjętego release’u.

Batch zweryfikował komplet publicznych rodziców bez importu producer truth,
załadował zatwierdzony model i opublikował atomowo **1232 wyniki**. Powtórzenie
prywatnego zainstalowanego CLI zwróciło ten sam batch; logiczne ponowne użycie
zachowało pierwotne czasy. Celowy rzeczywisty błąd INSERT nie pozostawił
częściowego batchu ani requestu. Viewer nie może uruchamiać zapisów.

Rzeczywiste HTTP sprawdziło autoryzację 401/403/404, paginację po scope,
GET anomalies/models/evaluations, niedostępność zapisów HTTP oraz uczciwe
`stale`/`unknown` dla historycznych wejść. Wymuszony SIGKILL obu usług i restart
zachowały dokładny release, wersje 1/2/3, aliasy, trzy projekcje ewaluacji,
1232 wyniki, jeden batch i dwa requesty. Projekt, kontenery i wolumeny testowe
zostały usunięte; prywatna maszyna Docker została zatrzymana.

## Artefakty i odtworzenie

[Manifest dwóch kapsuł](07-ready/capsules.json) wiąże zapisane modele, rzeczywisty
fit `fcd09f68187b8d21e9dff756a24688b790221023`, pełny numeryczny replay jakości,
model cards, bezpieczeństwo/licencje, przykład wejścia i smoke/compatibility.
Archiwa mają 480348 i 492162 bajty; pełne dowody po rozpakowaniu mają odpowiednio
10686040 i 10728596 bajtów. To dowody kwalifikacji modeli z wygenerowanymi
syntetycznymi wierszami numerycznymi i offline truth. Nie zawierają rzeczywistych
danych biznesowych ani sekretów. Eksporty source/training pozostają poza Git.
Gitleaks sprawdził osobno wszystkie 46 rozpakowanych plików JSON: zero findings.

CI odtwarza oryginalne źródło kwalifikacji z przypiętego commitu `48439eb`;
generuje świeże native source, snapshot, day coverage, DQ i niezależne cechy.
Nie zmienia modeli ani progów. Końcowy branch producenta `ai/07-qualification-source`
zawiera sprawdzone hashe kompatybilności fast path, zachowanie oryginalnych
opisów biznesowych w testach zmienionej proweniencji, czas na pełne
972 regresje danych oraz jawne oczekiwanie przeglądarki na dane po rollbacku.
Oryginalne bramki jakości i zasobów oraz zamrożone źródło naukowe są zachowane.
Wszystkie 58 tabel, CSV, raporty, context i ledger mają zachowaną zgodność
ordinary/indexed/cached; 16 testów przechodzi wraz z negatywnym testem pin drift.
Jego dokładny commit publikacji znajduje się w manifeście kapsuł.

Odtworzenie OCI z czystym producer checkout i jego native toolchain:

```sh
uv run --frozen python scripts/check_anomaly_oci.py --producer /path/to/frozen-producer
```

Opcjonalny `--prepared-receipt` używa istniejących, ponownie zweryfikowanych
publicznych rodziców. Kontroler korzysta z osobnego projektu Compose i nie
publikuje portów usług. Pełne wejście musi istnieć; bind mounts nie tworzą
brakujących katalogów. [Runbook](../reference/anomaly-portfolio-qualification.md)
opisuje wersjonowane cechy, ocenę i granice kwalifikacji.

## Odbiór publikacji

Required CI implementacji konsumenta po pushu zaliczyło `checks`, `secrets`,
`persistence`, obowiązkowy `anomaly-oci` i `required-result`. Poprzedni przebieg
PR zatrzymał limit całego zadania podczas pakowania cache po zaliczeniu bramek.
Producent zaliczył wszystkie 27 kontroli pełnego Required CI, w tym 972 testy
danych i wszystkie mierzone smoke flows, replay AI 07, rzeczywisty Docker oraz
Kubernetes z recovery i rollbackiem. [Wersjonowany wynik](07-ready/required-ci.json)
zachowuje dokładne commity, ID runów i wyniki każdej kontroli. Ta aktualizacja
odbioru zmienia dokumentację, dowody i układ CI. `required-result` wymaga sukcesu
`checks`, wszystkich grup `tests` i `acceptance`, `secrets`, `persistence` oraz
`anomaly-oci`. Pełne Required CI sprawdza też jej końcowy commit publikacji,
wskazany w PR #24.
Trwałość brokera, ACK/DLQ, projekcje i UI RetailOps należą do AI 10.
Wdrożenie produkcyjne nie jest dopuszczone przez ten odbiór.
