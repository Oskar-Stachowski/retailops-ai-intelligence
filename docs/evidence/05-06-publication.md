# AI 05.6 — odbiór integracji i atomowej publikacji

Data: **2026-09-30**. Branch `ai/05-mlflow-serving`; zmiany pozostają lokalne.
**Odbiór techniczny przeszedł na PostgreSQL i kontrolowanych fixture.**
Nie zaimportowano rzeczywistego qualified release’u AI 04 i nie potwierdzono
servingu na takim modelu. Implementację i polecenia opisuje
[runbook](../forecast-publication.md).

## Wynik PostgreSQL

[Raport publikacji](05-06-publication.json) ma `status=passed`, purpose
`publication_transaction_fixture_only` i `forecast_quality_approved=false`.
Jednorazowy projekt `retailops_ai_outputs_6445e1dc5b` miał zainstalowany pakiet
w obrazie `sha256:8b55af68441753ec188de17286f8ef938b00755ca5a0036f6f6177bd71315f6c`.
Synthetic profile zawiera 20 produktów × 1 lokalizację × 14 horyzontów:
280 wierszy, dzielonych na partycje 256 + 24. Release’y oraz gates są jawnymi
SQL-only stubami; nie utworzono wersji/promocji w rzeczywistym MLflow.

Baza utrwaliła **7 runów, 8 zamkniętych prób, 5 manifestów fixture,
10 partycji i 1 pointer**. Pełny stan po SIGKILL/restart był identyczny:
`23bb5372fb6eb30ae483171f638b3b5cc6fa8886997faf2f37968fc90adf88b2`.
Kontroler usunął swój projekt i wolumen. Nie opublikowano rzeczywistych prognoz.

Odbiór potwierdził:

- rejestrację bez release’u bez utworzenia runu oraz współbieżną idempotencję;
- zachowanie release/version/input pins po zmianie zatwierdzonego head;
- odmowę commit samego manifestu bez sukcesu i historii;
- rollback surowego SQL `succeeded` i historii przy brakującej partycji;
- wspólny commit partycji, manifestu, runu, historii i pointera;
- awarię drugiego inserta bez częściowych danych i ze zgodnym hashem poprzedniego pointera;
- bounded retry z tymi samymi pinami i odmowę starego tokenu;
- dokładnie jeden output przy dwóch równoległych zakończeniach;
- zachowanie nowszego pointera po późniejszym zakończeniu starszego żądania;
- wygaśnięcie lease po pierwszym insercie z rollbackiem **całego stanu**, w tym próby zmiany lease;
- odmowę rzeczywistego numeric childa wobec brakujących dowodów Registry, bez publikacji;
- blokadę SQL update/delete manifestów i partycji oraz trwałość całego stanu po restarcie.

[Regresja kolejki](05-06-queue-regression.json) również przeszła po migracji:
projekt `retailops_ai_queue_3b43550375`, 9 runów, 12 prób, 2 mechaniczne receipts.
Ponownie sprawdzono rzeczywiste HTTP/MLflow, scope, concurrency, heartbeat,
cancellation, retry/deadlines/backpressure, SIGKILL częściowego obliczenia,
stare tokeny i niezmienność stanu po restarcie. To `lifecycle_mechanics_only`,
bez zatwierdzenia jakości AI 04.

[Regresja rejestru wejść](05-06-input-store-regression.json) przeszła na
wcześniejszym obrazie tej implementacji: projekt `retailops_ai_outputs_d989ba679b`,
image `sha256:2d01b2447b5e87b05f335ac085351ae65cfb55279f341add2f9a0eb1a9ad4df7`.
To oddzielny odbiór `verified_inputs_only`: 3 profile, 0 runów/release’ów/outputów
i identyczne pełne wejścia po SIGKILL/restart. Nie jest dowodem publikacji.

## Kontrole lokalne

**319 różnych testów** obejmuje worker/runtime/inputs, API/access/run/data
contracts, lifecycle/Registry/MLflow i bramki CI. Jednostkowo sprawdzono
content-addressed scope/horizon derivation, pełny grain, receipts, identity,
daty i domeny. Brak partycji, duplikat, zmieniony receipt, ujemna/non-finite
wartość lub przeliczony hash niespójnej zawartości są odrzucane.
Preflight, obcy release/profil, niepełny count i inne lineage nie publikują.
Worker wiąże budżet, image, release, heartbeat i wynik; utrata lease nie zapisuje
sukcesu ani błędu nowej próby. Kontrola faktycznych limitów childa pozostaje w 05.5b.

Dodatkowo przeszły Ruff/format, mypy 204 pliki, snapshoty forecast jobs/access/
intelligence/knowledge/forecast/lifecycle, linki docs i Required CI oraz wheel/sdist.
Snapshot OpenAPI został zaktualizowany o `input-runtime-incompatible`;
test snapshotu i 21 testów CI ponownie przeszły. `forecast-publication-smoke`
należy do Required CI i ma test blokujący usunięcie tej bramki.
Nie powtarzano treningu/evaluatora AI 04 ani nie wykonywano wywołań AWS.

Przejściowy brak miejsca zatrzymał Docker Desktop i przerwał wcześniejsze próby.
Po zwolnieniu miejsca i restarcie usługi odbiory przeszły. Usunięto również
pozostały testowy wolumen `retailops_ai_queue_aaac8ac47a_postgres_data`, utworzony
przez przerwaną próbę tej sesji. Nie wykonano globalnego prune/reset ani
usuwania danych projektu i backupów.

## Pozostała bramka

Odbiór techniczny **nie kwalifikuje modelu**. Pełny odbiór serving wymaga
spójnego, zakwalifikowanego handoff AI 04, importu/review/promocji oraz
pomiaru rzeczywistego batchu na jego release’ie. Archiwum 04.8 w tym worktree
nadal ma `not_ready` i inny lock features. Następny zakres implementacyjny
to AI 05.7 — scoped read API, paginacja i freshness; zdarzenia są w AI 10.
Zdalny Required CI nie został odebrany i **AI 05 pozostaje otwarte**.
