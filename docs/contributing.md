# Zasady zmian

Rozwijaj jedną spójną funkcję na branchu `ai/...`. Commituj kod, kontrakty,
dokumentację i mały dowód razem. PR zaczyna się od problemu i zachowania,
podaje wykonane kontrole, pozostały zakres, zgodność oraz sposób wycofania.

Przed PR wykonaj `make ci-local`. Required CI nie filtruje ścieżek: także
nowe moduły, docs i kontrakty uruchamiają bramki. `required-result` wymaga
sukcesu wszystkich jobs; skipped/cancelled/failed nie oznacza zaliczenia.
Actions są przypięte pełnym SHA, zmiany wersji mają osobny przegląd.
Workflow waliduje checkout PR, ma wyłącznie `contents:read` i nie komentuje PR-ów.

Po utworzeniu zdalnego repo i pierwszym zielonym przebiegu skonfiguruj
PR-before-merge, aktualność gałęzi i wymagany `required-result` również dla
administratorów. Obecność pliku workflow nie dowodzi takiej ochrony.

Zachowuj granice z [ADR](architecture/decisions.md): osobne DB, brak kopii
generatora/UI i bezpośredniego odczytu operacyjnej bazy. Nowe dane i wyniki
mają wersje i lineage. Cechy nie mogą używać przyszłych danych ani truth.
Testy błędów mają wykazywać blokadę, nie tylko istnienie odpowiedniej funkcji.

Sekrety, zbiory danych, modele, wektory, logi oraz state/plan Terraform
pozostają poza Git. Tiny fixtures wymagają jawnego wyjątku do .gitignore
i przeglądu. Przed publikacją sprawdź dokładny diff i wynik skanera.
[Licencja](../LICENSE) jest MIT, zgodnie z bazowym RetailOps.
