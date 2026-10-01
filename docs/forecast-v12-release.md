# AI 05 — kwalifikacja wejścia, przegląd i prognozowanie v12

Ten zakres dodaje osobny kontrakt `inference`, odbiór odczytu/prognoz oraz
prywatną decyzję operatora. Oryginalny [eksport v12](mlflow-v12-evidence.md)
i [pin offline](forecast-v12-runtime.md) zachowują swoje flagi i sygnaturę.
Decyzja ma nowy content-addressed `v12-inference-release-sha256-*`.
Nie jest wersją MLflow, aliasem `champion` ani aktywnym release head w DB.
Dotychczasowy worker i `/api/v1` nadal obsługują kontrakt jednej quantity.

## Odbiór przed decyzją

`qualify_v12` wymaga jawnych run/cohort/fold/recipe IDs i całego zakończonego
eksportu. Oryginalny verifier AI 04 sprawdza wszystkie artefakty i znaczenia
przez osobny przypięty wheel. `forecast_model_status=not_ready` kończy odbiór
odmową, przed odczytem wejść i próbą prognozowania.

Pakiet `PreparedInputs` jest ponownie budowany z pełnych, zweryfikowanych
feature/curated parents i porównywany z dostarczonym `inputs.json`.
Samo podanie poprawnych identyfikatorów źródła nie zalicza tego sprawdzenia.
Produkcja wymaga wejścia v1.1 z dowodem świeżości źródła; jego brak nie może
być ukryty przez użycie starszego v1.0. `unknown` lub historyczny watermark
pozostają takimi danymi — receipt odbioru nie twierdzi, że źródło jest aktualne.

Polityka jest celowo jawna: ta sama kohorta, receptura, source/snapshot IDs,
feature package ID, curated descriptor SHA, lock i domyślna FeaturePolicy.
Nowy feature package lub inne źródło wymagają nowej kwalifikacji i przeglądu.
Nie ma automatycznego mapowania kohorty na inne dane ani wyboru po metrykach.
Późniejszy worker ma pobierać wejścia z zaufanego magazynu, po jego odbiorze
źródła. Walidacja samych obiektów JSON nie dowodzi pochodzenia obserwacji.

Nowa sygnatura zmienia tylko `row.role` z ról oceny na `inference`.
Zachowuje zamrożone ograniczenia kategorii, kanałów, horyzontów, recipe/cohort
IDs i zakaz actual/label availability. Hash wiąże sygnaturę po tej zmianie;
oryginalny plik nie jest modyfikowany. Origin musi zamykać dzień UTC, być po
selection cutoff i nie być w przyszłości. Nie musi należeć do holdout window.
Wszystkie kontrole historii, PIT, bazowych prognoz i kalendarza nadal obowiązują.

Dwie izolowane próby `load/predict` muszą dać identyczny hash prognoz.
Receipt wiąże pin, politykę, scope/profile, pełny wynik pierwszej próby,
wejście, rzeczywisty czas/RSS i limity. Nie ocenia jakości ponownie, nie
trenuje i nie publikuje prognoz. `serving_eligible=false` zostaje w kwalifikacji.
Ważność wynosi domyślnie 24 h w CLI, maksymalnie 7 dni od odbioru.

Przykład po ukończeniu AI 04, z uprzednio przygotowanymi pakietami danych:

```bash
.venv/bin/python scripts/mlflow_v12_release.py qualify \
  --run-dir /private/path/completed-v12-export \
  --verifier-python /private/path/pinned-ai04-venv/bin/python \
  --run-id functional-v12-run-sha256-RUN_ID \
  --cohort-id COHORT_ID --fold FOLD_NAME \
  --recipe-id functional-v12-recipe-sha256-RECIPE_ID \
  --inputs-dir /private/path/verified-inputs \
  --feature-dir /private/path/verified-features \
  --curated-dir /private/path/verified-curated \
  --output-root /private/path/v12-qualifications
```

## Prywatny przegląd

`approve` uwierzytelnia jednego operatora przez istniejące prywatne pliki
policy/credentials. Wymaga jednocześnie roli `promoter` i `model:decide`.
Nie przyjmuje principal ID ani uprawnień z żądania decyzji.

Żądanie zgodne z [approval schema](../contracts/forecast/v12_inference/approval_request.schema.json)
wiąże oczekiwane qualification ID, image digest, uzasadnienie i dokładnie
10 raportów: source, features, pit, protocol, segments, signature, resources,
security_license, model_card, freshness_drift_compatibility.
Każdy musi być jawnie `passed`. Failed/not_ready/not_evaluable lub brak raportu
powoduje odmowę; nie ma automatycznego wystawiania tych raportów.

Raporty przechowuje się pod `reports/GATE_NAME.json` we wskazanym katalogu.
Ich oryginalne bajty muszą odpowiadać receiptowi w żądaniu. Dokument musi
wiązać `qualification_id`, `gate`, `status=passed`; pozostała treść to dowody
i wynik rzeczywistego przeglądu operatora. Dowody jakości/protokołu odnoszą się
do ukończonej kampanii, a dowody źródła/PIT/sygnatury/zasobów do tej kwalifikacji.
Kontrole bezpieczeństwa/licencji i zgodności/świeżości wymagają osobnej oceny.
Zgodność JSON i checksumów nie zastępuje decyzji merytorycznej.

