# AI 05 — publikacja i odczyt prognoz v12

[Worker v12](forecast-v12-worker.md) zapisuje kompletny prywatny receipt
obliczeń. Osobna publikacja udostępnia wynik po ponownym sprawdzeniu modelu.
Nie uruchamia inferencji ponownie i nie zmienia receipt, runu ani eksportu
AI 04. Samo `succeeded` w kolejce nie oznacza opublikowania prognoz.
[API zadań v12](forecast-v12-jobs-api.md) rozróżnia receipt obliczeń i
referencję zweryfikowanych, opublikowanych prognoz.

## Publikacja

Prywatna operacja wymaga uwierzytelnionego account z rolą `pipeline`,
capability `forecast:run`, własnego runu i pełnego scope. Korzysta z tych
samych prywatnych plików policy/credentials co kolejka. Tożsamość nie
pochodzi z argumentów żądania.

```bash
.venv/bin/python scripts/forecast_v12_queue.py \
  --env-file /private/path/service.env \
  --policy-file /private/path/pipeline-policy.json \
  --credentials-file /private/path/pipeline-credentials.json \
  publish --run-id run-RUN_ID
```

Publikacja wymaga udanego runu, kompletnego receipt i historii próby.
Sprawdza przypięty release, obraz, wejście, dokładne klucze, profile ID
części, sumy kontrolne i pełne pokrycie scope/horyzontu. Mediana, średnia,
przedział, baseline i metadane receptury są przenoszone dokładnie.
Wartości `null` pozostają `null`; nie powstają zastępcze zera ani przedziały.

Blokada modelu w PostgreSQL jest wspólna z lifecycle. Podczas niej publikator
sprawdza brak niedokończonej decyzji, brak odrzucenia wersji, ważność
dopuszczenia i pełny binding/kapsułę w MLflow. Aktualne aliasy
`champion`/`rollback` muszą odpowiadać bieżącemu head bazy. Nowsza promocja
nie przepina starszego zadania: jego własne dopuszczenie musi nadal obowiązywać.

Cała publikacja mieści się w jednym niezmiennym dokumencie, maksymalnie
1400 wierszy i 8 MiB canonical JSON. Zapis do `ai.v12_forecast_outputs` jest
jedną transakcją. Awaria po INSERT przed commit nie pozostawia częściowego
wyniku; receipt nadal istnieje i można ponowić publikację. Trigger SQL
sprawdza udany run, historię, piny, czas, grain i zgodność wyników z receipt.
UPDATE/DELETE są zabronione. Jeden run ma najwyżej jedną publikację;
powtórzenie zwraca ten sam artefakt i czas, bez nadpisania.

Odczyt istniejącej publikacji nie odnawia dopuszczenia. MLflow i PostgreSQL
pozostają osobnymi systemami; administrator zmieniający MLflow poza
lifecycle nie uczestniczy w transakcji bazy.

## API

`GET /api/v1/forecasts/v12` wymaga Bearer tokenu i `forecast:read`.
Wbudowany reader wybiera namespace `retailops-demand-forecast-v12`.
Dotychczasowe `/api/v1/forecasts` zachowuje swój kontrakt v1.

Filtry i granice są zgodne z [odczytem v1](forecast-read.md):
`product_id`, `selling_location_id`, `channel`, para `target_from`/`target_to`,
`as_of`, `inference_run_id`, `limit`, `offset`, `view_sha256`.
Brak filtrów rozwija wyłącznie scope polityki serwera, do 20 produktów,
5 lokalizacji i 2 kanałów. Większy grant wymaga zawężenia filtrów.
Nieznane i powtórzone parametry są odrzucane; nagłówki ról nie nadają dostępu.

Bez przypięcia runu/origin wybierany jest najnowszy kompletny wynik dla
produktu, lokalizacji, kanału i horyzontu. Porządek to origin, czas żądania,
run ID; późniejsza publikacja starego zadania nie zastępuje nowszego.
Filtr dat działa po wyborze i nie przywraca starszej prognozy.
Przypięcie runu czyta niezmienny wynik; run niewidoczny lub bez publikacji
daje ten sam 404 `forecast-output-not-found`. Brak wyników bez przypiętego
runu daje `200/no_data`.

