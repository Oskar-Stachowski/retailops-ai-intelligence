# AI 05 — wersje i trwałe dopuszczenie v12

[Prywatne dopuszczenie inference](forecast-v12-release.md) może teraz trafić do
osobnego rejestru `retailops-demand-forecast-v12`. Rejestr zachowuje oryginalny
format mediany, średniej i przedziału. Każda wersja wiąże immutable approval ID,
sumy kontrolne, oryginalny run kampanii, recepturę, źródło i przegląd operatora.
Dotychczasowy katalog modeli i endpointy v1 zachowują swój kontrakt;
[publikacja i odczyt v12](forecast-v12-publication.md) mają osobny zakres.
Odpowiedzi nowych decyzji mają `runtime_status=not_integrated`.

## Import dopuszczenia

Najpierw importuje się kompletny, zakończony eksport przez
[adapter evidence](mlflow-v12-evidence.md), potem wykonuje kwalifikację i
rzeczywisty przegląd wszystkich 10 bramek. Oryginalne evidence pozostaje w
eksperymencie `retailops/forecast-v12-campaign-evidence`, z flagami uprawnień
równymi `false`. Import dopuszczenia nie zmienia tego runu ani jego artefaktów.

`upload` ponownie sprawdza cały lokalny eksport przypiętym verifierem AI 04.
Porównuje oryginalny tracking run z pinem i sprawdza jego manifest, card,
freeze, handoff, metryki, replay, sygnaturę i wybraną recepturę. Mała kapsuła
dopuszczenia trafia do `retailops/forecast-v12-serving-approvals`: 14 plików
obejmujących kwalifikację, wejście, próbę prognoz, decyzję i raporty, oraz
manifest ich sum kontrolnych. Limit pojedynczego pliku wynosi 4 MiB.
Nie kopiuje ponownie checkpointów kampanii i nie tworzy wersji modelu.

Przykład po otrzymaniu rzeczywistego handoff:

```bash
.venv/bin/python scripts/mlflow_v12_lifecycle.py \
  --policy-file /private/path/operator-policy.json \
  --credentials-file /private/path/operator-credentials.json \
  --env-file /private/path/service.env upload \
  --release-dir /private/path/v12-approved/APPROVAL_ID \
  --release-id v12-inference-release-sha256-APPROVAL_SHA \
  --image-digest sha256:IMAGE_SHA \
  --run-dir /private/path/completed-v12-export \
  --verifier-python /private/path/pinned-ai04-venv/bin/python \
  --campaign-run-id ORIGINAL_CAMPAIGN_MLFLOW_RUN_ID
```

CLI wymaga uwierzytelnionej roli `promoter` oraz `model:decide`. Prywatny
katalog blokad ma 0700, pliki 0600; powtórzenie tego samego importu porównuje
zapisane bajty i zwraca ten sam run. Run po przerwanym lub błędnym uploadzie
pozostaje `RUNNING`/`FAILED` i wymaga przeglądu; nie jest automatycznie dublowany.
Przed ukończeniem zapisu sprawdzane są ponownie kapsuła, kampania i ważność.

## Decyzje i aktywny release w bazie

Żądania muszą odpowiadać [kontraktowi decyzji](../contracts/model_lifecycle/v12/request.schema.json).
`register` wymaga approval tracking run ID, approval ID i SHA pliku
`release.json`, a pozostałe akcje dokładnego numeru zarejestrowanej wersji.
Każde żądanie zawiera własne `decision_id` i uzasadnienie. `promote` wymaga
także dokładnego digestu obrazu dopuszczonego przez operatora.

```bash
.venv/bin/python scripts/mlflow_v12_lifecycle.py \
  --policy-file /private/path/operator-policy.json \
  --credentials-file /private/path/operator-credentials.json \
  --env-file /private/path/service.env decide < /private/path/decision.json
```

`register` tworzy wersję i ustawia `candidate`. `promote` przyjmuje wyłącznie
nowego kandydata, ustawia `rollback` na dotychczasowego championa, a `champion`
na dopuszczoną wersję. `reject` zapisuje trwałe odrzucenie i blokuje późniejszą
promocję; aktywnego championa ani wersji rollback nie można odrzucić.
`rollback` może przywrócić dokładnie poprzedni release z historii bazy.
Tworzy nowy zapis decyzji i release, zachowując historię poprzednich promocji.

