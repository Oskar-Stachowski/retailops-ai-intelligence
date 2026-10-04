# Aktualny status

**AI 09 — wspólny rejestr prób development.**
[Przyrost 09.5](development-trial-registry.md) rezerwuje budżet przed fitami,
zachowuje awarie i nierozliczone próby pomiędzy restartami i katalogami wyników.
Historia obejmuje 11 istniejących uruchomień: 5 zakończonych i 6 przerwanych,
bez ponownego treningu na danych projektu. Obserwacja starych wyników nie
udaje rezerwacji wykonanej przed ich uruchomieniem. Końcowy protokół i audyt
dostępu do finalnego testu pozostają otwarte; AI 09 nadal not_ready.
[Receipt](evidence/09-05-development-trial-registry.md) podaje faktyczny
stan testów, pakietu i CI, także błąd wcześniejszego CI na Linuxie.
Pełne lokalne CI zaliczyło 1862 testy główne i 3 rzeczywiste testy TensorFlow,
pakiet oraz pozostałe bramki. Nowy commit wymaga własnego zdalnego Required CI.

**AI 09 — mniejsza pamięć wspólnego benchmarku development.**
[Przyrost 09.4](evidence/09-04-development-memory.md) poprzedza odczyt kodu i
dowodów AI 08: jego SQLite/cache/partycje stockout nie są dublowane.
Forecasting współdzieli identyczne frozen lineage w obrębie pełnego origin,
hashuje populację strumieniowo i wykonuje TF reload w osobnym procesie CPU.
Końcowa mała próba ma 939.02 zamiast 1300.36 MiB peak RSS, pod własnym
limitem 1 GiB, z identycznymi 20 160 predykcjami i wszystkimi metrykami.
Zachowano także nieudane próby i granice pomiaru. Odtworzenie bez refitów
przeszło natywnie (579.42 MiB) i z odłączonego wheel (577.00 MiB).
Signed zero ma osobną kontrolę bajtów. Pełne lokalne CI przeszło:
1839 testów głównych i 3 rzeczywiste testy TensorFlow, pakiet oraz pozostałe
bramki. Nowy commit wymaga własnego Required CI na PR.
Większy profil i niezależna ocena pozostają otwarte, AI 09 nadal not_ready.

**AI 09 — wspólny benchmark development na danych po AI 06.**
[Przyrost 09.3](forecast-development-comparison.md) i
[odbiór](evidence/09-03-forecast-development-comparison.md) obejmują baseline/RF/HGB/TF
na wszystkich 3360 kluczach validation, w tym 3040 eligible. Konfiguracje są
zapisane przed fitami; reload odtwarza identyczne prognozy i metryki bez treningu.
TF obniża MAE mediany względem history28 o 2,51%, poniżej wymaganego 5%.
Przedziały kandydatów pozostają niegotowe, a segmenty zachowują porażki i braki.
Validation użyta do early stopping jest diagnostyką, nie niezależną akceptacją.
Dwie próby przerwane przy 1 GiB całego procesu zachowano; completed benchmark
mieści się w osobnym budżecie 1,5 GiB. Pełne CI i wheel mają wynik w receipt.
Większa skala, kalibracja, globalny audyt, końcowe wyniki AI 07/08 i finalna
kampania nadal są wymagane. AI 09 pozostaje in_progress / not_ready.

**AI 09 — przygotowanie oceny i pierwszy rzeczywisty Keras CPU.**
[Plan i polecenia](evaluation-preparation.md) oraz
[dowód przyrostu 09.1](evidence/09-01-evaluation-preparation.md) przypinają
wymagania, trzy seedy danych i granice przyszłych eksperymentów TensorFlow.
Nowy zakres ma lokalny odbiór kontraktu i pakietu; pełna regresja oraz PR/CI
są raportowane w wersjonowanym receipt. [Przyrost 09.2](tensorflow-challenger.md)
dodaje rzeczywisty trening, supported MLflow flavor, checksummed preprocessing,
wspólny evaluator development, CPU reload i wymuszanie budżetu. Mała kontrolna
próba nie kwalifikuje jakości portfolio. Kalibracja przedziałów, pełna skala,
końcowy protokół i ocena trzech zastosowań pozostają otwarte. Preflight zwraca `not_ready`.

**AI 05 — lokalny przepływ finalnego v12 jest odebrany także na świeżym snapshocie.**
[Raport i pomiary](evidence/05-v12-real-serving.md) oraz
[wersjonowany zapis dowodów](evidence/05-v12-real-serving.json) obejmują cały
import 664 plików / około 29,8 GiB, kwalifikację oryginalnego predictora,
trzy działające wersje registry, trzy udane trwałe batch’e i 42 opublikowane wiersze.
Restart zachowuje zadanie i input; rollback nie przepina wcześniej przyjętego
runa; odrzucenie kandydata nie zmienia runtime. Wyniki obu historycznych wydań są identyczne.

Świeży source ma 56 dni historii do 2026-10-01 oraz 14 dni jawnych znanych
planów, bez przyszłych obserwacji. Pełny import i niezależna kontrola watermarku,
osobna source policy 1.1.0, wszystkie 10 raportów, wersja development 4 i worker
dały **14/14 `current`**, w tym 3 potwierdzone zamknięte dni jako `null`.
Batch z publikacją trwał 3,31 s, pierwsza strona API około 0,10 s.
Restart PostgreSQL/MLflow/API zachował job, dokładny hash i aktualny odczyt.
Kod producenta na aktualnym `main` przeszedł 772/772 testów danych.
Kod AI 05 z 556d349 ma zielony Required CI oraz 1730/1730 testów;
późniejszy commit raportu wymaga własnej kontroli publikacji.

