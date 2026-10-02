# AI 05 — odczyt receptury i prognozy v12 offline

`load_v12` weryfikuje kompletny eksport przez [adapter v12](mlflow-v12-evidence.md),
a następnie ładuje jawnie wskazaną recepturę. Operator podaje `run_id`,
`cohort_id`, nazwę folda i `recipe_id`. Nie ma domyślnej pierwszej/ostatniej
kohorty, wyboru modelu po metrykach ani aliasu `champion`.

Ten zakres przygotowuje odbiór odczytu i prognoz bez połączenia z MLflow,
DB, API lub Dockerem. Wynik ma cel `offline_load_predict_acceptance` i zawsze
`serving_eligible=false`, `model_refits=0`, `published_forecast_outputs=0`.
Można sprawdzić mechanikę oryginalnego `not_ready`; taka próba nie zalicza bramek.

## Receptura, środowisko i wejście

Pin wiąże run/campaign/freeze/replay IDs, kohortę, cały plan folda, recepturę,
checksumy manifestu/sygnatury/receptury, source/snapshot IDs oraz kod i lock.
Przed odczytem `load_v12` wykonuje pełny oryginalny `verify_run` przez wskazany
zainstalowany wheel AI 04. Każda prognoza ponownie sprawdza małe artefakty;
podproces potwierdza również cały aktualny fingerprint wheel i zależności.
Nie kopiuje checkpointów ani archiwum kampanii i nie uruchamia oceny jakości.

Wejście to istniejący `PreparedInputs` z [pipeline wejściowego](forecast-runtime.md).
Musi mieć tę samą parę source/snapshot IDs co wskazana kohorta, ten sam lock
oraz domyślną `FeaturePolicy` v1 używaną przez kompaktowy pipeline v12.
Same deklaracje ID nie zastępują odbioru źródła: rzeczywisty pakiet należy
zbudować z wcześniej zweryfikowanych feature/curated parents.

Loader i podproces sprawdzają pełny grain, historię, dostępność danych,
zgodność cech obserwowanych z historią, świeżość i kalendarz.
Bazowe mediany/średnie/przedziały wylicza oryginalne `empirical_baselines`
wyłącznie z dostarczonej historii. Nie przyjmuje dowolnych `baseline_points`
od klienta. `actual` i `label_available_at` są tworzone jako `null`.
Brak historii/kalibracji nie staje się wymyślonym zerem.

Origin musi zamykać dzień UTC, być późniejszy od `selection_cutoff`, nie być
w przyszłości i należeć do okna `development_holdout` wskazanego folda.
Ten ostatni warunek zachowuje rolę obecną w sygnaturze eksportu. Nie odczytuje
holdout labels i nie wykonuje ponownej oceny tego okna.
[Osobny kontrakt inference i prywatny przegląd](forecast-v12-release.md) dopuszcza
daty poza tym oknem, po odbiorze źródła i jawnej decyzji. Nowe źródło lub feature
package wymagają kolejnej kwalifikacji; kontrakt offline zachowuje tę granicę.

Obsługiwane są empiryczne warianty `baseline`, `zero_only`, `additive`.
`hgb_blend` powoduje odmowę: potrzebuje osobno przypiętego modelu produkującego
`hgb_mean`. Loader nie podstawia w jego miejsce innej prognozy.

## Wyniki i limity

Każdy wynik zachowuje klucz oraz `candidate`, `baseline`, `metadata`.
Obie prognozy mają osobne `median`, `mean`, `interval.lower/upper`.
Mediana i przedział kandydata muszą być identyczne z zachowanym odniesieniem;
średnia może być inna i może wychodzić poza przedział kwantyli.
Wartości `null` pozostają `null`; nie ma zlania celów do jednej quantity.

