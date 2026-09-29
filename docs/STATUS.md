# Aktualny status

Aktualizacja: **2026-09-29**. **Etap 11 — RAG jest odebrany lokalnie.**
[Instrukcja użytkowa](knowledge-semantic.md) opisuje rzeczywiste embeddings,
przygotowanie, kwalifikację, aktywację i rollback. [Końcowy odbiór](evidence/11-completion.md)
wiąże implementację z pomiarami i ograniczeniami. Zdalna publikacja przechodzi
przez chroniony `main` oraz Required CI; stan wykonania pokazuje
[workflow repozytorium](https://github.com/Oskar-Stachowski/retailops-ai-intelligence/actions/workflows/required-ci.yml).

**AI 04.1 — zadanie i kalendarz forecastingu:**
[kontrakt i polecenia](forecasting.md) definiują observed sales, grain,
cutoff 23:59:59 UTC, horyzonty 1–14 i okna 7/14 z jedną granicą wiedzy.
Manifest wiąże kalendarz ze zweryfikowanym curated AI 03; inventory i truth
features są wyłączone. [Odbiór lokalny](evidence/04-01-calendar.md) podaje
testy i powtarzalny smoke. Branch `ai/04-01-task-calendar` jest osobny od AI 12.
Kolejny zakres to **04.2 — panel i cechy**; model pozostaje `not_ready`.
Ten zakres nie ma jeszcze publikacji ani zdalnego Required CI.

**AI 03.3 — kontrakt handoff odebrany lokalnie:** [snapshot źródła](source-snapshot-handoff.md)
ma wspólną wersję 1.0.0, pełny mały fixture oraz niezależną walidację schema,
identity i transportu bez generatora/DB.
**AI 03.4 — typed importer odebrany lokalnie:** [CLI i runbook](source-snapshot-import.md)
opisują pełną weryfikację Parquet, canonical hashes, gates, atomową publikację
i niezmienny reimport. [Evidence](evidence/03-04-importer.md) podaje testy obu
smoke, partycje, truth opt-in i odłączony wheel.
**AI 03.5 — curated:** [runbook](curated.md) opisuje jawne mappings,
normalizację, quarantine, immutable IDs i odczyt z pełnej historii wersji.
[Evidence](evidence/03-05-curated.md) podaje 743 testy i pomiary smoke/as-of.
[AI 03.6 — bramka cross-repo](evidence/03-06-cross-repo.md) wiąże oba repo
przez pełne smoke i przypięte rewizje. Końcowy odbiór RetailOps określa wejście
do 04/06 oraz odrębne readiness use cases.
AI 12 rozwija się w osobnym worktree; forecasting zaczyna się od main z AI 03.
Branch 03 jest opublikowany w [PR #5](https://github.com/Oskar-Stachowski/retailops-ai-intelligence/pull/5).
Przypięte wyniki Required CI, testów i rzeczywistego Compose/persistence
znajdują się w [końcowym evidence cross-repo](evidence/03-06-cross-repo.md).

## Etap 11

- Zatwierdzony korpus: 29 dokumentów, 451 fragmentów, przypięte źródła Git,
  statusy, klasy dostępu i dokładne cytaty.
- Amazon Titan Text Embeddings V2, 1024 wymiary, `eu-north-1`, kontekst
  nagłówków i wersjonowany cache. Wywołania AWS są jawne i ograniczone budżetem.
- Golden set: 44 pytania, w tym 9 krytycznych. Bez zmiany etykiet i progów:
  **Recall@5 0,852941 ≥ 0,80; MRR 0,661275 ≥ 0,60; cytaty i krytyczne 1,0**.
- Trwałe runy odtwarzają pomiar bez AWS. Niezaliczony próg zachowuje raport
  `failed/gate_failed`, bez outputu. Sukces nie aktywuje indeksu automatycznie.
- Użytkowa kwalifikacja wymaga udanego runa, zgód, raportu jakości i kompletnego
  przeglądu podobieństw. Aktywacja i rollback mają CAS, idempotencję i niezmienne piny.
- Właściwy indeks jest aktywny w lokalnej bazie w kanale `retrieval`.
  PostgreSQL odtworzył wszystkie 44 wyniki golden. Runtime wymaga jawnego
  `RAG_BEDROCK_ENABLED=true` i poświadczeń AWS procesu.
- 643 testy regresji; dodatkowa kontrola 77 testów po dopracowaniu current/report
  również przechodzi. Pełny odbiór Compose obejmuje migrację `0008_rag_semantic`,
  pgvector, HTTP, runy, SQL gates, aktywację/rollback, SIGKILL i trwałość danych.

Nie pozostały otwarte blokady implementacji lub jakości Etapu 11.
Fake pozostaje wyłącznie ścieżką testową i nigdy nie uprawnia do użytkowej aktywacji.
Szczegółowe wcześniejsze evidence opisuje historyczne, mniejsze zakresy odbioru;
nie stanowi bieżącej listy braków.

## Fundament i dalsza praca

Etap 01 ma odbiór lokalny i zdalny: pakiet/CLI, settings, HTTP/telemetry,
lokalne poświadczenia i scope, odrębne PostgreSQL AI/pgvector i MLflow,
wykonywalne kontrakty danych/run/tool, jawne migracje i Required CI.
[Uruchomienie](local-stack.md), [uprawnienia](access-control.md),
[kontrakty](data-contracts.md), [odbiór zdalny](evidence/01-remote-ci.md).

[Bieżący odbiór danych](evidence/03-06-cross-repo.md) jest wspólny z RetailOps.
Po pełnej bramce 03 można rozdzielić forecasting **04** w AI i ledger **06**
w RetailOps; nowe źródło po 06 wymaga ponownego importu i zależnych ocen. Równolegle można przygotować
interfejsy i test doubles **AI 12**. Pełne zamknięcie agenta wymaga **AI 10 i 11**;
11 jest gotowy, 10 nadal należy do późniejszego ciągu danych/ML/integracji.
[Pisemna mapa etapów i repozytoriów](https://github.com/Oskar-Stachowski/retailops-cloud-native-platform/blob/main/docs/plans/ai/kolejnosc-i-repozytoria.md).

## Granice

Odbiór dotyczy lokalnego retrieval na konkretnym zatwierdzonym snapshotcie.
Zmiana dokumentacji na `main` nie aktualizuje automatycznie korpusu. Kolejna
wersja wymaga nowego snapshotu, przeglądu i ewaluacji.
Nie ma jeszcze generowania odpowiedzi, ewaluacji groundedness ani wykonywania
narzędzi agenta — to AI 12. Pipeline danych, modele, integracja zdarzeń oraz
wdrożenie AWS/EKS mają dalsze bramki. Limit AWS na proces nie zastępuje wspólnego
budżetu wielu replik ani produkcyjnego IAM. Nie deklarujemy wdrożenia w chmurze.
