# AI 05.7a — odczyt opublikowanych prognoz

`GET /api/v1/forecasts` czyta kompletne wyniki z PostgreSQL. Wymaga ważnej
tożsamości i osobnej capability `forecast:read`; `pipeline` z samym
`forecast:run` oraz administrator bez tej capability nie otrzymują danych.
[Publikacja](forecast-publication.md) odpowiada za atomowy zapis,
[odbiór odczytu](evidence/05-07-read.md) opisuje lokalną weryfikację.

## Zapytanie i odpowiedź

Filtry: `product_id`, `selling_location_id`, `channel`, `target_from`,
`target_to`, `as_of`, `inference_run_id`, `limit`, `offset`, `view_sha256`.
Nazwy lokalizacji zachowują wspólny kontrakt danych tego repo.
Brak filtrów rozwija wyłącznie scope z polityki serwera. Żądanie może objąć
maksymalnie 20 produktów × 5 lokalizacji × 2 kanały; większy grant wymaga
zawężenia filtrów. Żądany produkt, lokalizacja lub kanał spoza grantu daje
`422 forecast-scope-invalid`. Nagłówki ról i parametry tożsamości nie nadają
uprawnień. Nieznane i powtórzone parametry są odrzucane.

`limit` domyślnie wynosi 50, maksimum 200. `offset` mieści się w 0–2800.
Obie granice dat muszą wystąpić razem, w prawidłowej kolejności, ze spanem
do 31 dni. `as_of` to origin zamykający dzień o 23:59:59 UTC.
Odpowiedź ma `items`, `pagination:{limit,offset,total,next_offset}`,
`generated_at`, `data_status`, `selection`, `view_sha256` i wersjonowaną
`freshness_policy`. Pusta lista ma `200/no_data`; nie udaje błędu zależności.
Brak lub niewidoczny przypięty run daje ten sam `404 forecast-output-not-found`.
Pusta strona poza końcem dostępnej listy zachowuje jej prawdziwy `total`.

Bez origin/run wybierany jest najnowszy kompletny wynik dla produktu,
lokalizacji, kanału i dziennego horyzontu 1–14. Kolejność wyboru:
origin, czas żądania, run ID. Uzupełniające wyniki z mniejszego scope/okna
nie usuwają pozostałych prognoz. Starszy origin nie wygrywa przez późniejszy
czas publikacji. Filtr dat działa **po wyborze**; nie przywraca starszego
wyniku, gdy najnowszy target nie mieści się w oknie.
`as_of` ogranicza wybór do konkretnego origin, `inference_run_id` do
konkretnego niezmiennego wyniku. Pole `selection` ujawnia tę semantykę.

Sortowanie stron: produkt, lokalizacja, kanał, target date, origin, prediction ID.
Pierwsza strona zwraca `view_sha256`, obliczony z tożsamości użytkownika,
scope, filtrów i wszystkich wybranych prediction IDs. Kolejne strony wymagają
tego samego hasha. Brak hasha daje `409 forecast-view-required`, zmiana
wyniku lub filtrów `409 forecast-view-changed`. Wtedy pobierz ponownie
pierwszą stronę albo przypnij `inference_run_id`. Zmiana zegara/freshness nie
zmienia tożsamości predykcji ani widoku; każda strona ma swój czas oceny.

Item zawiera klucz prognozy, observed sales units, model name/numeric version,
qualification/model/config checksums, evaluation ID, feature schema, image,
release/source/curated/features IDs, inference run,
original/execution profile IDs, czas publikacji, freshness i `quality_status`.
Prediction ID jest projekcją `forecast-read-v1` nad artefaktem i kluczem;
to osobny kontrakt od zamkniętego `PredictionRecord`, którego pełnego
`ModelRecord` nie odtwarzamy z brakujących danych. API nie ujawnia URI MLflow,
plików modelu, pełnego scope manifestu ani surowych raportów kwalifikacji.

Przedziały nie są zapisane w obecnym outputcie, więc `prediction_interval=null`
ma powód `not_published`. API nie wylicza zastępczego przedziału. `quality_status`
odzwierciedla gates przypiętej kwalifikacji podczas publikacji; nie oznacza
nowej ewaluacji modelu ani monitoringu AI 13.

## Świeżość i zależności

Polityka `forecast-read-v1` ma budżet wieku origin **86400 s**.
Starszy origin daje `stale/origin_age_exceeded`, nawet gdy replay opublikowano
przed chwilą. Nowszy nieopublikowany run dla tego samego produktu, lokalizacji,
kanału i horyzontu daje `stale/newer_run_unpublished`; dotyczy także nowej
nieudanej próby tego samego origin. Poprzedni kompletny wynik nadal jest
widoczny. Udany wynik o nowszym porządku nie jest obniżany przez starszą awarię.

**Manifest 05.6 nie utrwala jeszcze osobnego source watermark.** API nie
zastępuje go `generated_at` ani datą ostatniej sprzedaży. Jeśli nie ma znanego
powodu nieaktualności, freshness ma `unknown/source_watermark_unavailable`
oraz `source_watermark=null`. Ten zakres nie nadaje statusu `current`.
Do pełnego odbioru świeżości potrzebny jest wersjonowany watermark kompletności
źródła w handoff/publikacji i jego próg dla danej polityki.

Odczyt działa w transakcji `REPEATABLE READ READ ONLY`. Najpierw SQL ogranicza
środowisko i scope, potem aplikacja weryfikuje całe manifesty, receipts,
identity/count/grain/domain wszystkich partycji oraz piny udanych runów.
Sprawdza też wiersze poza żądaną stroną i widocznym podzbiorem. Uszkodzony
pasujący artefakt daje `503 forecast-output-invalid`, bez częściowej odpowiedzi.
Błąd bazy daje bezpieczne 503; brak wymaganej migracji `503 database-not-ready`.

Maksimum to **32 pasujące outputy** na odczyt. Przekroczenie daje
`429 forecast-read-budget`; zawęź origin lub przypnij run. API nie wybiera
po cichu pierwszych 32, co mogłoby zgubić najnowszą prognozę części scope.
Partycje odczytywane są jednym zapytaniem, bez osobnego zapytania na predykcję.
Zapytania odczytu mają `statement_timeout=3s`; rozmiary artefaktów ogranicza
kontrakt publikacji. Historia całkowicie spoza scope nie zużywa tego limitu.

Read API i jego `/ready` potrzebują PostgreSQL z właściwą migracją i pgvector.
Nie potrzebują Bedrock ani połączenia z MLflow przy odczycie. Nie zmieniają
aliasów, release’u, aktywacji indeksu ani wyników modelu.

## Weryfikacja i pozostała praca

```bash
make forecast-read-smoke
python scripts/check_forecast_read.py --report reports/forecast-read.json
```

Kontroler tworzy własny projekt Docker bez portów hosta, z zainstalowanym
pakietem, rzeczywistym HTTP i PostgreSQL. Synthetic inputs i SQL-only stub
release’y sprawdzają odczyt; nie kwalifikują modelu AI 04. SIGKILL/restart
zachowuje pełny stan i identyczny widok. Kontroler usuwa własne kontenery,
wolumeny i tag obrazu, pozostawiając współdzielony cache oraz inne projekty.

Do wykonania w AI 05: pełny watermark
freshness, handoff i batch na rzeczywistym qualified release AI 04 oraz
zdalny Required CI. Outbox/zdarzenia należą do AI 10.

Katalog modeli i wersji ma osobny zakres [AI 05.7b](model-catalog.md).
