# AI 05 — prywatna kolejka i worker v12

V12 ma osobne trwałe zadania obliczeń, przypięte do dopuszczonego release’u
w PostgreSQL. Worker zachowuje medianę, średnią i przedział oraz oryginalne
baseline’y, metadane i wartości null. Udany run oznacza kompletny receipt
obliczeń: `published_forecast_outputs=0`. [Publikacja i API v12](forecast-v12-publication.md)
są osobnym krokiem po sukcesie; dotychczasowe endpointy v1 zachowują swój kontrakt.
Zlecanie i odczyt trwałych zadań są także dostępne przez
[uwierzytelnione API v12](forecast-v12-jobs-api.md), z bezpieczną projekcją metadanych.

## Przyjęcie zadania

Prywatny CLI uwierzytelnia jeden account z prywatnych plików policy i credentials.
`submit` wymaga roli `pipeline`, capability `forecast:run` i pełnego scope
produktów, lokalizacji oraz kanału. `get`, `attempts` i `receipt` wymagają
`forecast:read` lub własnego runu z `forecast:run`; cały scope nadal musi
należeć do account. Principal ID i uprawnienia pochodzą ze zweryfikowanej
polityki. Żądanie nie zawiera ścieżki, kodu, URL ani numeru wersji modelu.

Wejście wskazuje wcześniej zarejestrowany `PreparedInputs` v1.1, po
[pełnej weryfikacji feature/curated parents](forecast-input-store.md).
Kolejka sprawdza oryginalny profile ID, źródło, snapshot, feature package,
curated descriptor, lock, domyślną politykę i kompletny grain. Obowiązuje
[polityka dopuszczenia v12](forecast-v12-release.md): nowe źródło lub feature
package wymagają nowej kwalifikacji i przeglądu. Samo zapisanie skonstruowanego
JSON w prywatnej bazie nie stanowi odbioru pochodzenia obserwacji.

```bash
.venv/bin/python scripts/forecast_v12_queue.py \
  --env-file /private/path/service.env \
  --policy-file /private/path/pipeline-policy.json \
  --credentials-file /private/path/pipeline-credentials.json \
  submit --idempotency-key demand-20261001 < /private/path/batch-request.json
```

Kształt żądania opisuje [request schema](../contracts/forecast_jobs/v12/request.schema.json):
profile ID, origin zamykający dzień UTC, scope, kanał i okna 7/14.
Limit wynosi 20 produktów, 5 lokalizacji i 1400 dziennych wierszy.
Puste filtry rozwijają się wyłącznie do jawnie przyznanego scope.
Idempotency key ma 1–128 znaków liter/cyfr/`_.-`. Ten sam key i treść
zwracają ten sam run; inna treść powoduje konflikt.

Przyjęcie czyta [aktywny release v12](mlflow-v12-lifecycle.md) z bazy i
zapisuje immutable binding, image digest, źródło, profil, scope i politykę.
Blokuje niedokończoną decyzję lifecycle, brak lub wygaśnięcie dopuszczenia,
niezgodne wejście i przekroczenie pojemności. Późniejsza promocja nie zmienia
już przyjętego runu ani jego retry. Odrzucenie wersji blokuje jej wykonanie.

## Worker i dzielenie batchu

Operator wskazuje konkretny DB release ID oraz lokalne kapsułę, zakończony
export i przypięty interpreter AI 04. Najpierw worker porównuje MLflow z
historią bazy, weryfikuje pełny eksport i lokalne dopuszczenie. **Dopiero potem
przejmuje zadanie.** Dłuższa weryfikacja nie zużywa lease zadania.
Nie pobiera dowolnego pliku z żądania ani nie wybiera modelu po metrykach.

```bash
.venv/bin/python -m retailops_ai.forecast_jobs.v12_worker \
  --env-file /private/path/service.env --once \
  --release-id v12-model-release-sha256-RELEASE_SHA \
  --approval-dir /private/path/approved/APPROVAL_ID \
  --run-dir /private/path/completed-v12-export \
  --verifier-python /private/path/pinned-ai04-venv/bin/python
```

`IMAGE_DIGEST` w prywatnej konfiguracji musi odpowiadać dopuszczeniu.
Jest wymaganym pinem operatora; wdrożenie musi osobno potwierdzić digest
faktycznego obrazu. Worker obsługuje jedną próbę pasującą do wskazanego release’u.
`idle` oznacza brak takiego zadania; inne oczekujące release’y wymagają
workera z ich zweryfikowanymi zasobami.

