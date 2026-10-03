# AI 10 — pierwszy przyrost integracji wyników

Status: **implementacja w toku; AI 10 nie jest ready**. Ten przyrost wdraża
forecast v2, atomowy outbox po stronie AI i kompatybilny projektor/read API
po stronie RetailOps. Anomalie, stockout risk, sugestie, UI oraz snapshot/REST
są kolejnymi przyrostami. Nie zmieniono sesji ani worktree AI 07/08.

## Granice kontraktu

- Topic: `retailops.intelligence.v2`, wersja envelope `2.0`, source `retailops-ai`.
- Obsługiwany typ: `forecast_generated`. Trzy pozostałe typy z planu są
  zarezerwowane i **odrzucane**, dopóki nie mają zatwierdzonych schematów ML.
- Payload to dokładny `V12ForecastItem` z istniejącego API ML. Zachowuje
  candidate mean, reference median/interval, exclusion/null, grain,
  model/release/approval, źródłowe dataset IDs, run i politykę świeżości.
  Nie wylicza nowego przedziału ani nie tłumaczy daily store/channel na legacy
  firmowy forecast okresowy.
- Schemat i registry powstają przez
  `python scripts/update_intelligence_event_contracts.py`.
  `--check` wykrywa drift. Kopia w RetailOps ma pin SHA-256 w `upstream.json`.
  Nie edytuj ręcznie wygenerowanego modelu pól.
- Envelope wymaga wszystkich dziewięciu pól. Nieznany major/minor/type/topic,
  dodatkowe pola zamkniętego payloadu i błędne zależności są odrzucane.
- `event_id` to UUIDv5 namespace
  `b85cb398-e62a-5f70-91ed-6c0fbe868a50` i
  `forecast_generated:<prediction_id>`. Partition key jest canonical SHA-256
  `{product_id, selling_location_id, channel}`. Retry zachowuje wynik,
  event ID, key, publication time i freshness ocenioną przy publikacji.
- `forecast_events` weryfikuje pełną publikację z runem i receipt przez
  istniejący reader. Prywatny, nieopublikowany computation nie jest eventem.

## Zapis i dostarczenie

Head bazy dla tego przyrostu: `0020_intelligence_outbox`, po
`0019_v12_development`. To migracja osobnej bazy AI. Nie uruchamiaj jej na
bazie RetailOps ani na runtime należącym do innej sesji.

`PostgresV12Publisher(..., events_enabled=True)` zapisuje kompletne wyniki
oraz wszystkie ich zdarzenia w **jednej transakcji**. Błąd inserta outbox
cofa również publikację. Opcja jest domyślnie wyłączona; dotychczasowy
publication flow nie rozpoczyna automatycznie wysyłki. Włączenie przy
powtórnym `publish` pozwala jawnie uzupełnić outbox już istniejącej,
zweryfikowanej publikacji, z tymi samymi tożsamościami.

Outbox worker blokuje jeden pending row przez `FOR UPDATE SKIP LOCKED`,
wysyła z `acks=all` i idempotent producer, czeka maksymalnie 15 s na
delivery receipt, a następnie zapisuje broker partition/offset. Brak receipt,
awaria brokera lub przerwanie przed commit pozostawia pending row.
Przerwanie po dostarczeniu i przed zapisem receipt może powtórzyć event;
odbiorca ma obowiązek go deduplikować. To at-least-once, nie globalne
exactly-once. Jeden przebieg workera jest ograniczony do `1..1400` eventów.

## Uruchomienie w osobnym runtime

1. Przypnij oba commity i sprawdź zgodność wygenerowanych schema/registry.
   Migracje i testy wykonuj na własnych, izolowanych bazach i grupie v2.
2. W repo AI utwórz własne środowisko:

   ```sh
   uv sync --locked --extra snapshot --extra forecast
   python scripts/update_intelligence_event_contracts.py --check
   retailops-ai migrate --env-file /private/path/ai.env
   ```

   Baza musi mieć wcześniejsze bootstrap/schema/pgvector z istniejącego
   runbooka stosu AI. Pole `DATABASE_URL` ma driver `postgresql+psycopg`.
3. W RetailOps wykonaj `alembic upgrade head` na własnej bazie źródłowej.
   Przyrost dodaje `ai_forecast_results` i `ai_intelligence_inbox`.
   Compose topic-init tworzy v2 osobno, zachowując pętlę topiców legacy.
4. Przygotuj poza Git dwa prywatne pliki: istniejące credentials/policy
   pipeline AI i broker configuration JSON. Broker file musi być zwykłym
   plikiem bieżącego użytkownika, z uprawnieniami `0600`, do 16 KiB.
   Zawiera `bootstrap.servers` i ewentualne poświadczenia transportu.
   Worker zawsze wymusza `enable.idempotence=true`, `acks=all` i bounded timeout.
