# Lokalny PostgreSQL, API i MLflow

Wymagane: Docker z Compose v2, Python/uv z [instrukcji rozwoju](development.md).
Uruchom z katalogu repo:

```bash
make bootstrap
make compose-up
```

Kontroler generuje losowe, różne hasła i token metryk w ignorowanym
`.local/compose.env` (0600, katalog 0700). Nie wyświetlaj pliku ani rozwiniętego
`docker compose config`. `make compose-config` waliduje konfigurację bez jej wypisywania.
Nie używa `compose.env.example` jako zestawu haseł. Nazwa projektu i wolumeny są
izolowane według bezwzględnej ścieżki checkoutu; przeniesienie repo tworzy nowy stos.
Przy zmianie lokalizacji najpierw zatrzymaj stary stos i zaplanuj migrację danych.

`make compose-up` kolejno: uruchamia DB i czeka na TCP health, buduje obrazy,
wykonuje `api-migrate` i `mlflow-migrate`, uruchamia API i MLflow z kontrolą health.
Powtórzenie jest bezpieczne dla aktualnej migracji. API nie migruje bazy przy starcie.
Aby jawnie ponowić migracje, wykonaj `make compose-up`; przy przyszłych zmianach
schematu zatrzymaj API przed migracją. Zadania migracji mają profil `maintenance`.
Runner AI trzyma PostgreSQL advisory lock przez cały przebieg Alembic.
Nie uruchamiaj konkurencyjnych migracji MLflow. Błędy kontrolera mają stały kod;
nie wypisują surowego stdout/stderr bibliotek.

## Usługi i zapis

| Usługa | Adres / miejsce zapisu | Własność |
|---|---|---|
| API | http://127.0.0.1:8081 | Użytkownik kontenera 10001; baza `retailops_ai`, rola `ai_app` |
| MLflow 3.16.1 | http://127.0.0.1:5010 | Użytkownik kontenera 10002; baza `retailops_mlflow`, rola `mlflow_app` |
| PostgreSQL 16 + pgvector 0.8.6 | Brak portu hosta; wolumen `postgres_data` | Administrator bootstrap, osobne nieuprzywilejowane role aplikacji |
| Artefakty MLflow | Wolumen `mlflow_artifacts`, `/var/mlflow/artifacts` | Duże pliki poza PostgreSQL, upload/download przez tracking server |

AI: schemat `ai`, wersja Alembic `0004_rag_denials`, tabela
`ai.service_metadata` oraz niemodyfikowalne [kandydaty RAG](knowledge-index.md).
Osobny kanał `test/offline_test` sprawdza [lifecycle](knowledge-lifecycle.md);
rzeczywisty korpus nie ma aktywnego retrieval. Metadata operacyjne nie zastępują
kontraktów dataset/run/prediction.
MLflow ma własne tabele w osobnej bazie. Role nie mogą łączyć się do bazy drugiej
aplikacji i nie mają superuser/createdb/createrole. Stos nie łączy się z operacyjną
bazą RetailOps i nie tworzy brokera. Nie należy tu zapisywać danych klientów.

Własny artifact root API nie jest jeszcze magazynem danych biznesowych; ten zakres
zapisuje duże artefakty przez MLflow. Importer i immutable storage danych będą w 03.
Obrazy bazowe mają wersję i digest; API ma przypięte zależności z lockfile,
MLflow używa oficjalnego obrazu z przypiętym sterownikiem PostgreSQL.

## Gotowość i sieć

`DATABASE_URL` to secret PostgreSQL/psycopg; nie trafia do odpowiedzi ani logów.
`NETWORK_MODE=compose` wymaga skonfigurowanej DB i pozwala na `HTTP_HOST=0.0.0.0`
wewnątrz kontenera. Porty hosta są wyłącznie loopback, DB nie ma portu hosta,
a sieć DB `ai_backend` jest internal. API/MLflow mają dodatkową sieć
`ai_frontend`, potrzebną do publikacji loopback na Docker Desktop; nie jest
ona blokadą egress tych usług. Granice egress wdrożenia należą do etapu 14. Host `api[:port]` jest akceptowany tylko w tym
trybie; pozostałe granice [HTTP](http-service.md) pozostają egzekwowane.
Bez trybu Compose CLI dopuszcza wyłącznie loopback.

`/ready` ma rolę `ai_api` i wymaganą sondę `ai_db`: rzeczywiste zapytania
o wersję Alembic, rozszerzenie vector i dostęp do tabeli. Brak migracji, inna wersja
lub awaria DB daje 503. `/health` pozostaje 200. Po powrocie DB gotowość wraca
bez restartu API. MLflow nie jest wymaganą sondą diagnostyki API; późniejsze role
zapisujące runy muszą dostać własny jawny kontrakt zależności.
Gotowość tej roli nie potwierdza modeli ani predykcji.

MLflow jest lokalnym tracking serverem bez aplikacyjnego auth; nie udostępniaj
portu 5010 przez publiczny tunel/proxy. Token metryk API nie chroni MLflow.
Kontenery używają lokalnej sieci bez TLS; zakres nie jest wdrożeniem produkcyjnym.
Hasło MLflow trafia przez `PGPASSWORD`, a URI backendu i argv migracji nie zawiera
hasła. Osoby kontrolujące Docker mogą odczytać env; to granica zaufania lokalnego
developmentu, nie secret manager. Bootstrap działa tylko na nowym wolumenie:
zmiana samego pliku haseł nie zmienia zapisanych haseł PostgreSQL.

[Lokalne auth API](access-control.md) działa z prywatnym plikiem dla lokalnego
serve. Obecny Compose nie montuje polityki: /api/v1 odmawia dostępu, a sondy
i token metryk zachowują dotychczasowe zachowanie.

## Zatrzymanie i weryfikacja

```bash
make compose-down
make compose-smoke
```

Shutdown usuwa kontenery i sieć, zachowuje oba wolumeny oraz plik poświadczeń.
Nie dodaje `-v` i nie usuwa danych. Test smoke uruchamia stos danego checkoutu,
utrwala rekord w bazie AI oraz eksperyment i run MLflow z artefaktem. Sprawdza izolację ról,
nieaktualny schemat, SIGKILL i restart, zatrzymanie DB i recovery oraz down/up.
Sprawdza także rzeczywiste wektory pgvector, kompletność i atomowość kandydata,
idempotencję, cache i izolację przestrzeni; fragmenty zachowują się po restarcie.
Weryfikuje brak haseł w logach. Na końcu zatrzymuje kontenery i pozostawia wolumeny.
Raport bez sekretów: `.local/persistence-smoke.json`. Porty 8081/5010 muszą być wolne.
Test zapisuje wyłącznie własne dane testowe; uruchamiaj go w deweloperskim checkoutcie.

Required CI wykonuje `make check` (w tym Compose config), testy adaptera i odrębny
`persistence` z rzeczywistym smoke. Wynik tego joba jest wymagany w `required-result`.
[Dowody tego zakresu](evidence/01-persistence.md) rozróżniają lokalny runtime i
testy z fake; repo jest opublikowane i main jest chronione, ale zdalny CI nowych commitów
pozostaje do wykonania po ich push.

Źródła: [Alembic](https://alembic.sqlalchemy.org/en/latest/tutorial.html),
[MLflow server](https://mlflow.org/docs/latest/self-hosting/architecture/tracking-server/),
[MLflow migration](https://mlflow.org/docs/latest/self-hosting/migration/),
[Compose readiness](https://docs.docker.com/compose/how-tos/startup-order/),
[pgvector](https://github.com/pgvector/pgvector).