Pierwsza strona zwraca `view_sha256` związany z użytkownikiem, scope,
filtrami i wszystkimi wybranymi prediction IDs. Kontynuacja wymaga tego
samego hasha: brak daje 409 `forecast-view-required`, zmiana widoku 409
`forecast-view-changed`. Zmiana zegara/freshness nie zmienia tożsamości
prognozy. Limit to 200 wierszy na stronę.

Item ma klucz biznesowy i `prediction:{key,candidate,baseline,metadata}`.
`candidate`/`baseline` zachowują osobne `median`, `mean`, `interval`.
Zawiera też ID publikacji, receipt, runu, release’u, profilu/części,
źródła/curated/features, wersję modelu, image digest i hashe dopuszczenia/pinu.
Nie ujawnia URI MLflow, ścieżek modelu, pełnego scope ani raportów przeglądu.
`quality_status=passed_at_publication` oznacza przegląd przy publikacji;
`approval_valid_until` zachowuje jego termin. Historyczny odczyt nie nadaje
nowej zgody na inferencję.

Polityka `forecast-read-v2` zachowuje wiek origin do 24 h, watermark i lag
obserwacji do 1 dnia. Nowszy run bez publikacji — również `succeeded` z samym
receipt — daje `stale/newer_run_unpublished` dla odpowiadających mu
serii/horyzontów. Zapisanie starego wyniku dziś nie odmładza jego źródła.

Odczyt działa w `REPEATABLE READ READ ONLY`, bez MLflow. SQL ogranicza scope
i namespace przed limitem. Maksymalnie 32 publikacje i 16 MiB łącznych
dokumentów/receipt są sprawdzane przed pobraniem payloadów; przekroczenie
daje 429 `forecast-read-budget`, bez cichego obcięcia. Timeout SQL to 3 s.
Dokumenty i receipt są sprawdzane w całości, także poza stroną i widocznym
podzbiorem. Niezgodność daje 503 `forecast-output-invalid`, bez częściowej odpowiedzi.

## Migracja i odbiór

Bieżący head to `0017_v12_outputs`, po `0016_v12_queue`. Tabela jest
oddzielna od publikacji v1. Przed wdrożeniem należy jawnie migrować bazę
według [runbooka Compose](local-stack.md). Trwałego stosu nie migrowano.
Downgrade wymaga backup/restore.

```bash
.venv/bin/python -m pytest -q tests/test_v12_publication.py
.venv/bin/python scripts/update_v12_batch_contracts.py --check
.venv/bin/python scripts/check_v12_publication.py
```

Runner używa własnych PostgreSQL/MLflow i testowego namespace
`retailops-demand-forecast-v12-mechanics`. API sprawdza prawdziwa aplikacja
ASGI z readerem PostgreSQL; nie jest to osobny wdrożony serwer HTTP.
Dane/export/predictor/przegląd są jawnymi małymi doubles z wymyślonymi seriami.
Odbiór obejmuje awarię transakcji, zachowanie 560 wierszy, idempotencję,
scope, paginację, uszkodzenie poza stroną, budżet bajtów i restart usług.
Obrazy są używane bez build/pull; usuwane są wyłącznie kontenery i anonimowe
wolumeny tego odbioru. Domyślny reader nie widzi testowego namespace.
Rozszerzony zestaw ma limit 600 s na fazę testów; limity lease/próby/predictora
pozostają bez zmian. Błąd/timeout zastępuje wcześniejszy wynik odbioru statusem
błędu i zachowuje prywatny log, zamiast pozostawiać stary raport `passed`.

[Dowód przygotowania](evidence/05-v12-publication.json) oddziela mechanikę
od kwalifikacji rzeczywistego modelu. Pozostają spójny backup/restore v12,
Required CI, rzeczywisty handoff AI 04, źródło i przegląd operatora,
a następnie końcowy batch i publikacja. **AI 05 pozostaje otwarte.**
