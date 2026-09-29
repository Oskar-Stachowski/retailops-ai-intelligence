# Aktualny status

Aktualizacja: **2026-09-29**. **Etap 11 — RAG jest odebrany lokalnie.**
[Instrukcja użytkowa](knowledge-semantic.md) opisuje rzeczywiste embeddings,
przygotowanie, kwalifikację, aktywację i rollback. [Końcowy odbiór](evidence/11-completion.md)
wiąże implementację z pomiarami i ograniczeniami. Etap 11 jest opublikowany na
chronionym `main` przez [PR #4](https://github.com/Oskar-Stachowski/retailops-ai-intelligence/pull/4);
[Required CI na main](https://github.com/Oskar-Stachowski/retailops-ai-intelligence/actions/runs/36461392661)
ma `success`.

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

## Rozpoczęty AI 12

[Pierwszy zakres](agent-tools.md) obejmuje osiem narzędzi tylko do odczytu,
typowane request/result schemas, principal z prywatnych poświadczeń, jawne
`assistant:query` i prawa poszczególnych źródeł, kontrolę całego scope oraz
wspólny budżet wywołań. Adapter wiedzy korzysta z jednego serwerowego pinu
AI 11. Przyszłe narzędzia biznesowe mają ścisłe kontrakty i testowe odpowiedniki;
brak rzeczywistego źródła jest jawnym `unavailable`.
[Odbiór](evidence/12-tools.md) opisuje lokalne testy i granice.
Ten zakres jest zapisany lokalnie na osobnym branchu `ai/12-tools`.

Następny zakres tego strumienia: wersjonowana konfiguracja/prompty i fake chat
provider, potem ograniczony graf oraz walidacja odpowiedzi. Rzeczywiste źródła
ML/operacyjne, Assistant API, trwały trace, admission, golden odpowiedzi,
chat Bedrock oraz E2E sugestii pozostają do realizacji. Pełne AI 12 wymaga AI 10.

## Fundament i dalsza praca

Etap 01 ma odbiór lokalny i zdalny: pakiet/CLI, settings, HTTP/telemetry,
lokalne poświadczenia i scope, odrębne PostgreSQL AI/pgvector i MLflow,
wykonywalne kontrakty danych/run/tool, jawne migracje i Required CI.
[Uruchomienie](local-stack.md), [uprawnienia](access-control.md),
[kontrakty](data-contracts.md), [odbiór zdalny](evidence/01-remote-ci.md).

Najbliższy pełny zakres to **AI 03**: typed Parquet i immutable eksport w
cloud-native, importer/curated w AI-intelligence. Równolegle można
kontynuować [rozpoczęte narzędzia **AI 12**](agent-tools.md). Pełne zamknięcie agenta wymaga **AI 10 i 11**;
11 jest gotowy, 10 nadal należy do późniejszego ciągu danych/ML/integracji.
[Pisemna mapa etapów i repozytoriów](https://github.com/Oskar-Stachowski/retailops-cloud-native-platform/blob/main/docs/plans/ai/kolejnosc-i-repozytoria.md).

## Granice

Odbiór dotyczy lokalnego retrieval na konkretnym zatwierdzonym snapshotcie.
Zmiana dokumentacji na `main` nie aktualizuje automatycznie korpusu. Kolejna
wersja wymaga nowego snapshotu, przeglądu i ewaluacji.
Generowanie odpowiedzi, ewaluacja groundedness i pełna ścieżka agenta
pozostają do realizacji w AI 12. Pipeline danych, modele, integracja zdarzeń oraz
wdrożenie AWS/EKS mają dalsze bramki. Limit AWS na proces nie zastępuje wspólnego
budżetu wielu replik ani produkcyjnego IAM. Nie deklarujemy wdrożenia w chmurze.
