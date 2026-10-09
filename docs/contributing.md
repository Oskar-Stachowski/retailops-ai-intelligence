# Zasady zmian

Rozwijaj jedną spójną funkcję na branchu `ai/...`. Commituj kod, kontrakty,
dokumentację i mały dowód razem. PR zaczyna się od problemu i zachowania,
podaje wykonane kontrole, pozostały zakres, zgodność oraz sposób wycofania.

Przed PR wykonaj `make ci-local`. Required CI nie filtruje ścieżek: także
nowe moduły, docs i kontrakty uruchamiają bramki. `required-result` wymaga
sukcesu wszystkich jobs; skipped/cancelled/failed nie oznacza zaliczenia.
Actions są przypięte pełnym SHA, zmiany wersji mają osobny przegląd.
Workflow waliduje checkout PR, ma wyłącznie `contents:read` i nie komentuje PR-ów.

GitHub main wymaga PR, aktualności gałęzi i `required-result` z GitHub Actions,
również dla administratorów. Odbiór obejmuje zdarzenie PR i późniejszy push na main.
Ręczne workflow_dispatch służy diagnostyce: jego zielone jobs nie zastępują
wymaganej kontroli PR. Workflow jawnie obejmuje opened/synchronize/reopened
oraz push na main. Gałąź bez PR korzysta z workflow_dispatch; otwarcie draft PR
włącza automatyczne pełne kontrole kolejnych commitów. Push na gałąź z PR nie
uruchamia drugiej kopii Required CI. Nie stosuj pominiętych jobs jako zielonego
zamiennika pełnej akceptacji.

Ciężkie jobs zależą od sukcesu `checks` i `secrets`. Błąd preflight kończy
`required-result` błędem, a nie zaliczeniem pominiętych testów. Cztery shardy
zbierają cały aktualny zestaw testów i publikują plan, JUnit oraz czasy faz
setup/call/teardown. Agregator sprawdza pełną, rozłączną kolekcję i wykonanie
każdego wybranego testu. Przy ponowieniu nieudanych jobs zachowuje wcześniejsze
udane shardy tego samego runu/commitu; zawsze sprawdza najnowszą próbę sharda.
Runner atomowo zapisuje raport po kolekcji, na granicach plików i podczas
wykonywania zakończonych faz co najwyżej raz na 10 sekund. Przerwane raporty
mają `exit_code: null` i `reporting_state: in_progress`; agregator ich nie
zalicza. Podział korzysta z przejrzanych czasów plików, zachowując wszystkie
testy. Limit joba testów wynosi 90 minut i nie zmienia budżetów diagnostyki.

Podział acceptance przenosi całe bramki: `full-raw-dq-check` do `ci-forecast`,
`day-qualification-check` do `ci-detectors`. Osobny `persistence-forecast`
obejmuje input-store, publication, read, catalog i evaluations. `persistence`
nadal obejmuje Compose, MLflow, lifecycle, queue i pełny v12 backup/restore.
Oba jobs są wymagane i używają odrębnych runnerów oraz własnych zasobów.

Sesje AI 09/10/12 integrują wspólne zmiany CI przez merge aktualnego `main`
przed końcowym Required CI. Konflikty rozwiązuj zachowując nowe raporty,
preflight, wszystkie wspólne i etapowe bramki oraz dokładne `needs` agregatora.
AI10 zachowuje dodatkowo observation replay/broker/outbox; AI12 zachowuje
agent-evaluate i swoje kontrole kontraktów. Nie przywracaj starego workflow
przez wybór całego pliku z gałęzi etapu. Aktualizuj testy kontraktu CI wraz
z dozwolonym rozmieszczeniem bramek, nigdy przez usunięcie kontroli pokrycia.

Zachowuj granice z [ADR](architecture/decisions.md): osobne DB, brak kopii
generatora/UI i bezpośredniego odczytu operacyjnej bazy. Nowe dane i wyniki
mają wersje i lineage. Cechy nie mogą używać przyszłych danych ani truth.
Testy błędów mają wykazywać blokadę, nie tylko istnienie odpowiedniej funkcji.

Sekrety, zbiory danych, modele, wektory, logi oraz state/plan Terraform
pozostają poza Git. Tiny fixtures wymagają jawnego wyjątku do .gitignore
i przeglądu. Przed publikacją sprawdź dokładny diff i wynik skanera.
[Licencja](../LICENSE) jest MIT, zgodnie z bazowym RetailOps.
