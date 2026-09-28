# Decyzje architektoniczne

**2026-09-27 · zaakceptowane dla projektu; wdrożenie komponentów według etapów.**
Źródło: plan RetailOps na `8a9e620`; architektura bazowa na
[cbf28b2](https://github.com/Oskar-Stachowski/retailops-cloud-native-platform/blob/cbf28b2/docs/plans/ai/architektura.md).
Te decyzje utrwalają granice; aktualne wdrożenie DB/MLflow opisuje ADR-12, a agent pozostaje planowany.

| ADR | Kontekst i wybrana decyzja | Rozważona alternatywa i konsekwencja |
|---|---|---|
| 01 — granice | Niezależne tempo rozwoju AI: osobne repo, API, release i własna baza. RetailOps ma generator, encje, frontend i workflow operacyjny; AI ma snapshot/curated/features/labels/predictions i lifecycle. | Monorepo/wspólna DB wiązałyby migracje i uprawnienia. Integrujemy przez wersjonowane pliki, później REST/zdarzenia; bez kopiowania generatora ani operacyjnych modeli DB. |
| 02 — storage | Metadata, wyniki i RAG potrzebują trwałości: PostgreSQL AI z pgvector; osobna baza i użytkownik MLflow. | Dodatkowy vector DB zwiększa zakres operacyjny. Indeksy i zastosowanie pgvector dopiero przy RAG; duże artefakty poza bazą, początkowo lokalnie, później w S3. |
| 03 — batch-first | Odtwarzalność i bounded work: trening oraz inference jako jobs/runy, wynik utrwalony przed read API. | Trening w handlerze HTTP utrudnia retry/timeout. Atomic output, run identity i idempotencja są warunkami późniejszej implementacji. |
| 04 — MLflow | Audyt eksperymentów, odrzuceń i promocji: tracking i registry MLflow. | Same pliki wystarczą przejściowo w 04, nie zastąpią lifecycle 05. Zachowujemy rejected/failed; serving wyłącznie kwalifikowanego modelu lub baseline. |
| 05 — GitOps | Jeden właściciel release AI: desired state w repo AI. | Osobne repo konfiguracji odłożone. Niezmienne tożsamości obrazów/modeli, migracje i rollback są odbierane w 14–15. |
| 06 — czas i truth | Brak przyszłej wiedzy w cechach: availability <= origin, wersjonowane korekty, oddzielne source facts/truth/labels. | Sam time split i drop kilku kolumn są niewystarczające. Oddzielne source/curated/feature/label/split IDs, kanonizacja i allowlisty; missing nie jest zerem. |
| 07 — agent | Sygnały wymagają oceny człowieka: agent read-only, narzędzia z auth/scope, evidence i świeżością. | Automatyczne mutacje workflow wykluczone. Agent nie tworzy zamówień, cen ani operacyjnych decyzji. |
| 08 — środowiska | Najpierw lokalna weryfikacja, następnie ograniczony czasowo pokaz AWS. | Stały EKS nie jest potrzebny do pierwszej prognozy. Nowe koszty i cloud apply wymagają własnego zleconego zakresu, budżetu i cleanup. |
| 09 — zdarzenia | Nowy daily grain i lineage: przyszłe AI outputs na retailops.intelligence.v2. | Cicha zmiana v1 łamie demo. Legacy v1 zostaje; v2 wymaga schematów, topic init, outbox/inbox, projectora i prób awarii w 10. |

## Pierwszy wynik i handoff danych

Pierwszy slice K1 prognozuje **observed_sales_units**, grain:
produkt × selling location × kanał × origin × target date. Nie obiecuje
nieograniczonego popytu. Pierwszy import używa wersjonowanych plików; brak
brokera, operacyjnej DB lub AWS nie blokuje tego wariantu.

Inventory features są wyłączone przed ledgerem 06. Nie podstawiamy future
snapshotu ani zera w miejsce nieznanego zapasu. Stockout/anomaly mają własne
bramki; pełny procurement nie jest warunkiem pierwszego forecastingu.
Plany ceny/promocji muszą być znane w origin. Nie fabrykujemy store_id
przy brakującym grain API. To niezmienne reguły dla przyszłych kontraktów.

Dotychczasowy RF RetailOps ma status `rejected`. Jest odniesieniem migracyjnym,
nie championem nowego serwisu. Nowy snapshot wymaga ponownej uczciwej oceny.
Dane klientów nie są wymagane w opisanym zakresie.

## ADR-10 — Python, zależności i licencja

Python **3.11.15**, zakres pakietu `>=3.11.15,<3.12`, uv **0.12.19** i
jeden `uv.lock`. Powód: dostępny lokalnie interpreter oraz opublikowane
koła CPython 3.11 dla scikit-learn 1.9.1 i TensorFlow 2.21.0 na macOS ARM64
i Linux x86_64/CPU, sprawdzone w metadata PyPI 27.09.2026.
To sprawdzenie dostępności, nie benchmark ani wspólny test runtime tych bibliotek.

Python 3.14 i mieszane minor versions zwiększyłyby teraz ryzyko niezgodności
ML; aktualizację 3.11 trzeba planować przed końcem wsparcia upstream.
Ciężkie biblioteki ML zostaną dodane z właściwym etapem i sprawdzone we wspólnym
środowisku. Fundament instaluje tylko zależności swoich działających funkcji.

uv zastępuje ręczne pip freeze i wiele lockfile. `--locked` ma przerywać
pracę przy rozbieżności, a backend budowania również jest przypięty.
MIT zachowuje decyzję licencyjną macierzystego RetailOps; oryginalny
copyright i pełny tekst są w root LICENSE. Kod zewnętrzny wymaga własnego
przeglądu licencji przy dodaniu.

Źródła decyzji technicznej: [TensorFlow install](https://www.tensorflow.org/install/pip),
[scikit-learn install](https://scikit-learn.org/stable/install.html),
[uv locking](https://docs.astral.sh/uv/concepts/projects/sync/).


## ADR-11 — lokalna diagnostyka HTTP

Pierwszy działający serwis używa FastAPI/Uvicorn, bez DB i modeli. Rola foundation
ma kontrolę startup; API, domena, wykonanie sond i telemetry są oddzielnymi
modułami. Wymagane zależności blokują readiness, opcjonalne dają degraded.
Pełna lista zewnętrznych usług jako obowiązkowy health check blokowałaby
niezależne funkcje, dlatego zależności są jawnie dobierane w composition root.

CLI dopuszcza tylko loopback. Metryki wymagają własnego tokenu, przy jego braku
pozostają wyłączone. Nie wdrażamy pozornej tożsamości viewer/admin do serwisu,
który jeszcze nie obsługuje danych biznesowych. Nowe role i persistence wymagają
własnych sond i granic dostępu przed ich udostępnieniem.

Kontekst W3C obsługuje OpenTelemetry, z osobnym spanem i izolacją żądań przez
contextvars. Pure ASGI middleware nie ma ograniczenia propagacji kontekstu
BaseHTTPMiddleware. Trace i metryki nie eksportują danych poza proces.
Identyfikatory, trasy i logi mają jawne ograniczenia opisane w
[instrukcji HTTP](../http-service.md). Nie kopiujemy dowolnych incoming headers.

Źródła implementacyjne:
[FastAPI — bezpieczne handlers](https://fastapi.tiangolo.com/tutorial/handling-errors/),
[Starlette — middleware i contextvars](https://starlette.dev/middleware/),
[OpenTelemetry — propagacja](https://opentelemetry.io/docs/languages/python/propagation/).
Przypięte wersje potwierdzono w metadata PyPI i lokalnych testach.


## ADR-12 — lokalna persistence i jawne migracje

PostgreSQL 16 z pgvector 0.8.6: oddzielne bazy i role AI/MLflow, bez dostępu do
operacyjnej DB RetailOps. Metadata są w bazie, duże artefakty MLflow w osobnym
trwałym wolumenie. Nie dodajemy brokera ani dataset/modelowych tabel przed kontraktami.

Alembic jest wykonywany przez CLI z advisory lock, MLflow przez własne zadanie
upgrade. Proces HTTP nie migruje przy starcie. Alternatywą było create_all/startup
każdej repliki, które nie daje wersjonowania ani kontrolowanego lifecycle.
Readiness ai_api sprawdza rzeczywistą DB i dokładną wersję schematu.

Tryb Compose pozwala na bind kontenera, ale publikuje porty tylko na loopback
i izoluje DB w internal network. Rola foundation zachowuje lokalną granicę.
To decyzja developmentu; publiczne auth/TLS i release wymagają własnych etapów.
[Instrukcja i źródła](../local-stack.md), [pomiar](../evidence/01-persistence.md).
