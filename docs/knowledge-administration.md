# Administracja indeksami wiedzy

API przyjmuje trwałe runy indeksowania i odczytuje bieżący indeks. Worker tworzy
niemodyfikowalnego kandydata oraz raport. Zakończenie runa nie przełącza wskaźnika;
[kwalifikacja i aktywacja](knowledge-lifecycle.md) mają oddzielne bramki.

## Zakres obecnego odbioru

Obsługiwane profile mają `environment=test`, provider `fake` i politykę
`offline-index-mechanics-v1`. Jest to odbiór budowy, persistence i uprawnień.
Nie potwierdza jakości semantycznej ani zatwierdzenia rzeczywistego korpusu.
[Odbiór](evidence/11-administration.md) obejmuje 542 testy oraz rzeczywisty
HTTP/PostgreSQL z awariami workera i retencją po restartach.
Profil zawiera jawny `CorpusApproval` dla syntetycznych źródeł; samo uruchomienie
API lub workera nie tworzy zgody. W bazie lokalnego korpusu nie instalujemy
takiego profilu. Rejestr i golden labels pozostają propozycją.

Użytkowe runy z golden evaluation wymagają kolejnego rozszerzenia profilu,
zatwierdzenia źródeł/etykiet i odbioru jakości. Fake report nie zastępuje tej bramki.
Real provider i agent należą do etapu 12.
[Kontrola kwalifikacji](knowledge-qualification.md) przygotowuje związany manifest
i sprawdza jawne decyzje; nie rozszerza jeszcze profilu ani nie nadaje aktywacji.

## Uprawnienia i HTTP

Każdy endpoint wymaga zweryfikowanego Bearer tokena, roli `admin` oraz jawnego
grantu `knowledge:index`. `knowledge:read`, `forecast:read` i `access:admin`
nie nadają tego dostępu. Zwykły operator/agent nie może dostać grantu indeksowania.
Zmiana prywatnej polityki wymaga restartu API, jak w [instrukcji dostępu](access-control.md).

| Endpoint | Wynik |
| --- | --- |
| `POST /api/v1/knowledge-index-runs` | `202`, wspólny kontrakt Run z `run_type=knowledge_index`, nagłówek `Location` |
| `GET /api/v1/knowledge-index-runs/{run_id}` | Trwały stan runa; `404 index-run-not-found`, gdy brak runa w środowisku |
| `GET /api/v1/knowledge-indexes/current` | Metadane jednego przypiętego indeksu; `404 index-not-configured`, gdy brak aktywnego wskaźnika |

POST wymaga `Idempotency-Key`: 1–128 znaków, pierwszy alfanumeryczny, pozostałe
alfanumeryczne lub `_.-`. Body zawiera wyłącznie `corpus_config_id`, `sources`,
`index_config_id`, `evaluation_set_id`. Źródła to oba repozytoria z pełnymi SHA
istniejących commitów, zgodnymi z zatwierdzonym snapshotem. Kolejność źródeł
nie zmienia hash requestu. API odrzuca dodatkowe ścieżki, URL, modele, wymiary,
prompty i tożsamości przesłane w body.

Idempotencja jest ograniczona przez środowisko i principal. Ten sam klucz i
semantycznie ten sam request zwracają ten sam Run, również po zakończeniu;
zmieniony request daje `409 idempotency-conflict`. Nieznana/niezatwierdzona
konfiguracja daje `422 configuration-not-approved`. Walidacja i autoryzacja
odbywają się przed zapisem. Maksymalnie 100 runów `queued/running` w środowisku
ogranicza kolejkę; dalsze nowe zgłoszenia otrzymują `429 queue-full`.
Problemy mają statyczny opis i bezpieczny kod, bez wartości requestu/DSN/tokenów.

GET current zwraca index ID, referencje manifestu i raportu, corpus/chunk/config
IDs, wymiar, liczby dokumentów/fragmentów i czas aktywacji. W środowisku testowym
jawnie wskazuje `lane=offline_test` i `purpose=lifecycle_validation_only`.
Odczyt wskaźnika przypina jedną wersję; dodatkowe metadane czyta z jej
niemodyfikowalnego zdarzenia. Nie ma endpointu aktywacji ani narzędzia agenta
do indeksowania/promocji.

## Zatwierdzony snapshot i worker

[Schema profilu](../contracts/knowledge/v1/index-build-profile.v1.schema.json)
wiąże zgodę, cały manifest fragmentów, konfigurację embeddingów i politykę
odbioru. Snapshot ma hash całej treści oraz konfiguracji. Referencje tekstu
pozostają `untrusted_reference`.

Kontrolowane polecenie `retailops-ai knowledge-profile-register` wymaga
`--profile`, `--retailops-repo`, `--ai-repo` i opcjonalnego `--env-file`.
Przed zapisem ponownie odtwarza fragmenty z przypiętych Git commits, porównuje
pełny snapshot i sprawdza środowisko. Nie pobiera źródeł ani nie zatwierdza
ich automatycznie. Zmiana źródeł, statusów, dostępu lub konfiguracji wymaga
nowego zatwierdzonego profilu. HTTP nie przyjmuje plików profilu.

`retailops-ai knowledge-index-work --run-id … --env-file …` wykonuje jeden
wskazany run. Nie ma jeszcze automatycznego planowania workerów. Stany to
`queued → running → succeeded/failed`, z możliwością kontrolowanego anulowania
przez `knowledge-index-cancel --run-id …`. Sukces ma wyłącznie `candidate`:
`index_id`, `manifest_ref`, `evaluation_report_ref`. Błąd bramki daje `gate_failed`
i brak outputu. Raport techniczny zachowuje `activation_allowed=false`.

Osobna sesja PostgreSQL trzyma blokadę workera. Po śmierci procesu można wznowić
ten sam run i attempt z niezmiennymi pinami/czasem startu. Drugi żywy worker
dostaje `worker-busy`. Token przejęcia i transakcja końcowa blokują publikację
wyniku przez anulowanego/starego workera. Awaria DB pozostawia `running` do
wznowienia. Kandydat zapisany przed awarią może pozostać w bazie; nie staje się
aktywny, a powtórny zapis sprawdza jego pełną niezmienność.

Profile i raporty są niemodyfikowalne. Constraints/triggery blokują zmianę
pinów, nielegalny stan, cofanie terminalnego runa, niekompletny sukces i usuwanie
historii. To mechanika jednego lokalnego workera, bez retry scheduler/cloud queue.
Model blokad sesyjnych opisuje [PostgreSQL 16](https://www.postgresql.org/docs/16/explicit-locking.html#ADVISORY-LOCKS).

[Schemas wiedzy](../contracts/knowledge/v1/),
[run indeksowania](../contracts/knowledge/v1/knowledge-index-run.v1.schema.json) i
[OpenAPI dostępu](../contracts/access/v1/access.openapi.json) są wersjonowane.
Run indeksowania używa wspólnego envelope/states. Istniejący kontrakt
[ML run/v1](../contracts/intelligence/v1/run.v1.schema.json) i bundle danych
zachowują dotychczasowe wejścia/outputy i kody błędów; odrzucają nowy wariant.
