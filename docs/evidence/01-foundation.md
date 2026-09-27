# Weryfikacja pierwszego fundamentu AI 01

2026-09-27, macOS ARM64, Python 3.11.15, uv 0.12.19.
Źródło planu RetailOps: `8a9e620`. Hash kodu, lockfile, środowisko,
wersje i zakres kontroli: [zapis pomiarów](01-foundation.json).

## Zakres

Niezależne repo, MIT, zasady zmian/security, ADR-01–10, pakiet, typed settings,
CLI `version/config-check`, kontrakt metadanych v1 oraz podstawowe Required CI.
Nie jest to odbiór całego etapu 01.

## Wykonane kontrole lokalne

- `uv lock` i `uv sync --locked`: przypięte zależności i instalacja pakietu.
- `make lint type-check test package`: Ruff/format, Mypy strict (6 plików),
  **22 passed in 1.43s**, wheel i sdist.
- `gitleaks dir . --redact --no-banner`: brak znalezisk.
- `actionlint -shellcheck='' .github/workflows/required-ci.yml`: poprawny workflow.
- Testy obejmują brak i złą konfigurację bez ujawnienia wartości, brak wskazanego
  dotenv, pierwszeństwo env, odrzucenie pustej ścieżki, niezgodnego kontraktu,
  nieprzypiętych actions, pominiętych bramek i filtrów ścieżek.

Pełne komendy i instalacja narzędzi są w [development](../development.md).
Zapisane metadata PyPI potwierdzają koła CPython 3.11 dla przyszłych scikit-learn
i TensorFlow na macOS ARM64/Linux x86_64; tych bibliotek nie instalowano ani
nie uruchamiano w nowym repo.

Workflow jest przygotowany, bez zdalnego uruchomienia i ochrony main.
Nie wykonano prób serwera HTTP, PostgreSQL, MLflow, brokera, modeli ani AWS.
Następny zakres: [status projektu](../STATUS.md).
