# Prognoza v12 w narzędziu AI12

`NativeForecastTool` podłącza istniejący `PostgresV12ForecastReader` do
`get_demand_forecast`. Adapter odczytuje opublikowany wynik; nie wykonuje
inference, treningu, promocji ani zapisu. Rejestracja pozostaje po stronie
serwera z kwalifikowanymi źródłami i konfiguracją.

```python
from retailops_ai.adapters.forecast_v12_tool import NativeForecastTool
from retailops_ai.forecast_jobs.v12_reader import PostgresV12ForecastReader

reader = PostgresV12ForecastReader(engine, environment="local")
adapters["get_demand_forecast"] = NativeForecastTool(reader, "local")
```

Zewnętrzny kontrakt narzędzia zachowuje nazwę i schemat żądania. Pole `result`
ma rozłączny discriminator `contract_type`: historyczny `tool_result` lub
nowy `forecast_v12_tool_result`. [Schemat natywnego wyniku](../contracts/agent/v1/native-forecast-read.v1.schema.json)
zawiera oryginalną stronę `V12ForecastPage`, dokładne żądanie i środowisko.
Nie konstruuje `PredictionRecord` ani `ModelRecord` z aliasu lub identyfikatora.
Zachowuje candidate i baseline: mean, median, interval, metadata, exclusion,
wszystkie identyfikatory publikacji, release, receipt, model version, approval,
pin, obraz, źródło, Curated, FeatureSet oraz ocenę świeżości.

Reader sprawdza publikację, zakończony run i computation receipt zgodnie
z [kontraktem publikacji](forecast-v12-publication.md). Wykonuje ograniczony
odczyt PostgreSQL w transakcji `REPEATABLE READ`, `READ ONLY`, z timeoutem SQL
3 s. Obowiązuje limit 32 kandydatów i 16 MiB odczytu. Adapter używa tego
readera w wątku; executor nadal ogranicza oczekiwanie do 5 s, rozmiar odpowiedzi
i całe wykonanie. Timeout nie uruchamia retry ani kolejnych stron.

Adapter wymaga roli `operator`, `assistant:query`, `forecast:read` i całego
żądanego zakresu sprzedaży. Przekazuje readerowi tę samą tożsamość z zakresem
ograniczonym do dokładnie żądanych produktów, lokalizacji i kanału. Nie
modyfikuje pierwotnego grantu i nie utożsamia lokalizacji sprzedaży z magazynem.
Środowisko musi być `local` lub `test`, a namespace produkcyjny
`retailops-demand-forecast-v12`; przestrzenie mechanics/development są odrzucane
przy konstrukcji i ponownie przed odczytem.

Przed odczytem liczba kombinacji produkt × lokalizacja × dzień musi mieścić
się w żądanym limicie. Niepusty wynik wymaga pełnego pokrycia tej siatki,
jednej strony od offsetu 0, unikalnych prediction IDs i grainów, dokładnego
origin i horyzontu oraz sprawdzonych dat publikacji/approval/freshness.
Partial, następna strona, obcy scope lub model kończą się bez zaakceptowanych
dowodów. Sprawdzony pusty wynik ma `no_data` i ref widoku; nie tworzy liczby zero
ani sztucznego timestampu dostępnej prognozy.

Prognoza dzienna ma istniejącą politykę `forecast-read-v2`: origin do 24 h,
kompletne źródło i brak nowszego nieopublikowanego runu. Odrębny limit 300 s
dotyczy wieku sprawdzonej strony, a nie wieku zamkniętego origin. Executor
ponownie sprawdza oba czasy i ważność approval w chwili użycia; przyszła strona,
stale lub unknown nie wspierają odpowiedzi. Nie przedłuża to ważności sugestii
wynoszącej 300 s.

Graf tworzy fakt wyłącznie z jawnie opublikowanego candidate mean obserwowanej
sprzedaży, z jednostką, grainem, datą celu, mean source i referencjami. Nie
oblicza go z mediany lub interval. Brak mean, w tym closed target, daje
ograniczenie i brak takiego faktu. Pełny natywny wynik pozostaje w zaakceptowanej
migawce narzędzia. `passed_at_publication` nie oznacza aktualnego wdrożenia;
prognoza obserwowanej sprzedaży nie identyfikuje nieocenzurowanego popytu.

Natywna prognoza obsługuje odczyt odpowiedzi. Nie jest konwertowana do
historycznego warunku `review_replenishment`, który wymaga osobnych zgodnych
źródeł ryzyka, zapasu i zatwierdzonego modelu wdrożonego. Integracja natywnego
model status i stockout, fizyczny mapping oraz przepływ AI10 outbox/v2 pozostają
otwarte. Brak adaptera innego narzędzia jest nadal błędem zależności.

Nowe kandydaty konfiguracji `.native-v12.v1.json` wiążą zmieniony kod i schematy.
Wcześniejsze manifesty, etykiety, receipts i wyniki Bedrock pozostają zachowane.
Profil pytań ma `labels_state=proposed`, dopuszczony tylko w jawnym teście.
Testy adaptera i HTTP używają małych, oznaczonych fixtures i fake chat; nie
kwalifikują produkcyjnej publikacji PostgreSQL ani Sonnet/Titan.

```bash
make agent-security-test
make agent-evaluate PROVIDER=fake
make ci-checks
```