To osobny [lokalny namespace developerski](forecast-v12-development.md).
Domyślne API nie pokazuje jego danych. Pełna ocena 100 produktów / 2 lokalizacji
zachowuje 221 passed, 3 failed i oryginalne `not_ready`; ograniczony viewer
nie widzi zbiorczego raportu. Origin 2026-09-16 pozostaje historyczny i API
zwraca `stale` dla wcześniejszych prognoz. Nie było refitu ani zmiany oryginalnych artefaktów AI 04.

Nowy odbiór [backup/restore](forecast-v12-backup.md) sprawdza cały stan obu baz
PostgreSQL i artefaktów na małych fixture z head `0019_v12_development`.
Nie jest niezależnym backupem rzeczywistej kampanii; klon APFS na tym samym
dysku także nim nie jest. Osobny trwały stos odbioru ma tę migrację;
dotychczasowego długotrwałego stosu nie zmieniono.

**Formalna publikacja AI 05 pozostaje otwarta:** wymagane są zielone Required CI
bieżących commitów i integracja [AI PR #8](https://github.com/Oskar-Stachowski/retailops-ai-intelligence/pull/8)
oraz [source PR #81](https://github.com/Oskar-Stachowski/retailops-cloud-native-platform/pull/81).
Lokalny świeży snapshot, kwalifikacja i odbiór batch/API są zakończone.
Produkcji nie dopuszczono. Historyczne raporty poszczególnych przyrostów
zachowują swoje wcześniejsze wyniki i granice.

Aktualizacja: **2026-10-02**. **Etap 11 — RAG jest odebrany lokalnie.**
[Instrukcja użytkowa](knowledge-semantic.md) opisuje rzeczywiste embeddings,
przygotowanie, kwalifikację, aktywację i rollback. [Końcowy odbiór](evidence/11-completion.md)
wiąże implementację z pomiarami i ograniczeniami. Zdalna publikacja przechodzi
przez chroniony `main` oraz Required CI; stan wykonania pokazuje
[workflow repozytorium](https://github.com/Oskar-Stachowski/retailops-ai-intelligence/actions/workflows/required-ci.yml).

**AI 04 — `ready`, finalna wersja v12, z jawną akceptacją trzech odstępstw.**
Właściciel projektu zakończył iterację developerską na v12 2026-10-01.
[Decyzja odbioru](evidence/04-v12-acceptance.md) obowiązuje po przyjęciu tego
commitu przez PR i zielonym Required CI chronionego `main`.
[Wersjonowany zapis decyzji](evidence/04-v12-acceptance.json) wiąże akceptację
z jednym konkretnym eksportem, pełnymi metrykami i dowodami odtworzenia.

Pełna kampania v12 obejmuje **64/64 kohorty i 27 396 096 wierszy prognoz**.
Oryginalny protokół jakości nadal daje **221 passed / 3 failed** oraz
`forecast_model_status=not_ready` i `quality_qualification_status=not_ready`.
Zaakceptowane odstępstwa MSE wynoszą **+0,204738%, +0,000619%, +0,043292%**.
Nie przepisano ich na zaliczone i nie zmieniono progów oceny.
`stage_status=ready` oznacza świadomy odbiór etapu przez właściciela,
nie nowy wynik statystyczny ani zgodę na wdrożenie produkcyjne.

[Finalne v12](forecast-functional-v12.md) ma zaliczony niezależny replay,
trwały eksport **663 plików / 31 994 594 655 B** i weryfikację rzeczywistego
runu z odłączonego wheel. Kontrola `make forecast-acceptance-check` sprawdza
oryginalne sumy SHA-256, komplet metryk i dokładny zakres trzech wyjątków.
Nie dopuszcza innego runu, v13 ani rozszerzenia decyzji na promocję modelu.

V13 jest **superseded**: [przygotowanie 36900199207](https://github.com/Oskar-Stachowski/retailops-ai-intelligence/actions/runs/36900199207)
zostało anulowane po wyborze v12. Lokalne oczekiwanie na ocenę i kolektor
zatrzymano przed oceną holdoutów v13. Zachowano istniejące pliki, rezerwacje
seedów, freeze i historię; automatyczne uruchamianie generacji po pushu wyłączono.

Odbiór kodu: [PR #7](https://github.com/Oskar-Stachowski/retailops-ai-intelligence/pull/7),
bez omijania ochrony `main` i Required CI. Źródło zostało przyjęte przez
[PR #77](https://github.com/Oskar-Stachowski/retailops-cloud-native-platform/pull/77).
Dalsze prace AI 05 korzystają z eksportu v12 i osobnego adaptera dwóch celów;
MLflow, promocja, batch i serving mają własny odbiór. Portfolio final test
pozostaje nietknięty. Historyczne wyniki [04.7](evidence/04-07-quality.md),
[04.8](evidence/04-08-handoff.md), [korekt](evidence/04-quality-remediation.md)
i [v11](forecast-functional-v2.md) zachowują pierwotne statusy.

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
