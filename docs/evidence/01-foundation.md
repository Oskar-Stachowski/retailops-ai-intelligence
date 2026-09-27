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


## Czysty checkout i próby blokad

Kod `e03d7df5200f75526a2e7c711bf502ddce97dd9a` odtworzono przez lokalny clone
do nowego katalogu. `uv sync --locked` w trybie offline wykorzystało uprzednio
pobraną zawartość cache; następnie `make ci-local` przeszło w całości:
**22 passed in 1.81s**, Mypy, Ruff, dokumentacja, build oraz oba skany sekretów.
To świeże środowisko z lockfile i lokalnego cache, nie nowy download ani CI Linux.

Oddzielnie wykonano i przywrócono po próbach:
- zmiana `schema_version` przykładu na `999`: test kontraktu zakończył się exit 1;
- zmiana zależności bez aktualizacji lockfile: `uv sync --locked` zakończyło się exit 1;
- syntetyczny, nieaktywny token w osobnym katalogu: Gitleaks zakończył się exit 1.
Checkout po próbach pozostał czysty; źródłowe fixtures nie zostały zmienione.

Wheel zainstalowano bez editable w trzecim środowisku. Zależności runtime
pochodziły z `uv sync --locked --no-dev --no-install-project`, następnie
`uv pip install --no-deps <wheel>`. CLI i import uruchomione poza checkoutem
korzystały z site-packages; `uv pip check` potwierdziło zgodność ośmiu pakietów.
Sumy artefaktów i kody wyjścia są w JSON.

Komendy odtworzenia, przy zainstalowanym uv 0.12.19/Gitleaks 8.30.1:
```bash
git clone --local --no-hardlinks /sciezka/do/retailops-ai-intelligence /tmp/retailops-ai-checkout
cd /tmp/retailops-ai-checkout
uv sync --locked
make ci-local
```
W tej próbie wskazano interpreter/narzędzie i cache przez jawne ścieżki w
`/private/tmp/retailops-ai-01-6h3iswz6`. Historyczny katalog roboczy nie jest
wymagany przy pobraniu zależności od nowa z lockfile.
