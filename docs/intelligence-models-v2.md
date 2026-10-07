# AI 10 — native anomaly i stockout w intelligence v2

Status: **in_progress**. Ten przyrost podłącza zaakceptowane publiczne typy
AI 07/08 do trwałej integracji. Nie kwalifikuje modelu ponownie. Pełny capture
źródła, zgodność snapshot/replay i temporalne E2E trzech rzeczywistych modeli
pozostają bramkami całego AI 10.

## Kontrakty i ziarno

| Typ | Payload właściciela | Oryginalny identyfikator | Ziarno |
| --- | --- | --- | --- |
| `forecast_generated` | `V12ForecastItem` | `prediction_id` | produkt, punkt sprzedaży, kanał, origin, dzień/horyzont |
| `anomaly_detected` | `anomaly_portfolio.serving_contract.Item` | `anomaly_id` | rodzaj obserwacji, produkt, punkt sprzedaży, kanał, waluta, dzień |
| `stockout_risk_scored` | `stockout_runtime.public_contracts.RiskItem` | `risk_id` | produkt, fizyczna lokalizacja zapasu, as-of, horyzont 7 dni |

Envelope ma topic `retailops.intelligence.v2`, source `retailops-ai`, wersję
`2.0`. Payload pozostaje dokładnym typem właściciela, wraz z nullability,
decyzją/model probability, statusami, release, runem i pochodzeniem. Anomaly
ma korelację native `batch_id`; stockout ma native `run-…`. UUIDv5 eventu
wiąże typ i niezmienny identyfikator wyniku. Partition key hash obejmuje
całe ziarno sprzedażowe anomaly lub fizyczne ziarno stockout.

`scripts/update_intelligence_event_contracts.py --check` weryfikuje trzy
wygenerowane schematy oraz registry. Schemat forecast pozostaje zgodny
bajtowo z poprzednim przyrostem. Registry publikuje pola oryginalnego
`Decision`, aby niezależny konsument mógł zweryfikować native anomaly ID.

## Atomowa publikacja

Head `0025_model_intelligence_outbox` dodaje `ai.model_intelligence_outbox`
po wspólnym merge migracji `0024_ai10_integration`. Żadna wcześniejsza
migracja nie jest przepisywana. Tabela ma FK do native anomaly result lub
kompletnego native stockout output, niezmienny dokument i osobny receipt
dostarczenia. Downgrade z niepustym outboxem jest odmawiany.

`PostgresResults.publish` zapisuje komplet anomaly i ich zdarzenia w tej
samej transakcji. Retry/reuse odczytuje oryginalne zapisane payloady i chwile
publikacji; nie zamienia ich na późniejsze timestamps. Błąd outboxu cofa
również batch i request. Powtórzenie zweryfikowanej publikacji może uzupełnić
outbox historycznego batcha po migracji.

`StockoutQueue.complete` zapisuje output, zakończony run, historię i zdarzenia
w jednej transakcji. Błąd outboxu pozostawia run running i brak outputu.
Historyczne ukończone runy nie otrzymują zdarzeń automatycznie; nowy odbiór
wykonuje native batch przez istniejący worker na przypiętym release.

## Uruchomienie krok po kroku

1. Przypnij oba commity integracji i porównaj SHA schematów/registry z
   konsumenckim `upstream.json`. Pracuj na własnych bazach i grupie brokera.
2. Wykonaj jawne migracje AI do `0025_model_intelligence_outbox` oraz Source
   do `a10f0c7e0600`. Bazy AI i Source pozostają oddzielne.
3. Wykonaj istniejący, autoryzowany native anomaly batch/publish oraz native
   stockout worker. Sprawdź kompletność ich outputów i przypięty release.
   Stan development lub mechanics nie uprawnia do deklaracji jakości ML.
4. Przygotuj prywatne pliki AI env i konfiguracji brokera zgodnie z
   [instrukcją forecast outbox](intelligence-integration-v2.md). Worker
   obsługuje środowiska `local`/`test` i osobny lock narzędzia dostarczenia.
5. Dostarcz ograniczony batch wyników native:

   ```sh
   uv run --locked --project tools/intelligence-delivery \
     python scripts/intelligence_outbox.py \
     --env-file /private/path/ai.env \
     --broker-config /private/path/broker.json \
     --model-results --max-events 100
   ```

   Bez `--model-results` zachowana jest dotychczasowa ścieżka forecast.
   Limit jednego przebiegu pozostaje `1..1400`; większy backlog wymaga
   kolejnych ograniczonych przebiegów.
6. Uruchom istniejący checkpoint consumer Source w osobnej grupie v2.
   Projekcja wyniku, model inbox, transport receipt i SQL cursor mają wspólny
   commit. Broker ACK następuje dopiero po tym commit.
7. Przygotuj prywatną osobistą politykę Source
   `RETAILOPS_INTELLIGENCE_MODEL_ACCESS_POLICY`: capabilities `anomaly:read`
   oraz/lub `stockout:read`, z osobnymi grantami selling/stock locations,
   produktów i release. Anomaly scope dodatkowo obejmuje kanał i walutę.
   Credential SHA-256 i principal ustala serwer; demo user niczego nie nadaje.
