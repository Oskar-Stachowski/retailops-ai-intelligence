# AI12 — audyt przygotowania bez kosztów AWS

Zakres odpowiada planowi AI12 i kontraktowi integracji w Source na
`5c05445e8105c378107099785bae7355388ed7e7`:
[etap 12](https://github.com/Oskar-Stachowski/retailops-cloud-native-platform/blob/5c05445e8105c378107099785bae7355388ed7e7/docs/plans/ai/etapy/12-agent-bedrock.md),
[integracja agenta](https://github.com/Oskar-Stachowski/retailops-cloud-native-platform/blob/5c05445e8105c378107099785bae7355388ed7e7/docs/plans/ai/kontrakty/integracja-agent.md).
Własny checkout AI12 i prywatna baza testowa izolują tę pracę od innych sesji.
Nowe wywołania inference, query embeddings, reembedding i zasoby AWS pozostają
wstrzymane. Dotychczasowych kosztów i historycznych dowodów nie zeruje się.

## Kroki planu

| Wymaganie | Dostępna implementacja i kontrola bez AWS | Granica odbioru |
| --- | --- | --- |
| 1. Tożsamość, scope i osiem narzędzi | [Katalog natywny](agent-native-tools.md), zamknięte schematy, server-owned scope, osobne fizyczne granty, limit wierszy/czasu i SELECT-only Source. Testy narzędzi, HTTP i rzeczywisty PostgreSQL. | Bieżące produkcyjne źródła i namespace modeli wymagają osobnej weryfikacji przed naturalnym LLM; brak danych nie jest uzupełniany fixtures. |
| 2. Provider, modele, prompty i konfiguracja | [Bedrock transport](agent-bedrock.md), lazy klient, ścisłe model/profile/region, wersjonowane prompty i hashe, scripted timeout/throttle/JSON/citation/forbidden-tool controls. | Stare rzeczywiste próby zachowują swój kod i zakres; nie kwalifikują `.prepaid.v5`. |
| 3. Ograniczony graf i trace | [Graf](agent-graph.md): auth, plan, narzędzia/retrieval, evidence, synthesis, walidacja i trwałe wyniki; jedna extra round i jedna repair. Durable SQL admission, deadline i bezpieczny trace. | Brak ogólnej historii rozmów jest jawnym zakresem MVP. |
| 4. Wierność danych i sugestie człowieka | Typed numerical facts, wzór różnicy i oba okresy, literalne cytaty/statusy, conflict/stale/missing controls, deterministyczna polityka. [API rekomendacji](assistant-api.md) zwraca tylko uprawnione, niewygasłe immutable wpisy. | Odczyt historycznego model release nie potwierdza bieżącego wdrożenia. Polityka nie wylicza zamawianej ilości. |
| 5. API, błędy i izolacja klasycznego ML | Queries, owner/admin safe runs, lista/detail rekomendacji, wspólne problem-details; 424/429/502/503/504 i kontrolowane insufficient/refused. [OpenAPI](../contracts/assistant/v1/assistant.openapi.json). | Awaria Assistant nie zmienia health ani istniejących odczytów ML. |
| 6. Golden i miary | Zamrożone 50 przypadków, 36 krytycznych, argumenty i liczba narzędzi, schemat, liczby/groundedness/cytaty/odmowy/akcje, latency/token/cost. [Pakiet przeglądu v5](evidence/12-prepaid-label-review-packet-v5.json). | Etykiety 50/26 i nowe równoważne dokumentowe supports pozostają proposed; brak niezależnego odbioru człowieka. |
| 7. Bounded rzeczywisty Bedrock | [Plan v5](evidence/12-prepaid-test-plan-v5.json), pięć osobnych propozycji `not_run`, piny modeli/cennika, zatrzymanie przed `--execute`, oddzielny kandydat natywnego runtime i rezerwa embeddings. | Płatna kwalifikacja jest wyłączona zgodnie z poleceniem użytkownika. Nowy budżet nie jest zatwierdzony. |

## Krytyczne kontrole

| Kontrola planu | Dowód bez AWS |
| --- | --- |
| Agent → persisted recommendation → v2 → Source API/UI | [Osobny workflow E2E](../.github/workflows/ai12-source-prepaid.yml): oryginalny Assistant API i odczyty AI rekomendacji, atomic SQL/outbox, locked publisher, Redpanda, przypięty konsument Source, SQL/API i Chromium. LLM i obserwacja Source są jawnie testowe. |
| Stable ID, retry i brak wykonania | SQL/outbox tests sprawdzają UUID/bytes/hash, rollback, ACK+SQL crash, competing workers i expiry. E2E sprawdza duplicate → jeden business row oraz UI bez execution controls. |
| Scope, principal i cofnięcie praw | HTTP/auth i native SQL tests; odczyt rekomendacji ponownie kontroluje owner, tool capabilities, całe scope, knowledge i fizyczny magazyn. Cudzy/niedostępny wpis daje 404; listy filtrują przed paginacją. |
| Prompt/chunk injection, sekret i writer | Zamknięte katalogi narzędzi, server evidence validation i krytyczne przypadki golden/security. Brak narzędzia SQL/HTTP/shell/FS/writer oraz endpointu execute. |
| Nieznane narzędzie, argumenty i nieograniczona pętla | Graph/tool/HTTP tests, bounded tool calls, extra/repair, deadline i rezerwacja SQL przed modelem. |
| Stale, conflict, index/model drift | Native tools i runtime startup/readiness checks; rzeczywisty AI11 cache/pin i brak zgadywania deployment attestation. |
| Fałszywe liczby, cytaty i znaczenie | Kanoniczne typed facts i literalne źródła, negatywne golden; bounded repair albo kontrolowany błąd. |
| Retry, circuit, cache i bezpieczne logi | Counted transport/retry tests, brak retry dla auth/schema; scoped retrieval/cache checks. Pełne prywatne payloady i CoT nie są trace. Brak response cache pozostaje dopuszczonym zakresem MVP. |

[Odbiór lokalny kodu v3](evidence/12-prepaid-v3-local-acceptance.json) wiąże
25 rzeczywistych SQL/HTTP testów, 136 testów runtime oraz wheel golden
50/50 i krytyczne 36/36 z application checksum v3. Rodzina v5 integruje późniejszy kod
`main/656d7dd` bez zmian w adapterach, API i retrieval. [Lokalny odbiór przypięć v5](evidence/12-prepaid-v5-local-acceptance.json)
sprawdza nowy release i wheel przy zablokowanej sieci. Aktualną rewizję SQL
kwalifikują CI dokładnego HEAD i jego artefakty w draft PR32.

## Artefakty i punkt zatrzymania

Jedno [evaluation release v5](../agent/evaluation-release.fake.prepaid.v5.json)
wiąże kod, graf, konfigurację, prompty/schematy, golden i dependency lock.
Osobny [kandydat natywny](../agent/native-bedrock-runtime.prepaid.proposed.v5.json)
wiąże Source/Curated/DQ/coverage i kwalifikowany indeks AI11. Runtime celowo
odrzuca proponowane trasy. Opublikowane rodziny v1/v2/v3/v4 nie są nadpisywane.

[Preflight dokumentowy v5](evidence/12-prepaid-native-preflight-v5.json) odtwarza
te same wyniki top-5 z istniejących Titan vectors. Proponowane równoważne
instrukcje startu zwiększają kompletne pokrycie z 3/6 do 4/6; nie zmieniają
pytań, wymagań, rankingu ani statusów źródeł. Nadal brakuje instrukcji kontroli
przed commitem w top-5 oraz verified evidence dla zabezpieczenia metryk.
W tych przypadkach zachowuje się `insufficient_evidence` i przekazuje wynik
do niezależnego przeglądu. Synthetic golden pozostaje osobnym testem mechaniki.

Natywna polityka v2, FTS/hybrid/rerank, response cache, ogólny klasyfikator
pytań i feedback są rozszerzeniami po MVP. Native-v2 transport wymaga odbioru
Source, jeśli zostanie wybrany; zaakceptowany v1 wystarcza do obecnego E2E.
Nie stanowi to zgody na przeniesienie development namespace do canonical.

Przed płatnymi próbami pozostają niezależny przegląd etykiet i supports,
aktualne piny źródeł/modeli dla wybranego zakresu oraz nowa zgoda na koszt.
[Runbook](ai12-paid-qualification.md) podaje dokładną procedurę. AI12 pozostaje
`in_progress`; przygotowanie i zielone testy bez AWS nie zastępują odbioru LLM.
