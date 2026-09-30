# AI 05.4a — trwała kolejka i oddzielny worker

Ten zakres przygotowuje przyjmowanie zadań, leasing, retry i recovery,
niezależnie od kwalifikacji modelu AI 04. [Odbiór](evidence/05-04-queue.md)
używa wyłącznie jawnych fixture `lifecycle_mechanics_only` w `APP_ENV=test`.
[Pakiet wejścia i loader 05.5a](forecast-runtime.md) są przygotowane osobno.
[Integracja i atomowa publikacja 05.6](forecast-publication.md) mają oddzielny
odbiór techniczny PostgreSQL; [read API prognoz](forecast-read.md) ma zakres 05.7a.
Etap 05 nie jest jeszcze zamknięty.

## Przyjęcie i odczyt

`POST /api/v1/forecast-runs` wymaga zweryfikowanych poświadczeń,
roli `pipeline`, osobnego `forecast:run` i zakresu produktów, lokalizacji
oraz kanałów. Viewer, operator, admin i promoter nie dziedziczą tej
możliwości. Klient nie ustala użytkownika, roli, wersji modelu, URL ani
ścieżki do artefaktu. [OpenAPI](../contracts/access/v1/access.openapi.json)
i [schematy](../contracts/forecast_jobs/v1/request.schema.json) są
sprawdzane w CI; dotychczasowy kontrakt intelligence/run v1 pozostaje zamknięty.

Przykład kształtu żądania — ID wskazuje uprzednio zarejestrowany profil,
nie dowolny plik klienta:

```json
{
  "schema_version": "1.0",
  "profile_id": "batch-profile-sha256-<64 znaki hex>",
  "as_of": "2026-09-29T23:59:59Z",
  "horizons_days": [7, 14],
  "product_ids": ["p-101"],
  "selling_location_ids": ["s-03"],
  "channel": "store",
  "model_alias": "champion"
}
```

Wymagany `Idempotency-Key` ma 1–128 znaków liter/cyfr/`_.-`, pierwszy znak
jest literą lub cyfrą. Przyjęcie zwraca `202` i względny `Location`.
Hash klucza, hash żądania i run powstają w jednej transakcji. Klucze są
oddzielne dla środowiska, konta i tego endpointu. Powtórzenie zwraca bieżący
stan tego samego runu; zmiana treści z tym kluczem daje `409`. Kolejność
filtrów nie zmienia identity żądania. Puste listy rozwijają się do całego
jawnie przyznanego scope, bez poszerzania uprawnień. Limit to 20 produktów,
5 lokalizacji, jeden kanał i okna 7/14, z dziennymi kluczami 1–max(horyzont).
Mechanics fixture ma dodatkowo najwyżej 64 wiersze i wymaga pełnego coverage.

`GET /api/v1/forecast-runs/{run_id}` oraz `/attempts` sprawdzają cały
przypięty scope. `forecast:read` pozwala czytać runy w swoim zakresie;
`forecast:run` pozwala czytać własne runy. Obcy zakres zwraca `404`, również
przy powtórzeniu klucza. Historia zawiera wyłącznie zamknięte próby, maksymalnie
5; publiczny record nie zawiera lease tokenu, connection stringów ani klucza.
Brak modelu lub niedokończona decyzja lifecycle daje `409`, brak przygotowanego
wejścia `422`, pełna kolejka `429`, niedostępna/niezgodna baza `503`.

W `APP_ENV=local` kolejka przyjmuje [zarejestrowane rzeczywiste wejścia](forecast-input-store.md)
po zatwierdzeniu spójnego release’u; pełne warunki i atomowy zapis opisuje
[AI 05.6](forecast-publication.md). Mechanics zachowują oddzielny namespace
i nie mogą stać się rzeczywistą prognozą.

## Piny i przejścia

Przyjęcie odczytuje zatwierdzony release z niezależnego audytu PostgreSQL,
nie rozwiązuje ponownie mutable aliasu MLflow. Run przypina numeryczną
wersję, run/source URI modelu, qualification/checksumy, image digest,
profil, source/curated/features IDs, origin, scope i politykę wykonania.
Retry zachowuje wszystkie te wartości. Model wybrany w AI 04 może być
baseline’em; niniejszy odbiór nie zatwierdza jakości żadnego modelu AI 04.