8. Odczytaj dosłowny oryginalny identyfikator przez
   `/intelligence/v2/anomalies/{anomaly_id}` lub
   `/intelligence/v2/stockout-risks/{risk_id}`. W istniejącym widoku Anomalies
   RetailOps podłącz osobisty credential i sprawdź native grain, status,
   odczytywaną freshness oraz lineage. Original payload pozostaje niezmienny.

## Awarie i odtwarzanie

Brak ACK brokera pozostawia pending outbox. Dostarczenie i awaria przed SQL
receipt mogą powtórzyć dokładnie ten sam event; odbiorca deduplikuje.
Worker przed wysyłką ponownie porównuje zdarzenie z native wynikiem w bazie.
Nie naprawiaj dokumentu ręcznym UPDATE. Awaria konsumenta przed commit nie
ACKuje; po commit/przed ACK replay zachowuje jeden efekt. Poison lub konflikt
ID trafia do trwałej raw kwarantanny, a kolejny poprawny offset jest zachowany.
Naprawy wykonuj przez istniejący autoryzowany replay z original ID i grain key.

Read API pokazuje immutable history, sortowaną po business as-of, generated
time i ID. Spóźniony starszy wynik nie zastępuje nowszego. API ponownie
oblicza świeżość odczytu osobno od oryginalnego statusu publikacji; dawne
wyniki nie są domyślnie current. Strony wiąże hash widoku/grantu, obcy scope
jest niedostępny, policy revocation działa bez restartu API.

## Testy i granice dowodów

Lokalnie zaliczono pełne `make ci-checks` i 109 focused tests obejmujących
native contracts/batch/read oraz wcześniejsze forecast/delivery/replay.
PostgreSQL jest sprawdzany w CI przez istniejący stockout jobs acceptance
i qualified anomaly OCI acceptance: nowy błąd outboxu musi cofnąć całą
publikację. Test delivery callbacks używa jawnego double, nie dowodzi brokera.
Source Required CI dodatkowo wymaga rzeczywistych SQL/broker/SIGKILL testów
obu model projections oraz built UI/API/PostgreSQL drill.

`contracts/events/v2/model-fixtures.json` oznacza oba przykłady jako
`synthetic_contract_mechanics_only`. Literalny anomaly quality status wynika
z native schema i nie jest receipt kwalifikacji tego fixture. Stockout fixture
ma namespace test-mechanics i `mechanics_only`. Nie wolno użyć tych przykładów
do zamknięcia pełnej integracji trzech rzeczywistych modeli na 102 dniach.
Zdalny odbiór nowego commitu wymaga własnych wyników CI przed ready/merge.

## Zachowanie oryginalnych wyników do odbioru cross-repo

Oba rzeczywiste acceptory (`accept_anomaly_lifecycle.py` i
`check_stockout_final_acceptance.py`) zachowują przed cleanup pełny census
native wyników w `native-outbox/<census_id>/`. Odczyt SQL używa jednej
transakcji repeatable-read/read-only. Każdy event, oryginalny payload, grain
key i ewentualny broker receipt muszą zgadzać się z pełnym opublikowanym
batchem. Brakująca lub dodatkowa pozycja przerywa eksport. Receipt i JSONL
są immutable, związane SHA-256; samo ich istnienie nie poświadcza jakości ML
ani wysłania wiadomości do brokera.

Jednodniowa kwalifikacja wykonawcza AI 08 z 5 października wygasła
6 października. Osobny workflow `AI10 qualified stockout output` odtwarza
siedem dokładnie przypiętych oryginalnych artefaktów i sprawdza sześć
zakończonych ocen. Istniejący `qualify_stockout_final.py` rekonstruuje publiczne
wejścia z pełnych rodziców i wykonuje zamrożony model, tworząc nową jednodniową
kwalifikację. Odtworzenie używa osobnego zainstalowanego wheel z zaakceptowanego
commitu AI08 `68a3ede16da4bd8b93609c2489514c08e95a7384`: rodzice wiążą
historyczne hashe kodu curation, które różnią się po późniejszej integracji
main. Oryginalnych manifestów i hashów nie zastępuj aktualnymi. Późniejszy
cold worker, obraz, registry i outbox używają bieżącego kodu AI10; jego hash
zamrożonego scorera, locka i public-input adaptera musi zgadzać się z dowodami.
Workflow nie zmienia starej kwalifikacji i nie trenuje, nie kalibruje oraz
nie uruchamia ponownie ocen końcowych. Dalej wymagane są niezmienione bramki
review, secret scan, testy modelu, rzeczywisty MLflow/PostgreSQL, cold worker
i authenticated API. Raport zachowuje oryginalny pełny output i jego SHA-256.

Workflow przekazuje ten output przypiętemu konsumentowi Source na tym samym
runnerze. Konsument wymaga oryginalnego commitu/run ID, pełnego census i
oryginalnych 40 payloadów; wysyła każdy event dwukrotnie przez rzeczywisty
broker i sprawdza SQL checkpoints, deduplikację oraz wszystkie literalne ID
przez authenticated TCP API. To odbiór przekazania plikowego committed outbox;
nie jest jeszcze dowodem wysyłki z oryginalnej bazy AI ani widoku UI tych
rzeczywistych wyników. Wynik wykonania tego nowego workflow nadal jest wymagany.
