# Dokumentacja RetailOps AI

Zacznij od [statusu](STATUS.md), [decyzji](architecture/decisions.md) i
[poleceń lokalnych](development.md). [Contributing](contributing.md) opisuje
zmiany i PR-y, [security](security.md) — granice dostępu i zgłoszenia.
[Weryfikacja fundamentu](evidence/01-foundation.md) podaje faktyczny zakres prób.

## Mapa repo

| Lokalizacja | Bieżąca zawartość |
|---|---|
| `src/retailops_ai/` | CLI, typed settings, kontrakt metadanych; bez serwera |
| `contracts/` | JSON Schema i przykład metadanych CLI v1 |
| `tests/` | Konfiguracja, błędy, zgodność kontraktu i blokowanie osłabionego CI |
| `scripts/` | Sprawdzenie linków i wymagań workflow |
| `.github/` | Required CI, szablon PR, wskaźnik do security |
| `docs/` | Aktualne zasady, status, ADR-y i evidence |
| `uv.lock` | Jedyna blokada zależności projektu |

Dokumentację dla ludzi utrzymujemy w `docs/`; root README jest wejściem,
a licencja i konfiguracje narzędzi pozostają przy kodzie. Usuwamy rozwiązane
wnioski i wykonane zadania z aktywnej listy. Evidence opisuje pomiar i jego
ograniczenia; nie jest listą zakończonych zadań ani deklaracją wdrożenia.
