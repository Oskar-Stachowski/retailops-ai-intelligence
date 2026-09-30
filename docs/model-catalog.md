# AI 05.7b — katalog modeli i wersji

Katalog udostępnia metadane modeli użytych w opublikowanych prognozach
widocznych dla użytkownika. Wymaga ważnej tożsamości i osobnej capability
`forecast:read`, tak jak [odczyt prognoz](forecast-read.md).
[Odbiór](evidence/05-07-catalog.md) opisuje testy HTTP/PostgreSQL.

## Endpointy i zakres

- `GET /api/v1/models` — lista modeli z liczbą widocznych wersji;
- `GET /api/v1/models/{model_name}` — metadane jednego modelu;
- `GET /api/v1/models/{model_name}/versions` — lista widocznych wersji.

Obecny kontrakt obsługuje `retailops-demand-forecast`. Model istniejący
wyłącznie w Registry, bez publikacji w żądanym środowisku i scope, nie jest
widoczny w tym katalogu. Nie jest to administracyjny spis całego Registry.
Pusta lista modeli zwraca `200/no_data`; brak, nieznana nazwa i niewidoczny
model mają wspólne `404 model-not-found` przy odczycie szczegółu lub wersji.

Wspólne filtry: `product_id`, `selling_location_id`, `channel`. Brak filtra
rozwija scope z grantu serwera. Maksimum żądania to 20 produktów × 5 lokalizacji
× 2 kanały; większy grant trzeba zawęzić. Filtr spoza grantu daje
`422 model-scope-invalid`, zbyt duży scope `422 model-scope-limit`.
Sam `forecast:run`, rola admin lub parametry tożsamości nie nadają dostępu.
Nieznane i powtórzone parametry są odrzucane.

Listy przyjmują `limit` (domyślnie 50, maksimum 200), `offset` (0–1000)
i `view_sha256`. Odpowiedź zawiera `items`,
`pagination:{limit,offset,total,next_offset}`, `generated_at`, `data_status`
i hash widoku. `total` liczy wyłącznie widoczne elementy. Wersje są sortowane
numerycznie: 2 przed 10. Nie ma osobnego filtra statusu; dostępna jest cała
widoczna historia wersji w podanym zakresie.

Pierwsza strona daje hash zależny od tożsamości użytkownika, rozwiniętego
scope, rodzaju listy oraz pełnej projekcji jej metadanych. `offset>0` wymaga
tego hasha (`409 model-view-required`). Zmiana metadanych, zakresu lub
użytkownika daje `409 model-view-changed`; pobierz wtedy pierwszą stronę.
Zmiana samego zegara nie zmienia widoku. Zawężenie filtra do dokładnie tego
samego rozwiniętego zakresu zachowuje jego tożsamość.

## Znaczenie metadanych

Model podaje `visible_version_count` i nullable `approved_release` z trwałego
headu PostgreSQL. Release pojawia się tylko wtedy, gdy jego wersja ma publikację
widoczną w żądanym scope; zawiera ID, numer wersji i digest obrazu.
Null oznacza brak widocznego zatwierdzonego release'u, bez ujawniania numeru
wersji spoza zakresu. Zapis release'u nie dowodzi bieżącej zgodności runtime,
braku oczekującej decyzji lifecycle ani gotowości nowego batchu.

Wersja podaje numeric version, rodzinę i flavor modelu, źródłowy MLflow run ID,
receipts modelu i model card (SHA/bytes), qualification/config SHA, evaluation ID,
feature schema i czas ostatniej publikacji **w widocznym scope**.
Status `approved_release_recorded` oznacza wersję widocznego headu;
`previously_published` oznacza pozostałe widoczne wersje. Nie jest to nowa
kwalifikacja jakości ani stan aliasu `champion` w żywym Registry.

API nie zwraca URI plików, model card, surowej kwalifikacji, gates, wartości
metryk ani pełnego zakresu treningu/ewaluacji. `registry_aliases` i
`deployed_model_version` pozostają null, `deployment_status=not_attested`,
`drift_status=not_run`. Freshness to
`unknown/registry_and_runtime_not_observed`: odczyt nie kontaktuje się z MLflow
ani procesem wdrożonego modelu. Nie nadaje statusu `current` samym headem bazy.

## Kontrole i limity

Odczyt korzysta z `REPEATABLE READ READ ONLY`. SQL filtruje środowisko,
model i scope przed wyborem ostatniej widocznej publikacji każdej wersji,
liczeniem i limitem. Sprawdza manifest, identity i piny zakończonego runu,
niezmienny binding wpisanej wersji oraz ukończenie decyzji enrollment.
Widoczny head musi mieć spójny release i ukończoną decyzję.
Katalog nie odczytuje wartości partycji prognoz; ich pełną integralność
sprawdza osobny endpoint prognoz. Najnowszy pasujący manifest jest dowodem
użycia wersji, nie ponowną oceną wszystkich historycznych outputów.

Budżet to 1000 różnych widocznych wersji; przekroczenie daje
`429 model-read-budget`, bez cichego obcięcia wyników. Powtórne publikacje
tej samej wersji nie zużywają limitu wersji. Binding i release mają limit
64 KiB przy pobraniu z bazy, a zapytania `statement_timeout=3s`.
Rozbieżne/uszkodzone metadane dają bezpieczne `503 model-metadata-invalid`,
błąd bazy 503, brak migracji `503 database-not-ready`.
Odczyt potrzebuje PostgreSQL; nie wykonuje wywołań MLflow ani Bedrock.

## Odbiór i dalszy zakres

```bash
make model-catalog-smoke
python scripts/check_model_catalog.py --report reports/model-catalog.json
```

Kontroler używa własnego jednorazowego projektu Docker bez portów hosta,
zainstalowanego pakietu, prawdziwego HTTP oraz PostgreSQL. Synthetic inputs
i SQL-only stub release'y sprawdzają mechanizm; nie zatwierdzają modelu AI 04.
Kontroler usuwa własne kontenery, wolumeny i tag obrazu, zachowując inne
projekty i współdzielony cache. Test należy do persistence Required CI.

[Historyczne evaluations 05.7c](evaluations.md) mają osobny trwały zakres
i odtworzone metryki. Scope publikacji prognozy nie uprawnia użytkownika
do globalnych metryk oceny tego modelu. [Freshness prognoz 05.7d](forecast-freshness.md)
ma osobny odbiór. Pozostają spójny qualified handoff AI 04, rzeczywisty batch na jego
release'ie oraz zdalny Required CI. **AI 05 pozostaje otwarte.**
