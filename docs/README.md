# Dokumentacja RetailOps AI

Zacznij od [statusu](STATUS.md), [decyzji](architecture/decisions.md) i
[poleceń lokalnych](development.md). [Contributing](contributing.md) opisuje
zmiany i PR-y, [security](security.md) — granice dostępu i zgłoszenia.
[Semantyczny RAG](knowledge-semantic.md) opisuje rzeczywiste embeddings,
kwalifikację jakości, aktywację i rollback Etapu 11. Historyczne evidence
zachowuje zakres poszczególnych pomiarów; bieżące bramki podaje status.
[Kontrakty danych/run/tool](data-contracts.md) i [ich odbiór](evidence/01-contracts.md)
opisują wersje, lineage i walidację offline.
[Handoff źródła 03.3](source-snapshot-handoff.md) opisuje samowystarczalny fixture,
registry snapshotu i niezależny checker. [Importer 03.4](source-snapshot-import.md)
sprawdza typed Parquet i publikuje immutable source. [Curated 03.5](curated.md)
normalizuje fakty, mapuje lokalizacje, zachowuje kwarantannę i odczyt as-of.
[Forecasting 04.1](forecasting.md) definiuje zadanie, kalendarz i granice wiedzy;
[odbiór](evidence/04-01-calendar.md) potwierdza lokalne testy i smoke.
[Panel i cechy 04.2](forecast-features.md) zachowują aktywny kalendarz,
braki/zera, kategorię i znane plany oraz przypiętą historię dla wszystkich horyzontów.
[Manifesty i split 04.3](forecast-manifests.md) opisują dojrzałe etykiety,
pełne coverage i preprocessing dopasowany wyłącznie na train.
[Baseline'y i evaluator 04.4](forecast-baselines.md) zachowują wspólne klucze,
wybierają na validation i raportują poprawne MAE/WAPE.
[Modele RF i HGB 04.5](forecast-models.md) mają pełny train-only pipeline,
wspólny evaluator i ograniczony trening; wybór pozostaje diagnostyczny.
[Backtesting 04.6](forecast-backtesting.md) wyznacza chronologiczne foldy,
kontroluje dojrzałość etykiet i wspólne klucze oraz raportuje pooled MAE/WAPE.
[Metryki i niepewność 04.7](forecast-quality.md) dodają przekroje, bias,
kalibrację przedziałów i jawne bramki jakości bez promocji modelu.
[Inventory snapshot 1.1 — AI 06.6b.2c.1](reference/inventory-snapshot-11.md)
rozszerza typed import o source 2.7, native ledger i private qualification.
[Odbiór](evidence/06-inventory-handoff.md) obejmuje oba standardowe profile
dwukrotnie. Curated 1.1 i domyślne przełączenie pozostają kolejnym zakresem.
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
[Fake embeddings i kandydacki indeks](knowledge-index.md) opisują przypiętą
przestrzeń, cache treści, kontrolę wymiaru oraz transakcyjny zapis w pgvector.
[Odbiór indeksu](evidence/11-index.md) potwierdza 433 testy i realny smoke
na świeżej bazie oraz zapis pełnego korpusu 302 fragmentów.
[Lifecycle indeksu](knowledge-lifecycle.md) opisuje jawne zgody, bramki jakości,
atomową aktywację testową, przypinanie wersji i rollback.
[Odbiór lifecycle](evidence/11-lifecycle.md) potwierdza 463 testy, współbieżność,
rollback i zachowanie pełnego pin po restartach na świeżej bazie.
[Retrieval i golden set](knowledge-retrieval.md) opisują exact cosine, kontrolę
scope, statusy, live deny i wersjonowany golden set (aktualnie 44 pytania).
[Odbiór retrieval](evidence/11-retrieval.md) podaje 498 testów, rzeczywisty PG/HTTP,
historyczny raport fake.
[Administracja indeksami](knowledge-administration.md) opisuje osobny grant,
trwałe runy, zatwierdzone snapshoty, worker i odczyt bieżącego indeksu.
[Odbiór administracji](evidence/11-administration.md) potwierdza 542 testy,
rzeczywisty HTTP/PG, wznowienie workera, idempotencję i trwałość runów.
[Kontrola podobnych treści](knowledge-review.md) opisuje raport dokładnych/near
powtórzeń, pełną listę referencji i limity bez automatycznego usuwania lub zgody.
[Odbiór podobieństwa](evidence/11-similarity.md) podaje kontrolę 20 dokumentów/
302 fragmentów, 573 testy oraz niezależne porównanie wszystkich par.
[Odświeżenie źródeł i etykiet](knowledge-sources.md) opisuje przypięte snapshoty,
scope twierdzeń i binding obu list golden sections przed ewaluacją.
[Odbiór źródeł](evidence/11-sources.md) podaje 29 dokumentów/451 fragmentów,
44 pytania, 578 testów i identyczny indeks odtworzony z czystego checkoutu.
Aktualny raport fake nie otwiera aktywacji.
[Kontrola przed kwalifikacją](knowledge-qualification.md) wiąże konfiguracje,
etykiety, raport oraz decyzje i zwraca jawną listę blokad w manifestach.
[Odbiór kwalifikacji](evidence/11-qualification.md) potwierdza 611 testów,
identyczny manifest z czystego checkoutu, odtwarzanie wyników
i brak aktywacji nawet dla idealnego fake z oboma zgodami.

[Zatwierdzone profile golden](knowledge-golden-jobs.md) wiążą zgody właściciela,
etykiety i progi. Worker zachowuje pełny raport także po niezaliczonym progu.
[Odbiór profili](evidence/11-golden-jobs.md) opisuje testy oraz realne PG/HTTP.

## Mapa repo

| Lokalizacja | Bieżąca zawartość |
|---|---|
| `src/retailops_ai/` | CLI/settings oraz warstwy api/domain/pipelines/adapters lokalnego serwera |
| `contracts/` | OpenAPI/CLI, access/intelligence/knowledge oraz source_snapshot, curated i forecast/v1 |
| `knowledge/` | Przypięty rejestr RAG, konfiguracje, golden set oraz osobne zgody właściciela |
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

- [Pełna bramka cross-repo AI 03.6](evidence/03-06-cross-repo.md) — wspólny odbiór RetailOps i AI, publikacja oraz kolejne etapy.
