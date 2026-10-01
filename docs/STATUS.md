# Aktualny status

**AI 05 — zadania v12 w API:** [runbook](forecast-v12-jobs-api.md)
opisuje POST202/Location i GET stanu/historii na tej samej trwałej kolejce
co CLI. Metadane ograniczają cały scope i nie ujawniają prywatnych kapsuł.
Sukces obliczeń daje receipt; referencja prognozy wymaga osobnej publikacji
i weryfikacji całego outputu. Namespace testowy jest jawnie wstrzykiwany;
domyślne API używa produkcyjnej nazwy v12. Rzeczywisty model AI 04,
katalog/evaluations v12, backup/restore i zdalny Required CI pozostają otwarte.

**AI 05 — adapter eksportu v12:** [import kampanii do MLflow](mlflow-v12-evidence.md)
obsługuje pełny run, osobne metryki mediany/średniej/przedziału, oryginalne
identyfikatory, wszystkie artefakty, powtórzenia oraz zachowanie failed import.
Oryginalny verifier działa przez osobny przypięty wheel AI 04. Adapter jest
testowany na małych fixtures bez odczytu aktywnej kampanii; rzeczywisty import
czeka na końcowy eksport.

**AI 05 — loader v12 offline:** [odczyt i prognozy](forecast-v12-runtime.md)
wiążą run/cohort/fold/recipe oraz źródło i środowisko. Oryginalny predictor
działa w izolowanym podprocesie; bazowe prognozy pochodzą z historii as-of,
wynik zachowuje medianę/średnią/przedział. Testy obejmują osobne fixtures;
próba przypiętego wheel dała 14 wyników identycznych z oryginalnym predictorem.
Końcowy eksport, rzeczywiste wejście i pełny odbiór serving pozostają dalszą pracą.

**AI 05 — inference i prywatne dopuszczenie v12:** [kwalifikacja i przegląd](forecast-v12-release.md)
dodają jawny kontrakt inference poza oknem oceny, weryfikację feature/curated
parents, powtarzalną próbę prognoz i decyzję uwierzytelnionego operatora z 10
raportami. Wersja, źródło, limity i ważność są przypięte; brak jakości, podmiana
pakietu albo utrata lease powodują odmowę. Rzeczywisty algorytm dał 14 prognoz
identycznych z oryginałem na osobnej fixture. Nie dopuszczono rzeczywistego
modelu ani nie zmieniono aliasów trwałego stosu.

**AI 05 — lifecycle v12 i trwały release:** [rejestracja i decyzje](mlflow-v12-lifecycle.md)
wiążą osobny namespace MLflow z kapsułą przeglądu i immutable historią bazy.
Promocja, odrzucenie, rollback i odzyskiwanie po utracie odpowiedzi są odebrane
na małych fixture, również na rzeczywistych jednorazowych PostgreSQL/MLflow
z restartem usług. Head i ukończenie decyzji zapisują się atomowo; nie powstaje
druga wersja przy wznowieniu utraconej odpowiedzi. Nowy head migracji to
`0015_v12_lifecycle`, po którym dodano kolejkę opisaną poniżej; trwałego stosu nie migrowano.

**AI 05 — kolejka i worker v12:** [prywatne zadania obliczeń](forecast-v12-worker.md)
przypinają release i wejście, dzielą batch zgodnie z limitami oraz zapisują
kompletny wynik mediany/średniej/przedziału z historią próby. Kontrola lease,
retry i anulowania blokuje zapis częściowego wyniku. Odbiór używa jawnych
fixture i osobnego PostgreSQL/MLflow; nie kwalifikuje rzeczywistej kampanii.
Migracja kolejki to `0016_v12_queue`; trwałego stosu nie migrowano.

