# AI 10 — typowany klient chronionych odczytów źródła

Status: drugi przyrost; cały AI 10 pozostaje `in_progress`.
`SourceClient` czyta `/integration/v2/*` przez osobne credentials usługi.
Nie używa bezpośredniego połączenia AI z bazą RetailOps ani demo usera.

## Uruchomienie krok po kroku

1. Uruchom własne RetailOps według `docs/runbooks/source-reads-v2.md`.
   Ustaw prywatny grant produktów, kanałów, magazynów i zasobów; nie zmieniaj
   runtime AI 07/08. Legacy endpointy pozostają interfejsem demo.
2. Sprawdź `src/retailops_ai/source_rest/upstream.json`: repo, pełny commit
   kontraktu, ścieżkę i SHA-256 OpenAPI. `contract_source_commit` oznacza
   właściciela kontraktu, nie commit działającego serwera. Każdy odczyt
   sprawdza digest kontraktu i nagłówek `bounded-live`.
3. Przygotuj poza Git prywatny config JSON, zwykły plik właściciela `0600`:

   ```json
   {"base_url":"https://ZATWIERDZONY_RETAILOPS","credential":"PRYWATNY_TOKEN_USLUGI"}
   ```

   Origin nie zawiera ścieżki/query/userinfo. HTTP wymaga jawnego
   `allow_http_loopback=true` i jest dozwolone tylko na loopback.
   Połączenie sieciowe wymaga HTTPS. Token nie trafia do argv ani env workera.
4. Przygotuj prywatny query JSON `0600`, np.:

   ```json
   {
     "product_id":"UUID_PRZYZNANEGO_PRODUKTU","channel":"store",
     "sold_from":"2026-09-01T00:00:00Z","sold_to":"2026-09-30T23:59:59Z",
     "limit":50,"offset":0,"sort_by":"sold_at","sort_order":"asc"
   }
   ```

   Query i response modele są generowane z rzeczywistego OpenAPI. Unknown
   query fields, np. `store_id`, są odrzucane. Inventory wymaga warehouse
   i okresu; forecasts obu dat; products i risks jednego produktu.
   Default limit 50, max 100, okres do 90 dni, offset do 10000.
5. Odczytaj jedną stronę do nowego prywatnego pliku:

   ```sh
   make source-rest-read ARGS='--config /private/path/source.json --query /private/path/query.json --resource sales --output /private/path/new-page.json'
   ```

   CLI nie nadpisuje plików. Payload i metadata zapisuje w `0600`; drukuje
   tylko status lub bezpieczne code/retryable. Config/query mają do 64 KiB.

## Odporność i znaczenie danych

Tylko GET, max 3 próby, 5 s/próbę i 15 s całej operacji. Worker jest osobnym
procesem kończonym po deadline, również przy DNS lub wolnym body. Retry
obejmuje transport i 429/500/502/503/504. Auth/query/schema errors nie są
retry. Redirecty i proxy z env są wyłączone. Retry zachowuje trace/correlation.
Circuit breaker po 3 nieudanych operacjach blokuje żądania na 30 s; udany GET
resetuje go. Współbieżne użycie tego samego klienta daje `client_busy`.

Body ma max 1 MiB. Klient odrzuca non-finite JSON, brak wymaganych pól,
sprzeczną paginację, duplicate IDs i foreign product/channel/warehouse.
Dodatkowe opcjonalne pola odpowiedzi zachowuje. Zmiana digestu wymaga repinu.
`sales_pages` ma default 3 strony/300 wierszy, max 5/500 i jeden deadline 15 s.
Zmienione total lub nakładanie IDs daje `source_changed`; przekroczony budżet
nie zwraca częściowych danych jako kompletnego wyniku.

To **bounded live reads**, nie immutable snapshot. Każda strona upstream
ma osobną read-only repeatable-read transakcję; między stronami mogą wystąpić
insert/update. Wykrycie części zmian nie dowodzi spójności całego eksportu.
Metadata oddziela `fetched_at` od `business_as_of`. Świeży timestamp bez
watermarku daje `unknown`, stary `stale`, pusta strona `missing`; nie nadajemy
`current` na podstawie sukcesu HTTP. To stan strony, nie kwalifikacja ML.
Każdy wynik zawiera `source_resource`, `semantics` i rzeczywisty próg
`max_business_age_seconds`; legacy forecast/risk są jawnie oznaczone.

Sales nadal nie ma sklepu, order ID, availability/record version. Warehouse
nie jest zmapowany do selling location. `require_full_sales_grain()` daje
`unsupported_grain`, `snapshot()` — `snapshot_unsupported`. Nie wymyślamy
offsetów brokera. Historyczny snapshot nadal przechodzi przez AI 03.
Legacy forecast to ilość produktu w okresie, a risk heurystyka; nie zastępują
forecast v2 i probability AI 08. Zachowujemy jednostki, nullability i pola.

## Regeneracja i dowody

```sh
python scripts/update_source_rest_contract.py --source-repo /path/to/retailops --source-commit PELNY_COMMIT
python scripts/update_source_rest_contract.py --check
make source-rest-check
```

Generator czyta konkretny artefakt Git; nie kopiuje generatora lub `ml/`.
`contracts-check` i CI wykrywają drift. Główne `uv.lock` i `pyproject.toml`
pozostają bez zmian; transport używa stdlib i istniejącego Pydantic.
Testy localhost są jawnymi fixture. Osobny cross-repo test rzeczywistego
HTTP/API/PostgreSQL wymaga przypiętego klienta i własnych testowych rekordów.

Rollback: zatrzymaj własnego klienta i usuń jego grant z prywatnej policy,
restartując wyłącznie własne API. Nie ma migracji ani cleanup wspólnej bazy.
Pozostają pełny grain/export, snapshot/log handoff, korekty, modele AI 07/08,
approved-head, UI i pełne temporalne E2E trzech modeli.
