# RetailOps AI Intelligence

**AI 09: in_progress / not_ready — integracja po gotowych AI 07–08.**
[Aktualny status](docs/STATUS.md) podaje pozostały zakres i granice dowodów.
[Przygotowanie diagnostyki 1.9](docs/evidence/09-54-ledger-query-capacity-preparation.json)
przypina audytowane indeksy Source przy 12 GiB i pełnym profilu. 106 kontroli
AI oraz 22 z paczki przeszły; uruchomienie wymaga pełnego CI obu repozytoriów.
[Audyt pamięci i czasu](docs/ai09-capacity-audit.md) obejmuje wszystkie pięć
faz przygotowania danych przed kolejnym canonical; zachowuje pełne dane i limity.
TensorFlow i mechanika audytu są zaimplementowane; pełna kampania, kalibracja
i końcowy odbiór trzech zastosowań pozostają otwarte.
[Pełne portfolio](docs/ai09-full-scenario-portfolio.md) wiąże wszystkie warianty
i wymaga ich zweryfikowanych wyników development przed dostępem do final.
[Polityka wymaganych segmentów](docs/ai09-full-scenario-portfolio.md) przypina
ich właścicieli przed wynikami, zachowuje wszystkie raporty diagnostyczne
i blokuje kwalifikację przy niedostatecznej próbie krytycznej.
[Wspólne prognozy development](docs/ai09-campaign-scoring.md) zachowują pełne
klucze sześciu modeli; kalibracja i końcowa kwalifikacja nadal są wymagane.

**AI 07: kompletny odbiór kwalifikacji `synthetic_ai_07_portfolio_v4`.**
[Końcowy odbiór](docs/evidence/07-completion.md) i
[integracja z main](docs/evidence/07-main-integration.md) obejmują oba modele,
lifecycle, batch/API i OCI. Pełne `ready` na main potwierdzają scalenia PR #24/#97
oraz zielony Required CI dokładnych HEAD i merge commitów obu repozytoriów.

**AI 08 jest READY.** [Końcowy raport](docs/evidence/08-28-final-ready.md) obejmuje
kwalifikowany model, rzeczywisty lifecycle/batch/API oraz zielone CI obu main.

**AI 10: READY — pełny odbiór integracji.**
[Instrukcja krok po kroku](docs/ai10-acceptance.md) i
[raport bounded](docs/evidence/ai10-bounded-acceptance.json) wiążą snapshot/REST/stream,
real SQL/ACK oraz oryginalne 40 stockout, 1232 anomaly i 56 forecast w RetailOps API/UI.
Pełny V12 ma 273 testy granic i rzeczywisty Source E2E, bez failures/errors/skips.
V12 zachowuje quality `not_ready` i zaakceptowany development namespace.
Końcowe dowody i status są objęte chronionymi PR #38/#104 oraz Required CI obu main.

**Status: AI 04 — finalna v12, `ready` z zaakceptowanymi odstępstwami jakościowymi.**
[Decyzja i zakres odbioru](docs/evidence/04-v12-acceptance.md) obowiązują po
chronionym merge i Required CI. Etapy 01 i 11 są odebrane lokalnie.
Obecny zakres: pakiet Python, konfiguracja, CLI, lokalny serwis diagnostyczny HTTP,
PostgreSQL/pgvector, oddzielny MLflow, migracje, Compose, wykonywalne kontrakty
danych/run/tool, lokalne poświadczenia i scope API oraz kontrole CI.
[Etap 11 — semantyczny RAG](docs/knowledge-semantic.md) obejmuje zatwierdzony
korpus, wersjonowane fragmenty i rzeczywiste embeddings Bedrock, pgvector,
filtry uprawnień/statusów, 44 pytania golden, trwałe runy oraz kwalifikację,
atomową aktywację i rollback. Aktualny odbiór i granice opisuje
[status](docs/STATUS.md). Generowanie odpowiedzi i narzędzia agenta należą do AI 12.
[Uprawnienia API](docs/access-control.md).
[Kontrakty i walidacja offline](docs/data-contracts.md).
[Uruchomienie stosu](docs/local-stack.md) i [HTTP](docs/http-service.md).

- [Dokumentacja i uruchomienie](docs/README.md)
- [Aktualny status i następna praca](docs/STATUS.md)
- [Decyzje architektoniczne](docs/architecture/decisions.md)
- [Zasady zmian](docs/contributing.md) i [bezpieczeństwo](docs/security.md)
- [Licencja MIT](LICENSE)

Aktualne statusy i zależności: [plan RetailOps](https://github.com/Oskar-Stachowski/retailops-cloud-native-platform/tree/main/docs/plans/ai).
Plan bazowy audytu: [RetailOps na cbf28b2](https://github.com/Oskar-Stachowski/retailops-cloud-native-platform/tree/cbf28b2/docs/plans/ai).
Generator, frontend i baza operacyjna należą do RetailOps. To repo ma własny lifecycle.
