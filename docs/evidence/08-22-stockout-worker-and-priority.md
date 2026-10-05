# AI 08.22 — worker, priorytety i rzeczywisty odbiór MLflow/backup

Na 2026-10-05 UTC izolowany run 37255271458 zaliczył SQL 0021 oraz pełny backup/
odtwarzanie po poprawce nawiasów. Kolejny run 37255965904 zaliczył rzeczywisty
stockout MLflow: import pełnej paczki, weryfikację jej bajtów i smoke, rejestrację,
utracone odpowiedzi, częściowe aliasy, rollback, restart oraz pełny backup.
Drugi odbiór korzystał z prawdziwego MLflowStockoutRegistry i kontrolowanego
PostgreSQL, a model/paczka pozostawały jawnie syntetyczną próbą test-mechanics.
Nie jest to dowód jakości produkcyjnego klasyfikatora.

Worker przed leasingiem wiąże model/release/image z DB i pełną paczką MLflow.
Niezakończona decyzja, reject, wygaśnięcie zgody lub niezgodne aliasy blokują
wykonanie. Mały numeryczny proces dostaje wyłącznie zweryfikowane wejścia publiczne,
przypiętą recepturę/politykę i limity. Nie dziedziczy DB, GitHub ani AWS credentials.
Izolowany Python, limity CPU/AS/file i pomiar RSS/wall/output ograniczają proces.
Heartbeat ponownie dowodzi własności zadania; publikacja jest atomowa dopiero
po pełnym replay wyniku oraz końcowym guard MLflow. Utrata lease nie publikuje.

Widok ryzyka dodaje priority dla pełnego zarejestrowanego input profile w origin.
Nie jest to deklaracja budżetu całej firmy poza tym profile. Obliczenie obejmuje
wszystkie eligible fizyczne klucze przed filtrem produktu/lokalizacji/uprawnień.
Status already_stockout nie zużywa incident-risk capacity. Dla częściowego scope
globalna decyzja o danym uprawnionym wierszu zostaje, a rank/nieuprawnione liczności
są wstrzymane. Kolejka wymaga jednego inference_run_id; nie miesza profili/origin.
Current stockouts filtruje najnowszy stan, nie wyszukuje starego pasującego statusu.
Freshness pozostaje jawny również dla pozycji wybranych w historycznym origin.

Przykłady odczytu, z prywatnym uwierzytelnieniem:

```text
GET /api/v1/stockout-risks?inference_run_id=run-<32hex>&view=attention_queue
GET /api/v1/stockout-risks?view=current_stockouts
python -m retailops_ai.stockout_jobs.worker --once --env-file <private-file> --release-id stockout-release-sha256-<64hex>
```

Nowy checker integracyjny obejmuje rzeczywisty cold worker, guard MLflow i SQL,
HTTP 202 po commit, idempotency, 401/404, widok priority oraz restart/restore.
Ten rozszerzony checker wymaga własnego rzeczywistego receipt CI; nie przypisujemy
mu zaliczenia wcześniejszego, mniejszego testu.

Weryfikacja lokalna: 132 focused tests passed w 11,54 s, w tym 9 nowych testów
cold worker/failure/lease i 5 global capacity/scope/current-state. Do pomiaru
procesu na macOS wymagany był odczyt psutil poza sandboxem; nie zmieniono runtime
limitu RAM i nie ominięto pomiaru. Ruff i format 749 plików oraz konfigurowany
Mypy 451 plików przechodzą. Gitleaks przeskanował 26,74 MB bez sekretów.
Schemat API jest aktualizowany wraz z query/page i pełną kontrolą kontraktów.

Końcowa kampania 28bcfec3 jest nadal ta sama; kod/lock/recipe/policy pozostają
przypięte. Zgoda właściciela nie została jeszcze otrzymana, final outcomes nie
zostały otwarte, model produkcyjny nie został promowany. Finalna niezależna jakość,
rzeczywista kwalifikacja/karta, osobny odbiór/promocja i pełne CI main są otwarte.
**AI 08 pozostaje not ready.**