Wynik wiąże pełny pin, profile ID, kolejność/liczbę kluczy i hash prognoz.
Raport podaje rzeczywisty czas cold load, obliczeń i peak RSS podprocesu.
Cold load nie obejmuje wcześniejszej pełnej weryfikacji archiwum.
Powtórzenie ma identyczne prognozy; czasy wykonania i `generated_at` są nowe.

Jedno wywołanie dopuszcza do 256 wierszy, 4 MiB żądania, 1 MiB wyniku
i 16 KiB stderr. Domyślnie ma limit 60 s i 512 MiB RSS; maksima to 120 s
i 1 GiB. Przekroczenie limitu kończy i zbiera podproces. Nie ma retry.
Proces `-I -B` nie otrzymuje poświadczeń ani `PYTHONPATH`; biblioteki liczą
na jednym wątku. Scratch jest prywatny i usuwany po próbie.

Wersjonowane [schema pinów](../contracts/forecast/v12_offline/runtime_pin.schema.json),
[wywołania](../contracts/forecast/v12_offline/execution.schema.json) i
[wyniku](../contracts/forecast/v12_offline/runtime_result.schema.json) są osobnym
kontraktem offline. Kontrola snapshotów:

```bash
.venv/bin/python scripts/update_v12_runtime_contracts.py --check
```

## Próba na rzeczywistym eksporcie

Po ukończeniu AI 04, z katalogu AI 05 i gotowym zweryfikowanym pakietem wejścia:

```bash
.venv/bin/python scripts/check_v12_runtime.py \
  --run-dir /private/path/functional-v12-run-sha256-RUN_ID \
  --verifier-python /private/path/pinned-ai04-venv/bin/python \
  --run-id functional-v12-run-sha256-RUN_ID \
  --cohort-id COHORT_ID --fold FOLD_NAME \
  --recipe-id functional-v12-recipe-sha256-RECIPE_ID \
  --inputs-dir /private/path/verified-inference-inputs
```

CLI zwraca tylko piny, liczbę wierszy, hash i pomiary, bez treści prognoz.
Exit 0 oznacza ukończoną próbę offline; exit 1 daje
`v12_runtime_acceptance_failed`, bez treści danych i poświadczeń.

[Dowód przygotowania](evidence/05-v12-runtime.json) odróżnia portable unit/process
tests od próby rzeczywistego predictora z przypiętego wheel AI 04.
Ta druga używa małych wymyślonych validation rows i jawnego transport double
pełnego eksportu, nie aktywnej kampanii ani rzeczywistej kwalifikacji źródła.
Próbę rzeczywistego algorytmu (offline oraz osobnej ścieżki inference) uruchamia
się osobno ze wskazanym wheel:

```bash
AI04_VERIFIER_PYTHON=/private/path/pinned-ai04-venv/bin/python \
  .venv/bin/python -m pytest -q tests/check_v12_native.py
```

Ten plik nie wchodzi do domyślnej kolekcji testów; brak wskazanego środowiska
przy jawnym uruchomieniu jest błędem, nie pominięciem. Portable CI używa jawnego
installed predictor double do sprawdzenia granicy procesu.

14 prognoz zgodziło się dokładnie z oryginalnym predictorem; powtórzenie
zachowało hash. Rzeczywisty final export i production serving nadal czekają
na osobny odbiór. Po odbiorach [inference/przeglądu](forecast-v12-release.md),
[lifecycle v12 w MLflow/DB](mlflow-v12-lifecycle.md) oraz
[kolejki/workera v12](forecast-v12-worker.md) oraz [publikacji i read API](forecast-v12-publication.md)
pozostają spójny backup/restore i odbiór rzeczywistego przepływu.
## Zamknięte dni

Kompletny zakres 7/14 dni zachowuje również dni, w których źródłowy kalendarz
potwierdza zamknięcie sklepu. Oryginalny predictor zwraca brak wartości, a
adapter wiąże go z `exclusion_reason=closed_target`. Jest to `null`, nie zero.
Brak kalendarza, nieznany status lub sprzeczne flagi nadal powodują odmowę.