5. Opublikuj zatwierdzony, zakończony run z jawną opcją:

   ```sh
   python scripts/forecast_v12_queue.py \
     --env-file /private/path/ai.env \
     --policy-file /private/path/pipeline-policy.json \
     --credentials-file /private/path/pipeline-credentials.json \
     publish --run-id RUN_ID --integration-events
   ```

   Nie używaj mechanics/development namespace jako dowodu jakości ML.
6. Przygotuj osobne środowisko klienta brokera i dostarcz bounded batch:

   ```sh
   uv sync --locked --project tools/intelligence-delivery
   uv run --locked --project tools/intelligence-delivery \
     python scripts/intelligence_outbox.py \
     --env-file /private/path/ai.env \
     --broker-config /private/path/broker.json --max-events 100
   ```

   Exit 2 oznacza niedostępny outbox/transport; nie usuwa backlogu.
   Sprawdzenie przez operatora na bazie AI:

   ```sql
   SELECT environment, count(*) AS pending
   FROM ai.intelligence_outbox WHERE delivered_at IS NULL GROUP BY environment;
   ```

   Główny `uv.lock` pozostaje identyczny z zamrożonym środowiskiem ML
   (SHA-256 `33c53d1a1f08d5c90b3b61c79e6aeb732f0eebc6e8be36e735c93ca277492587`).
   Klient `confluent-kafka==2.15.1` ma oddzielny lockfile i venv w
   `tools/intelligence-delivery`. Wszystkie współdzielone biblioteki zachowują
   wersje rdzenia; `scripts/check_intelligence_delivery.py` odrzuca drift.
   Dopisanie klienta do głównego locka zmieniłoby tożsamość wcześniejszych
   profili i eksportów modeli. Nie wyłączaj walidacji ich pinów.
7. Uruchom dedykowany consumer RetailOps według jego runbooka. Nie dopisuj
   v2 do subskrypcji legacy ani no-op handlers.
8. Skonfiguruj prywatną policy odczytu RetailOps i wykonaj uwierzytelniony
   `GET /intelligence/v2/forecasts/{prediction_id}`. Sprawdź pełny payload,
   źródło i status świeżości. `received_at` nie zastępuje forecast origin.

## Weryfikacja i zakres dowodu

```sh
make integration-replay-test
make integration-failure-test
```

Pierwsza komenda sprawdza schema i generację eventów z kompletnej publikacji.
Druga wykonuje test na osobnym rzeczywistym PostgreSQL. Tworzy i usuwa
wyłącznie własny kontener `retailops-ai10-outbox-*`, na losowym porcie.
Test używa jawnych doubles istniejących lifecycle/provider guards oraz
małych tabel tych zależności; **rzeczywista nowa migracja outbox i transakcja
publishera pozostają wykonywane**. Sprawdza rollback publikacji/outbox,
idempotent publication, brak delivery receipt i przerwanie po dostarczeniu.
Nie jest to ponowna kwalifikacja modelu ani pełna migracja historycznych tabel.

RetailOps ma komendy o tych samych nazwach dla testów rzeczywistego brokera,
projekcji, read API i awarii. Ich fixture jest jawnie oznaczona mechanics.
JUnit oraz logi pozostają w ignorowanych katalogach `reports/` obu repo.
Pierwsze pobranie przypiętych obrazów wykonaj przed bounded testami.

## Odtwarzanie i ograniczenia

- Przy błędzie brokera zachowaj pending outbox; po naprawie ponów worker.
- Przy błędzie bazy consumer kończy pracę bez ACK. Nie uruchamiaj ręcznego
  commita omijającego rekord. Wznów tę samą dedykowaną grupę po naprawie.
- Wadliwe raw są utrwalane w istniejącej kwarantannie przed ACK. Replay
  wymaga jawnej decyzji operatora; v2 zachowuje key tego samego grainu.
- Rollback aplikacji: wyłącz opcję publikacji eventów, zatrzymaj własne
  workery v2 i zachowaj tabele/history/backlog. Nie wykonuj destructive
  downgrade ani cleanup wspólnego stosu. Backup/restore całej bazy pozostaje
  wymagany przed zmianą schematu w runtime zawierającym dane.
- Ten przyrost daje **immutable history**, nie aktywną projekcję „latest”.
  Approved-release/as-of/run selection, monitoring/fencing rozszerzonego
  checkpointu, shared Compose overlay i auth transportu są do odbioru w
  następnych przyrostach.
- Nadal otwarte: typed upstream REST, wersjonowany eksport z pełnym grainem,
  snapshot/log checkpoint handoff i korekty faktów; modele 07/08; sugestie
  fixture; istniejący frontend; rzeczywiste cross-repo E2E na profilu 102 dni.
  Sam test schematu, licznik ani transport fixture nie zamyka AI 10.