```bash
.venv/bin/python scripts/mlflow_v12_release.py approve \
  --run-dir /private/path/completed-v12-export \
  --verifier-python /private/path/pinned-ai04-venv/bin/python \
  --qualification-dir /private/path/v12-qualifications/QUALIFICATION_ID \
  --policy-file /private/path/operator-policy.json \
  --credentials-file /private/path/operator-credentials.json \
  --request-file /private/path/review-request.json \
  --reports-dir /private/path/review-evidence \
  --output-root /private/path/v12-approved
```

Przegląd ponownie weryfikuje cały eksport. Pakiet dopuszczenia zachowuje
kwalifikację, wynik próby, wejście, wszystkie raporty i decyzję z tożsamością
operatora. Zapis jest atomowy, bez nadpisywania, z fsync i kontrolą skopiowanych
bajtów. Podmiana w trakcie długiej weryfikacji nie publikuje niespójnego pakietu.
Katalog ma 0700, pliki 0600; loader wymaga właściciela i prywatnych uprawnień,
odrzuca symlinki, dodatkowe pliki, błędne hashe i niespójne decyzje.
Nie kopiuje checkpointów ani całej kampanii.

## Prognozy po przeglądzie

`load_approved_v12` wymaga oczekiwanego immutable release ID i zgodnego image
digest oraz ponownie sprawdza eksport. Digest jest wymaganym pinem środowiska,
nie automatyczną atestacją faktycznego obrazu hosta. Przyszły worker musi używać
zweryfikowanego digestu własnego wdrożenia. Loader nie śledzi mutable aliasów.
Ważność decyzji obowiązuje przed próbą, podczas niej i przed zwrotem wyniku.
Limitów zasobów nie można zwiększyć ponad zaakceptowane wartości.

```bash
.venv/bin/python scripts/mlflow_v12_release.py predict \
  --run-dir /private/path/completed-v12-export \
  --verifier-python /private/path/pinned-ai04-venv/bin/python \
  --release-dir /private/path/v12-approved/RELEASE_ID \
  --release-id v12-inference-release-sha256-RELEASE_ID \
  --image-digest sha256:REVIEWED_IMAGE_DIGEST \
  --inputs-dir /private/path/verified-inputs
```

CLI zwraca tylko ID, liczbę wierszy i hash, bez prognoz i credentiali.
Każdy błąd ma publiczny kod `v12_inference_release_failed`, exit 1.
Próba nie zapisuje wyników do publicznego magazynu.

Wynik zachowuje osobne candidate/baseline z medianą, średnią i przedziałem
oraz metadata. Klucz ma rolę `inference`; oryginalny export pin nadal ma
`serving_eligible=false`. Dopiero odrębny context wiąże ważną decyzję z
`qualified_forecast_v12`. `published_forecast_outputs=0`, `model_refits=0`.
Obowiązują dotychczasowe granice 256 wierszy/4 MiB żądania/1 MiB wyniku,
60 s/512 MiB domyślnie, maksymalnie 120 s/1 GiB.

Callback `tick` pozwala przyszłemu workerowi kontrolować lease/fencing i
heartbeat. Jego wyjątek zabija i zbiera podproces, bez zwrotu częściowego wyniku.
Ten zakres nie dodaje trwałej kolejki, batch splitting ani publication API v12.

## Weryfikacja i pozostały zakres

Snapshoty [kontraktów v12](../contracts/forecast/v12_inference) sprawdza
`scripts/update_v12_inference_contracts.py --check`, również w `make contracts-check`.
Portable testy obejmują pełną ścieżkę na jawnych małych verifier/source/predictor
doubles, braki bramek, fałszywy context, podmiany, wygaśnięcie i utratę lease.
Osobna próba rzeczywistego przypiętego wheel AI 04 sprawdza oryginalny algorytm:

```bash
AI04_VERIFIER_PYTHON=/private/path/pinned-ai04-venv/bin/python \
  .venv/bin/python -m pytest -q tests/check_v12_native.py
```

Próba daje 14 wyników identycznych z bezpośrednim predictorem również poza
holdout window. Fit używa wyłącznie małych wymyślonych validation rows.
Pełny verifier, odbiór źródła i raporty decyzji są w tej próbie jawnymi doubles;
nie zaliczają rzeczywistego modelu. [Dowód przygotowania](evidence/05-v12-release.json)
rozróżnia te granice. Nie odczytano danych aktywnej kampanii AI 04.

[Lifecycle v12 w MLflow/DB](mlflow-v12-lifecycle.md) dodaje rejestrację,
decyzje i aktywny release head, a [kolejka/worker v12](forecast-v12-worker.md)
kompletny receipt obliczeń. Następne zakresy to atomowa publikacja wszystkich
trzech celów i read API.
Rzeczywisty odbiór tych elementów wymaga końcowego eksportu i źródła AI 04;
AI 05 pozostaje otwarte.
