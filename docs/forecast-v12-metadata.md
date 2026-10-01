# AI 05 — katalog modeli i oryginalne oceny v12

API udostępnia wersje użyte w kompletnych, zweryfikowanych publikacjach
prognoz oraz historyczne raporty całej kampanii v12. Każdy odczyt wymaga
Bearer tokenu, `forecast:read` i odpowiedniego zakresu produktów,
lokalizacji oraz kanałów. Nie powstają nowe prognozy ani nowe oceny jakości.

## Odczyt

Dodano pięć ścieżek:

- `GET /api/v1/models/v12`
- `GET /api/v1/models/v12/{model_name}`
- `GET /api/v1/models/v12/{model_name}/versions`
- `GET /api/v1/evaluations/v12`
- `GET /api/v1/evaluations/v12/{evaluation_id}`

Listy przyjmują `product_id`, `selling_location_id`, `channel`, `limit`,
`offset` i `view_sha256`. Lista ocen przyjmuje też `quality_status=passed`
albo `not_ready`: to status całej oryginalnej kampanii. Jej poszczególne
segmenty mogą mieć status `passed`, `failed` lub `not_ready`.
Szczegóły przyjmują wyłącznie filtry zakresu. Nieznane lub powtórzone
parametry są odrzucane. Kolejna strona wymaga SHA widoku z pierwszej strony;
zmiana danych, użytkownika lub zakresu daje 409.

Domyślne backendy wybierają `retailops-demand-forecast-v12`, również przy
`APP_ENV=test`. Osobny namespace `retailops-demand-forecast-v12-mechanics`
wymaga jawnego wstrzyknięcia backendu testowego. Dotychczasowe 20 ścieżek
OpenAPI i wszystkie ich definicje schema zachowano bez zmian.

## Co pokazuje katalog

Wersja jest widoczna tylko wtedy, gdy kompletna publikacja przecina
zweryfikowany zakres użytkownika. Katalog sprawdza cały wynik obliczeń,
receipt, wejście, świeżość źródła, niezmienną rejestrację i zakończenie
decyzji, także poza widocznymi wierszami i pierwszą stroną. Nie udostępnia
adresów artefaktów MLflow, ścieżek źródła ani prywatnych kapsuł dopuszczenia.

Wersja podaje oryginalny run, runy MLflow, cohort/fold/recipe, źródło,
snapshot, SHA kodu i zależności oraz hash i ważność dopuszczenia. Pokazuje
wersję przypiętą do zapisanych prognoz, nawet gdy później zmienił się head.
Zapisany approved release jest pokazywany tylko, gdy jego wersja jest
widoczna w tym zakresie. Aktualne aliasy i wdrożenie nie są obserwowane:
`registry_aliases=null`, `deployed_model_version=null`,
`deployment_status=not_attested`, `drift_status=not_run`.

`evaluation_id` jest stabilną referencją namespace + oryginalny run.
Nie gwarantuje, że raport został już zaimportowany ani że użytkownik ma
dostęp do całego jego zakresu. Katalog jest ograniczony do 1000 wersji,
16 MiB danych przed odczytem JSON i 3 s na zapytanie SQL.

## Ocena wymaga dostępu do całej kampanii

Raport zawiera zbiorcze metryki. Dlatego osoba z dostępem do produktu A
nie otrzyma raportu obejmującego A i B. Zakres jest ustalany ze wszystkich
zachowanych wierszy oryginalnych plików prediction, również wyłączonych
z obliczeń, a nie ze smoke testu inference ani deklaracji użytkownika.
Filtr `product_id=A` nie zmienia raportu A+B w raport produktu A.
SQL stosuje kontrolę całego zakresu przed liczeniem, stronicowaniem
i sprawdzaniem limitu bajtów. Ukryty szczegół daje 404.

Szczegóły zachowują wszystkie oryginalne segmenty, osobne candidate/baseline
i mean/median/interval, wartości `null`, liczby wierszy, kompletność,
diagnostykę, kalibrację i powody braku gotowości lub porażek.
Nie stosujemy nowych progów i nie otwieramy portfolio final testu.
Oryginalny raport jest zapisany wraz z receipt pliku i SHA projekcji.
Przechowywany status jakości nie oznacza dopuszczenia do serving:
`serving_eligible=false`, `registered_model_version=null`, świeżość `unknown`.
Surowa treść błędu wykonania pozostaje prywatna; API pokazuje tylko fakt
jego wystąpienia.

Zapis jest niezmienny i idempotentny. Limit to 256 raportów na środowisko
i namespace, 8 MiB kanonicznego dokumentu, 10000 segmentów, 16 MiB na
odczyt i 3 s na zapytanie SQL. Import strumieniowy ma limit 50 mln
wierszy i 16 KiB na wiersz. Limity chronią zasoby; ich przekroczenie
kończy operację błędem zamiast częściową odpowiedzią.

## Prywatny import po otrzymaniu końcowego eksportu

Import wymaga uwierzytelnionego operatora `promoter` z `model:decide`.
CLI przyjmuje oryginalny pełny eksport i osobno zainstalowany, przypięty
verifier AI 04. Nie przyjmuje gotowej projekcji JSON jako dowodu.

```bash
.venv/bin/python scripts/register_v12_evaluation.py \
  --run-dir /private/path/completed-v12-export \
  --verifier-python /private/path/pinned-ai04-wheel/bin/python \
  --policy-file /private/path/operator-policy.json \
  --credentials-file /private/path/operator-credential.json \
  --env-file /private/path/ai05.env
```

Verifier sprawdza oryginalny eksport i replay przed projekcją. Importer
ponownie sprawdza bajty przed i po odczycie członkostwa, zgodność cohort,
fold/role/kluczy i liczników raportu. Nie wykonuje nowych refitów.
Nie ma publicznego endpointu zapisu oceny ani promocji modelu.
Przed użyciem potrzebna jest migracja `0018_v12_evaluations`.
Trwałego stosu w tym przyroście nie migrowano.

## Odbiór i dalsza praca

```bash
.venv/bin/python -m pytest -q tests/test_v12_metadata.py tests/test_access.py
.venv/bin/python scripts/update_access_contracts.py --check
.venv/bin/python scripts/update_v12_lifecycle_contracts.py --check
.venv/bin/python scripts/check_v12_metadata.py
```

Test jednorazowy obejmuje dotychczasowy registry, kolejkę, publikację,
nowy katalog, zapis ocen i odczyt po restarcie. Używa rzeczywistego
PostgreSQL dla danych AI oraz rzeczywistego MLflow z testowym backendem
SQLite. API jest uruchamiane przez ASGI TestClient. Oryginalny verifier,
źródło, predictor i przegląd są jawnymi małymi doubles. Dwie wymyślone
oceny obejmują dwa produkty; jeden ma wyłączone członkostwo.
Ten test nie odbiera produkcyjnego backendu PostgreSQL dla metadanych
MLflow ani osobno wdrożonego serwera TCP HTTP.

Required CI uruchamia teraz `make v12-backup-smoke`, obejmujący również
ten odbiór i [wspólne odtworzenie v12](forecast-v12-backup.md).
`v12-metadata-smoke` pozostaje samodzielnym testem z backendem SQLite.
[Evidence](evidence/05-v12-metadata.json) opisuje lokalny wynik i granice.
Zdalny wynik Required CI pozostaje nieodebrany. Do zakończenia AI 05
pozostają rzeczywisty końcowy eksport,
kwalifikacja i przegląd modelu, pełny odbiór serving oraz jawna migracja
trwałego środowiska. Aktywnej kampanii AI 04 nie odczytywano ani nie zmieniano.
