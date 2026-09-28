# Odbiór kontraktów etapu 01

Pomiar **2026-09-28**, macOS ARM64, Python 3.11.15, uv 0.12.19.
Implementacja: `ae21d4e37dc56fb6d12c53f103190aa663b9a0f0`;
baza: `7bd17e65a3bb5416620526a8b48e14986923d140`.
[Raport](01-contracts.json), [kontrakty i polecenia](../data-contracts.md).

Kontrole obejmują wykonywalne schemas/semantykę i zgodność kodu z fixtures.
Przykłady są syntetycznymi metadanymi, nie danymi źródłowymi lub działającymi modelami.
Dotychczasowe HTTP/DB/MLflow pozostają zakresem [osobnego pomiaru persistence](01-persistence.md).

## Zakres prób

- Dziesięć rodzin dokumentów, Draft 2020-12, wymagane 1.0 i zamknięte pola.
  Niezależna walidacja jsonschema oraz Pydantic. 55 ręcznie zapisanych przypadków
  błędnych rozróżnia structural i semantic rejection.
- Ownership, facts/truth/labels, missing/null vs zero, UTC i granica mikrosekundy,
  przyszłe fakty vs znane plany, pełny grain/horizon i zakresy dat.
- Kanoniczne hashe logiczne każdej roli, osobne byte checksums, zmiana config/seed/
  provenance/classification, stabilność względem czasu zapisu/ścieżki/kolejności,
  brak własnych IDs i cyklu hashów.
- Zamknięty graf, spójne model/run/output refs, dojrzałe train/selection windows,
  cutoff przed testem, etykiety dostępne w treningu; wygenerowanie wyniku w runie.
- Legalne przejścia stanów, przypięte wejścia, zakaz partial output i przepisania
  terminalnego wyniku. Bounded read tool, scope/freshness, no_data/error i duplikaty.
- Bezpieczny offline CLI; duplicate JSON keys/NaN/Infinity/rozmiar są odrzucane.
  Celowo osłabiony snapshot daje niezerowy exit kontraktowego gate.
- Required CI obejmuje nowe moduły i contracts-check. Poprawiono wyrażenie
  PERSISTENCE_RESULT w required-result; guard i actionlint wykrywają błędny workflow.

## Kontrole lokalne

`make ci-local` — exit 0: **208 passed in 8.83s**, bez pominięć.
Ruff/format, Mypy strict (40 plików), docs, intelligence snapshot check,
wheel/sdist, Compose config oraz Gitleaks historii i katalogu przechodzą.
Actionlint przechodzi. Diagnostyczne OpenAPI/HTTP fixtures nie zostały zmienione.
Nowy jsonschema 4.26.0 jest zależnością dev; runtime nie dodaje biblioteki ML.

## Czysty checkout i paczka runtime

Czysty checkout implementacji, nowe venv: `make bootstrap ci-local` — exit 0,
**208 passed in 9.56s**, bez pominięć; wszystkie bramki oraz oba skany przechodzą.
Próba celowo osłabionego schema wykrywa exit 1; checkout pozostaje czysty.

Wheel z tego checkoutu zainstalowano w osobnym venv z produkcyjnymi zależnościami
z uv.lock i sprawdzeniem hashów. `uv pip check` przechodzi; import pochodzi z
site-packages, jsonschema dev nie jest zainstalowany. Poza źródłami i przy celowo
błędnej konfiguracji środowiskowej valid bundle daje exit 0, late feature exit 2,
a prywatne wejście tylko stały błąd bez wartości. Nie utworzono katalogu artefaktów.
Checksum wheel jest w raporcie.

## Ograniczenia

Schemas nie implementują importera, source quality gates, odczytu/byte verification,
historycznych korekt, treningu lub serving API. Stan runa nie jest bazą ani workerem.
Tool nie ma executora/auth/agenta. Nie dodano zdarzeń ani streamingu.
Deklaracje passed w fixture nie są dowodem gotowości rzeczywistych danych/modelu.

Nie ponawiano pełnego Compose crash/restart: runtime persistence i migracje
nie uległy zmianie. Powiązany wcześniejszy pomiar pozostaje jawnie datowany.
Nowe commity są lokalne; zdalnego CI tego zakresu nie odebrano.
Etap 01 pozostaje in_progress; bieżące wymagania są w [statusie](../STATUS.md).

