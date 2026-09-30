# AI 05.7b — odbiór katalogu modeli i wersji

Data: **2026-09-30**. Branch `ai/05-mlflow-serving`; zakres pozostaje lokalny.
**Odbiór techniczny HTTP/PostgreSQL przeszedł.**
[Runbook](../model-catalog.md) opisuje semantykę i pozostałe bramki.
Nie kwalifikowano rzeczywistego modelu AI 04.

## HTTP i PostgreSQL

Polecenie: `python scripts/check_model_catalog.py --report reports/ai05-catalog-acceptance.json`.
[Raport](05-07-catalog.json) ma `status=passed`, `real_http=true`, purpose
`sql_fixture_only`, `forecast_quality_approved=false` i zero rzeczywistych
opublikowanych prognoz. Nie było wywołań MLflow ani Bedrock.

Kontroler użył świeżego projektu `retailops_ai_catalog_cc50730f83` bez portów
hosta i zainstalowanego pakietu w obrazie
`sha256:6960f561841c95e1bf57b4ed090ef5df92eef2e0353d1f194a94763b9193ac02`.
Wejścia to synthetic fixture 20 produktów × 1 lokalizacja × 14 dni.
Trzy wersje/release'y 2, 10, 11 i ich gates są **SQL-only stubami**;
nie tworzą rzeczywistych MLflow versions, promocji ani plików modelu.

Powstało 36 zakończonych runów/prób, 36 manifestów i partycji fixture,
2 pointery prognoz, 3 wpisy wersji i 1 head modelu. Podstawowy czytelnik
widzi wersje 2 i 10 jednego produktu; czytelnik pełnego scope widzi też 11.
Ostatnie 33 publikacje powtarzają wersję 11 dla produktu spoza scope
podstawowego czytelnika.

HTTP/SQL potwierdziły:

- 401 bez credentials, 403 dla `forecast:run` bez `forecast:read`, zamknięte filtry i 422 poza scope;
- numeric sort 2 przed 10, scoped `total`, wspólne 404 dla niewidocznego i nieznanego modelu;
- stabilne strony z hashem, 409 bez hasha kontynuacji i po zmianie widoku;
- ukrycie wersji 10 i jej headu przed pierwszą publikacją;
- brak numeru headu 11 i jego release'u w scope użytkownika widzącego tylko 2/10;
- widoczny head 11 wyłącznie dla czytelnika obejmującego jego publikację;
- brak surowych gates/URI/metadanych spoza scope, aliases/runtime null i freshness unknown;
- liczenie różnych wersji po filtrze SQL mimo 33 powtórnych publikacji;
- odrzucenie rozbieżnego bindingu enrollment i metadanych >64 KiB przez bezpieczne 503;
- identyczny katalog, wersje, registry i cały stan publikacji po SIGKILL/restart PostgreSQL.

Kontrolowana korupcja dotyczyła własnej jednorazowej bazy: właściciel tabeli
wyłączył user triggers, zmienił binding wersji 2 i włączył triggers w jednej
transakcji. `finally` odtworzył dokładny oryginał. Hash całego stanu i HTTP
po odtworzeniu były identyczne z pomiarem przed uszkodzeniem. Normalne odczyty
nie zmieniają tabel ani triggers. Kontroler usunął własne kontenery, wolumeny
i tag obrazu, zachowując pozostałe zasoby.

Pełny hash stanu po odbiorze i restarcie:
`c4f1bb5be569fb6ca52fa83f34daa2ffa669ba4cb29ea4ecfb8dc65860dd631c`.
Hash listy wersji podstawowego czytelnika:
`4cd9256b3c9bdea4b66a60d52e07155f04d102763a38589fcb514135ef06879f`.

## Kontrole lokalne

144 testy katalogu, forecast read, access, Required CI i cleanup przeszły.
Sprawdzają także zmianę użytkownika/scope, stabilność hasha przy zmianie
zegara, brak backendu, bezpieczne błędy i odrzucenie nieznanych pól.
Osobna regresja publication/queue/runtime/inputs/HTTP/data contracts/Registry/
lifecycle/MLflow obejmuje 300 testów. Łącznie przeszły **444 różne testy**,
bez powtarzania treningu ani ewaluacji AI 04.
Końcowe 22 testy katalogu i snapshotu access zostały ponowione po uzupełnieniu
OpenAPI o 429 szczegółu modelu; dodatkowy odbiór HTTP potwierdził ukrycie
jeszcze nieopublikowanej wersji.
Ruff/format, mypy (214 plików), snapshots kontraktów, linki docs, guard CI,
gitleaks oraz budowa wheel/sdist przeszły.
Katalog należy do persistence Required CI; test blokuje pominięcie jego
bramki. Testy cleanupu chronią istniejące obrazy i usuwają własny tag po błędzie.

## Pozostałe bramki

Katalog sprawdza manifest/run i enrollment/release pins; nie odczytuje
wartości partycji prognoz ani wszystkich dawnych manifestów wersji.
Nie potwierdza żywych aliasów, runtime, driftu ani nowej jakości modelu.
[Historyczne evaluations 05.7c](../evaluations.md) mają trwały zakres
i odtworzone metryki; scope publikacji prognozy nie uprawnia do globalnych ocen.
Pozostają source watermark freshness, spójny qualified handoff AI 04,
batch na jego rzeczywistym release'ie oraz zdalny Required CI brancha.
**AI 05 pozostaje otwarte.** Outbox i zdarzenia należą do AI 10.
