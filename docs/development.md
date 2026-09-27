# Praca lokalna

Wymagany Python **3.11.15**, uv **0.12.19** i Make. Zależności oraz backend
builda są przypięte w `pyproject.toml` i `uv.lock`. Środowiska docelowe:
macOS ARM64 do rozwoju i Linux x86_64/CPU do CI oraz przyszłego challengera.

## Pierwsze uruchomienie

Z katalogu repo, jeśli uv nie jest zainstalowane:

```bash
python3.11 -m venv .tools
.tools/bin/python -m pip install uv==0.12.19
make bootstrap UV=.tools/bin/uv
.tools/bin/uv run --locked retailops-ai --help
.tools/bin/uv run --locked retailops-ai version
.tools/bin/uv run --locked retailops-ai config-check --env-file .env.example
make check UV=.tools/bin/uv
```

Przy uv dostępnym w PATH można użyć `make bootstrap` i `make check` bez override.
Wszystkie polecenia projektu korzystają z tego samego pakietu, także
`uv run --locked python -m retailops_ai`. `config-check` niczego nie tworzy,
nie sprawdza DB, nie wykonuje zapytań sieciowych i nie potwierdza readiness usług.

`APP_ENV` wymaga `local` lub `test`; `ARTIFACT_ROOT` jest niepustą ścieżką,
interpretowaną względem bieżącego katalogu, jeżeli jest względna.
`LOG_LEVEL` to DEBUG/INFO/WARNING/ERROR, domyślnie INFO. Nieznane klucze
w jawnie wskazanym dotenv są błędem. Zmienne procesu mają pierwszeństwo
przed plikiem. Brak konfiguracji, pliku lub błędna wartość daje exit 2
i kod błędu bez wyświetlania wartości. `--help` i `version` nie wymagają konfiguracji.
Plik `.env` nie jest automatycznie wczytywany: wybierz go przez `--env-file .env`.

## Kontrole

| Polecenie | Zakres |
|---|---|
| `make lint` | Ruff i format |
| `make type-check` | Mypy strict dla pakietu i helperów |
| `make test` | Przypadki poprawne i błędne; kontrakt metadanych/CI |
| `make docs-check` | Lokalne linki i minimalny kontrakt workflow |
| `make package` | Wheel i sdist w ignorowanym `dist/` |
| `make check` | Wszystkie powyższe |
| `make secrets` | Gitleaks 8.30.1: historia Git i aktualny katalog, z redakcją |
| `make ci-local` | `check` oraz `secrets` |

Gitleaks jest osobnym narzędziem; zainstaluj wersję 8.30.1 z
[oficjalnego wydania](https://github.com/gitleaks/gitleaks/releases/tag/v8.30.1)
albo wskaż `GITLEAKS=/ścieżka/do/gitleaks`. Workflow używa tej samej wersji.
Brak narzędzia przerywa kontrolę. Na świeżym repo skan historii wymaga pierwszego commitu.

Zmiany zależności wykonuj przez uv, aktualizując lockfile w tej samej zmianie.
CI stosuje `--locked`, więc rozbieżność pyproject/lock nie naprawia się po cichu.
Po zmianie kontraktu metadanych odśwież schema fixture i przykład oraz
przeprowadź testy kompatybilności. Nie nadawaj jej znaczenia kontraktu forecastingu.
