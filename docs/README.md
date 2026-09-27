# Dokumentacja RetailOps AI

Zacznij od [statusu](STATUS.md), [decyzji](architecture/decisions.md) i
[poleceń lokalnych](development.md). [Contributing](contributing.md) opisuje
zmiany i PR-y, [security](security.md) — granice dostępu i zgłoszenia.
[Instrukcja HTTP](http-service.md) opisuje lokalny serwis i granice dostępu.
[Weryfikacja HTTP](evidence/01-http.md) i [fundamentu](evidence/01-foundation.md)
podają faktyczny zakres prób.

## Mapa repo

| Lokalizacja | Bieżąca zawartość |
|---|---|
| `src/retailops_ai/` | CLI/settings oraz warstwy api/domain/pipelines/adapters lokalnego serwera |
| `contracts/` | OpenAPI, JSON Schema i przykłady diagnostyki HTTP oraz metadanych CLI v1 |
| `tests/` | Konfiguracja, HTTP i socket, awarie, korelacja, kontrakty oraz bramki CI |
| `scripts/` | Sprawdzenie linków i wymagań workflow |
| `.github/` | Required CI, szablon PR, wskaźnik do security |
| `docs/` | Aktualne zasady, status, ADR-y i evidence |
| `uv.lock` | Jedyna blokada zależności projektu |

Dokumentację dla ludzi utrzymujemy w `docs/`; root README jest wejściem,
a licencja i konfiguracje narzędzi pozostają przy kodzie. Usuwamy rozwiązane
wnioski i wykonane zadania z aktywnej listy. Evidence opisuje pomiar i jego
ograniczenia; nie jest listą zakończonych zadań ani deklaracją wdrożenia.
