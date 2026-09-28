# Dokumentacja RetailOps AI

Zacznij od [statusu](STATUS.md), [decyzji](architecture/decisions.md) i
[poleceń lokalnych](development.md). [Contributing](contributing.md) opisuje
zmiany i PR-y, [security](security.md) — granice dostępu i zgłoszenia.
[Kontrakty danych/run/tool](data-contracts.md) i [ich odbiór](evidence/01-contracts.md)
opisują wersje, lineage i walidację offline.
[Uprawnienia API](access-control.md) i [odbiór](evidence/01-access.md) opisują
zweryfikowane poświadczenia, scope i bezpieczne uruchomienie.
[Lokalny stos DB/API/MLflow](local-stack.md) opisuje persistence i migracje.
[Dowody persistence](evidence/01-persistence.md) pokazują rzeczywiste próby awarii.
[Instrukcja HTTP](http-service.md) opisuje lokalny serwis i granice dostępu.
[Weryfikacja HTTP](evidence/01-http.md) i [fundamentu](evidence/01-foundation.md)
podają faktyczny zakres prób. [Odbiór zdalnego CI](evidence/01-remote-ci.md)
potwierdza kontrolę PR oraz push na main i ochronę obu repozytoriów.
[Korpus wiedzy](knowledge-corpus.md) opisuje pierwszy zakres etapu 11,
kandydacki rejestr obu repo i walidację źródeł Git.
[Dowody korpusu](evidence/11-corpus.md) podają pomiar deterministyczności,
testy negatywne i granice tego zakresu.
[Parser i chunker](knowledge-chunks.md) opisuje budowę fragmentów, ich tożsamość
i cytaty do przypiętych rewizji Git.
[Odbiór chunków](evidence/11-chunks.md) potwierdza zmiany/usunięcia źródeł,
pełną mapę fragmentów i odtwarzalność rzeczywistego korpusu.

## Mapa repo

| Lokalizacja | Bieżąca zawartość |
|---|---|
| `src/retailops_ai/` | CLI/settings oraz warstwy api/domain/pipelines/adapters lokalnego serwera |
| `contracts/` | Diagnostyczne OpenAPI/CLI, access/v1, intelligence/v1 oraz knowledge/v1: schemas korpusu, konfiguracji chunkera i fragmentów |
| `knowledge/` | Rejestr propozycji dokumentów RAG i przypięta konfiguracja parsera/chunkera |
| `tests/` | Konfiguracja, HTTP i socket, awarie, korelacja, kontrakty oraz bramki CI |
| `scripts/` | Kontroler Compose, rzeczywisty smoke, kontrakty i bramki workflow |
| `.github/` | Required CI, szablon PR, wskaźnik do security |
| `docs/` | Aktualne zasady, status, ADR-y i evidence |
| `compose.yaml`, `infra/` | Lokalny stos oraz bootstrap odrębnych baz i ról |
| `uv.lock` | Jedyna blokada zależności projektu |

Dokumentację dla ludzi utrzymujemy w `docs/`; root README jest wejściem,
a licencja i konfiguracje narzędzi pozostają przy kodzie. Usuwamy rozwiązane
wnioski i wykonane zadania z aktywnej listy. Evidence opisuje pomiar i jego
ograniczenia; nie jest listą zakończonych zadań ani deklaracją wdrożenia.
