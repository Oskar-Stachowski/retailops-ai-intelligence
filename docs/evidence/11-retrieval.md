# Odbiór retrieval, filtrów i golden set — etap 11

**2026-09-28 · mechanizmy offline/test odebrane; użytkowa aktywacja zamknięta.**
Implementacja `1e6fbf14ca29f4758f97f5fd81754c66f6e18336` na `ai/rag-corpus`,
baza `4fb5d28`. [Pomiar JSON](11-retrieval.json),
[pełny raport golden](11-retrieval-golden.json),
[instrukcja](../knowledge-retrieval.md), [status](../STATUS.md).

## Zakres odebrany

Exact cosine pgvector działa na jednym przypiętym, kwalifikowanym indeksie.
SQL filtruje environment/space/dimension, repo/type/status/access oraz live deny
przed rankingiem i outputem. Tie-break po chunk ID, dywersyfikacja repo/dokumentów,
top-k do 5 i budżet serializowanego kontekstu do 24 KB/6000 estymowanych tokenów
mają deterministyczne reguły. Cytat zachowuje source SHA/path/heading/linie,
checksums i metadata statusu/fact scope.

Serwerowy principal i `knowledge:read` mają osobny, jawny knowledge scope.
Endpoint `/api/v1/knowledge/search` odmawia rozszerzenia zakresu i nie dziedziczy
odczytu z admin. Purpose implementation/verified/history ogranicza stany źródeł.
Treść wyniku jest `untrusted_reference`, bez odpowiedzi LLM lub wykonania narzędzi.
Migration `0004_rag_denials` odbiera dostęp do document ID w każdej wersji
danego środowiska, również przy ponownym użyciu starego pin.

## Kontrole

- **498 testów** przechodzi w worktree (58,92 s) i świeżym checkoutcie commitu
  (66,14 s). 35 nowych przypadków obejmuje authorization, scope/status/history,
  źródła i remisy, context limits, niepoprawne wektory, injection, HTTP oraz golden.
  Po końcowym zaostrzeniu history/positive-label guards powtórzono te 35 przypadków;
  czysty checkout obejmuje cały finalny kod.
- W obu checkoutach przechodzą Ruff/format, strict Mypy (76 plików), docs,
  snapshots intelligence/access/knowledge, wheel/sdist, Compose config i Gitleaks.
  Actionlint przechodzi. Nie dodano zależności runtime; konfiguracja retrieval
  znajduje się również w zainstalowanym pakiecie i obrazie API.
- PostgreSQL 16/pgvector 0.8.6 na dziesięciu syntetycznych dokumentach potwierdza
  exact cosine i zgodność kolejności z niezależną ścieżką offline. Identyczna treść
  pod różnymi access/status/document IDs nie ujawnia forbidden fragmentów.
  Sprawdzono repo/type/status/history filters, dywersyfikację i pusty kontekst
  przy budżecie jednego tokena. Najdłuższy pomiar fixture SQL w świeżym odbiorze
  wyniósł około 4,52 ms; nie jest to pomiar produkcyjnego korpusu.
- Osobny rzeczywisty proces HTTP na loopback używa prywatnych credentials
  i prawdziwej DB. Kontrole 401/403/422, no-store i brak credential w błędach
  przechodzą. Po wcześniejszym odczycie zatwierdzono deny; następna odpowiedź
  odmawia tego dokumentu. Próba usunięcia blokady jest odrzucana przez SQL.
- Adversarial fixture z instrukcjami zmiany roli, aktywacji, zakupu i ujawniania
  sekretów pozostaje materiałem referencyjnym. Nie ma efektu narzędziowego lub
  zmiany tożsamości. Ten korpus jest poza zwykłym rejestrem produkcyjnym.
- Po swapie zachowany pin nadal zwraca własny indeks i egzekwuje nowe deny.
  Rollback odtwarza wcześniejszy manifest. SIGKILL/restart i down/up zachowują
  pełny bieżący pin; dotychczasowe próby outage, recovery, MLflow i izolacji
  nadal przechodzą. Stosy użyte do odbioru zatrzymano; wolumeny pozostają.

Required CI persistence wykonuje te próby przez `compose-smoke`. Odbiór jest
lokalny; nie wykonano push lub zdalnego Required CI dla tych commitów.

## Golden set i rzeczywisty korpus

Przed pierwszą ewaluacją zapisano **36 pytań i progi**. Etykiety wskazują
oczekiwane sekcje, forbidden sources, status, answerability, role/scope oraz
required/forbidden tools. Nie pochodzą z wyników rankera. Korpus i etykiety są
propozycją do przeglądu, nie zaakceptowanym release.

Prywatna ewaluacja niezmienionego kandydata 20 dokumentów/302 fragmentów daje:

| Kontrola | Wynik fake | Próg |
|---|---|---|
| Recall@5 | 0,01786 | ≥0,8 |
| MRR | 0,03571 | ≥0,6 |
| Binding cytatów | 1,0 | 1,0 |
| Krytyczne przypadki | 7/7 | 100% |
| Groundedness odpowiedzi | niemierzone; brak odpowiedzi | ≥0,95 |
| Agent/tools | etykiety; brak runtime agenta | osobny odbiór w 12 |

Raport ujawnia nieprzechodzącą jakość naturalnych pytań z fake i ma
`measured_thresholds_passed=false`, `activation_allowed=false`.
Binding cytatu oznacza zgodność z przypiętym chunkem, nie prawdziwość twierdzenia.
Nie udzielono zgody na rzeczywisty korpus i nie aktywowano go. Idealny wynik
syntetycznej ewaluacji również nie umożliwia aktywacji; sprawdza to test negatywny.

Zainstalowany CLI w czystym checkoutcie, poza katalogiem repo, z innym hash seed
i niepoprawnymi settings serwisu odtworzył te same metryki, outcomes i chunk IDs.
Czasy wykonania pozostają osobnym pomiarem. Raport zapisano jako nowy plik 0600;
niezależny JSON Schema validator sprawdził jego strukturę. Koszt modelu offline
wynosi 0 USD; nie było AWS/Bedrock ani real embeddings.

## Pozostały zakres i postęp

**Około 80% etapu 11** to szacunek implementacji siedmiu zakresów, uwzględniający
otwarte odbiory. Nie jest metryką jakości raportu. Do zamknięcia pozostają
administracyjne index runs/read-current HTTP, przegląd/odświeżenie źródeł
i etykiet, near duplicates oraz release manifest/kwalifikacja użytkowa.
Jakość z real embeddings, Bedrock smoke i agent mają osobny odbiór w etapie 12.

Istniejące manifesty creation/storage pozostają immutable, z revision 0002
i `retrieval_version=not_implemented`; bieżący wynik/raport osobno wiąże nową
wersję i config retrieval. Nie ma production RLS/OIDC, globalnego cache,
administracji HTTP ani gwarancji 5 s jako deadline całego requestu.
