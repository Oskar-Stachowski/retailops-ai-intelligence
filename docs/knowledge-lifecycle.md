# Lifecycle indeksu i atomowy wskaźnik — etap 11

Ścieżkę rzeczywistych embeddings i użytkowej kwalifikacji opisuje
[semantyczny RAG](knowledge-semantic.md). Poniższe polecenia fake pozostają
oddzielną ścieżką testową.

Kandydat pozostaje niemodyfikowalny. Osobne rekordy opisują decyzję o korpusie,
walidację techniczną i historię aktywacji. Zmiana jednego wskaźnika nie modyfikuje
fragmentów ani wektorów; stary indeks pozostaje dostępny do odtworzenia i rollback.

Ten zakres wdraża mechanizm punktu 5 dla **syntetycznych indeksów `test`**,
w osobnym kanale `offline_test`. Fake vectors i walidacja techniczna nie odbierają
jakości retrieval. Domyślny kanał `retrieval` odmawia kwalifikacji i aktywacji
z kodem `golden_evaluation_required`; nie ma obejścia przez flagę `force`.
Rzeczywisty korpus ma [osobne zgody właściciela](knowledge-golden-jobs.md),
bez aktywnego retrieval.
[Dowody odbioru](evidence/11-lifecycle.md) obejmują czysty checkout i świeżą bazę.

## Co trzeba sprawdzić