**AI 05 — publikacja i odczyt v12:** [runbook](forecast-v12-publication.md)
opisuje osobną, idempotentną publikację kompletnego receipt oraz
`GET /api/v1/forecasts/v12`, z zachowaniem mediany/średniej/przedziału,
baseline i null. Odczyt ogranicza scope, sprawdza cały wynik i świeżość,
stabilizuje paginację i blokuje częściową odpowiedź po uszkodzeniu.
Odbiór używa małych jawnych doubles, rzeczywistych PostgreSQL/MLflow
oraz aplikacji ASGI; nie jest kwalifikacją rzeczywistego modelu AI 04.
Bieżący head migracji to `0017_v12_outputs`; trwałego stosu nie migrowano.
Backup/restore v12, Required CI i odbiór rzeczywistego przepływu pozostają otwarte.

Aktualizacja: **2026-09-30**. **Etap 11 — RAG jest odebrany lokalnie.**
[Instrukcja użytkowa](knowledge-semantic.md) opisuje rzeczywiste embeddings,
przygotowanie, kwalifikację, aktywację i rollback. [Końcowy odbiór](evidence/11-completion.md)
wiąże implementację z pomiarami i ograniczeniami. Zdalna publikacja przechodzi
przez chroniony `main` oraz Required CI; stan wykonania pokazuje
[workflow repozytorium](https://github.com/Oskar-Stachowski/retailops-ai-intelligence/actions/workflows/required-ci.yml).

**AI 04.1–04.8 — zadanie, modele, backtesting, jakość i evidence runu:**
[kontrakt i polecenia](forecasting.md) definiują observed sales, grain,
cutoff 23:59:59 UTC, horyzonty 1–14 i okna 7/14 z jedną granicą wiedzy.
Manifest wiąże kalendarz ze zweryfikowanym curated AI 03; inventory i truth
features są wyłączone. [Panel i cechy](forecast-features.md) mają kalendarzowe
lagi 1/7/14/28, rolling z count, zero/missing/closed, kategorię, kalendarz
i znane plany ceny/promocji. Typed draft inputs zachowują cold start i coverage.
[Formalne manifests 04.3](forecast-manifests.md) wiążą feature/label/split IDs,
minimum/freshness, mature labels i wszystkie oceniane klucze. Preprocessing
dopasowuje się tylko na eligible train jednego folda; development holdout
jest oddzielony, portfolio final test pozostaje poza tym protokołem.
[Odbiór 04.1](evidence/04-01-calendar.md), [04.2](evidence/04-02-features.md)
i [04.3](evidence/04-03-manifests.md) podają lokalne kontrole i powtarzalne smoke.
[Baseline'y i evaluator 04.4](forecast-baselines.md) porównują last observed,
średnią kalendarzową 7 dni i seasonal naive7 na identycznych eligible keys.
Wybór według MAE używa tylko validation; h=8–14 nie odczytują przyszłych actuals.
Raport zachowuje coverage i brakujące predykcje, z poprawnym MAE/WAPE dla zer.
[Odbiór 04.4](evidence/04-04-baselines.md) dokumentuje temporalny pomiar i replay.
[Modele RF i HGB 04.5](forecast-models.md) mają pełny train-only pipeline,
direct horizon feature, wspólny evaluator i egzekwowane limity CPU/RAM/czasu.
Wybór diagnostyczny wymaga poprawy validation MAE >5% wobec najlepszego baseline'u.
[Odbiór 04.5](evidence/04-05-models.md) wiąże pomiary z artefaktami i replay.
W jednofoldowym odbiorze 04.5 wybrano HGB: MAE lepsze od seasonal naive7
o 6,7% na validation i 2,2% na development holdout. To pomiar protokołu,
bez kwalifikacji produkcyjnej jakości lub zmiany odrzucenia RF w RetailOps.
[Backtesting 04.6](forecast-backtesting.md) dodaje expanding/rolling-origin
plan, trzy odrębne treningi z mature labels, rozłączne okna oceny i pooled
MAE/WAPE. [Odbiór 04.6](evidence/04-06-backtesting.md) zapisuje wynik,
audyt wspólnych kluczy i niezależne odtworzenie ze źródła.
Na temporalnym fixture wybory validation to RF/HGB/RF; wspólne holdouty
mają 5400 ocenianych kluczy, pooled MAE strategii 1,442420 i WAPE 0,157508.
Wynik jest development evidence; portfolio final test pozostaje nietknięty.
Branch `ai/04-01-task-calendar` jest osobny od AI 12.
[Ocena jakości 04.7](forecast-quality.md) dodaje RMSE, bias, under/overforecast,
MAPE z pokryciem, przekroje i przedziały kalibrowane tylko na validation.
[Odbiór 04.7](evidence/04-07-quality.md) zachowuje pełne wyniki oraz blokady:
**145 bramek passed, 79 failed, 8 not_ready**. Pooled holdout strategii ma
RMSE 2,216533, normalized bias −4,37% i empirical interval coverage 91,67%
przy nominalnym 90%, ale wynik globalny nie zalicza krytycznych segmentów.
Brakuje próby koszyka zero; część kategorii ma nadmierny bias/regresję,
a przedziały dla wysokiego wolumenu pokrywają tylko 69,37% obserwacji.
Niski wolumen ma MAE o 28,63% gorsze od zamrożonego baseline'u i zbyt
szerokie przedziały. Drugi fold nie poprawia globalnego holdout MAE.
Progi nie zostały poluzowane; model pozostaje `not_ready`.
[Plikowy run 04.8](forecast-run.md) utrwala komplet rodziców, predykcje,
modele, config, metryki, card, signature i blokady w jednym archiwum
z checksumami. [Odbiór 04.8](evidence/04-08-handoff.md) wskazuje jego ID,
walidację i sposób importu w AI 05 bez przypisywania historycznego treningu
do MLflow. **AI 04 jest zrealizowane jako development evidence, lecz model
nie przeszedł bramki jakości i nie jest gotowy do serving.** Dopuszczenie
wymaga oddzielnego rozwiązania braków próby i jakości na późniejszych danych,
bez strojenia na final test. Ten branch nie ma jeszcze zdalnej publikacji
ani Required CI.

**AI 05.1 — lokalny tracking i magazyn MLflow:** istniejący z AI 01
PostgreSQL, rola i trwały wolumen mają teraz [backup/restore i politykę
retencji](mlflow-store.md). [Odbiór](evidence/05-01-store.md) sprawdza
rzeczywiste przeniesienie eksperymentu, runu i artefaktu do pustego projektu
oraz odczyt po odtworzeniu. Kolejny zakres to import evidence 04.8 do
MLflow. Model pozostaje `not_ready`; registry, promocja, batch i API nie
są jeszcze częścią odbioru 05.1.

**AI 05.2 — historyczne evidence 04.8 w MLflow:** [importer i semantyka
runu](mlflow-evidence.md) zachowują oryginalne ID, czasy eksportu, lineage,
metryki wraz z ważnością i pełne archiwum z sumami kontrolnymi. [Odbiór
lokalny](evidence/05-02-import.md) potwierdza rzeczywisty import i powtórzenie
bez drugiego runu. Quality nadal ma 145 passed, 79 failed, 8 not_ready;
status modelu to `not_ready`. Registry, promocja, batch i API pozostają do
wykonania w kolejnych zakresach AI 05.

**AI 05.3a — kontrola registry przed wersją:** [review i odrzucenie](mlflow-registry.md)
sprawdziły import 04.8, zapisały audyt z rolą `promoter` oraz powtórzyły
decyzję bez duplikatu. [Odbiór](evidence/05-03-review.md) wskazuje runy i
backup. `retailops-demand-forecast` nie ma wersji ani aliasów; pełna
promocja/rollback na modelu AI 04 czekają na jego kwalifikację.

**AI 05.3b — Registry i recovery:** [mechanizmy lifecycle](mlflow-lifecycle.md)
utrwalają niezależny audyt w PostgreSQL, wersje, aliasy i niezmienne
release pins. [Odbiór](evidence/05-03-lifecycle.md) sprawdza dwie promocje,
rollback, odrzucenie trzeciej wersji, utracone odpowiedzi oraz SIGKILL/restart
w jednorazowym Registry `retailops-demand-forecast-mechanics`.
Nie zatwierdza jakości AI 04 ani działającego runtime; pointer oznacza
zatwierdzony release. Rzeczywista kwalifikacja modelu i późniejsze wpięcie
runtime nadal wymagają odbioru. Zmiany są lokalne, bez zdalnego Required CI.

**AI 05.3c — wspólny backup/restore:** [procedura](lifecycle-backup.md)
wiąże dane aplikacji AI, metadane MLflow i pełny wolumen artefaktów w jednym
pakiecie. [Odbiór](evidence/05-03-store.md) sprawdza blokadę zapisów ról
aplikacji, SIGKILL kontrolera i jawne wznowienie, checksumy każdej tabeli,
sekwencji i całego archiwum oraz recovery niedokończonej rejestracji po
odtworzeniu do nowego projektu. Cel z częściowym restore pozostaje offline;
istniejący cel nie jest nadpisywany. Testy używają wyłącznie izolowanych
modeli mechanicznych. **AI 05.3 czeka na kwalifikację rzeczywistego modelu
AI 04.**
Nie ma jeszcze serving prognoz ani zdalnego odbioru Required CI tych zmian.

**AI 05.4a — trwała kolejka i worker:** [runbook](forecast-worker.md) opisuje
POST202/GET runów, oddzielny grant `pipeline` + `forecast:run`, scope,
przypięte release/data/policy, leasing z heartbeat, automatyczny bounded retry
i prywatne anulowanie. [Odbiór](evidence/05-04-queue.md) potwierdza rzeczywiste
HTTP/PostgreSQL/MLflow, 9 runów, 12 prób i 2 kompletne wyniki fixture,
wyścig workerów, SIGKILL po częściowym obliczeniu, odrzucenie starego tokenu,
zmianę aliasu bez zmiany pinów oraz zachowanie pełnego stanu po restarcie bazy.
**Mechanika działa lokalnie na jawnych fixture; AI 05 pozostaje otwarte.**
Implementację integracji profili i atomowej publikacji opisuje 05.6 poniżej.
Odczyt prognoz opisuje 05.7a poniżej; pozostaje odbiór qualified release’u.
Nie ma zdalnego Required CI tych zmian. AI 04 jest rozwijane w osobnej sesji;
jego nowsza kwalifikacja nie została zaimportowana do tego worktree.

**AI 05.5a — pakiet wejścia i loader release’u:** [runbook](forecast-runtime.md)
opisuje zweryfikowane features/curated, pełny grain, historię, niezmienne
profile, image/lock/schema/config pins oraz inferencję RF/HGB/baseline bez
refit. [Odbiór](evidence/05-05-runtime.md) potwierdza po 14 wartości na
rzeczywistych archiwalnych artefaktach AI 04; RF/HGB zgadzają się z adapterem
diagnostycznym. Peak RSS całej próby: ~310 MiB. **Nie załadowano rzeczywistego
zakwalifikowanego release’u i nie opublikowano prognoz.** Archiwum pozostaje
`not_ready` i ma starszy lock features niż model/runtime, więc wymaga
spójnego pakietu z AI 04 przed servingiem. Nowsze zmiany AI 04 są w osobnej
sesji. Rejestr i supervisor mają odbiór 05.5b poniżej. Do wykonania:
odbiór zatwierdzonego, spójnego modelu. Implementację integracji
z kolejką i atomowym outputem opisuje 05.6, odczyt prognoz 05.7a poniżej; zdarzenia w AI 10.
Nowa bramka kontraktów należy do `make check`, bez zdalnego Required CI.

**AI 05.5b — trwałe wejścia i ograniczony preflight:** [runbook](forecast-input-store.md)
opisuje niezmienne profile PostgreSQL, idempotentny zapis, rozdzielenie
środowisk i limity pojemności. Supervisor loadera nie przekazuje childowi
poświadczeń i ogranicza czas, pamięć oraz IO. [Odbiór](evidence/05-05-input-store.md)
potwierdza rzeczywisty pakiet, wyścig zapisów, rollback limitu JSONB oraz
SIGKILL/restart bez zmiany treści. Worker odrzucił brak zatwierdzonego release’u;
nie utworzono prognoz ani runów. Po migracji ponownie odebrano kolejkę fixtures.
Do wykonania: odbiór zakwalifikowanego, spójnego pakietu AI 04.
Implementację runu, fencing i publikacji opisuje 05.6, a odczyt prognoz 05.7a poniżej.
Zdarzenia należą do AI 10.
Brak zdalnego Required CI; AI 05 pozostaje otwarte.

**AI 05.6 — atomowa publikacja i integracja workera:** [runbook](forecast-publication.md)
opisuje przyjęcie zarejestrowanych profili, filtrowany profil wykonania,
release pins, ograniczony supervisor z heartbeat oraz jedną transakcję
partycji/manifestu/runu/historii/pointera. [Stan weryfikacji](evidence/05-06-publication.md)
zawiera testy jednostkowe na synthetic inputs i SQL-only stub gates.
Odbiór PostgreSQL potwierdza rollback częściowego zapisu, odrzucenie starego
tokenu, wygaśnięcie lease podczas publikacji, zachowanie nowszego pointera
i identyczny pełny stan po restarcie. Ponownie przeszła regresja kolejki.
Nie zaimportowano qualified release’u AI 04;
pełny odbiór rzeczywistego batchu czeka na spójny zakwalifikowany handoff.
Odczyt prognoz z paginacją i oceną freshness opisuje 05.7a poniżej; zdarzenia AI 10.
Zdalny Required CI tego brancha pozostaje nieodebrany; AI 05 jest otwarte.

**AI 05.7a — odczyt prognoz:** [runbook](forecast-read.md) opisuje
`GET /api/v1/forecasts`, oddzielne `forecast:read`, SQL scope przed limitami,
stable sort, default/max limit 50/200 i hash widoku wymagany dla dalszych stron.
Każdy odczyt weryfikuje całe manifesty/partycje oraz piny udanych runów.
[Odbiór](evidence/05-07-read.md) rozdziela testy techniczne od jakości modelu.
Starszy origin i nowsza nieudana próba dają `stale`; brak osobnego source
watermark daje `unknown`, bez deklarowania `current` na podstawie replay.
[Watermark i freshness 05.7d](forecast-freshness.md) mają osobny odbiór poniżej.
Do wykonania: spójny qualified handoff AI 04 oraz rzeczywisty
batch na jego release’ie. Zdalny Required CI nadal nie jest odebrany;
outbox/zdarzenia pozostają w AI 10. **AI 05 pozostaje otwarte.**

**AI 05.7b — katalog modeli i wersji:** [runbook](model-catalog.md) opisuje
trzy endpointy `/models`, szczegół modelu i `/versions`. Katalog obejmuje
wersje z publikacją w autoryzowanym scope; filtr SQL poprzedza distinct/count
oraz limit 1000 wersji. Numeric sort, piny enrollment/release i hash paginacji
chronią odczyt. Nie udostępnia globalnych metryk ani URI plików.
Head spoza scope pozostaje ukryty, aliases/runtime/drift nie są potwierdzane,
freshness pozostaje `unknown`. [Odbiór](evidence/05-07-catalog.md) podaje
rzeczywisty HTTP/PostgreSQL, próby uszkodzenia i restartu oraz granice fixture.
Scoped historyczne evaluations opisuje 05.7c poniżej, watermark 05.7d.
Qualified batch AI 04 nadal pozostaje do wykonania.
Zdalny Required CI tego brancha nie jest odebrany. **AI 05 pozostaje otwarte.**

**AI 05.7c — historyczne oceny:** [runbook](evaluations.md) opisuje listę i szczegół
`/evaluations`, całkowite pokrycie scope raportu przez grant, filtr statusu,
immutable import i stabilne strony. Metryki MAE/WAPE zostały odtworzone z
zapisanych predykcji/etykiet kalkulatorem AI 04, bez treningu. Pierwotny status
jakości oraz stare code/lock pins są zachowane, bez nowej kwalifikacji modelu.
[Odbiór](evidence/05-07-evaluations.md) obejmuje HTTP/PostgreSQL, 37 synthetic
ocen i rzeczywisty historyczny export (12012 memberships), odmowę częściowego
scope, niezmienność i SIGKILL/restart. Tabela pochodzi z migracji
`0013_forecast_evaluations`; bieżący DB head to `0017_v12_outputs` opisane powyżej.
Trwałego stosu nie zmieniano. Pozostają qualified handoff AI 04,
batch na jego release'ie oraz zdalny Required CI. **AI 05 pozostaje otwarte.**

**AI 05.7d — watermark i świeżość:** [runbook](forecast-freshness.md) opisuje
przypiętą deklarację kompletności źródła, osobną dostępność obserwacji w cutoff,
wejścia/outputy 1.1 oraz odczyt `forecast-read-v2`. Origin ma maksimum 24 h,
watermark musi obejmować origin, a obserwacja może mieć lag do 1 dnia zgodnie
z dobowym close. Starsze artefakty 1.0 zachowują canonical IDs i nie otrzymują
domyślnego `current`. [Odbiór](evidence/05-07-freshness.md) obejmuje rzeczywisty
HTTP/PostgreSQL, mixed-version odczyt, atomową odmowę rehashed niezgodnego dowodu,
tamper/restore i SIGKILL/restart. Przygotowanie 1.1 ze zweryfikowanego archiwum
AI 04 zachowało parenty i stare locki; nie kwalifikowało modelu ani nie publikowało
prognoz z tego archiwum. Migracja freshness to `0014_forecast_freshness`;
**bieżący head to `0017_v12_outputs`** opisane powyżej;
trwałego stosu nie migrowano. Pozostają qualified handoff AI 04, rzeczywisty
batch na jego release'ie oraz zdalny Required CI. **AI 05 pozostaje otwarte.**

**AI 03.3 — kontrakt handoff odebrany lokalnie:** [snapshot źródła](source-snapshot-handoff.md)
ma wspólną wersję 1.0.0, pełny mały fixture oraz niezależną walidację schema,
identity i transportu bez generatora/DB.
**AI 03.4 — typed importer odebrany lokalnie:** [CLI i runbook](source-snapshot-import.md)
opisują pełną weryfikację Parquet, canonical hashes, gates, atomową publikację
i niezmienny reimport. [Evidence](evidence/03-04-importer.md) podaje testy obu
smoke, partycje, truth opt-in i odłączony wheel.
**AI 03.5 — curated:** [runbook](curated.md) opisuje jawne mappings,
normalizację, quarantine, immutable IDs i odczyt z pełnej historii wersji.
[Evidence](evidence/03-05-curated.md) podaje 743 testy i pomiary smoke/as-of.
[AI 03.6 — bramka cross-repo](evidence/03-06-cross-repo.md) wiąże oba repo
przez pełne smoke i przypięte rewizje. Końcowy odbiór RetailOps określa wejście
do 04/06 oraz odrębne readiness use cases.
AI 12 rozwija się w osobnym worktree; forecasting zaczyna się od main z AI 03.
Branch 03 jest opublikowany w [PR #5](https://github.com/Oskar-Stachowski/retailops-ai-intelligence/pull/5).
Przypięte wyniki Required CI, testów i rzeczywistego Compose/persistence
znajdują się w [końcowym evidence cross-repo](evidence/03-06-cross-repo.md).

## Etap 06 — inventory

**AI 06 ma [końcowy odbiór](evidence/06-inventory-complete.md).**
[Snapshot/import/curated 1.1](reference/inventory-snapshot-11.md) obsługuje
source 2.7, 43 facts/plans i oddzielne private evaluation truth. Ledger, historyczny
routing, sprzedaż/zwroty, orders/plans/receipts i snapshots są niezależnie uzgadniane.
Curated zachowuje causal availability, fizyczny grain i odczyty as-of bez future fallback.
Pełny pipeline obu profili dwukrotnie spełnia budżet 300 s / 1024 MiB.
Inventory readiness dotyczy danych; modele 04/05/08 wymagają własnej oceny.
AI 04 i AI 12 zachowują odrębne branche/worktrees.

## Etap 11

- Zatwierdzony korpus: 29 dokumentów, 451 fragmentów, przypięte źródła Git,
  statusy, klasy dostępu i dokładne cytaty.
- Amazon Titan Text Embeddings V2, 1024 wymiary, `eu-north-1`, kontekst
  nagłówków i wersjonowany cache. Wywołania AWS są jawne i ograniczone budżetem.
- Golden set: 44 pytania, w tym 9 krytycznych. Bez zmiany etykiet i progów:
  **Recall@5 0,852941 ≥ 0,80; MRR 0,661275 ≥ 0,60; cytaty i krytyczne 1,0**.
- Trwałe runy odtwarzają pomiar bez AWS. Niezaliczony próg zachowuje raport
  `failed/gate_failed`, bez outputu. Sukces nie aktywuje indeksu automatycznie.
- Użytkowa kwalifikacja wymaga udanego runa, zgód, raportu jakości i kompletnego
  przeglądu podobieństw. Aktywacja i rollback mają CAS, idempotencję i niezmienne piny.
- Właściwy indeks jest aktywny w lokalnej bazie w kanale `retrieval`.
  PostgreSQL odtworzył wszystkie 44 wyniki golden. Runtime wymaga jawnego
  `RAG_BEDROCK_ENABLED=true` i poświadczeń AWS procesu.
- 643 testy regresji; dodatkowa kontrola 77 testów po dopracowaniu current/report
  również przechodzi. Historyczny odbiór semantyczny obejmował migrację `0008_rag_semantic`,
  pgvector, HTTP, runy, SQL gates, aktywację/rollback, SIGKILL i trwałość danych.

Nie pozostały otwarte blokady implementacji lub jakości Etapu 11.
Fake pozostaje wyłącznie ścieżką testową i nigdy nie uprawnia do użytkowej aktywacji.
Szczegółowe wcześniejsze evidence opisuje historyczne, mniejsze zakresy odbioru;
nie stanowi bieżącej listy braków.

## Fundament i dalsza praca

Etap 01 ma odbiór lokalny i zdalny: pakiet/CLI, settings, HTTP/telemetry,
lokalne poświadczenia i scope, odrębne PostgreSQL AI/pgvector i MLflow,
wykonywalne kontrakty danych/run/tool, jawne migracje i Required CI.
[Uruchomienie](local-stack.md), [uprawnienia](access-control.md),
[kontrakty](data-contracts.md), [odbiór zdalny](evidence/01-remote-ci.md).

[Bieżący odbiór danych](evidence/03-06-cross-repo.md) jest wspólny z RetailOps.
Po pełnej bramce 03 można rozdzielić forecasting **04** w AI i ledger **06**
w RetailOps; nowe źródło po 06 wymaga ponownego importu i zależnych ocen. Równolegle można przygotować
interfejsy i test doubles **AI 12**. Pełne zamknięcie agenta wymaga **AI 10 i 11**;
11 jest gotowy, 10 nadal należy do późniejszego ciągu danych/ML/integracji.
[Pisemna mapa etapów i repozytoriów](https://github.com/Oskar-Stachowski/retailops-cloud-native-platform/blob/main/docs/plans/ai/kolejnosc-i-repozytoria.md).

## Granice

Odbiór dotyczy lokalnego retrieval na konkretnym zatwierdzonym snapshotcie.
Zmiana dokumentacji na `main` nie aktualizuje automatycznie korpusu. Kolejna
wersja wymaga nowego snapshotu, przeglądu i ewaluacji.
Nie ma jeszcze generowania odpowiedzi, ewaluacji groundedness ani wykonywania
narzędzi agenta — to AI 12. Pipeline danych, modele, integracja zdarzeń oraz
wdrożenie AWS/EKS mają dalsze bramki. Limit AWS na proces nie zastępuje wspólnego
budżetu wielu replik ani produkcyjnego IAM. Nie deklarujemy wdrożenia w chmurze.