Migracja `0010_forecast_queue` przechowuje kolejkę, niezmienne profile,
historię prób i kompletne receipts mechaniki. Triggery blokują zmianę pinów,
nielegalne przejścia oraz update/delete historii. Udany run wymaga
uprzednio zapisanego kompletnego outputu w tej samej transakcji. Częściowy
wynik nie jest publikowany. Receipts testowe mają jawne
`forecast_quality_approved=false` i nie trafiają do read API prognoz.

Worker atomowo przejmuje run przez blokadę i nowy losowy lease token.
Heartbeat, zamknięcie oraz anulowanie sprawdzają/zmieniają stan pod blokadą
wiersza. Czas pochodzi z bazy po uzyskaniu blokady. Wygasła próba zapisuje
`failed` w historii; następna próba dostaje nowy token i większy numer.
Stary worker nie może przedłużyć lease, zapisać wyniku ani zamknąć nowej próby.
Recovery następuje podczas kolejnego wywołania workera, bez background task
w pamięci API. Kolejka bez uruchamianego workera sama nie wykonuje zadań.

Domyślna polityka przypięta przy przyjęciu: 3 próby, lease 30 s,
heartbeat 5 s, próba 120 s, cały run 600 s i backoff 5 s; maksymalnie
100 aktywnych runów oraz 20 na konto. [Schemat polityki](../contracts/forecast_jobs/v1/policy.schema.json)
ogranicza retry do 5, czas runu do 900 s i wymaga heartbeat < połowa lease
oraz lease ≤ timeout próby ≤ timeout runu. Konfiguracja przyszłego
workera nie zmienia polityki już przyjętego runu. Wygaśnięcie czasu
oczekiwania anuluje run; wyczerpanie prób kończy go `failed` bez outputu.

## Polecenia prywatnego workera i odbioru

Wykonane polecenie odbioru, bez publikacji portów hosta:

```bash
.venv/bin/python scripts/check_forecast_queue.py
# Równoważna bramka Required CI:
make forecast-queue-smoke
```

Kontroler tworzy świeży projekt Compose z UUID, buduje pakiet w obrazie,
migruje bazy i wykonuje HTTP oraz osobne procesy workera. Po SIGKILL i
restarcie porównuje hash pełnej treści runów, profili, historii i outputów.
Usuwa wyłącznie własny projekt i wolumeny; nie zatrzymuje stosów innych sesji.

W prywatnym kontenerze skonfigurowanym przez Compose wykonuje:

```bash
python -m retailops_ai.forecast_jobs.worker --once --mechanics
```

Ta komenda jest włączona tylko z `APP_ENV=test`. Obsługuje jedną próbę;
`idle` oznacza brak gotowego zadania. Obliczenia działają w osobnym procesie
bez odziedziczonych poświadczeń bazy. Proces używa zaufanego, numerycznego
bindingu MLflow i sprawdza capsule, checksumy, signature i load smoke.
Mechanics child ma limit 20 s CPU, 512 MiB przestrzeni adresowej,
limit wall time timeout próby + 5 s oraz 256 KiB JSON na wejściu/wyjściu.
Supervisor odnawia lease; po anulowaniu, utracie lease lub awarii bazy
kończy własny proces obliczeń. Nie zapisuje sukcesu bez potwierdzenia lease.
Po SIGKILL supervisora obliczenia mogą dobiec do własnego limitu czasu,
ale nie mają dostępu do kolejki i nie mogą publikować wyniku.

Anulowanie jest prywatną operacją z dostępem do bazy, bez publicznego HTTP:

```bash
python -m retailops_ai.forecast_jobs.worker --cancel run-<32 znaki hex> --mechanics
```

Anulowanie jest terminalne i unieważnia lease. Do zmiany migracji użyj
procedury [backup/restore](lifecycle-backup.md); downgrade tej migracji
nie usuwa zapisanych runów. Backup schematu `ai` obejmuje także nowe tabele.

## Pozostały zakres

[Rejestr i supervisor 05.5b](forecast-input-store.md) oraz
[integracja/publikacja 05.6](forecast-publication.md) są zaimplementowane.
Oba zakresy mają odbiór PostgreSQL na jawnych fixture.
Pozostaje odbiór na spójnym, zakwalifikowanym release’ie AI 04,
pełna freshness z source watermark w AI 05.7
oraz zdalny Required CI. Zdarzenia i outbox/projekcje należą do AI 10.