[Instrukcja etapu 11](https://github.com/Oskar-Stachowski/retailops-cloud-native-platform/blob/b8de65b53b3f27863bb11999ca6e9a5d6d41a760/docs/plans/ai/etapy/11-rag.md)
wymaga kompletności, access metadata i golden set przed aktywacją.
[Kontrakt integracji](https://github.com/Oskar-Stachowski/retailops-cloud-native-platform/blob/b8de65b53b3f27863bb11999ca6e9a5d6d41a760/docs/plans/ai/kontrakty/integracja-agent.md)
oddziela wyniki fake od jakości semantycznej. W obecnym zakresie:

- `CorpusApproval` to jawna decyzja człowieka będącego właścicielem przeglądu
  lub zatwierdzonego pipeline. Wiąże corpus/config IDs, środowisko, właściciela,
  reviewer, zakres źródeł/statusów/access/wyłączeń i czas UTC. Nie zmienia
  pierwotnego rejestru `review_state=proposed`. Nie generujemy zgody z walidacji
  technicznej ani w imieniu właściciela rzeczywistego korpusu.
- `IndexValidation` sprawdza zamknięty graf, metadata, citation/vector binding
  oraz odtworzenie fake vectors z tekstu i konfiguracji. Zawiera jawne
  `golden_evaluation=not_evaluated_fake_vectors`. Wynik jest deterministyczny;
  poprawny strukturalnie, lecz inny wektor daje `failed`.
- Kwalifikacja testowa wymaga zapisanej, pełnej wersji kandydata, właściwej
  zgody i dokładnie odtworzonego raportu `passed`. Oba dokumenty mają checksum
  identity; źródła/status/access są związane przez pełny corpus ID.
- Kanał retrieval wymaga późniejszego rzeczywistego golden evaluation i własnej
  wersji polityki kwalifikacji. Obecna polityka dopuszcza wyłącznie test lifecycle,
  nie użytkowe wyszukiwanie ani deklarację zakończenia całego punktu 5.

Zgoda jest jawnym artefaktem z zaufanego źródła. Hash wiąże zawartość, nie podpis
ani tożsamość autora. Controlled CLI/CI i dostęp do prywatnych poświadczeń DB są
granicą lokalnego developmentu; `reviewer_kind=approved_pipeline` nie uwierzytelnia
samodzielnie pipeline. `actor` opisuje operatora zmiany, nie principal z HTTP.
Właściciel DB/Docker może ominąć tę granicę; nie jest to produkcyjny podpis release.
API viewer/operator/admin i agent nie otrzymują endpointu lub narzędzia aktywacji.

## CLI i artefakty

Walidacja jest offline i nie wymaga settings/DB:

```bash
.tools/bin/uv run --locked retailops-ai index-validate \
  --candidate .local/rag/index-candidate.json \
  --output .local/rag/index-validation.json
```

Plik jest nowy, atomowy, `0600`; nie nadpisujemy istniejącego. Wynik nie jest zgodą
na korpus. Błąd zwraca exit 2 i stały, bezpieczny kod. Dane dokumentów pozostają
w artefakcie kandydata. Schemas decyzji/raportu/request/pin są w
[knowledge/v1](../contracts/knowledge/v1/).

Kwalifikacja i zmiana wskaźnika korzystają z settings oraz izolowanej DB AI.
Poniższe interfejsy służą zatwierdzonym fixtures `APP_ENV=test`; pliki zgody
i walidacji należy przekazać z kontrolowanego procesu do kontenera maintenance:

```text
retailops-ai index-qualify --index-id ID --approval APPROVAL.json \
  --validation VALIDATION.json --lane offline_test [--env-file PATH]
retailops-ai index-activate --index-id ID --expected-generation N \
  --request-id rag-change-<32 hex> --actor OPERATOR --lane offline_test [--env-file PATH]
retailops-ai index-rollback --index-id OLD_ID --expected-generation N \
  --request-id rag-change-<nowe 32 hex> --actor OPERATOR --lane offline_test [--env-file PATH]
retailops-ai index-current --lane offline_test [--env-file PATH]
```

Bez `--lane` wybierany jest `retrieval`; kwalifikacja/zmiana są blokowane.
Tryb `offline_test` odrzuca `APP_ENV=local`. `index-current` bez aktywnej wersji
zwraca `not_active` z null pin. Nie przekazuj URL z hasłem w argv ani przez publiczny
port DB. [Uruchomienie i migracje](local-stack.md) zachowują dotychczasową procedurę.

## Transakcje, retry i rollback

Migracja **0003_rag_lifecycle** dodaje `rag_corpus_reviews`, `rag_qualifications`,
`rag_index_changes` i `rag_active_indexes`. Reviews, qualifications i historia
blokują UPDATE/DELETE. Wskaźnik nie może zostać usunięty; nowa generacja wymaga
zdarzenia wskazującego poprzedni indeks i kwalifikowany nowy indeks w tym samym
środowisku/kanale. Deferred constraint nie pozwala zatwierdzić samego zdarzenia
bez odpowiadającego mu wskaźnika.

Writer ma jedną transakcję i advisory lock. `expected_generation` jest warunkiem
porównania: pierwszy zapis oczekuje 0, każdy kolejny zwiększa generację o 1.
Dwie zmiany oczekujące tej samej generacji mają jednego zwycięzcę; druga dostaje
konflikt zamiast nadpisania. Aktualnie serialization lock jest wspólny dla store
oraz lifecycle; to świadome ograniczenie przepustowości lokalnego MVP.

`request_id` jest idempotency key. Ponowienie identycznej operacji zwraca
`replayed`, bez zapisu. Inna operacja pod tym samym ID jest odrzucana. Retry starej
operacji po późniejszym swapie zwraca historyczny receipt/pin, **nie zmienia bieżącego
wskaźnika**. `index-current` odczytuje bieżący stan. Nową zmianę lub rollback
wykonuj z nowym request ID oraz właśnie odczytaną generacją.

Rollback wymaga indeksu wcześniej aktywnego w tym samym kanale/środowisku.
Odtwarza dokładny poprzedni manifest pod nową generacją; nie cofa licznika ani
historii. Kwalifikowany indeks, który nigdy nie był aktywny, nie jest celem rollback.
Wyjątek po zapisie historii lub wskaźnika wycofuje oba i zachowuje poprzedni stan.

Odczyt wskaźnika, kwalifikacji i manifestu jest jednym SQL statement snapshot.
`IndexPin` zawiera niezmienny manifest z embedding config, IDs korpusu/fragmentów
i checksumami oraz generację/review/report IDs. Caller zachowuje ten pin i używa
jego index ID przez cały run; kolejne kroki nie odczytują ponownie current.
[Retrieval](knowledge-retrieval.md) używa tego interfejsu w `test`; agent jest planowany.

Podstawa: [PostgreSQL 16 — blokady](https://www.postgresql.org/docs/16/explicit-locking.html),
[statement snapshots](https://www.postgresql.org/docs/16/transaction-iso.html)
i [deferred constraint triggers](https://www.postgresql.org/docs/16/sql-createtrigger.html).

## Granice

Kwalifikacja testowa nie potwierdza jakości semantycznej. Użytkowa kwalifikacja
korzysta z osobnego [release semantycznego](knowledge-semantic.md), udanego runa
oraz odebranego golden set. Source status `specified` nadal oznacza plan,
a `verified` przypięty pomiar. Stare kandydaty i zgody pozostają immutable;
pilne blokady dokumentów obowiązują również stare piny. Nie ma cache wyników
współdzielonego między użytkownikami. Podpisane attestations i role produkcyjne
należą do późniejszego wdrożenia.
