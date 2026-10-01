# AI 05.7d — watermark i świeżość prognoz

`GET /api/v1/forecasts` używa polityki `forecast-read-v2` i envelope `1.1`.
Świeżość opisuje aktualność **danych konkretnej prognozy**, nie jakość modelu,
stan wdrożenia lub dostarczenie zdarzenia. `generated_at` nie zastępuje origin,
watermarku ani dostępności obserwacji w cutoff.

## Warunki statusu

Dzienny task zamyka origin o 23:59:59 UTC. Wersjonowane progi:

- wiek origin: maksymalnie **86400 sekund**, granica jest włączona;
- opóźnienie watermarku względem origin: **0 sekund**;
- opóźnienie ostatniej kompletnej obserwacji względem dnia origin: **1 dzień**.

Agregat dobowy jest dostępny po zamknięciu dnia, a origin kończy się sekundę
przed północą. Obserwacja poprzedniego dnia mieści się zatem w polityce;
nie wypełnia to brakującej obserwacji w origin ani nie przesuwa cutoff.

`current/within_policy` wymaga wszystkich warunków i braku nowszej
nieopublikowanej próby w tym samym product/location/channel/horizon.
Potwierdzone zero i potwierdzony dzień zamknięty są kompletnymi obserwacjami.
Brakujący lub opóźniony rekord nie staje się zerem.

Kolejność przyczyn:

1. Nowsza nieopublikowana próba: `stale/newer_run_unpublished`.
2. Starszy origin: `stale/origin_age_exceeded`.
3. Brak deklaracji, nieobsługiwana polityka lub `not_ready` źródła: `unknown`.
4. Deklaracja nie obejmuje origin: `stale/source_watermark_lag_exceeded`.
5. Brak kompletnej obserwacji w historii: `unknown/source_observation_unavailable`;
   obserwacja starsza niż poprzedni dzień: `stale/source_observation_lag_exceeded`.
6. Wszystkie warunki spełnione: `current/within_policy`.

Poprzedni kompletny output pozostaje odczytywalny po awarii nowej próby.
Zegar i ocena świeżości nie zmieniają prediction IDs ani hasha stron.
Zmiana polityki z v1 na v2 wymaga pobrania nowej pierwszej strony.

## Źródło dowodu i cutoff

Przygotowanie wejścia sprawdza cały curated i features przed odczytem oraz
ponownie po odczycie. Nowe `PreparedInputs` mają format **1.1** i
`source_freshness`. Dowód zawiera dokładny descriptor curated, zadeklarowany
watermark `daily_demand_observations` oraz osobny wykaz ostatnich kompletnych
obserwacji **dostępnych w origin**. Hash descriptora i curated ID muszą zgadzać
się z rodzicem przypiętego feature manifestu. Wykaz obserwacji jest sprawdzany
przeciw typed histories, których availability nie może przekroczyć cutoff.

Obsługiwana deklaracja to `daily-demand-1.0.0` z semantyką
`synthetic_sales_day_close_without_return_guarantee`. Potwierdza dzienną
obserwowaną sprzedaż; nie gwarantuje inventory lub dojrzałości zwrotów.
Inna polityka nie daje `current`. Curated 1.1 bez osobnej deklaracji tego
strumienia daje `unknown`; zakres projection inventory nie jest jej zamiennikiem.

Deklaracja całego archiwum może obejmować dni późniejsze niż historyczny origin.
Efektywny `source_watermark` wynosi wtedy minimum z końca zadeklarowanego dnia
i origin. Nigdy nie przenosi granicy wiedzy do przyszłych obserwacji.
Odrębne `source_watermark_as_of` zachowuje czas deklaracji; API sprawdza również
obserwację rzeczywiście dostępną w cutoff. Deklaracja z przyszłości względem
rejestracji/publikacji jest odrzucana.

Odpowiedź ujawnia wyłącznie daty/metryki świeżości autoryzowanego itemu:
watermark, czas deklaracji, jej status/politykę, wiek i lag oraz ostatni kompletny
dzień obserwacji. Nie zwraca descriptora curated, pełnego scope ani URI.

## Publikacja, zgodność i odporność

Worker kopiuje sprawdzony dowód do content-addressed output manifestu **1.1**,
z obserwacjami ograniczonymi do scope wykonania. Zapis jest częścią tej samej
transakcji co partycje, ukończony run, historia i pointer. SQL trigger porównuje
cały dowód z oryginalnym, niezmiennym profilem rejestracji. Rehash błędnego
dowodu nie omija tej kontroli.

Reader porównuje dowód także ze zarejestrowanym profilem, w tej samej
`REPEATABLE READ READ ONLY` transakcji co outputy. Limit zapytania SQL pozostaje
3 s/32 outputy; pobierane metadata profilu mają limit 512 KiB. Descriptor
ma maksimum 192 KiB canonical JSON, a manifest nadal maksimum 256 KiB.
Uszkodzony dowód daje `503 forecast-output-invalid`, bez częściowego wyniku.

Format **1.0** pozostaje czytelny, z tymi samymi canonical IDs i bez dopisywania
watermarku do archiwum. Brak dowodu daje `unknown`, chyba że znana przyczyna
nieaktualności wymaga `stale`. Nie można oznaczyć nowego dowodu jako 1.0 ani
opublikować 1.0 dla profilu 1.1. Schematy JSON opisują tę zależność.

Migracja freshness: **`0014_forecast_freshness`**, przed uruchomieniem nowego API
i workera zgodnie z [runbookiem Compose](local-stack.md). Migracja rozszerza
constraints i dodaje trigger; nie przepisuje istniejących danych ani identity.
Readiness/DB guard wymagają obecnie head **`0017_v12_outputs`** opisanego w
[kolejce v12](forecast-v12-worker.md). Trwałego stosu nie migrowano w tym
odbiorze; użyto własnego jednorazowego projektu.

## Odbiór i pozostałe bramki

```bash
make forecast-read-smoke
python scripts/check_forecast_read.py --report reports/forecast-read.json
```

Rozszerzony smoke należy do istniejącej bramki persistence Required CI.
Obejmuje mieszany odczyt starych i nowych manifestów, rzeczywisty HTTP/SQL,
`current/stale/unknown`, odrzucenie przyszłej deklaracji, atomowy rollback
przy rehashed niezgodnym dowodzie, uszkodzenie/restore oraz SIGKILL/restart.
Fixtures i ich stub gates są jawnie syntetyczne; nie kwalifikują modelu AI 04.

**AI 05 pozostaje otwarte:** potrzebny jest spójny qualified handoff AI 04,
odbiór rzeczywistego batchu na jego release'ie oraz zdalny Required CI brancha.
Zdarzenia/outbox należą do AI 10. Nowy proces źródłowy z AI 06 wymaga własnej
deklaracji watermarku i ponownej kwalifikacji modelu przed statusem `current`.