Nowy release ma identyfikator `v12-model-release-sha256-*`. Zawiera pełne
powiązanie wersji, approval, image digest oraz poprzedni release/version.
Osobne tabele `ai.v12_model_*` przechowują decyzje, fazy, wersje, release’y
i aktualne wskazanie. Historię chronią SQL constraints, klucze obce i zakaz
UPDATE/DELETE. Zapis release’u, aktualnego wskazania i ukończenia decyzji
jest jedną transakcją. Odroczony trigger odmawia commit bez ukończenia decyzji.

`review` czyta konkretny numer wersji z wejścia `{"model_version":"1"}` i
pokazuje approval, aktualny release, aliasy, odrzucenie oraz niedokończone
decyzje. Nie wybiera wersji po mutable aliasie ani nie zmienia stanu.

## Awaria i wznowienie

Blokada advisory w PostgreSQL serializuje decyzje dla jednego modelu.
Plan z tożsamością operatora i dokładnym żądaniem jest zapisany przed zmianami
MLflow. Niedokończona decyzja blokuje nową decyzję dla tego modelu.
Wznowienie wymaga dokładnie tego samego żądania, ID i operatora.

Po utracie odpowiedzi tworzenia wersji mechanizm szuka wersji oznaczonej
tym `decision_id`. Jeżeli istnieje dokładnie jedna zgodna wersja, używa jej.
Jeżeli wynik jest nieznany, odmawia ponownego POST, aby nie dublować wersji.
Zmienione artefakty, URI, obraz, metadane albo nieoczekiwany alias powodują
odmowę. Ręczna ingerencja w MLflow wymaga przeglądu operatora.

MLflow i baza nie mają wspólnej transakcji. Podczas przerwanej promocji aliasy
mogą wskazywać etap pośredni, lecz baza zachowuje poprzedni aktywny release.
Przyszły worker musi przyjmować konkretny release z bazy, sprawdzać wszystkie
powiązania oraz odmawiać pracy przy niedokończonej decyzji lub niespójności.
Ukończone powtórzenie historycznej decyzji tylko zwraca wynik; nie nadpisuje
nowszego championa ani wskazania w bazie.

Nowa decyzja wymaga ważnego dopuszczenia. Już zapisany plan można dokończyć
po jego wygaśnięciu wyłącznie w celu rozliczenia rozpoczętych zmian metadata.
To nie przedłuża ważności: inference nadal odmawia wykonania po terminie,
a nowa promocja wymaga aktualnej kwalifikacji i przeglądu.

## Migracja i odbiór

Nowy head to **`0015_v12_lifecycle`**, po `0014_forecast_freshness`.
Po nim dodano [kolejkę v12](forecast-v12-worker.md); wspólny DB guard i readiness
wymagają obecnie dokładnie `0017_v12_outputs` opisane w [publikacji v12](forecast-v12-publication.md). Istniejący stos
potrzebuje jawnej migracji przed uruchomieniem nowego kodu, według
[runbooka Compose](local-stack.md). CLI nie migruje bazy automatycznie.
Downgrade historii wymaga backup/restore; migracja nie usuwa istniejących tabel.
W tym odbiorze migrowano wyłącznie własną jednorazową bazę testową.

```bash
.venv/bin/python -m pytest -q tests/test_v12_lifecycle.py
.venv/bin/python scripts/update_v12_lifecycle_contracts.py --check
.venv/bin/python scripts/check_v12_lifecycle.py
```

Portable testy używają jawnych małych doubles dla eksportu, źródła, prognoz
i raportów. Osobny runner uruchamia prawdziwe PostgreSQL i MLflow z istniejących
obrazów, z losowymi portami loopback i własnymi zasobami. Sprawdza HTTP,
wersje, aliasy, transakcje, błędy między etapami oraz restart obu usług.
Używa tylko `retailops-demand-forecast-v12-mechanics` w `APP_ENV=test`;
usuwa własne kontenery i anonimowe wolumeny. Nie buduje i nie pobiera obrazów.
[Dowód przygotowania](evidence/05-v12-lifecycle.json) odróżnia ten odbiór
mechaniki od kwalifikacji rzeczywistej kampanii.

[Kolejka/worker v12](forecast-v12-worker.md) zapisują kompletny receipt obliczeń
z dzieleniem batchu zgodnie z limitami. Po [publikacji i API v12](forecast-v12-publication.md)
pozostaje sprawdzenie spójnego
backup/restore v12. Końcowy odbiór wymaga rzeczywistego eksportu i źródła,
rzeczywistych raportów operatora oraz Required CI. **AI 05 pozostaje otwarte.**
