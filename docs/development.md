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
Kontrole instalują opcjonalny extra `snapshot` dla typed importera; przy ręcznym
uruchomieniu całych testów użyj `uv run --locked --extra snapshot pytest`.
[Osobne CLI importera](source-snapshot-import.md) nie wymaga konfiguracji API/DB.
[CLI curated](curated.md) buduje dane i odczytuje historię as-of z tego samego extra.
[CLI forecast](forecasting.md) definiuje zadanie i buduje przypięty kalendarz
bez modeli, DB i AWS.
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
| `make test` | Konfiguracja, HTTP, provider fakes, kontrakty/CI oraz rzeczywisty proces na loopback |
| `make docs-check` | Lokalne linki i minimalny kontrakt workflow |
| `make handoff-check` | Odbiór przypiętego fixture source snapshot 1.0.0 |
| `make snapshot-import-check` | Dwa świeże import/reimport/verify, immutable bytes i limity zasobów smoke |
| `make curated-check` | Dwa świeże import/build/rebuild/verify/as-of, mapping/quarantine i zasoby smoke |
| `make forecast-calendar-check` | Zweryfikowany parent, powtarzalny kalendarz, historia i znane plany z jednym cutoffem |
| `make forecast-features-check` | Aktywny panel, typed Parquet inputs, zgodność z historią i niezmienny rerun |
| `make forecast-manifests-check` | Formalny feature set i schema smoke; krótki calendar nie udaje kwalifikacji splitu |
| `make forecast-baselines-check` | Baseline'y as-of i brak kwalifikacji krótkiego fixture; z jawnymi rodzicami temporalny evaluator, replay i immutable rerun |
| `make package` | Wheel i sdist w ignorowanym `dist/` |
| `make check` | Wszystkie powyższe |
| `make secrets` | Gitleaks 8.30.1: historia Git i aktualny katalog, z redakcją |
| `make ci-local` | `check` oraz `secrets` |
| `make serve` | Lokalny serwer HTTP z jawnym dotenv |
| `make compose-up/down` | Lokalny stos DB/API/MLflow, jawne migracje, zachowane wolumeny |
| `make compose-config` | Walidacja Compose bez wypisywania sekretów |
| `make compose-smoke` | Rzeczywiste próby persistence i awarii, następnie shutdown |
| `make contracts-check` | Porównanie intelligence/access/knowledge/forecast snapshots z kodem, walidacja struktury rejestru korpusu |
| `make contracts` | Regeneracja HTTP oraz intelligence/access/knowledge/forecast schemas/examples do przeglądu |

Gitleaks jest osobnym narzędziem; zainstaluj wersję 8.30.1 z
[oficjalnego wydania](https://github.com/gitleaks/gitleaks/releases/tag/v8.30.1)
albo wskaż `GITLEAKS=/ścieżka/do/gitleaks`. Workflow używa tej samej wersji.
Brak narzędzia przerywa kontrolę. Na świeżym repo skan historii wymaga pierwszego commitu.

Zmiany zależności wykonuj przez uv, aktualizując lockfile w tej samej zmianie.
CI stosuje `--locked`, więc rozbieżność pyproject/lock nie naprawia się po cichu.
Po zmianie kontraktu metadanych odśwież schema fixture i przykład oraz
przeprowadź testy kompatybilności. Metadane aplikacji i HTTP mają osobne znaczenie od [kontraktów danych/run/tool](data-contracts.md).
Przykłady intelligence są syntetycznymi metadanymi; contract-check nie odczytuje
artefaktów ani nie wykonuje quality gates.


Serwis i granice dostępu opisuje [instrukcja HTTP](http-service.md).
Test procesu wymaga możliwości otwarcia krótkotrwałego portu na loopback;
w sandboxie bez socket bind nie jest pomijany, tylko kończy się błędem.
Testy ASGI/provider fakes nie używają sieci zewnętrznej. Aktualny klient testowy
httpx2 odpowiada wymaganiom przypiętej wersji Starlette.

Persistence i wymagania Docker opisuje [instrukcja lokalnego stosu](local-stack.md).

[Tożsamość i uprawnienia API](access-control.md) opisują access-init, prywatne
pliki i API_AUTH_FILE. Config-check weryfikuje jawny plik oraz rozdział tokenów;
nie tworzy danych i nie sprawdza usług zewnętrznych.

[Korpus wiedzy](knowledge-corpus.md) opisuje offline `corpus-check`, który
wiąże wybrane dokumenty obu repo z commitami Git i opcjonalnie zapisuje nowy
kandydacki manifest. Nie wymaga uruchomionego stosu DB/API.
[Fake index](knowledge-index.md) dodaje offline `index-build` i jawny `index-store`
do odrębnej bazy AI. Build nie uruchamia DB lub AWS; zapis nie aktywuje indeksu.
[Parser/chunker](knowledge-chunks.md) dodaje offline `chunk-build`; konfiguracja
reguł i rozmiaru jest wersjonowana w `knowledge/chunker.v1.json`.
