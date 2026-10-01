# Import kampanii AI 04 v12 do MLflow

Adapter zapisuje kompletny eksport `forecast-functional-v12-run-1.0.0` w
eksperymencie `retailops/forecast-v12-campaign-evidence`. Zachowuje oryginalne
run/campaign/freeze/replay IDs, czasy eksportu, wszystkie pliki i checksumy,
receptury, cohort lineage oraz raporty niezaliczonych bramek. Status modelu
jest osobnym tagiem: `FINISHED` oznacza zakończony import, także dla `not_ready`.

Globalne/pooled metryki mają osobne nazwy dla `candidate`/`baseline` oraz
`median`/`mean`/`interval`: MAE mediany, MSE/bias średniej i coverage/score
przedziału. Pełne segmenty, przyczyny błędów i wartości `null` pozostają w
oryginalnym `campaign/metrics.json`. Brak miary nie jest logowany jako zero.
Adapter nie trenuje, nie ocenia nowych danych i nie regeneruje źródła.

Rozmiary plików sprawdza osobny kontrakt v12: dopuszcza puste pliki i checkpointy
większe niż 256 MiB, zachowując limit całej kampanii 64 GiB z AI 04.
Sygnatura v12 opisuje wynik jako trójkę `candidate`, `baseline`, `metadata`;
wspólny `output_schema` definiuje medianę, średnią i przedział dla obu prognoz.

## Przypięty weryfikator i komendy

Eksport sprawdza również fingerprint całego kodu i zależności AI 04.
Operator wskazuje osobny, wcześniej zweryfikowany interpreter z zainstalowanym
wheel AI 04 zgodnym z kodem i lockiem eksportu. To zaufany input operatora;
eksport nigdy nie wskazuje interpretera ani wykonywalnego kodu.

Podproces `-I -B` importuje weryfikator z `site-packages`, bez `PYTHONPATH`,
poświadczeń AWS/DB/MLflow i zapisu bytecode. Oryginalny `verify_run` sprawdza
bajty, checkpointy, niezależny replay, quality gates i kontrakt raportów.
Nieprawidłowy interpreter lub niepełny eksport daje odmowę przed zapisami MLflow.
Domyślny timeout to 3600 s; jawny `--verify-timeout` dopuszcza 1–7200 s.

Z katalogu AI 05, po zakończeniu eksportu:

```bash
.venv/bin/python scripts/mlflow_v12_evidence.py \
  --run-dir /private/path/functional-v12-run-sha256-RUN_ID \
  --verifier-python /private/path/pinned-ai04-venv/bin/python \
  --verify-only
```

`--verify-only` nie łączy się z MLflow i nie tworzy katalogu importu.
Po weryfikacji import do istniejącego MLflow na `127.0.0.1:5010`:

```bash
.venv/bin/python scripts/mlflow_v12_evidence.py \
  --run-dir /private/path/functional-v12-run-sha256-RUN_ID \
  --verifier-python /private/path/pinned-ai04-venv/bin/python
```

CLI daje exit 0 dla `verified`, `imported` lub `already_imported`, a exit 1
z kodem `mlflow_v12_import_failed` dla błędu, bez treści danych i credentials.
Obecna kampania ma osobny wheel w
`/private/tmp/retailops-ai04-stage-b-ci/.venv`; nie należy zmieniać tego środowiska
w trakcie AI 04. Rzeczywisty import czeka na ukończenie końcowego eksportu.

## Powtórzenia i awarie

Pliki są przesyłane strumieniowo pod oryginalnymi ścieżkami, w tym wszystkie
checkpointy. Nie powstaje druga lokalna kopia archiwum kampanii.
`.local/mlflow-v12-imports` przechowuje tylko prywatne blokady; `--work-dir`
nie może zachodzić na eksport. Lock serializuje import tego samego runu
w procesach jednego hosta, nie między wieloma hostami.

Powtórzenie wymaga dokładnie jednego ukończonego, zweryfikowanego runu.
Sprawdza parametry, tagi, metryki i bajty każdego zdalnego pliku. Zmieniony
tag ID jest też wyszukiwany przez oryginalny parametr i nazwę runu.
Uszkodzony lub niejednoznaczny run powoduje odmowę zamiast nowego importu.
Błąd zachowuje przesłane pliki oraz `FAILED/import_status=failed`.
Niepotwierdzony zapis failed statusu daje osobny błąd. Nie ma automatycznej
próby ponowienia ani usuwania istniejących wyników.

## Granice odbioru

Zakres: eksport → weryfikacja → MLflow tracking. [Stary importer](mlflow-evidence.md),
[API historycznych ocen](evaluations.md) i [runtime](forecast-runtime.md)
zachowują dotychczasowe kontrakty. Nowe evidence nie jest przemianowane na v1.

Handoff v12 ma `deployable_service_contract=false`. Nawet oryginalne `ready`
nie nadaje `registration_eligible`, `promotion_eligible` lub `serving_eligible`.
Loader, kontrakt runtime, rzeczywisty load smoke, dopuszczenie wersji,
batch i read API v12 wymagają osobnego odbioru AI 05.

Testy obejmują małe jawne fixtures, rozdzielenie celów, zachowanie bajtów,
idempotencję, uszkodzenia, failed import i rzeczywisty transport HTTP na loopback.
Fixtures mapowania delegują semantykę do oryginalnego verifiera i nie są nową
kwalifikacją modeli. Odbiór rzeczywistej końcowej kampanii na istniejącym
serwerze MLflow pozostaje do wykonania po ukończeniu AI 04.

[Dowód lokalnego odbioru adaptera](evidence/05-v12-adapter.json) wiąże sumy
kontrolne implementacji z **51 zaliczonymi testami**, bez skips, oraz kontrolami
Ruff, Mypy, dokumentacji i rzeczywistym transportem HTTP.
