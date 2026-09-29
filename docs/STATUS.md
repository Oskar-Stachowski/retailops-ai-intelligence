# Aktualny status

Aktualizacja: **2026-09-29**. **Etap 11 — RAG jest odebrany lokalnie.**
[Instrukcja użytkowa](knowledge-semantic.md) opisuje rzeczywiste embeddings,
przygotowanie, kwalifikację, aktywację i rollback. [Końcowy odbiór](evidence/11-completion.md)
wiąże implementację z pomiarami i ograniczeniami. Etap 11 jest opublikowany na
chronionym `main` przez [PR #4](https://github.com/Oskar-Stachowski/retailops-ai-intelligence/pull/4);
[Required CI na main](https://github.com/Oskar-Stachowski/retailops-ai-intelligence/actions/runs/36461392661)
ma `success`.

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
AI 12 ma osobny worktree; zaakceptowany AI 03 z `main` jest włączony w branch
`ai/12-tools`. Branch 03 jest opublikowany w [PR #5](https://github.com/Oskar-Stachowski/retailops-ai-intelligence/pull/5).
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

## Rozpoczęty AI 12

[Pierwszy zakres](agent-tools.md) obejmuje osiem narzędzi tylko do odczytu,
typowane request/result schemas, principal z prywatnych poświadczeń, jawne
`assistant:query` i prawa poszczególnych źródeł, kontrolę całego scope oraz
wspólny budżet wywołań. Adapter wiedzy korzysta z jednego serwerowego pinu
AI 11. Przyszłe narzędzia biznesowe mają ścisłe kontrakty i testowe odpowiedniki;
brak rzeczywistego źródła jest jawnym `unavailable`.
[Odbiór](evidence/12-tools.md) opisuje lokalne testy i granice.
Zakresy AI 12 są zapisywane lokalnie na osobnym branchu `ai/12-tools`.

[Drugi zakres](agent-chat.md) dodaje konfigurację modelu/budżetu, sześć
wersjonowanych promptów, checksum całej konfiguracji i schematów, kontrolę
CLI offline oraz scripted fake chat. Sesja modelu dzieli deadline z narzędziami;
retry i jedna naprawa zużywają wspólny budżet tokenów/kosztu. Draft przechodzi
kontrolę zakresu planowanych narzędzi oraz powiązania referencji, cytatów,
as-of i freshness z rzeczywiście pobranymi wynikami.
[Odbiór](evidence/12-chat.md) opisuje testy i granice tego przygotowania.

[Trzeci zakres](agent-graph.md) dodaje acykliczny LangGraph, jedno dogranie
brakujących danych i jedną wspólną naprawę, dokładne wywołania z typed requestu,
katalog kanonicznych faktów i sprawdzalne porównanie okresów sprzedaży.
Podmienione liczby, jednostki, okresy, swobodne wnioski, braki dowodów,
konfliktujące prognozy i niejednoznaczny mapping mają kontrolowane wyniki.
Bezpieczny trace ma odczyt właściciela, scope, retencję i limit w pamięci.
[Odbiór](evidence/12-graph.md) podaje testy i granice profilu.

[Czwarty zakres](agent-evaluation.md) dodaje deterministyczną politykę trzech
kandydatów do przeglądu przez człowieka, wiązanie do wybranych faktów i expiry.
Wersjonowany golden obejmuje 50 przypadków, w tym sześć pytań z AI 11,
niezależnie zapisane oracles i scripted replies. Release wiąże config grafu,
politykę/prompty v4, schemas, golden, ewaluator i lock. `make agent-evaluate`
jest bramką `make check`; pomiar dotyczy jawnych fixtures i kanonicznych twierdzeń.
[Odbiór](evidence/12-evaluation.md) podaje wyniki oraz ograniczenia.

[Piąty zakres](assistant-api.md) dodaje kontrakt HTTP queries/runs, lokalne
auth i kontrolę całego scope, trwały zapis run/odpowiedź/review candidate
w bazie AI oraz wspólne admission PostgreSQL. Trace sprawdza również cofnięte
prawa narzędzi/wiedzy; admin wymaga osobnego assistant:audit.
[Odbiór](evidence/12-assistant.md) opisuje rzeczywiste próby PG/HTTP i granice.
Standardowe serve obsługuje opcjonalny runtime dokumentacyjny opisany niżej;
bez jego konfiguracji query daje 503. Fake jest dostępny wyłącznie w testach.

[Chat Bedrock](agent-bedrock.md) ma adapter Converse/CountTokens, kontrolę
formularza/dostępu konta, zweryfikowane profile EU, circuit breaker i ograniczony
smoke. Formularz oraz aktywacja Haiku 4.5 i Sonnet 4.6 zostały wykonane po
potwierdzeniu danych projektu osobistego. Zgoda kosztowa wynosi **1,50 USD
łącznie**; [rejestr wszystkich prób](evidence/12-bedrock-budget.json) zachowuje
**1,4017210 USD** szacunków/rezerw, w tym pełny cap przerwanej próby.

[Poprzedni mieszany test Sonnet](evidence/12-bedrock-real.md): **5/6**, w tym
3/3 przypadki biznesowe i 2/2 zabezpieczenia serwera. Historyczny przypadek dokumentacji
dał `invalid_evidence`: fixture nie zawierał odpowiedzi, chociaż etykieta
wymagała `answered`. Wynik i koszt tej próby pozostają zachowane.
[Bieżąca poprawka dowodów dokumentowych](agent-document-evidence.md) wprowadza
`typed-facts-v2`, prompty v4 oraz golden v2. Pytanie musi mieć jawne wymagania
i pobrane źródła pokrywające każde z nich; sam cytat, score lub status
`verified` nie wystarcza. Sześć oryginalnych pytań ma poprawione fixtures/etykiety
opisane w [przeglądzie](evidence/12-document-label-review.json).
[Ponowny test Sonnet](evidence/12-bedrock-runs/sonnet-4-documents.json)
zaliczył **6/6 pytań dokumentacji**, bez napraw: pięć kompletnych odpowiedzi
i jedno poprawne `insufficient_evidence`. Koszt szacowany wyniósł
**0,2439129 USD**. Aktualny stan budżetu uwzględnia też późniejszy test runtime.
[Odbiór poprawki](evidence/12-document-evidence.md) zawiera pełne wyniki.
Sonnet zaliczył ten ograniczony test; pełny golden rzeczywistego
modelu i retrieval nadal wymaga odbioru.
Test używa rzeczywistego chatu i syntetycznych narzędzi/retrieval.

[Runtime dokumentacyjny](assistant-document-runtime.md) podłącza standardowe
API do dwóch jawnych tras pytań, zweryfikowanego importu AI 03, rzeczywistego
PostgreSQL/pgvector, Titan i Sonnet. Resolver zachowuje source UUID i sprawdza
przypisanie kanału w całym okresie. Przypięte konfiguracje, brak automatycznej
zmiany indeksu i kontrola pełnych dowodów pozostają obowiązkowe.
[Ponowny odbiór](evidence/12-bedrock-runs/sonnet-6-runtime.json): **2/2**,
4 Converse + 2 embeddings, bez napraw; odpowiedzi i trace zapisane w bazie,
obcy operator nie ma dostępu. Projekcja sprawdzonych faktów ogranicza kontekst
wysyłany do modelu. [Evidence zakresu](evidence/12-document-runtime.md) podaje
regresję, testy SQL, pierwotną nieudaną próbę i ograniczenia.
Pozostaje **0,0982790 USD** zatwierdzonego budżetu; dalszy większy test wymaga
nowej zgody. Kwoty są szacunkami/rezerwami, nie rachunkiem AWS.

Następny zakres bez AI 10: rozszerzenie zbioru pytań/routingu i kwalifikacja
rzeczywistego modelu oraz retrieval na pełnym golden. Obecne dwie trasy nie
są ogólnym plannerem języka naturalnego. Resolver source działa na przyjętym
syntetycznym fixture AI 03; adaptery rzeczywistych źródeł biznesowych i ML
pozostają do podłączenia. Pełne AI 12 wymaga AI 10 i E2E
sugestii/outbox/v2/read API/UI. Profil sprawdza kanoniczne fakty i literalne
cytaty, bez deklaracji jakości swobodnych odpowiedzi. Etykiety i progi są
lokalnym profilem developmentu, bez niezależnego business/model approval.

## Fundament i dalsza praca

Etap 01 ma odbiór lokalny i zdalny: pakiet/CLI, settings, HTTP/telemetry,
lokalne poświadczenia i scope, odrębne PostgreSQL AI/pgvector i MLflow,
wykonywalne kontrakty danych/run/tool, jawne migracje i Required CI.
[Uruchomienie](local-stack.md), [uprawnienia](access-control.md),
[kontrakty](data-contracts.md), [odbiór zdalny](evidence/01-remote-ci.md).

[Bieżący odbiór danych](evidence/03-06-cross-repo.md) jest wspólny z RetailOps.
Po pełnej bramce 03 można rozdzielić forecasting **04** w AI i ledger **06**
w RetailOps; nowe źródło po 06 wymaga ponownego importu i zależnych ocen. Równolegle można przygotować
[rozwijany runtime **AI 12**](assistant-api.md). Pełne zamknięcie agenta wymaga **AI 10 i 11**;
11 jest gotowy, 10 nadal należy do późniejszego ciągu danych/ML/integracji.
[Pisemna mapa etapów i repozytoriów](https://github.com/Oskar-Stachowski/retailops-cloud-native-platform/blob/main/docs/plans/ai/kolejnosc-i-repozytoria.md).

## Granice

Odbiór dotyczy lokalnego retrieval na konkretnym zatwierdzonym snapshotcie.
Zmiana dokumentacji na `main` nie aktualizuje automatycznie korpusu. Kolejna
wersja wymaga nowego snapshotu, przeglądu i ewaluacji.
Generowanie swobodnych odpowiedzi, ewaluacja rzeczywistego modelu i pełna
ścieżka agenta pozostają do realizacji w AI 12. Pipeline danych, modele, integracja zdarzeń oraz
wdrożenie AWS/EKS mają dalsze bramki. Limit AWS na proces nie zastępuje wspólnego
budżetu wielu replik ani produkcyjnego IAM. Nie deklarujemy wdrożenia w chmurze.
