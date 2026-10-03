# AI 10 — dowód pierwszego przyrostu forecast v2

Data: 2026-10-03. Status całego etapu: **in_progress**.
To dowód mechaniki kontraktu i trwałości, bez nowej kwalifikacji jakości ML.

## Przypięty zakres

- Repo AI: branch `ai/10-intelligence-contract`, baza
  `f4ae14fe6588b91506383a709b5a0185208a4ea6`.
- Repo RetailOps: branch `ai/10-intelligence-projection`, baza
  `ce7a93f6410b06f7c4d4e33be2212a0d65734243`.
- Prace wykonano w dwóch własnych worktree w `/private/tmp`.
  Nie przełączano głównych checkoutów ani sesji AI 07/08.
- Nowe rewizje: AI `0020_intelligence_outbox`, RetailOps `a10f0c7e0200`.
- Dokładny commit producenta schematu jest przypinany w RetailOps w
  `services/api/app/contracts/intelligence-v2/upstream.json`.
  Commit tego dowodu jest częścią tego samego PR co kod AI.

Wynik to [forecast v2 i atomowy outbox](../intelligence-integration-v2.md),
osobny consumer/projektor RetailOps, ograniczone uwierzytelnione read API
oraz replay v2 w istniejącej kwarantannie. Payload zachowuje oryginalne
functional/lineage, zamiast tworzyć ponownie wynik ML po stronie odbiorcy.

## Wykonane kontrole

| Kontrola | Wynik i granica dowodu |
|---|---|
| Pełna regresja AI przez `make ci-local` | 1738 passed, 2 failed, 1 skipped; 30 min. Oba błędy dotyczyły starego lock SHA w syntetycznym fixture workera. |
| Regresja publikacji po poprawce fixture | 24/24 passed; zawiera oba poprzednio błędne przypadki i nowy test odrzucenia niezgodnych zależności. Produkcyjnej walidacji pinów nie osłabiono. |
| Własny rzeczywisty PostgreSQL dla outbox | 1/1 passed z `REQUIRE_AI10_OUTBOX_TESTS=1`: rollback output/outbox, stabilne IDs, brak receipt, przerwanie po delivery i ponowienie. |
| Pozostałe bramki AI | `make ci-local -o test` passed: lint/format, mypy 326 modułów, docs/CI guard, wszystkie offline checkery, wygenerowane kontrakty, wheel/sdist, Compose config i gitleaks. `-o test` pomija wyłącznie powtórzenie wykonanej pełnej regresji. |
| RetailOps, regresja API bez markerów DB/broker | 697 passed; 5 przypadków zależnych od sandbox/obrazu Dockera ponowiono z wynikiem 5/5 passed. 68 przypadków oznaczonych integration były poza tym przebiegiem. |
| RetailOps, rzeczywisty broker/DB/API AI 10 | 11/11 passed: duplicate, rollback, SIGKILL przed ACK, DB/quarantine outage, poison raw, kolizje identity, scope API i paginacja 102 rekordów. |
| RetailOps, końcowa regresja po uściśleniu typów DB | 56/56 passed; helpery DB, legacy consumer/runner/quarantine i kontrakt AI 10. |
| RetailOps, kontrole statyczne | Compose config/local-boundary, pełny backend lint/format, mypy 11 modułów, Bandit high/high passed. |

JUnit pozostaje poza Git: AI `reports/ai10-*.xml`, RetailOps
`ci-cd/reports/ai10/*.xml`. Dane fixtures są jawnie syntetyczne. Test outbox
używa małych doubles wcześniejszych zależności lifecycle, lecz wykonuje
rzeczywistą nową migrację i transakcję istniejącego publishera. Test brokera
wykonuje migracje RetailOps i rzeczywiste zapisy/ACK/read API.

Pełnego 30-minutowego zestawu AI nie powtarzano po zmianie samych fixtures;
ponowiono cały powiązany moduł. Zdalny Required CI musi potwierdzić dokładne
commity PR. Job persistence uruchamia nowe `integration-replay-test` i
`integration-failure-test`, z wymaganym rzeczywistym testem outbox; przed
bounded drill pobiera przypięty obraz. RetailOps API CI już wymaga testów
brokera i teraz jawnie pobiera ich dwa przypięte obrazy przed testami.
Ten raport nie deklaruje zielonego zdalnego CI ani odbioru etapu.

## Kolejna praca i warunki odbioru

1. Przypiąć zatwierdzone publiczne wyniki AI 07/08. Dopiero wtedy generować
   schematy anomaly/stockout i adaptery tych zdarzeń. Nie wymyślać pól modelu.
2. Wdrożyć bounded typed REST i immutable upstream export z pełnym grainem,
   mapowaniem lokalizacji, availability i kompletnym snapshot/log handoff.
   Dowieść korekt oraz replay bez luki lub podwójnego efektu.
3. Dodać jawny wybór approved release/as-of/run i aktywny head projekcji,
   rozszerzony checkpoint/fencing oraz metryki backlog/lag/freshness.
4. Uruchomić oddzielne DB przez własny shared-broker Compose overlay oraz
   pełne transport/auth zgodne z docelowym środowiskiem.
5. Podłączyć rzeczywiste trzy wyniki do istniejącego UI RetailOps. Sugestie
   pozostają fixture; brak danych i stale/error muszą być widoczne.
6. Wykonać rzeczywiste cross-repo E2E na profilu 102 dni: dostępność wyników,
   lineage, duplikaty/restart/awarie/korekty, bounded read i rollback.
7. Dopiero po pełnym Definition of Done i zielonym CI rozważać odbiór AI 10.
   W tym przyroście nie uruchamiano deploymentu ani automatycznych decyzji.