Batch jest dzielony deterministycznie na prostokątne zakresy produktów
w jednej lokalizacji. Każda seria zachowuje wszystkie dni i całą historię.
Część ma najwyżej 256 wierszy i 4 MiB żądania; budżet bajtów może wymusić
mniejsze części. Brak miejsca nawet dla jednej serii powoduje odmowę.
Scope 20 × 5 × 14 daje typowo 10 części, gdy mieści się budżet bajtów.

Każda część korzysta z wcześniej zweryfikowanego modelu i uruchamia
istniejący izolowany predictor `-I -B`, bez poświadczeń bazy, API lub chmury.
Obowiązują dopuszczone limity RSS i wall time oraz pozostały czas próby.
Nie ma refitu, generowania źródła ani automatycznej zmiany receptury.
Worker sprawdza scope, profile IDs, dokładne klucze, wszystkie trzy wyniki,
sumy kontrolne i brak duplikatów przed zapisaniem całego receipt.

## Lease, awaria i zapis wyniku

Polityka jest przypięta przy przyjęciu: domyślnie 3 próby, lease 30 s,
heartbeat 5 s, próba 120 s, cały run 600 s i backoff 5 s. Pojemność to
100 aktywnych runów na środowisko, 20 na account. Retry nie zmienia pinów.

Heartbeat i zamknięcie wymagają aktywnego tokenu, zgodnego record oraz
niewygasłego czasu z bazy po blokadzie wiersza. Nowa próba otrzymuje nowy token.
Stary worker nie może odnowić lease, zamknąć ani zapisać wyniku nowej próby.
Wygasłe próby są rozliczane podczas kolejnego przejęcia, z zachowaniem historii.
Anulowanie unieważnia token i jest terminalne. Prywatna operacja wymaga
ustawień bazy, bez publicznego endpointu mutacji:

```bash
.venv/bin/python -m retailops_ai.forecast_jobs.v12_worker \
  --env-file /private/path/service.env --cancel run-RUN_ID
```

Podczas obliczeń worker sprawdza niedokończone decyzje, odrzucenie/ważność
wersji i zgodność aktualnych aliasów z DB head. Callback utraty lease lub
błędu kończy i zbiera podproces predictora. Przed sukcesem ponownie
weryfikowane są remote capsule i binding MLflow. Alias nowszej wersji nie
przepina starszego zadania; musi jednak zgadzać się z bieżącym head bazy.

Wyniki części pozostają prywatne i nie mają osobnych wskaźników sukcesu.
Kompletny receipt, terminalny run i historia próby są jedną transakcją.
Czas i token są ponownie sprawdzane po zapisie receipt. Błąd w połowie,
anulowanie lub utrata własności nie publikują częściowego wyniku.
Trigger SQL blokuje zmiany pinów, nielegalne przejścia i sukces bez receipt;
odroczony trigger wymaga historii zamkniętej próby. Historia i receipt
nie podlegają UPDATE/DELETE.

MLflow i baza pozostają osobnymi systemami, z kontrolą niespójności.
Nie ma wspólnej transakcji z administracyjną zmianą dokonaną poza lifecycle.
Przed wdrożeniem wymagany jest odbiór całego rzeczywistego przepływu.

## Migracja i odbiór

Migracja kolejki to **`0016_v12_queue`**, po `0015_v12_lifecycle`.
Bieżący head to `0017_v12_outputs` opisany w [publikacji v12](forecast-v12-publication.md).
Tabele `ai.v12_batch_runs`, `ai.v12_batch_attempts` i `ai.v12_batch_receipts`
są oddzielne od kolejki/publikacji v1. Readiness i wspólny DB guard wymagają
jawnej migracji według [runbooka Compose](local-stack.md). Trwałego stosu
nie migrowano w tym odbiorze. Downgrade historii wymaga backup/restore.

```bash
.venv/bin/python -m pytest -q tests/test_v12_queue.py
.venv/bin/python scripts/update_v12_batch_contracts.py --check
.venv/bin/python scripts/check_v12_queue.py
```

Portable testy używają jawnych małych doubles i wymyślonych serii.
Odizolowany runner sprawdza prawdziwe PostgreSQL/MLflow, przyjęcie, retry,
fencing, anulowanie, atomowy receipt oraz restart usług. Używa namespace
`retailops-demand-forecast-v12-mechanics` i wymaga `APP_ENV=test` oraz
`--mechanics`. Korzysta z istniejących obrazów i usuwa własne kontenery
oraz anonimowe wolumeny; nie buduje ani nie pobiera obrazów.
[Dowód przygotowania](evidence/05-v12-queue.json) opisuje granice fixture.

Po [publikacji i read API](forecast-v12-publication.md) pozostają
spójny backup/restore v12 i Required CI. Rzeczywisty handoff, źródło,
przegląd operatora i końcowy batch nadal wymagają ukończonego AI 04.
**AI 05 pozostaje otwarte.**
