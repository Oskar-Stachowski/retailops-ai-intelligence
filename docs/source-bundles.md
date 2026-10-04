# AI10 — pobranie i import niezmiennego eksportu Source

Status całego AI10: `in_progress`. Interfejs przesyła gotowy eksport Source
Snapshot 1.0/1.1/1.2. Importer obsługujący natywny 1.2 pochodzi z zatwierdzonego
commitu `1a1916630fa3ed6601a27fec5b5feaf480fc9b1b`; kod producenta jest przypięty
do `dee564ef98dd7c0324dd2c88d418dc544e7ecdd6`. Dokładne wersje i sumy kopii
kontraktów zapisuje `src/retailops_ai/source_bundle/upstream.json`.
Cały zatwierdzony importer ma osobną kopię `source_snapshot_native` i działa
wyłącznie w świeżym procesie transferu. Oryginalne moduły `source_snapshot`
pozostają identyczne z bazą v12; pełny pin kampanii jest nadal
`8f12dc3744880f1b2a68b4a009640dce4dcf543d8b3396c038bc175c7e3ee011`.
Kontrola typów sprawdza osobno dokładny namespace wykonania natywnej kopii.

## Instrukcja

1. Operator Source przygotowuje zatwierdzony, kompletny eksport bez evaluation
   truth, publikuje jego niezmienne bajty i nadaje osobny credential z grantem
   na konkretny `bundle_id`. Instrukcja Source: `docs/runbooks/source-bundles.md`
   w przypiętej wersji repozytorium. Uprawnienie obejmuje cały wskazany eksport.
2. W tym repozytorium utwórz osobne środowisko, korzystając z przypiętego locka:

   ```sh
   uv sync --locked --project tools/source-bundle-verification
   ```

   Ten profil wymaga Python 3.11 i zawiera wyłącznie Pydantic, Arrow oraz
   JSON Schema z ich zależnościami. Nie zmienia głównego locka ani środowisk ML.
3. Zapisz config jako zwykły plik `0600`, należący do użytkownika uruchamiającego
   klienta. Credential pobierz z secret managera i trzymaj poza repozytorium:

   ```json
   {
     "base_url": "https://source.example.invalid",
     "credential": "<przydzielony osobny credential>",
     "bundle_id": "<jawnie przydzielony bundle_id>"
   }
   ```

   HTTPS jest domyślne. `allow_http_loopback: true` dopuszcza tylko lokalny
   odbiór HTTP. URL nie może zawierać credential, query ani fragmentu.
4. Wymagane zastosowanie wybierz na podstawie kwalifikacji oryginalnego eksportu.
   Przykład dla natywnego eksportu anomaly:

   ```sh
   PYTHONPATH=src uv run --locked --project tools/source-bundle-verification python -m retailops_ai.source_bundle.cli --config /private/source-bundle.json --generated-root data/generated --require-use-case anomaly_source
   ```

   Można powtórzyć `--require-use-case`, jeżeli eksport kwalifikuje każdy z nich.
   Bez tego parametru wymagane jest `forecast_source`. Nie pomijaj odmowy
   kwalifikacji. Żaden API worker ani model nie uruchamia tego importu samoczynnie.
5. Zachowaj wynik z `source_dataset_id`, `snapshot_id`, `bundle_id` i statusem
   `published` albo `reused`. Importer weryfikuje pełną listę bajtów, sumy
   fizyczne i logiczne, typy tabel, kontrakty, namespace oraz raport kwalifikacji.
   Publikacja jest atomowa i nie nadpisuje istniejącego identyfikatora.
6. Ponów tę samą operację: identyczny import ma zwrócić `reused` z tymi samymi
   oryginalnymi identyfikatorami. Konflikt lub uszkodzenie wymaga zbadania
   źródłowego eksportu; nie poprawiaj opublikowanych bajtów w miejscu.

## Granice i odbiór

Pobieranie odbywa się w osobnym procesie z domyślnym limitem całkowitym 180 s
(maksymalnie 300 s), także dla powolnego body. Brak redirectów i proxy z env,
credential tylko na stdin procesu, maksymalnie dwie próby błędów przejściowych,
64 MiB na plik, 2 GiB razem i 10 000 plików. Nie publikuje częściowego importu.
Sam typowany import ma oddzielny limit procesu 600 s.
Zła tożsamość, checksum, schema, ścieżka, evaluation truth lub wymagane
zastosowanie kończą operację odmową. Błędy CLI nie wypisują credential.

`make source-bundle-check` sprawdza przypięte kopie; testy transportu działają
na własnych tymczasowych socketach. Wymagany odbiór między repozytoriami
generuje eksport natywny 1.2, używa rzeczywistego API Source i wykonuje dwa
rzeczywiste importy wraz z kontrolami odmowy dostępu i uszkodzenia pliku.
Mały eksport jest fixture odbioru protokołu, nie kwalifikacją modelu ML.

Ten przyrost nie eksportuje operacyjnej bazy SQL, nie ustala granicy
snapshot/offset, nie implementuje replay i nie zmienia istniejącego kontraktu
capabilities bounded REST. `replay_handoff` pozostaje `false`. Odbiór wszystkich
trzech modeli i UI E2E wymaga dalszych prac AI10.
