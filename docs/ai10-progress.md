# AI 10 — bieżący stan i kontynuacja

Aktualizacja bieżąca: **2026-10-07**. Status AI 10: **in_progress**.
AI 07 i AI 08 są **ready** na zaakceptowanym main AI
`18e771f9c2e89e91bf7afeb1744e0cd9112f50b5`. Poniższe starsze sekcje opisują
historyczne przyrosty z 4–5 października; ich oceny gotowości 07/08 nie są
bieżącymi blockerami. Oryginalne receipts zachowano bez zmian.

Bieżąca integracja jest na `ai/10-ready` w PR-ach:
[AI #28](https://github.com/Oskar-Stachowski/retailops-ai-intelligence/pull/28)
i [RetailOps #100](https://github.com/Oskar-Stachowski/retailops-cloud-native-platform/pull/100).
Wszystkie wcześniej odebrane przyrosty połączono z zaakceptowanym main.
Nie przeniesiono jeszcze tego etapu na `origin/main`.

### Odebrane komponenty bieżącej integracji

**Aktualny checkpoint 2026-10-07:** wspólny CI PR #34 jest na main `89b64d2`,
a PR AI09 #33 na nowszym main `a128bb3`. Integracja AI10 zachowuje wszystkie
własne bramki SQL/broker/replay, outbox i pełny v12 backup/recovery.
[Core CI](evidence/ai10-shared-ci-upstream-accepted.json) ma 15/15 success;
[AI10 CI `433f5ed`](evidence/ai10-ai-integrated-head-ci.json) ma 17/17 success.
Source `4b03664`, run `37617019955`, ma 30/30 success. Nowa poprawka hooka
wymaga ponownego Required CI na aktualnym headzie.

**Powtórzony stockout i anomaly na zintegrowanym `433f5ed`:** run
`37624498603` odebrał oba modele i oryginalne SQL publishery, bez refitów.
[Stockout](evidence/ai10-native-stockout-final-head-accepted.json): 40 wyników,
40 oryginalnych ACK, 40 projekcji i duplicates oraz pełny TCP API/built UI.
[Anomaly](evidence/ai10-native-anomaly-final-head-accepted.json): 1232 wyników
i ACK, 1232 projekcji i duplicates, 25 stron built UI oraz live revocation.
Niezależne Source parsery ponownie zweryfikowały oba pełne census.

**V12 — trzecia próba failed:** run `37624038527` na `2b4f6f2` zaliczył
258 boundary tests, rzeczywisty preflight aplikacji, pełną kwalifikację/cold
probes, review gates, MLflow import i registry/preload. Kontroler doszedł do
actual API intake i cold worker, ale `ai10_v12_outbox_failure_not_exercised`
zatrzymał test atomowego rollbacku: hook ignorował nową linię na początku
SQL rzeczywistego emittera. [Receipt failed](evidence/ai10-v12-native-outbox-hook-failure.json)
nie poświadcza finalnego publishera ani Source API/UI. Hook teraz rozpoznaje
whitespace i dokładną nazwę tabeli; test kompiluje SQL rzeczywistego emittera.
13 testów runner/workflow, lint/format 1112 plików, mypy 656+10 i docs/contracts
passed. Nowy pełny zdalny run pozostaje wymagany. Usunięto sześć własnych
handoff objects i unikalny secret; oryginalne S3 oraz quality `not_ready` zachowano.

Niższe checkpointy pozostają historią zakresów i wcześniejszych prób.

**Nowszy main AI09 — zgodność current campaign pin:** dołączono `e1f864c`
z audytowanym eksportem i zaakceptowanymi optymalizacjami. Bieżący campaign hash
zmieniły dokładnie `data_contracts/identity.py` i `forecasting/features_store.py`.
Guard importu bundle wymaga teraz pinu `d0df4db9a143e8e0dd5573dec8f55e5f6650da88af0dae61c800230d6d09c2ef`;
poprzedni `cdbd3c` zachowano. Wszystkie 26 wire/importer/schema/lock pins,
oryginalny frozen v12 i jego decyzja pozostają zachowane. [Dowód zgodności](evidence/ai10-accepted-main-campaign-repin.json)
obejmuje 141 wykonanych testów bez failures/skips, rzeczywisty HTTP bundle import,
byte/error parity serializacji oraz odmowę origin spoza kalendarza. Lint/format
1107 plików, mypy 654+10 i wszystkie kontrakty passed. Końcowy CI nowego HEAD
pozostaje wymagany; ten przyrost nie ponawia naukowej kwalifikacji modeli.

**Powtarzalność v12:** archive i native workflow uruchamiają się wyłącznie przez
`workflow_dispatch`, ze świeżą nazwą task-owned secretu i SHA prywatnej mapy.
Native wymaga również jawnej zamkniętej daty UTC inference. Nie ma automatycznego
downloadu po push ani fallbacku do usuniętych handoff secrets. Oba workflowy
przeszły actionlint; trwający native run zachowuje definicję z własnego commitu.

**Anomaly — oryginalny publisher SQL odebrany:** run `37615427814`, job
`112772493567`, AI `eaad6c4` i Source `cbbf711`, zakończył się success.
Pełny frozen model, registry, atomowy batch, SIGKILL/recovery i 1232 rzeczywiste
AI SQL ACK przeszły Source broker/SQL/TCP API i 25 stron built UI. Source ma
1232 projekcje i 1232 duplicates, ACK vector `[1064,1400]`.
[Pełny receipt](evidence/ai10-native-anomaly-original-sql-accepted.json) wiąże
SHA ZIP, raporty oraz niezależną weryfikację wszystkich payloadów i ACK.
Wcześniejsza [nieudana próba](evidence/ai10-native-anomaly-original-sql-failure.json)
pozostaje zachowana jako failed bez poświadczenia oryginalnego publishera.

**V12 — druga próba zatrzymana na rzeczywistej konfiguracji API:** run
`37612565927` na `8665f43` zaliczył 254 boundary tests, wszystkie 669 plików,
kwalifikację i dwa frozen cold predictions, dziesięć review gates, pełny
664-file import MLflow HTTP oraz development registration/promotion/preload.
Actual intake zatrzymał `isolated_ai_database_required`: controller używał
`v12_test`, a rzeczywista aplikacja wymaga `ai_app` i `retailops_ai`.
[Receipt failed](evidence/ai10-v12-native-database-failure.json) zachowuje zakres
bez twierdzenia o finalnym SQL/publisher/Source API/UI. Własne sześć handoff
objects i unikalny secret usunięto; oryginalne S3 zachowano.
Poprawka używa kanonicznych nazw w nadal osobnym kontenerze UUID, z prywatnym
hasłem i loopback portem. Wczesny preflight wywołuje rzeczywistą aplikację przed
archiwum. Sześć runner guards, 22 łączne testy runner/selection/workflow,
lint/format 1109 plików, mypy 655+10 i docs/contracts passed. Pełny nowy odbiór
v12 pozostaje wymagany; original quality nadal `not_ready`.

**Aktualny Required CI:** AI `fe62436`, run `37619676065`, ma 16/16 success;
Source `4b03664`, run `37617019955`, ma 30/30 success.
[Receipt Source](evidence/ai10-source-final-head-ci.json) wiąże exact head.
Nowe zmiany runtime/CI wymagają odbioru aktualnego headu.

**Usprawnienia odbioru:** manual lane stockout/anomaly/all; automat wybiera
jedną ścieżkę tylko dla rozpoznanych plików acceptora, przy common/unknown oba.
Native v12 sam odzyskuje oryginalne archiwum; osobny archive-only nie jest
prerequisite. Nie wdrożono cache semantycznego verifiera ani skip cold/recovery.
[Kroki i impact](plan-usprawnien-ai-00-10.md) podają ograniczenia szacunków.
Wspólne usprawnienia CI PR #34 z `02554284` zintegrowano lokalnie, zachowując
wszystkie bramki AI10: replay/broker SQL, transactional outbox i pełny v12
backup/recovery. Pełny native v12 ponowiono ręcznie w runie `37624038527`
na `2b4f6f2`; wczesny preflight aplikacji passed. Zdalny Required CI
zintegrowanego headu oraz odbiór/merge PR #34 nadal pozostają wymagane.

**Integracja z nowszym main AI09:** merge zachowuje `3329a81`, wszystkie
kontrakty i kod AI09 oraz wymagany osobny job TensorFlow. Żadna bramka AI10
SQL/broker/replay nie została zastąpiona. Wszystkie 41 negatywnych kontroli
kontraktu CI, lint/format 1101 plików, mypy 652+10 i pełne contracts-check
przeszły lokalnie; końcowy Required CI nadal wymaga dokładnego nowego HEAD.

**Pełny v12 — pierwsza próba pozostaje failed:** run `37604263732`
zweryfikował wszystkie 669 oryginalnych plików, zaliczył 251 boundary tests,
kwalifikację z dwoma frozen cold predictions i dziesięć rzeczywistych review
gates. Import MLflow zatrzymał `v12_scratch_private_owned_directory_required`.
`Path.mkdir(parents=True, mode=0o700)` ustawiał 0700 tylko na ostatnim katalogu;
pośredni katalog oryginalnego runu powstawał z 0755. Recovery tworzy teraz
każdy katalog osobno z 0700 i sprawdza UID, typ oraz uprawnienia. Rzeczywisty
mały download → nested run → ScratchTracking hardlink odtwarza tę granicę;
symlink i publiczny parent są odrzucane przed siecią. Wszystkie 33 recovery,
scratch i runner guards passed. [Receipt nieudanej próby](evidence/ai10-v12-native-scratch-failure.json)
zachowuje prawdziwy zakres; SQL/publisher/Source API/UI wymagają następnego
pełnego wykonania. Usunięto dokładnie sześć własnych handoff objects i
unikalny secret; oryginalne S3 pozostaje zachowane.

**Oryginalny publisher stockout passed:** run `37608775665`, job
`112750656385`, AI `b1df980` i Source `cbbf711`. Oryginalny frozen model,
registry/cold worker, 40 native wyników i 40 rzeczywistych SQL ACK przeszły
pełny Source broker/SQL/TCP API/built UI, 40 duplicates, original payloads,
lineage i live revocation. ACK vector `[34,46]`. ZIP 662587 B niezależnie
zweryfikowano względem SHA `e9637201743809a5ecf2c4c98fab2899cab9efd7c4a0bd8a97b0aad422f0c55c`;
[receipt](evidence/ai10-native-stockout-original-sql-accepted.json) zachowuje
oryginalne raporty. Niezależny lokalny Source parser ponownie zweryfikował
wszystkie 40 native outputów i oryginalnych ACK bindings. Zero refitów i brak
production deployment pozostają zachowane. Publisher anomaly jest odebrany; pełny v12 pozostaje pending.

**Publisher z oryginalnej bazy — nowy przyrost, runtime pending:** Source
`b73468a` dodaje wiązanie pełnego census z oryginalnymi AI SQL ACK i SHA
rzeczywistych konsumowanych bytes. Natywne acceptory stockout/anomaly
zachowują własną bazę do końca broker/SQL/API/built UI odbioru Source.
Caller przypina dokładny Source i wymaga zamkniętego runtime publishera;
prywatne control/child logs nie trafiają do artefaktów. Lokalnie 16 testów
AI, 35 testów Source, setup-plan rzeczywistego odbioru i mypy 606+10 passed.
Fixture nie kwalifikują modeli. Pełny nowy workflow musi jeszcze wykonać
publisher na oryginalnych frozen wynikach.

V12 run `37604263732` zakończył pełne recovery i później zatrzymał się na
guardzie opisanym wyżej. Required CI AI
`c8df654` wykrył błąd ponownego uruchomienia bazy w istniejącym compose smoke;
następny pełny CI ma ponownie sprawdzić tę samą bramkę bez zmiany jej limitów.

**Source capture → rzeczywisty AI SQL/ACK — odebrany przyrost:**
Source `f4535a3` zachowuje oryginalny TLS/SCRAM broker podczas niezależnego
odbioru AI. `check_ai10_source_sql_handoff.py` tworzy osobny własny PostgreSQL,
porównuje pełny prefix SQL z capture Source, potwierdza prawdziwe offsety,
ponawia trzy oryginalne rekordy i późną korektę, a następnie porównuje pełny
replay drugiej grupy. Oczekiwane sumy 4 → 7 nie doliczają duplikatów.
Run `37607732767`, job `112747249701`, zakończył się **success**: 9 testów
bez pominięć, prefix ACK `[0, 0, 3]`, final ACK `[0, 0, 4]` i zgodność pełnego
SQL replay. ZIP 4400 B pobrano i niezależnie sprawdzono względem SHA
`a145565bf85a8bc2fc27eaa3492473b856f01471f067cd0ae189e7e05a5a5401`.
[Receipt](evidence/ai10-source-sql-handoff-accepted.json) zachowuje dokładne
Source/AI SHA oraz wszystkie raporty. Trzy negatywne lokalne guards,
mypy 607+10 i Source setup-plan także passed.
To nadal protokół `daily_demand_versions` na jawnych obserwacjach testowych,
bez kwalifikacji modeli i bez twierdzenia o pełnym 43-table SQL snapshot.

Nowy Required CI `a9f087f` przeszedł poprzednio blokujący compose smoke.
Jego lint wykrył dwa komentarze `noqa` przesunięte przez formatowanie;
komentarze poprawiono, a pełny lint/format 1015 plików i docs-check passed.
Bramek bezpieczeństwa ani zasad publishera nie pominięto.

Native stockout w `37606671405` przeszedł oryginalny model/MLflow/SQL,
lecz odbiór Source zatrzymało błędne porównanie SHA wartości z pełnym
transport fingerprint `value/key/headers/timestamp`. Source `cbbf711`
porównuje osobno oryginalne wire bytes na ACK coordinates i cały fingerprint
SQL, przez bounded read bez zapisu offsetów. 41 testów parsera/negatywnych
bindings passed. Nowy native workflow przypina dokładne poprawione bytes;
stare własne próby z potwierdzonym błędnym assertem zatrzymano, zachowując
ich artefakty. V12 i przyjęty Source SQL handoff pozostają niezależne.

**Bieżący przyrost z 7 października:** pełny native anomaly workflow
`37594406280`, job `112705553204`, zakończył się **success**. Wszystkie 1232
oryginalne wyniki, 2464 broker receipts, 1232 duplicates, pełny TCP API i 25
stron built UI przeszły wraz z oryginalnym testem SIGKILL/recovery.
[Receipt](evidence/ai10-native-anomaly-accepted.json) zachowuje dokładny SHA ZIP.
Granica tego dowodu pozostaje file handoff committed outbox; publisher
oryginalnej bazy AI nie jest poświadczony przez ten wcześniejszy receipt.
Nowszy pełny receipt stockout jest wskazany na początku dokumentu.

Dedykowany Source capture workflow `37599182565` jest **success**.
[Receipt](evidence/ai10-source-capture-accepted.json) potwierdza rzeczywisty
Source SQL/TLS/SCRAM, crash po broker ACK, retry tych samych bajtów, pełny
prefix trzech partycji i niezależny replay AI z późną korektą 4 → 7.
To jest `daily_demand_versions`, bez twierdzenia o pełnym snapshotcie 43 tabel
lub o ACK/zapisie SQL po stronie AI.

Oryginalny v12 ma przygotowane nowe wejście inference: snapshot 43 tabel,
102 dni historii, 14 dni planów, import/curated/features i 56 wierszy dla
czterech szeregów. Publiczny `prepare_ai10_v12_inputs.py` zaliczył pełny
lokalny replay przygotowania. Oryginalne treningi i quality nie są uruchamiane
ponownie. Source `1a7f558` dodaje odbiór osobnej przestrzeni development z
dokładnym dokumentem właściciela, oryginalnym publisherem SQL i obowiązkowym
built UI, którego nowy runtime CI wymaga jeszcze wykonania.

Nowy workflow `AI10 original v12 native output` zachowuje pełny verifier,
dziesięć real review gates, pełny import do MLflow z HTTP checksum oraz
preload przed intake. Własna baza AI pozostaje aktywna podczas odbioru Source.
Shared-inode transport ogranicza zajętość jednorazowego runnera; nie jest
niezależnym backupem. Lokalnie: 48 testów Source policy, mypy 43 modułów,
frontend lint/build oraz setup-plan nowych fixtures passed. Ostateczny wynik
tego nowego natywnego runtime pozostaje pending.

Required CI na AI `5f961f7` wykrył resource-budget błąd fixture podczas testu
kopert zdarzeń. Envelope test nie wymaga nowego treningu: używa teraz
istniejących jawnych fixtures kontraktu, a oryginalny test native modelu
pozostaje w obowiązkowym CI. Produkcyjne limity modelu nie zostały zmienione.
Wszystkie 14 testów kopert po tej korekcie passed; final Required CI musi
jeszcze zaliczyć aktualny head obu PR-ów.

- Native anomaly `Item` i physical stockout `RiskItem` mają wspólny v2 topic,
  zachowują pełny oryginalny payload, native ID, run/release i lineage.
  Forecast v12 oraz legacy v1 zachowują wcześniejsze znaczenie.
- Migracja AI `0025_model_intelligence_outbox` utrwala native wynik i outbox
  w jednej transakcji. Awaria enqueue cofa cały batch; publisher wymaga ACK
  przed SQL delivery receipt. Cold-start API ponownie przechodzi.
- Source `a10f0c7e0600` dodaje immutable native model history, inbox oraz
  model pointer w atomowym transport/checkpoint receipt. Starszy wynik
  pozostaje historią; scope, principal i read freshness ustala serwer.
- Read API `/intelligence/v2/anomalies` i `/intelligence/v2/stockout-risks`
  oraz dwa panele w istniejącym widoku Anomalies zachowują native grain.
  UI ma osobiste credentials wyłącznie w pamięci i usuwa je po zmianie
  demo user, ukryciu karty, timeout lub unmount.
- Na commicie Source `77eef42096798ea9f31323f9f6ec5c491cd5b576`
  [API CI](https://github.com/Oskar-Stachowski/retailops-cloud-native-platform/actions/runs/37584520604)
  zaliczył **71 real SQL/broker tests**, w tym **13** nowych przypadków
  modelowych, a pełna seria API **1070 tests**. Built UI/API/PostgreSQL,
  authenticated cross-repo runtime, Compose recovery/rollback i kind
  persistence/rollback także passed. Jest to dowód komponentów; nie
  kwalifikacja trzech modeli ani pełny Required CI najnowszego heada.
- Lokalnie nowy eksport census i bramki native workflow mają **71 focused
  tests passed**, Ruff/format i mypy **599** modułów passed. Source ma
  **37** testów parsera/access/kontraktów passed i mypy **42** modułów passed.
  Testy kontenerowe wykonuje CI; lokalny Docker pozostaje wyłączony.

- [Native stockout CI](https://github.com/Oskar-Stachowski/retailops-ai-intelligence/actions/runs/37587931757)
  ponownie zakwalifikował oryginalny frozen model bez refitów i zaliczył
  **236** testów, rzeczywisty registry/cold worker oraz pełny batch
  **40** wyników z kompletnym immutable SQL outbox census. Lokalny Source
  parser także potwierdził wszystkie 40 oryginalnych payloadów z artefaktu.
  Końcowy broker/TCP consumer zatrzymał się przed uruchomieniem, ponieważ
  brakowało rejestracji fixtures; poprawka Source `dc82e2b` przywraca
  fixtures, a jej pytest setup plan passed. Cały native workflow wymaga
  jeszcze ponownego zaliczenia.

- Następna próba workflow na `91ea5b0` zatrzymała się po poprawnej nowej
  kwalifikacji na gitleaks: historyczne receipts zawierały 17 false positives
  hashów/tagów oraz jednego zdania Compose/Kubernetes. Zbadano je prywatnie;
  wyjątki są ograniczone dokładnym path AND literalną wartością. Lokalny
  skan całej historii i working tree ma **0 findings**. Source `bbf570d`
  dodaje pełny oryginalny anomaly odbiór i mandatory built-browser test dla
  obu modeli. Nowy workflow wymaga osobnych wyników execution, nie deklaruje
  jeszcze zaliczenia tych nowych odbiorów.

- Native stockout run `37592264044` na `3ada65d` zaliczył wykonanie modelu,
  transport pełnego census 40 wyników, 80 receipts, deduplikację oraz wszystkie
  literalne ID/auth przez rzeczywisty TCP API. Pierwszy built-browser odbiór
  przerwał tylko assert pełnego UUID w skróconym widocznym tekście. Source
  `53c85b4` sprawdza pełne native UUID w `aria-label` istniejących komórek;
  pełne ID/lineage pozostają porównywane w read API i szczegółach. Ponowne
  wykonanie całego browser odbioru nadal wymagane.
- **Stockout odebrany także w rzeczywistym UI:** job `112705553131` w
  [runie 37594406280](https://github.com/Oskar-Stachowski/retailops-ai-intelligence/actions/runs/37594406280)
  na AI `e49969c` i Source `53c85b4` jest **success**. Wszystkie 40 oryginalnych
  wyników, 80 receipts, 40 duplicates i 40 literal IDs zaliczyły SQL/TCP API,
  built Chromium, lineage oraz live revocation. ZIP pobrano i sprawdzono
  niezależnie względem rozmiaru i SHA. [Receipt](evidence/ai10-native-stockout-accepted.json)
  zachowuje granicę file handoff committed outbox; nie poświadcza publishera
  oryginalnej bazy AI ani wspólnego E2E trzech modeli.
- Anomaly w poprzednim runie `37592264044` zaliczył prawdziwy OCI/registry,
  SIGKILL restart, wszystkie **1232** native wyniki, **2464** broker receipts,
  **1232** duplicates oraz wszystkie literal IDs/auth w TCP API. Built UI
  napotkał tę samą poprawioną asercję UUID. Ponowny pełny odbiór UI trwa w
  runie `37594406280`; nie zaliczono go na podstawie samego poprzedniego API.
- Dla oryginalnego forecastu v12 przygotowano bounded remote recovery **669**
  plików (664 archive + 5 niezmienionych wheel/runtime/acceptance), bez
  treningu ani generowania Source. 18 testów boundary/digest/host/private
  paths/disk reserve passed; mypy **600** modułów i snapshot types **10**
  passed. Prywatny GET-only handoff w S3 ma 3h ważności i jeden zaszyfrowany
  tymczasowy repo secret; wszystkie 669 URL bindings lokalnie sprawdzono,
  signed URLs nie trafiły do Git/logów. Workflow ma zweryfikować pełne
  oryginalne bytes i semantic replay zaakceptowanym frozen wheel. Sam recovery
  nie zalicza jeszcze serving ani Source API/UI. Cleanup dotyczy wyłącznie
  sześciu własnych tymczasowych obiektów i własnego secretu.
- **Recovery oryginalnego v12 passed:**
  [run 37594406356](https://github.com/Oskar-Stachowski/retailops-ai-intelligence/actions/runs/37594406356)
  na `e49969c` zakończył się **success**. Zweryfikował pełne 664 archive files,
  niezmienione bytes i semantykę oryginalnym frozen wheel oraz dokładną decyzję
  właściciela. Zachowano oryginalne `not_ready` i zero refitów. Tymczasowy sekret
  oraz dokładnie sześć własnych obiektów zostały usunięte; oryginalnego S3
  archiwum nie zmieniono. Serving i Source API/UI v12 nadal nie są odebrane.
- Source `6fa6e00` dodaje rzeczywisty capture opt-in `daily_demand_versions`
  pod SQL authority barrier i z pełnym prefixem brokera, razem z retry
  nieobjętym SQL delivery receipt. **8** negatywnych/jednostkowych testów,
  mypy **43** modułów, Ruff/format i secret scan **0 findings** passed.
  Lokalny niezależny odbiorca AI potwierdził seal 2 facts / 3 receipts,
  capture + overlap = full replay oraz późną korektę 4 -> 7. To lokalny test
  wygenerowanych facts. Dedykowany `AI10 Source capture handoff` wymaga
  realnego SQL/TLS/SCRAM wykonania Source i dopiero potem odbioru jego
  oryginalnych bytes przez AI. Pełny 43-table SQL capture i AI SQL/ACK tego
  handoffu nie są tu poświadczone.

### Co dokładnie pozostaje do ready AI 10

1. **Zachować odebrany qualified stockout i domknąć oryginalny publisher.**
   Nowa kwalifikacja, frozen cold worker, cały batch i Source broker/SQL/API/UI
   passed. Nie powtarzać treningu ani ocen końcowych. Osobno pozostaje dowód
   wysyłki z oryginalnej bazy AI oraz integracja w pełnym bounded E2E.
2. **Domknąć publisher oryginalnej bazy anomaly.** Pełne 1232 native wyniki
   i 25 stron built UI są odebrane w `37594406280`; nowy workflow
   `37606671405` wykonuje oryginalny SQL publisher przed cleanupem.
3. **Domknąć rzeczywisty forecast v12 -> Source -> UI na temporalnych wejściach.**
   Zachować zatwierdzony development scope i oryginalne quality `not_ready`.
   Oryginalny pełny eksport ma 664 pliki, 31 994 707 803 bytes i jest
   zachowany w istniejącym S3. Odbiór wymaga sprawdzonego pełnego artefaktu
   i frozen wheel, bez podmiany na fixture oraz bez reklasyfikacji jakości.
4. **Domknąć Source snapshot/capture i trusted replay handoff.** Pełny
   43-table immutable file bundle działa; nie tworzy live SQL snapshotu
   z kompletnym wektorem granic. Obecny stream/replay obsługuje wyłącznie
   `daily_demand_versions`. Odbiór musi wiązać rzeczywisty broker prefix,
   wszystkie granice partycji, included facts i korekty; receipts publishera
   nie wystarczają jako boundary vector. Source capture i niezależny
   in-memory replay passed; nowy workflow wymaga także rzeczywistych AI
   SQL/ACK i tego samego brokera. Pełny 43-table SQL snapshot pozostaje
   jawnie niewspierany; pełny immutable file bundle jest osobną działającą ścieżką.
5. **Zebrać raport wspólnego bounded E2E i awarii na 102 dniach.** Porównać
   oryginalne IDs/payloads, sumy snapshot+overlap replay, deduplikację,
   późne korekty, DB/ACK/SIGKILL/DLQ, auth i degraded broker. Sugestie mogą
   pozostać jawnie oznaczonym fixture zgodnie z planem AI 10.
6. **Zaliczyć finalne Required CI obu dokładnych headów i opublikować main.**
   Uaktualnić README, stage registry, dokumentację, receipts i closure
   checklist. Następnie zwykły protected merge obu PR-ów i weryfikacja
   `origin/main`; starsze draft PR-y zamknąć dopiero po zachowaniu zmian.

Nowy workflow oraz przykłady nie oznaczają jeszcze `ready`. Szczegóły
uruchomienia i granice dowodów: [native models v2](intelligence-models-v2.md).

## Historia wcześniejszych przyrostów


Aktualizacja: 2026-10-04, 20:47 CEST / 18:47 UTC. Status całego etapu: **in_progress**.
Pierwszy spójny przyrost jest zapisany i opublikowany jako dwa draft PR-y:

| Repo | PR | Branch | Commit |
|---|---|---|---|
| AI Intelligence | [#16](https://github.com/Oskar-Stachowski/retailops-ai-intelligence/pull/16) | `ai/10-intelligence-contract` | `8406ff2c6ed416773ccba2283527470f79c5b8d5` |
| RetailOps | [#84](https://github.com/Oskar-Stachowski/retailops-cloud-native-platform/pull/84) | `ai/10-intelligence-projection` | `a6f706782295c702574e6e6f1efac31b50a35801` |

## Drugi przyrost — bounded source REST

Dwa kolejne draft PR-y są ułożone nad pierwszym przyrostem:

| Repo | PR | Branch | Commit |
|---|---|---|---|
| AI Intelligence | [#17](https://github.com/Oskar-Stachowski/retailops-ai-intelligence/pull/17) | `ai/10-source-rest` | `3a64b03ac707a7bc041bef4ad80e451c9ff8e49a` |
| RetailOps | [#86](https://github.com/Oskar-Stachowski/retailops-cloud-native-platform/pull/86) | `ai/10-source-read-gateway` | `a8fc7d6d28191b911efb98df3e0c68c63f07e38e` |

- RetailOps: osobne prywatne credentials i granty zasobów/product/channel/warehouse;
  chronione `/integration/v2` dla pięciu zasobów, wymagany produkt i bounded
  okresy/strony, read-only repeatable-read jednej strony i timeout DB.
- AI: typowany klient i query wygenerowane z faktycznego przypiętego OpenAPI,
  sprawdzanie digestu, worker HTTP z hard deadline, retry/breaker/trace,
  walidacja paginacji/scope oraz prywatny CLI bez nadpisywania plików.
- Metadata oddziela fetch/business time i oznacza znaczenie legacy forecast/risk.
  Bez watermarku świeży odczyt ma `unknown`; pusta strona `missing`, stara `stale`.
- Jawnie brak pełnego sales ML grain i immutable snapshot/replay handoff.
  Te REST strony nie zastępują historycznego importu AI 03.
- Pin OpenAPI: owner `271ad966b32879dc4ef583b6bee1abf19ae0c1cf`,
  SHA-256 `400d0685af483eb7a585311589144d69bbcf901f0c37264fce52ddddba45548c`.
  Source CI przypina finalny klient AI, bez wzajemnego cyklu SHA.
- Lokalnie: **33/33 testów HTTP** klienta passed, **39/39 gateway** passed,
  powiązane regresje passed, Ruff i mypy (333/15 modułów) passed,
  kontrakty/docs/wheel/sdist passed. Kampania v12 z dwóch rzeczywistych venv
  ma identyczny pin `8f12dc3744880f1b2a68b4a009640dce4dcf543d8b3396c038bc175c7e3ee011`.
- [Required CI AI](https://github.com/Oskar-Stachowski/retailops-ai-intelligence/actions/runs/37185276731)
  i [Required CI RetailOps](https://github.com/Oskar-Stachowski/retailops-cloud-native-platform/actions/runs/37185287151)
  mają stan: **success na obu dokładnych commitach; AI 4/4 jobs, RetailOps 26/26 jobs**.
  Rzeczywisty przypięty klient przez HTTP/API/PostgreSQL, 125 wierszy i pięć
  zasobów: passed. API: **811 passed**, coverage **86,52%**, 838,76 s.
  Obraz API, Compose/Kubernetes runtime i rollback również passed.
  AI: **1777 passed, 1 skipped**, 2269,24 s. Pominięty outbox przeszedł
  obowiązkowo osobno w persistence: **1/1 passed**, delivery/contract **13/13**.
  Wszystkie checkery, wheel/sdist i backup/restore/recovery v12 passed.
  [Końcowy zapis drugiego CI](ai10-rest-ci-receipt.json) zawiera dokładne SHA,
  run/job IDs, liczby, checksumy i zakres jawnego fixture.
  Lokalny Docker pozostał wyłączony, runtime AI 07/08 nie zmieniono.

Runbooki: [AI](https://github.com/Oskar-Stachowski/retailops-ai-intelligence/blob/3a64b03ac707a7bc041bef4ad80e451c9ff8e49a/docs/source-rest-v2.md),
[RetailOps](https://github.com/Oskar-Stachowski/retailops-cloud-native-platform/blob/a8fc7d6d28191b911efb98df3e0c68c63f07e38e/docs/runbooks/source-reads-v2.md).

### Zależności sprawdzone bez ingerencji w sesje

Na odczytanych lokalnych refach stan modeli nadal nie pozwala odebrać całego AI 10:

- AI 07 `502c3bcd42a7f92b080c5bdb5067e9e3fd3e71e3`:
  [odbiór pełnego DQ intake](https://github.com/Oskar-Stachowski/retailops-ai-intelligence/blob/502c3bcd42a7f92b080c5bdb5067e9e3fd3e71e3/docs/evidence/ai/07/07.5-full-dq-consumer/README.md)
  pozostawia model readiness `not_qualified`; wymagane są kwalifikacja dni,
  scoring/evaluation oraz anomaly lifecycle/batch/read serving.
- AI 08 `b74fa9a4e728cdb732e6da8d393c80ed29829642` (odczyt 2026-10-04):
  [odbiór indeksu historii](https://github.com/Oskar-Stachowski/retailops-ai-intelligence/blob/b74fa9a4e728cdb732e6da8d393c80ed29829642/docs/evidence/08-08-stockout-history-index.md)
  zachowuje wszystkie 1632 punkty i sześć development modeli, ale cały etap
  nadal `not ready`; większy profil, final test, progi, promocja i serving pozostają.
  Brak zmian w AI 05/v12 według odbioru właściciela.

Nie należy podmieniać tych wyników legacy forecast/risk ani publikować nowego
schematu modelowego jako uzgodnionego bez kontraktu jego właściciela. Następny
PR projekcji anomaly/stockout ma przypiąć dokładne finalne schematy i wyniki.

## Trzeci przyrost — checkpointy i fencing

[Draft PR RetailOps #87](https://github.com/Oskar-Stachowski/retailops-cloud-native-platform/pull/87)
jest ułożony nad #86, na `ai/10-intelligence-checkpoints`, commit
`c71dfea17348fee115e0fe8e38e4a42679ab165b`.

- Osobny serial consumer: transakcja projekcji lub raw kwarantanny, inbox,
  transport receipt i cursor partycji; synchroniczny ACK dopiero po DB commit.
- Owner/epoch z blokadą wiersza; nowy claim blokuje starego workera, a jego
  spóźniony release nie usuwa nowego ownera. Replay weryfikuje niezmienne raw,
  key, nagłówki (włącznie z powtarzającymi się nazwami) i timestamp.
- Przypięty cluster/topic UUID, jawny retained-log bootstrap, fail-stop przy
  retention gap, cofnięciu logu, luce lub broker commit wyprzedzającym bazę.
- Prywatny plik konfiguracji 0600, TLS/SASL, osobna grupa; status read-only
  opisuje transport, nie obiecuje business freshness ani liveness ownera.
- Wsparcie ograniczone do delete-only, nietransakcyjnego, ciągłego logu.
  Luki control/aborted records są fail-stop. Snapshot handoff nadal unsupported.
- Migracja `a10f0c7e0300` dodaje checkpoint/transport tables. Plan rollbacku
  przypina pełne historie i oba pliki nowych migracji. Compose/Kubernetes
  sprawdzają zachowanie konkretnych seeded checkpoint/transport rows.
- Lokalnie **43 nowych + 22 istniejące testy passed**, **10 rollback tests passed**;
  Ruff/format, mypy **18 modułów** i source OpenAPI check passed.
  **23 realne DB/broker testy nieuruchomione lokalnie** z powodu wyłączonego Docker;
  obowiązkowo są wykonywane w CI z `REQUIRE_BROKER_TESTS=1`.
- Pierwsza próba [Required CI](https://github.com/Oskar-Stachowski/retailops-cloud-native-platform/actions/runs/37189601697)
  ujawniła brak cluster ID w buforowanej metadata SDK: **850 passed, 22 failed**.
  Wszystkie nowe przypadki zatrzymały się przed przetwarzaniem checkpointów.
  Poprawiono jawny DescribeCluster i dodano regresję tej granicy.
  Realny checkpoint drill jest teraz przed szerokimi testami; pełna regresja
  scala coverage obu zestawów i nadal wymaga 80%.
  Compose/Kubernetes oraz 6 snapshots zachowały wszystkie nowe seeded rows.
  Druga próba potwierdziła realną tożsamość streamu i zapis na dwóch partycjach,
  lecz restart po SIGKILL przekroczył 45-sekundowy limit przez timeout sesji.
  Ustawiono session timeout 10 s / heartbeat 3 s, zachowując limit testu.
  Końcowe [Required CI](https://github.com/Oskar-Stachowski/retailops-cloud-native-platform/actions/runs/37190997576)
  jest **success, 26/26 jobs** na `c71dfea17348fee115e0fe8e38e4a42679ab165b`.
  **23/23 realnych checkpoint drills passed w 36,82 s** oraz **854 testy
  szerokiej regresji passed w 939,93 s**: **877 różnych testów**, coverage
  **86,65%** przy progu 80%. Wszystkie 238 plików statycznych formatted,
  mypy 18 modułów i source OpenAPI passed. Obraz API również passed.
  Compose/Kubernetes ponownie zachowały konkretne seeded rows w 3/6 snapshots,
  cleanup passed. Odciski ZIP/reportów i jednakowe drzewo technicznego merge
  z gałęzią przypina [końcowy zapis dowodów](ai10-checkpoint-ci-receipt.json).

[Runbook przyrostu](https://github.com/Oskar-Stachowski/retailops-cloud-native-platform/blob/c71dfea17348fee115e0fe8e38e4a42679ab165b/docs/runbooks/intelligence-checkpoints.md).
Nie zmieniono głównych checkoutów, AI 07/08, środowiska/deps AI ani kontraktu REST.

## Czwarty przyrost — aktywna publikacja z prywatnym review właściciela

[Draft PR RetailOps #88](https://github.com/Oskar-Stachowski/retailops-cloud-native-platform/pull/88)
jest ułożony nad #87, branch `ai/10-approved-forecast-head`, commit
`ef8c29e4c90541671762504af071d9d97e9aeabb`.

- Uwierzytelniony `/intelligence/v2/forecasts/active`: jawny wybór kompletnej
  publikacji, release/run/as-of/output, pełny grain i wszystkie checksumy
  sprawdzane przed grantami/paginacją. Replay lub nowsza dostawa nie zmienia head.
- Prywatna polityka operatora oddzielona od grantów odczytu; CLI wymaga
  pełnego istniejącego raportu AI lifecycle `review`, zgodności release/model/
  approval/champion, braku odrzucenia i pending decyzji. Mechanics/development
  są odrzucane. Raport ma pin pliku właściciela z faktycznego AI checkoutu.
- Zaufanie to prywatny handoff operatora, **nie podpis ani live check MLflow**.
  Przygotowanie zachowuje pierwotny czas przeglądu; wybór ważny najwyżej 15 min
  i przed końcem ML approval. Odnowienie wymaga nowego owner review.
  Odpowiedź jawnie podaje `approval_authority=private_operator_selection`.
- Brak/niekompletna dostawa, korupcja poza stroną, przyszły/wygasły review
  blokują aktywny widok. Reload każdej polityki umożliwia wycofanie; zmiana
  wyboru unieważnia hash paginacji. Historia i freshness nie są odnawiane.
- Maksymalnie 32 niezależne publikacje/2800 wierszy/16 MiB, preflight byte
  budget, read-only repeatable-read i timeout SQL 3 s. Prywatne pliki 0600,
  bez symlink/FIFO/duplikatów JSON; CLI tworzy atomowo nowy plik, nie aktywuje go.
- **104 lokalne testy passed**, w tym **44 nowe** i regresje kontraktu/runnera;
  rzeczywisty CLI także **5/5** z katalogu roboczego CI. Ruff i format
  **242 pliki**, mypy **22 moduły**, Bandit i executable OpenAPI passed.
- Pierwszy API CI potwierdził **23 checkpoint tests passed** oraz prawidłowy
  429 przy byte budget. Test oczekiwał błędnego formatu `detail`, podczas gdy
  istniejące API zwraca `error.message`; poprawiono asercje i dodano offline
  regresję HTTP. Runtime zachowuje dotychczasowy kontrakt błędów.
- [Required CI](https://github.com/Oskar-Stachowski/retailops-cloud-native-platform/actions/runs/37196137295)
  jest **success, 26/26 jobs** na końcowym commicie. **35/35 realnych drills
  passed** (23 checkpointy + 12 nowe DB/API/broker head) w 45,91 s;
  **898 testów szerokiej regresji passed** w 953,55 s. Łącznie **933 różne
  testy API**, zero skipped i coverage **87,09%** przy wymaganych 80%.
  Ruff/format 242 pliki, mypy 22 moduły, kontrakt właściciela, OpenAPI oraz
  końcowy obraz API passed. AI 03/06, security i cały Required CI również passed.
  Compose/Kubernetes i cleanup passed; zweryfikowane ZIP/report checksumy
  potwierdzają zachowanie wszystkich czterech niepustych tabel AI10 w 3/6
  snapshots. Ogólny recovery drill passed, lecz jego AI10 tabele są puste —
  dowód niepustych danych pochodzi z release/Kubernetes, nie tego recovery.
  [Końcowy zapis dowodów](ai10-approved-head-ci-receipt.json) przypina run/job IDs,
  dokładne SHA i jednakowe drzewo technicznego merge oraz gałęzi.
- Brak migracji i zmian AI core/deps; oba główne checkouty i sesje AI 07/08
  pozostają bez zmian. Rzeczywisty model nie jest kwalifikowany fixture testów.

[Runbook wyboru, aktywacji i wycofania](https://github.com/Oskar-Stachowski/retailops-cloud-native-platform/blob/ef8c29e4c90541671762504af071d9d97e9aeabb/docs/runbooks/intelligence-approved-head.md).
Automatyczna dystrybucja unieważnień właściciela, shared broker/DB/auth oraz
input snapshot/replay pozostają osobnymi zadaniami; ten przyrost nie zamyka AI 10.

## Pierwszy przyrost — zaimplementowane

1. `forecast_generated` na osobnym topicu v2, z pełnym oryginalnym payloadem
   ML i lineage. Pozostałe typy wymagają schematów właścicieli AI 07/08.
2. Atomowy output/outbox po stronie AI i bounded worker dostarczenia.
   Brak receipt lub crash zachowuje pending; retry używa tych samych IDs.
3. Dedykowany consumer RetailOps, transakcyjny inbox/projekcja, manual ACK,
   trwała kwarantanna i replay bez podwójnego efektu domenowego.
4. Uwierzytelnione API historii prognoz z grantami product/location/channel/
   release, stabilnym widokiem paginacji i osobną oceną freshness odczytu.
5. Osobne środowisko klienta brokera. Główny lock ML i pin kampanii v12
   pozostają identyczne; guard odrzuca drift bibliotek i lockfile.
6. Checksum-pinned plan addytywnej migracji RetailOps. Próby rollbacku mają
   zachować nowe tabele, wynik i inbox oraz sprawdzić starszą i nową aplikację.
   Inne historie/revisions nadal blokują rollback.
7. Runbooki uruchomienia, awarii i wycofania oraz obowiązkowe testy CI.

## Pierwszy przyrost — weryfikacja

- AI: końcowa zdalna pełna regresja **1744 passed, 1 skipped** (28 min 35 s).
  Pominięty test outbox jest obowiązkowo wykonywany w osobnym jobie persistence:
  rzeczywisty PostgreSQL drill **1/1 passed**, kontrakty i delivery guard **13/13 passed**.
  Wszystkie kontrole statyczne, offline checkery, wheel/sdist i secret scan passed.
- RetailOps: 697 testów API plus 5/5 ponowień zależnych od Dockera passed;
  11/11 rzeczywistych DB/broker/API drills passed; końcowa regresja 56/56,
  kontrakty/pin 17/17 i testy guardów release 25/25 passed.
- Pin kampanii v12 identyczny w dwóch rzeczywistych venv. Kafka jest obecna
  wyłącznie w środowisku delivery.
- Początkowe zdalne API CI RetailOps: 771/771 passed, coverage 85,40%.
  Dwa drille ujawniły potrzebę
  jawnego planu nowej migracji; poprawka jest w końcowym commicie.
- Poprawione zdalne drille Compose i Kubernetes przeszły na końcowym
  commicie RetailOps, zachowując rozszerzony schemat i dane.
- Bieżące [Required CI RetailOps](https://github.com/Oskar-Stachowski/retailops-cloud-native-platform/actions/runs/37153842595)
  na `a6f706782295c702574e6e6f1efac31b50a35801`: **success**, wszystkie 26 jobs.
  API: 771/771 passed, coverage 85,40%. AI 06: profile 30 i 102 dni przeszły
  po dwa razy. Observability CI tego commitu również passed.
- Bieżące [Required CI AI](https://github.com/Oskar-Stachowski/retailops-ai-intelligence/actions/runs/37153392494)
  na `8406ff2c6ed416773ccba2283527470f79c5b8d5`: **success**, wszystkie 4 jobs.
  Pełna bramka persistence obejmuje także backup/restore obu baz, wszystkich
  sekwencji i artefaktów v12 oraz recovery.
- [Zapis CI](ai10-ci-receipt.json) zawiera dokładne commity, run/job IDs, wyniki
  i hashe kontraktu. Ten przyrost jest gotowy do przeglądu; nie oznacza to
  odbioru całego AI 10.

## Instrukcje i dowody

- [Runbook AI](https://github.com/Oskar-Stachowski/retailops-ai-intelligence/blob/8406ff2c6ed416773ccba2283527470f79c5b8d5/docs/intelligence-integration-v2.md)
  i [dowód AI](https://github.com/Oskar-Stachowski/retailops-ai-intelligence/blob/8406ff2c6ed416773ccba2283527470f79c5b8d5/docs/evidence/10-forecast-integration.md).
- [Runbook RetailOps](https://github.com/Oskar-Stachowski/retailops-cloud-native-platform/blob/a6f706782295c702574e6e6f1efac31b50a35801/docs/runbooks/intelligence-v2.md)
  i [plan rollbacku](https://github.com/Oskar-Stachowski/retailops-cloud-native-platform/blob/a6f706782295c702574e6e6f1efac31b50a35801/docs/runbooks/application-rollback.md).

## Piąty przyrost — uwierzytelniony wspólny broker

[Draft PR RetailOps #89](https://github.com/Oskar-Stachowski/retailops-cloud-native-platform/pull/89)
jest ułożony nad #88, na `ai/10-shared-intelligence-runtime`; końcowy commit
`318d3704dc757390226066a197f6bee95571d31d`.
[Required CI 37204598373](https://github.com/Oskar-Stachowski/retailops-cloud-native-platform/actions/runs/37204598373):
**27/27 jobów passed**, w tym mandatory runtime, API, Compose update/rollback,
Kubernetes persistence/rollback, dane, UI build i security.

- Własny disposable Compose bez host ports ani cudzych sieci: dwie oddzielne
  instancje PostgreSQL, osobne DB/role/sieci baz, wspólny prywatny broker.
- Kafka/Admin TLS z weryfikacją CA od startu, SCRAM-SHA-256 i literal ACL;
  auto topic creation wyłączone. Oddzielne producer/consumer identity oraz
  source/intelligence HTTP tokens. Runtime role mają ograniczone table grants.
- Faktyczny przypięty worker outbox AI jest w osobnym obrazie z istniejącym
  delivery lock, owner `3a64b03ac707a7bc041bef4ad80e451c9ff8e49a`.
  AI code/main lock/ML dependencies i sesje 07/08 pozostają bez zmian.
- Runtime `111443162184`, **82,961 s**, passed: dokładny event/payload,
  receipt `projected@0:0`, SIGKILL własnego konsumenta, jawna symulacja resetu
  receipt po broker ACK, redelivery przez niezmieniony AI CLI i restart Source.
  Końcowo `duplicate@0:1`, checkpoint partycji 0 = 2, dwie pozostałe = 0;
  **jeden wynik, jeden inbox, trzy checkpointy**. Event/payload SHA-256 przed
  i po restarcie są identyczne. Cleanup wszystkich własnych zasobów passed.
- **16 jawnych odmów auth/ACL/DB privilege**: producer READ, consumer WRITE,
  obca grupa/topic, CREATE topic, złe hasło, obca CA, anonymous Kafka/Admin,
  workload Admin, obce DB credentials, CREATE ROLE i DELETE przez API reader.
  HTTP sprawdza trzy odmowy tokenów/anonymous, active head `503` i zachowanie
  oryginalnej freshness. Fixture nie aktywowała production head.
- Artifact `11304730545`: ZIP SHA-256
  `a96f180a90e12f3ad728807de517f542a6ead298df49e830f2e425371f637d75`,
  report SHA-256 `ef1d7234916601392a8743fb38a0b289a522afa55347655c261ddabff1318e98`.
  Pobrane bajty zweryfikowano; raport jest osadzony w receipt.
- Source API `111443162186`: **35 real durability tests / 45,47 s** oraz
  **900 broad regression tests / 940,80 s**, razem **935 różnych testów**,
  bez skipped, coverage **87,09%**. Osobny assessment subset 21 tests jest
  już zawarty w broad total, nie jest doliczany drugi raz.
- CI mypy: **22 moduły Source + 3 pliki runtime**. Contract gate:
  **16 change detector, 11 immutable input, 5 controller tests** passed.
  Lokalnie także **62 checkpoint/contract tests**, Ruff/format i inventory
  **188 referencji**; resolved Compose/private boundaries bez startu Docker.
- Rzeczywisty SDK wykazał błąd checkpoint identity: topic UUID używa standardowego
  Base64 z `+` i `/`. Naprawiono akceptację tych znaków bez zmiany zapisanych
  identyfikatorów; pusty/zerowy ID nadal odrzucany. Dwa nowe testy używają
  rzeczywistych `confluent_kafka.Uuid`. Kontener uruchamia Source CLI jako moduł,
  dzięki czemu import aplikacji działa bez hostowego `PYTHONPATH`.
- CI użyło technicznego merge `ea01346fd4c045e0a4f3213eed15d8b05c92cf81`,
  rodzice `[ef8c29e4c90541671762504af071d9d97e9aeabb, 318d3704dc757390226066a197f6bee95571d31d]`.
  Tree `7d77246d72763230beaf76bd99398d2420fb7603` jest identyczne z head PR.
- Wcześniejsze nieudane próby odkryły brak dev-only httpx w obrazie, wymagany
  zapisywalny katalog bootstrap brokera, nieprawidłowe oczekiwanie Admin 401,
  SDK ACL resource BROKER, odrzucane UUID, import Source CLI i zachowanie
  Consumer przy odmowie grupy. Wszystkie **9 nieudanych prób runtime** i ich
  zweryfikowane raporty pozostają w receipt; żadna nie jest zaliczeniem odbioru.

AI publication parent jest jawnym **storage double**, a model forecast to
`retailops-demand-forecast-v12-mechanics`. Rzeczywiste są outbox migration/worker
oraz Source migrations/checkpoint/projection/HTTP. Ten odbiór nie dowodzi pełnej
AI DB/publication, ML qualification/promotion, anomaly/stockout, source snapshot/
replay, supervision/revocation, istniejącego UI ani three-model 102-day E2E.
**Cały AI 10 pozostaje in_progress.**

[Runbook przyrostu](https://github.com/Oskar-Stachowski/retailops-cloud-native-platform/blob/318d3704dc757390226066a197f6bee95571d31d/docs/runbooks/intelligence-shared-runtime.md),
[zapis dowodów](ai10-runtime-ci-receipt.json), własny worktree
`/private/tmp/retailops-ai10-runtime`.

Odczyt zapisanych refów właścicieli 2026-10-04 nie zmienił gotowości całego AI 10:
AI 07 `1a1916630fa3ed6601a27fec5b5feaf480fc9b1b` domyka day qualification, ale
jego README nadal wymaga residual features, porównania baseline/Isolation Forest,
evaluation i lifecycle, model `not_qualified`. AI 08
`077add2c2419c0bf5827be8a7f77a897507485c2` dodaje bounded upstream partitions;
comparison/split/training, niezależna ocena/progi oraz lifecycle/batch/read nadal
pozostają. Był to wyłącznie odczyt zapisanych commitów, bez ingerencji w sesje.

## Kolejność dalszej pracy

1. Przejrzeć draft PR-y #16/#84 i #17/#86 (Required CI passed), a następnie
   source #87 z checkpointami (Required CI 26/26 passed), potem #88 z aktywnym
   wyborem publikacji (Required CI 26/26 passed) i #89 z uwierzytelnionym
   runtime (Required CI 27/27 passed). Wszystkie siedem draft PR-ów jest
   gotowych do przeglądu w kolejności stosu.
2. Przypiąć zatwierdzone publiczne schematy i rzeczywiste wyniki AI 07/08;
   rozszerzyć generację/projekcję o anomaly i stockout, bez wymyślania pól ML.
3. Po odebranym bounded typed REST (#17/#86, oba Required CI passed) wdrożyć
   wersjonowany eksport z pełnym grain i mapping, snapshot/log checkpoint
   handoff oraz korekty/replay bez luki. Bounded live reads same nie domykają eksportu.
4. Po #88 i odbiorze #89 rozszerzyć izolowany transport z osobnymi DB/auth
   o pełny AI database/publication runtime, supervision i automatyczny owner
   revocation handoff. Test #89 obejmuje jawny storage double AI publication,
   rzeczywisty outbox i Source; nie zastępuje pełnego ML runtime.
   Checkpoint/fencing strumienia wyników jest wdrożony w #87;
   prywatne metadane transportu nie zastępują freshness
   biznesowej ani monitoringu liveness. Nie wywodzić approval z grantów czy
   czasu przyjęcia wyniku. Granica eksportu źródłowych faktów pozostaje osobna.
5. Podłączyć trzy rzeczywiste wyniki do istniejącego UI; sugestie pozostają
   jawnie fixture. Zachować stany no-data/stale/error.
6. Wykonać pełne cross-repo E2E na profilu 102 dni i dopiero wtedy oceniać
   Definition of Done całego AI 10.

## Projekt dalszego przyrostu — pełny eksport i snapshot/replay

Poniższe kroki są projektem dalszej implementacji, nie istniejącym wsparciem
obecnych endpointów. Punktem wyjścia jest przypięty kontrakt AI 03; kontrakt
native 1.2 właściciela trzeba odczytać i przypiąć przed kolejną zmianą.

1. **Zamrozić kontrakty obu repo.** Wybrać dokładne source/snapshot/curated
   SHA i wersje oraz załączyć mapę rzeczywistych tabel, kluczy i availability.
   Zachować oddzielnie operational observations i simulation truth.
2. **Odtworzyć pełny grain ze źródła, nie z legacy DTO.** Obecne legacy SQL
   `/sales` nie dostarcza store/order/record version/ingested time. W już
   przypiętym snapshot contract 1.1 sales mają `order_reference` i `ingested_at`,
   orders mają `id`, `order_reference`, `store_id`, a order_items `order_id`
   i `product_id`. Sam join nie dowodzi jednoznacznego sale↔order_item.
   Korzystać z uzgodnionych native references i reguł curated; ambiguity ma
   zostać odrzucona/kwarantannowana, nie rozstrzygnięta losowo. Nie dopisywać
   sztucznych identyfikatorów do bounded-live response.
3. **Zachować historyczny mapping.** Selling location bierze się z jawnych
   channel assignments i store/channel, stock location z fulfillment routes
   obowiązujących i znanych w origin. Warehouse code nie jest tym mappingiem.
   Zachować jednostki opakowań, walutę i dokładność decimal z kontraktu.
4. **Wybrać rzeczywistą granicę eksportu.** Preferować utrwalony immutable
   eksport z manifestem i checksumami, wykorzystując istniejący AI 03 importer.
   Zwykłe live offset pages nie są źródłem snapshotu. Brak źródłowej historii,
   wersji lub spójnego capture oznacza jawne `unsupported`, tak jak dziś.
5. **Publikować atomowo i z limitami.** Prywatny staging, ograniczone części,
   recomputed physical/logical hashes i niezmienny snapshot ID; dopiero kompletny
   export staje się widoczny. Retry tego samego ID ma zweryfikować istniejące
   bajty. Konflikt nie nadpisuje eksportu; przerwanie nie publikuje połowy.
6. **Zaprojektować handoff w tej samej granicy capture.** Manifest ma wiązać
   dokładny topic/partition offset vector, included source event IDs/natural keys,
   record versions i zakres faktów. Offsety muszą pochodzić z tego samego
   trwałego capture, nie z odczytu brokera wykonanego po eksporcie. Jeśli źródło
   tego nie zapewnia, nadal nie ogłaszać snapshot/replay support.
7. **Oddzielić envelope dedup od tożsamości faktu.** Inbox event ID zapobiega
   ponownemu wykonaniu tej samej wiadomości. Drugi envelope tego samego faktu
   ma zostać rozpoznany przez źródłowy natural key/version. Późniejsza korekta
   zastępuje wkład poprzedniej wersji według polityki curated; nie dodaje obu.
8. **Nie przeskakiwać luki partycji.** Commit faktu/projekcji/inbox/outbox
   i checkpoint mają mieć ustaloną trwałą granicę. ACK dopiero po commit lub
   trwałej kwarantannie. DB/DLQ outage zatrzymuje daną partycję. Przy równoległości
   checkpoint advances tylko przez ciągłe ukończone offsety; fencing chroni
   przed starym workerem. Starszy replay nie nadpisuje aktywnego approved head.
9. **Odebrać mechanikę przed modelami.** Na własnej jednorazowej bazie i
   brokerze sprawdzić concurrent insert/update w czasie eksportu, snapshot plus
   overlap replay vs pełny przebieg, duplicate envelope, korekty, granice
   partycji, crash przed/po commit, niedostępną DLQ i resync z nowego snapshotu.
   Porównać konkretne natural keys, wersje i sumy, nie same liczniki odbioru.
10. **Zamknąć runtime i rollback.** Ewentualna addytywna migracja wymaga
    aktualizacji checksum-pinned rollback plan i prób zachowania nowych danych
    na obu wersjach aplikacji. Overlay używa osobnych baz/auth i wspólnego
    brokera; cleanup usuwa tylko własne zasoby. Nie uruchamiać cudzych stosów.
11. **Przypiąć modele i istniejący UI.** Po gotowości AI 07/08 wykorzystać
    publiczne finalne wyniki/schema, approved release/as-of/run i lineage.
    Sugestia pozostaje jawnie fixture etapu 12. UI zachowuje no-data/stale/error
    i nie przedstawia legacy heurystyki jako ML probability.
12. **Zaliczyć cały AI 10 dopiero na końcu.** Rzeczywiste three-model E2E na
    profilu 102 dni, konkretny trwały wynik w API i istniejącym UI, dowód
    braku utraty/podwojenia przy awariach, dokładne commity i Required CI.
    Fixture 30 dni kwalifikuje mechanikę, nie jakość lub gotowość modeli.

## Wznowienie pracy lokalnej

Prace są w worktree `/private/tmp/retailops-ai10-integration` i
`/private/tmp/retailops-ai10-platform` oraz, dla drugiego przyrostu,
`/private/tmp/retailops-ai10-rest` i `/private/tmp/retailops-ai10-source-rest`.
Trzeci przyrost jest w `/private/tmp/retailops-ai10-checkpoints`, czwarty w
`/private/tmp/retailops-ai10-head`, a piąty w `/private/tmp/retailops-ai10-runtime`.
Commitów nie utraci usunięcie tych
katalogów. Jeśli katalog zniknie, odtwórz tylko własny worktree z zapisanej
gałęzi przez `git worktree add --force PATH BRANCH`, a następnie wykonaj
bootstrap według runbooka. Nie przełączaj głównych checkoutów ani nie migruj
runtime AI 07/08. Dotychczasowych sesji, głównych branchy i stosów nie zmieniono.

## Szósty przyrost — pobranie niezmiennego natywnego eksportu

Status przyrostu: **zakończony i odebrany**, Required CI obu commitów success; cały AI10 nadal
`in_progress`. Klient: [draft AI #21](https://github.com/Oskar-Stachowski/retailops-ai-intelligence/pull/21),
`ai/10-source-bundle-client`, commit `3ae6ea7a5eff8355b14f1710fe00a34e05dac44f`.
Source: [draft #90](https://github.com/Oskar-Stachowski/retailops-cloud-native-platform/pull/90),
`ai/10-source-bundles`, commit `5ceb5f8c7c6ee27f3d38987d5cc698ffa380d5c8`.

- Source publikuje prywatną niezmienną listę bajtów istniejącego eksportu i
  udostępnia ją przez osobne chronione API, z jawnym grantem na cały bundle.
- AI pobiera komplet pod limitem procesu, sprawdza sumy i oryginalne IDs,
  a rzeczywisty importer sprawdza typy, logical hashes i kwalifikację zastosowania.
  Obsługa natywnego Snapshot 1.2 / Source 2.8 pochodzi z zatwierdzonego ownera
  `1a1916630fa3ed6601a27fec5b5feaf480fc9b1b`, bez zmian głównego `uv.lock`.
- Pełny lokalny odbiór 15:21–15:27 UTC: producer
  `dee564ef98dd7c0324dd2c88d418dc544e7ecdd6`, rzeczywiste API przez własny
  localhost HTTP, osobny minimalny locked receiver: **43 tabele / 31 623 wiersze /
  100 plików / 2 177 469 B**, import `published` i potem `reused`.
- Anonymous/wrong credential 401, obcy bundle 403, brak przydzielonego bundle 404,
  cross-scope REST 401, uszkodzony plik 503, odmowa importu bez katalogu wyniku,
  revocation na następnym request 401. Cleanup procesu, fixture i credentials passed.
- **17/17 Source + 11/11 nowych testów AI passed**; **23/23 regresji
  v12-development/handoff passed**. Ruff/format i mypy: 26 modułów API +
  1 drill / 341 modułów AI + 10 natywnych w odizolowanym namespace passed; kwalifikacja i wcześniejszy import 1.0 passed.
  Kontrola decyzji v12 zachowuje `not_ready`, 3 zaakceptowane odstępstwa i brak
  zgody na produkcję. Nie uruchomiono lokalnego Dockera ani cudzych sesji.
- Nowy obowiązkowy gate Source przypina dokładny klient AI i producer, generuje
  natywny fixture i wymaga rzeczywistych HTTP/import/reuse/negative checks.
  Zielony CI dotyczy dokładnych commitów; końcowy receipt
  jest zapisany w projekcie.

Runbook AI: `docs/source-bundles.md` na gałęzi klienta. Source:
`docs/runbooks/source-bundles.md` na gałęzi publikacji.

To dystrybucja istniejącego immutable eksportu, bez live SQL capture i bez
źródłowej granicy snapshot/offset/replay (`replay_handoff=false`). Dotychczasowe
capabilities bounded REST pozostają bez zmian. Fixture nie kwalifikuje modeli.
Pozostały zakres całego AI10: capture/replay, finalne modele 07/08, three-model
E2E i UI na wymaganym profilu oraz integracja przez chroniony main.

Worktrees szóstego przyrostu: `/private/tmp/retailops-ai10-bundles` i
`/private/tmp/retailops-ai10-bundle-client`; source producer jest osobnym
detached worktree `/private/tmp/retailops-ai10-native-owner`.

Po dodatkowej kontroli zgodności natywny importer wydzielono do osobnego
procesu. Wszystkie oryginalne moduły source_snapshot, forecasting, curated,
data_contracts oraz główny lock są identyczne z bazą #17. Pełny pin kampanii
v12 nadal wynosi `8f12dc3744880f1b2a68b4a009640dce4dcf543d8b3396c038bc175c7e3ee011`.
Powtórny pełny lokalny HTTP/import/reuse natywnego 1.2 w minimalnym środowisku
passed; receipt wiąże wszystkie 10 wykonywanych modułów z zatwierdzonym ownerem
(code SHA `c3ae15c2125a1794d2d02539831beb9b01500adca55c99b1debca81f528777f5`).
Obowiązkowy CI jest ponowiony na finalnej parze commitów powyżej.

Obowiązkowy natywny gate Source na finalnej parze commitów: **passed**
(Required CI `37214274334`, job `111471530916`, 15:47:31–15:52:29 UTC).
Artifact `11308225317`: ZIP SHA `fa6972c00b5c8f57e41cf7d9c7fb158ff5b7e9ac178f3afe888467e3dde44f2e`,
report SHA `35183c300d575bc73f34a736df0fb73c8fd5c1717e536b22abad51bad7bf2bb0`.
43 tabele / 31 623 wiersze / 100 plików, wszystkie negative checks i cleanup
passed. Techniczny merge CI `dadd6909bc17fa690222d96b25a44968222d9141` ma
identyczne drzewo `006e77dc22b0ceed05a77c1eca447cb34e29aa98` z head PR.

Odbiór z rzeczywiście zainstalowanego wheel (bez checkoutu na PYTHONPATH) także
passed: `published`, `reused`, pełne natywne 43 tabele, wszystkie 10 modułów i
7 assets w paczce. Wheel SHA `cddb6ac4aeac0f750752526b1f8b9ddee2e068a3f06837c31d31cfec8cda211b`.

Nowsze zapisane upstream dowody: AI07 `8581230b21e33a466c346b107ae936f0a98e3e08`
(07.7 qualified features) nadal `detector_readiness=not_qualified`; AI08
`df618620eb00050507c31e8e078b4543b3a2541f` (08.11 temporal storage) nadal
`not ready`. Odczytano tylko wersjonowane README przez `git show`.

### Końcowy odbiór szóstego przyrostu

- [AI Required CI](https://github.com/Oskar-Stachowski/retailops-ai-intelligence/actions/runs/37214213384):
  **4/4 jobs success**, **1788 passed / 1 skipped**, 1652,37 s. Pominięty
  outbox odebrano obowiązkowo osobno: **1/1 passed**, 12,84 s; delivery/contract
  **13/13 passed**, 12,16 s. Ruff, strict mypy 341 + 10 modułów, wszystkie
  kontrakty/checkery, wheel/sdist i trwały backup/restore/recovery v12 passed.
- [Source Required CI](https://github.com/Oskar-Stachowski/retailops-cloud-native-platform/actions/runs/37214274334):
  **28/28 jobs success**, **35 trwałości + 917 broad = 952 różnych testów
  passed / 0 skipped**, coverage **86,93%**. Osobny assessment 21 jest już
  zawarty w broad i nie powiększa tej sumy. Czasy: 43,30 s + 801,17 s.
  Natywny import, shared broker runtime, obrazy API/frontend, Compose,
  Kubernetes, security, recovery i rollback passed.
- Oba techniczne merge CI mają identyczne drzewa z headami PR; sprawdzono
  rodziców przez GitHub Git API. Source tree: `006e77dc22b0ceed05a77c1eca447cb34e29aa98`,
  AI tree: `54eff5ceff0715818bdfebf296b456dfe6bc00fd`.
- [Końcowy receipt](ai10-source-bundle-ci-receipt.json) zawiera run/job IDs,
  owner pins, oryginalne source/snapshot IDs, native import code checksum,
  wyniki auth/integrity/cleanup i zweryfikowane checksumy siedmiu archiwów CI.

Drafty [AI #21](https://github.com/Oskar-Stachowski/retailops-ai-intelligence/pull/21)
i [Source #90](https://github.com/Oskar-Stachowski/retailops-cloud-native-platform/pull/90)
są gotowe do przeglądu, ułożone odpowiednio nad #17 i #89. Nie wykonano merge
ani deploy. Chronione główne checkouty i sesje AI07/AI08 pozostały bez zmian.
Cały AI10 nadal `in_progress`: capture/replay, finalne modele 07/08 oraz
three-model UI E2E na profilu 102 dni wymagają dalszych odbiorów.

## Siódmy przyrost — prognozy AI w istniejącym frontendzie

[Draft PR RetailOps #91](https://github.com/Oskar-Stachowski/retailops-cloud-native-platform/pull/91)
jest ułożony nad #90, branch `ai/10-forecast-ui`, commit
`8d0a9fc402faa70a65bc1f5bebed3fdfc3703210`.

- Istniejąca strona Forecasts otrzymuje osobny panel dziennych prognoz AI,
  product/location/channel grain, mean/median, świeżość i odczyt lineage
  dokładnego wyniku. Domyślny widok wybiera kompletną publikację operatora;
  historia jest jawnie historyczna.
- Osobiste poświadczenie read-only jest tylko w pamięci karty do pięciu minut;
  znika przy rozłączeniu, ukryciu karty, wyjściu i zmianie użytkownika demo.
  Stały same-origin proxy, HTTPS poza loopback, brak redirectów/cookies/storage
  i anulowanie spóźnionego odczytu. Demo admin nie daje uprawnień AI.
- API odczytuje granty na nowo przy każdym żądaniu; cofnięcie poświadczenia
  działa bez restartu. Polityki odrzucają hardlink/FIFO/symlink oraz publiczne
  prawa. Odpowiedzi sukcesu/błędu AI mają `no-store`.
- Strony 50/20 z 70 wyników zachowują digest widoku; wymiana head daje 409.
  Wygaśnięcie wybranej publikacji usuwa wyświetlane wiersze. Bez wnioskowania
  procentu confidence ani stockout probability z reference interval.
- Lokalnie **42 frontend tests**, lint/build; **113 API/regression**,
  **18 detector tests / 3 subtests**, Ruff/format, mypy **27 API + 1 drill**
  oraz niezmieniony source OpenAPI: passed. Chromium z rzeczywistym built UI/
  API/auth i jawnym zastępnikiem projekcji w pamięci: passed; cleanup passed.
- Obowiązkowy job używa własnego PostgreSQL 16, migracji i aktualnego API,
  built frontend oraz Chromium. Pełny
  [Required CI](https://github.com/Oskar-Stachowski/retailops-cloud-native-platform/actions/runs/37218813073)
  ma stan **success, 29/29 jobs** na dokładnym końcowym commicie.
  Rzeczywista bramka PostgreSQL/API/Chromium: **1/1 passed**, 70 aktywnych wyników
  na stronach 50/20 oraz 77 rekordów historii w zakresie principal, bez 7 obcych rekordów.
  API: **35 real durability + 924 broad = 959 różnych testów**, zero skipped,
  coverage **87,05%** przy progu 80%. Czasy: 45,26 s / 895,92 s. Osobne 21 assessment
  jest już w broad; nie doliczamy ponownie. Frontend 42/42, lint/build/image
  passed. Cały runtime Compose/Kubernetes/rollback, import i security passed.
- [Receipt przyrostu](ai10-ui-ci-receipt.json) przypina commit, pliki oraz
  odczyt AI07 `56b5d24` i AI08 `92b8d5f`. Nadal wymagane są ich kwalifikacja,
  lifecycle/batch/read serving; snapshot/replay i trzy-model E2E pozostają.
  Fixture modelu/owner sprawdza mechanikę, nie jakość lub produkcyjny
  dostęp HTTPS/IdP/Nginx. Bez merge, deploy i nowej migracji.

[Runbook UI](https://github.com/Oskar-Stachowski/retailops-cloud-native-platform/blob/8d0a9fc402faa70a65bc1f5bebed3fdfc3703210/docs/runbooks/intelligence-forecast-ui.md).

Pierwsza próba CI potwierdziła nową bramkę UI z rzeczywistym PostgreSQL/API,
lecz zbieranie testów API zatrzymał błąd wcięcia. Poprawiono test bez zmiany
logiki; **46 durability tests zbiera się poprawnie lokalnie**. Pierwszy run
nie stanowi pełnego odbioru. Druga próba Required CI zakończyła się sukcesem.
Końcowy receipt weryfikuje 8 archiwów ZIP i 6 raportów, coverage XML oraz
screenshot UI. Techniczny merge `626c7eb54588fcd72c432dade3db2ca439f5f33f`
ma identyczne drzewo `63582815d7702bfe8a7a6f72170d782510475294` jak branch.
Główne checkouty zachowały HEAD/branch oraz pliki śledzone; lokalny Docker
pozostał wyłączony. Nie ingerowano w sesje AI07/08. Obecnie 10 draft PR-ów
przyrostów AI10 jest gotowych do review; bez merge/deploy.

Do pełnego AI10 nadal pozostają powiązany snapshot/offset handoff i replay
faktów z korektami, końcowe kwalifikowane anomaly/stockout lifecycle/serving,
projekcja i UI sugestii na jawnym requires_human_review fixture oraz rzeczywisty
E2E trzech modeli na 102 dniach. Ten odbiór nie zamyka tych wymagań.

## Ósmy przyrost — trwała projekcja sugestii i osobisty odczyt

[Draft RetailOps #92](https://github.com/Oskar-Stachowski/retailops-cloud-native-platform/pull/92),
branch `ai/10-suggestion-projection`, commit `090c26307fd508899ad6ef24504c151875db820b`, nad #91.

- Identyczne bajty kontraktów AI12 `a14b7899d9366c6ae555c0b6be4a8d027514ca91`,
  pin kodu identyfikacji/kanonikalizacji; adapter `fixture_only`, wyłączony domyślnie.
- Osobne niezmienne wyniki/inbox sugestii oraz atomowy checkpoint i receipt
  wskazujący dokładnie jeden zasób lub kwarantannę. Brak zmiany historycznych migracji.
- Osobiste `suggestion:read`, product/location/channel, grant polityki/configu
  i każdego model ref; API current/history/detail, czas bazy, pełny oryginalny
  payload, TTL do 300 sekund, digest widoku, no-store i natychmiastowa revocation.
  Bez endpointu wykonania/akceptacji; `requires_human_review=true`,
  `execution_authorized=false`.
- Compose/kind seedują oba niepuste rodzaje projekcji i receiptów, aby sprawdzić
  ich zachowanie przy rollbacku obrazu. Populowany downgrade schematu jest odrzucany.
- Lokalnie 164 testy regresji/kontraktów/auth oraz 25 rollback-plan tests passed;
  Ruff/format, 33 moduły mypy, owner checksums i niezmieniony source OpenAPI passed.
  Wszystkie 46 durability tests zbierają się poprawnie; 11 jest nowych.
- [Required CI](https://github.com/Oskar-Stachowski/retailops-cloud-native-platform/actions/runs/37224555721)
  **success, 29/29 jobs**, bez lokalnego Dockera. Nowe real PG/broker/API testy obejmują
  SIGKILL commit-before-ACK, atomowy rollback, kolizję/kwarantannę i jawny replay,
  wszystkie model refs, expiry/paginację, integrity, scope oraz downgrade guard.

[Receipt przyrostu](ai10-suggestion-ci-receipt.json) i
[runbook](https://github.com/Oskar-Stachowski/retailops-cloud-native-platform/blob/16a7e3226a20b8e063717ee4832faf8f290222bd/docs/runbooks/intelligence-suggestion-projection.md).

Ten przyrost nie stanowi dowodu rzeczywistego emitera outbox AI12 ani jakości
agenta/modeli. Pozostają UI sugestii, finalne 07/08, source capture/replay i
102-day three-model E2E. Cały AI10 nadal `in_progress`; bez merge/deploy.

Pierwszy run zatrzymał się przed testami API: lokalny commit AI12 nie jest
dostępny na GitHubie. CI w poprawce sprawdza checksumy zapisanych schematów
i envelope oraz mechanikę fixture; oryginalny kod ownera porównano lokalnie.
Nie stanowi to zdalnej kwalifikacji ani uruchomienia AI12. Automatyczny przegląd
odrzucił publikację archiwum prywatnego kodu AI12 w Source. Archiwum i kod
dowodu usunięto przed commit/push; bezpieczniejsza poprawka zawiera tylko
cztery pliki tekstowe, została zaakceptowana i opublikowana.

Druga próba: 26 required jobs passed, 46/46 real durability passed (82,21 s),
974 broad passed i 1 failed (758,64 s), coverage 87,61%. Błąd był w obserwacji
offsetów starszego strumienia zaraz po restarcie brokera (`NOT_COORDINATOR`).
Helper testu otrzymał read-only retry czterech błędów koordynatora do 10 s;
autoryzacja nie jest ponawiana. 12 testów helperów, w tym 6 nowych, passed.
Worker, ACK i fencing nie zmieniają logiki. Commit `090c263` uruchamia trzeci
odczyt Required CI. W drugiej próbie checksumy artefaktów i snapshoty potwierdziły
w Compose/kind zachowanie obu niepustych projekcji i receiptów bez downgrade/reseed.
Druga próba nie stanowi pełnego odbioru; końcowy odbiór poniżej zakończył się sukcesem.

### Końcowy odbiór ósmego przyrostu

[Required CI](https://github.com/Oskar-Stachowski/retailops-cloud-native-platform/actions/runs/37224555721)
na dokładnym końcowym commicie: **success, 29/29 jobs**. API: **46 real durability
+ 981 broad = 1027 różnych testów passed, zero failed/skipped**; coverage
**87,61%** przy progu 80%. Czasy: 72,04 s / 660,49 s. Osobne 21 assessment
są już w broad i nie powiększają sumy. JUnit zawiera dokładnie 11 nowych testów
sugestii, wszystkie passed. Ruff, format, mypy 33 moduły, źródłowy OpenAPI,
obrazy, frontend i security passed.

Zweryfikowano **9 archiwów ZIP**, raporty JSON, XML coverage/JUnit i checksum
screenshotu. Compose/kind zachowały identyczne snapshoty sześciu tabel
integracji przez upgrade/rollback: po jednym wyniku i inboxie forecast/suggestion,
checkpoint oraz dwa transport receipts. Cleanup obu odbiorów passed. UI prognoz
70 aktywnych / 77 historycznych / 7 obcych oraz pełny native import i uwierzytelniony
runtime mają `passed` na końcowym merge CI.

Merge CI `e36ab4e1f8d2c4c93e111cec9d55985f179b4ee7` ma rodziców końcowy head #92
i base #91 oraz identyczne drzewo `3a075dbd2b70857b253e93bde3aa361bbf2eee28`.
[Końcowy receipt](ai10-suggestion-ci-receipt.json) zapisuje job/run IDs, pin i
ograniczenie kwalifikacji ownera, migracje, oba nieudane podejścia oraz końcowy
wynik. Główne checkouty zachowały head/branch i pliki śledzone. Nie ingerowano
w sesje AI07/08/12; lokalny Docker nie był uruchamiany. **11 draftów AI10** jest
gotowych do przeglądu, bez merge/deploy.

Ósmy przyrost zakończony; cały AI10 nadal `in_progress`. Nadal potrzebne są
source SQL capture + offset handoff/replay korekt, finalne kwalifikowane 07/08,
rzeczywisty emiter outbox AI12 i UI przeglądu sugestii oraz 102-day three-model E2E.
Archiwum prywatnego kodu AI12 zostało odrzucone przez auto-review i usunięte przed
publikacją; bezpieczniejszy odbiór CI używa tylko zapisanych schematów i fixture,
a oryginalny kod właściciela porównano lokalnie.


## Dziewiąty przyrost — osobisty przegląd sugestii w UI

[Draft RetailOps #93](https://github.com/Oskar-Stachowski/retailops-cloud-native-platform/pull/93),
branch `ai/10-suggestion-ui`, head `0c3eb3fdb42e9ddd03554cdde33fe1df24dd3405`, nad #92.

Panel **AI suggestions** na istniejącej stronie Recommendations czyta osobiste
`suggestion:read`: bieżące sugestie / niezmienna historia, strony po 50 rekordów,
akcja/uzasadnienie i pełne dowody, trace/polityka/config/model refs oraz UTC expiry.
Każda sugestia wymaga human review; `execution_authorized=false`, bez przycisków
wykonania/akceptacji. Zegar oceny API minus czas żądania i monotonic timer nie
wydłużają ważności przez błędny zegar komputera. Expiry usuwa stronę i detail;
revocation, disconnect/demo/hidden/page exit usuwają pamięć i anulują żądania.

Lokalnie **49 frontend tests passed**, lint/build, Ruff/format oraz mypy dwóch
plików drill passed. Osobny Chrome + aktualne API/auth passed w 12,3 s z jawnym
plikowym dublerem projekcji; to nie dowód PostgreSQL. Własne procesy zatrzymano,
prywatne pliki usunięto. Nowa mandatory bramka wykona obydwa UI testy z rzeczywistą
bazą: 70 current / 72 history / 2 foreign, 50/20 stron, dokładny payload/evidence,
401/403/422/409, HTML refs jako tekst, anulowanie zakończonej odpowiedzi, live
revocation i ośmiosekundowy expiry przy zegarze cofniętym o osiem lat.

[Required CI](https://github.com/Oskar-Stachowski/retailops-cloud-native-platform/actions/runs/37227372186)
rozpoczęty; odbiór przyrostu pozostaje pending do ukończenia wymaganych bramek.
[Receipt](ai10-suggestion-ui-ci-receipt.json) i
[runbook](https://github.com/Oskar-Stachowski/retailops-cloud-native-platform/blob/0c3eb3fdb42e9ddd03554cdde33fe1df24dd3405/docs/runbooks/intelligence-suggestion-ui.md).
Bez nowej migracji, merge/deploy ani transferu prywatnego kodu AI12. Sesje AI07/08/12
pozostały nietknięte. Cały AI10 nadal `in_progress`: capture/replay, kwalifikowane
07/08, rzeczywisty emiter AI12 i 102-day three-model E2E pozostają do odbioru.


Pierwsza próba CI wykryła błąd montowania panelu: właściwe API zwróciło 200
z dokładnym payloadem, ale zakończenie starszego dashboardu usuwało stan AI
przez zmianę pozycji nieoznaczonego childa. Stały key zachowuje panel we wszystkich
trzech stanach strony. Test celowo wstrzymuje rzeczywistą odpowiedź dashboardu,
łączy AI i dopiero potem ją zwalnia; panel/dane muszą pozostać. Lokalny browser
passed w 11,8 s; lint/build passed. Pierwszy forecast UI test passed, sugestie
failed; pierwsza próba nie jest odbiorem. Zweryfikowano checksum artefaktu i logi.

Poprawiony head `25b76bc95d38d6d9ea4dbadd1e2710cdaaf462c1`, tree
`3ebef8f4df2b5d6c43205e5b502c0b716af2266f`, [Required CI 37227739990](https://github.com/Oskar-Stachowski/retailops-cloud-native-platform/actions/runs/37227739990)
rozpoczęty. Backend, migracje, transport i release-plan są identyczne z base #92.
Odbiór nadal pending; draft, bez merge/deploy.


Brama rzeczywistego PostgreSQL/API/Chromium na poprawionym merge zakończyła się
**success: 2/2 testy**, forecast 3,3 s, sugestie 12,4 s, łącznie 17,3 s.
Zweryfikowano ZIP raportu i obydwa screenshot SHA; cleanup serwerów i prywatnych
plików passed. Raport potwierdza 70 current / 72 history, full evidence, 401/403/
422/409, revocation, anulowanie odpowiedzi, utrzymanie stanu panelu po zakończeniu
dashboardu i ośmiosekundowe wygasanie przy błędnym zegarze komputera. Merge CI
`c9950cb83c94f5b5a7e65d484da5592f9b565fb7` ma rodziców base #92 i końcowy head #93
oraz to samo drzewo `3ebef8f4df2b5d6c43205e5b502c0b716af2266f`.
Pozostałe wymagane bramki jeszcze trwają; nie jest to pełny odbiór przyrostu.


### Końcowy odbiór dziewiątego przyrostu

[Required CI 37227739990](https://github.com/Oskar-Stachowski/retailops-cloud-native-platform/actions/runs/37227739990)
na końcowym headzie #93: **success, 29/29 jobs**. API: **46 real durability + 981
broad = 1027 różnych testów passed, zero failed/skipped**, coverage **87,61%**
przy progu 80%. Czasy: 76,89 s / 947,17 s. Osobne 21 assessment są już w broad;
JUnit potwierdza 11 testów sugestii. Każda z dwóch komend API zgłasza po jednym
ostrzeżeniu deprecacji przypiętego Starlette TestClient/httpx; żadnego błędu testu.
Nie zmieniano zależności w przyroście UI.

Frontend **49/49 tests passed**, lint/build/obraz oraz statyczne kontrole passed.
Rzeczywisty PostgreSQL/API/Chromium: **2/2 testy passed**, sugestie 12,4 s,
prognozy 3,3 s, łącznie 17,3 s. Dokładny payload i pełne dowody, osobne capability,
70 current / 72 history / 2 foreign, 50/20 stron, explicit stale/unknown,
401/403/422/409, live revocation, escaped refs, brak execution controls/storage,
late reply po disconnect, stabilne montowanie i expiry przy błędnym zegarze
mają potwierdzony odbiór. Synthetic hidden event sprawdza handler, nie cały
rzeczywisty lifecycle tła wszystkich przeglądarek. Prywatne pliki usunięto,
własne procesy zatrzymano.

Zweryfikowano **10 końcowych archiwów ZIP**, JSON/JUnit/coverage, oba screenshot
SHA oraz drzewo i rodziców technicznego merge CI. Native SourceSnapshot import
43 tabel / 31 623 wierszy i uwierzytelniony runtime passed. Compose/kind
zachowały identyczne SHA danych sześciu tabel i schemat przez upgrade/rollback,
w tym niepuste projekcje obu typów i dwa transport receipts; cleanup passed.
Backend, migracje, transport i release plan są niezmienione względem #92.

[Końcowy receipt](ai10-suggestion-ui-ci-receipt.json) zawiera job/run/artifact IDs,
SHA, poprawkę pierwszego nieudanego UI odbioru, wyniki i granice kwalifikacji.
[Draft #93](https://github.com/Oskar-Stachowski/retailops-cloud-native-platform/pull/93)
ma zaktualizowany opis oraz końcowy head `25b76bc95d38d6d9ea4dbadd1e2710cdaaf462c1`;
merge CI `c9950cb83c94f5b5a7e65d484da5592f9b565fb7` ma to samo drzewo
`3ebef8f4df2b5d6c43205e5b502c0b716af2266f`. Główne checkouty zachowały HEAD,
branch i pliki śledzone. **12 draftów AI10** jest gotowych do przeglądu.
Bez merge/deploy, lokalnego Dockera, ingerencji w sesje AI07/08/12 lub nowego
transferu prywatnego kodu AI12.

Dziewiąty przyrost zakończony; cały AI10 nadal `in_progress`. UI osobistego
przeglądu sugestii jest odebrane na jawnym fixture. Nadal pozostają source SQL
capture + offset handoff/replay korekt, kwalifikowane finalne 07/08, rzeczywisty
emiter outbox AI12 i 102-day three-model E2E. Odczytany bez zmian nowy AI08 head
`4faaf4b6c1997fda3a609645595165643bf302a9` łączy upstream 2.1 z treningiem,
lecz jego właściciel nadal oznacza cały AI08 jako not ready: większy profil,
niezależna jakość/calibration, karta nowych rodziców oraz lifecycle/batch/read API.
Odbiór UI nie zastępuje tych bramek ani kwalifikacji produkcyjnego HTTPS/IdP.


## Dziesiąty przyrost — odbiorca replay wersji obserwacji

Gałąź `ai/10-observation-replay`, head `34040095c0c7dfe3b6e8528b8846e2d19e6aa8cc`,
nad AI #21 (`3ae6ea7`). Nowy, ograniczony odbiorca natywnego
`daily_demand_versions` wiąże authority/cluster/topic ID, wszystkie partycje,
ciągłe offsets, niezmienne wersje i semantyczne receipts. Snapshot/capture plus
overlap oraz nowe fakty i korekta dają ten sam wynik co pełny przebieg. Korekta
zastępuje wkład, duplicate envelope nie dodaje wartości; as-of nie przecieka
przyszłej dostępności. Kolizje, luki, obce authority, regresja dostępności, brak
wersji i zmodyfikowany capture zatrzymują kandydata. Batch nie zmienia wejścia
przy błędzie. Nowe schematy są odrębne od SourceSnapshot.

Lokalnie 49/49 nowych testów passed, zero failed/skipped (24,22 s), pełny
Ruff/format, mypy 347 modułów, native typecheck 10 modułów, byte pins, schematy,
dokumentacja i wheel passed. Rzeczywisty immutable snapshot 1.0 i curation:
806/1612 wersji w capture, trzy partycje `[260,263,283]`, pełny replay z overlap
i nową korektą `[550,533,531]`, suma 11 047 → 11 050. Trzy odczyty as-of zgodne
z zamrożonym CuratedReader. Osobny lokalny proces zweryfikował pełny przypięty
SourceSnapshot 1.2 (43 tabele / 31 623 wiersze) i jego 1612 wersji; capture +
replay ma ten sam wynik co pełny przebieg. To inny fixture: końcowa suma 5569.

**Granica odbioru:** mechanika jednego odbiorcy w pamięci i proponowany protokół.
Nie ma jeszcze Source producenta/topicu ani odbioru SQL/ACK/DLQ/trwałego
checkpointu dla tego strumienia. Nie deklarujemy pełnego 43-table handoff,
kwalifikacji modeli ani live capture. REST/bundle nadal unsupported /
`replay_handoff=false`. Bez migracji lub zmian zamrożonych modułów/locków.
CI pozostaje pending; [receipt](ai10-observation-replay-ci-receipt.json).

**Korekta zakresu:** kanoniczny plan AI10 w Source #93 wyraźnie wymaga odbioru
sugestii na jawnym fixture; ten zakres jest zakończony w przyrostach 8–9.
Rzeczywisty producent/outbox agenta jest wymaganiem AI12, nie blokadą zamknięcia
AI10. Wcześniejsze wpisy `remaining`, które przypisały go do AI10, były błędne.
Pozostają source SQL capture/handoff, trwały odbiorca faktów, finalne 07/08
i rzeczywisty three-model E2E na 102 dniach. Cały AI10 nadal `in_progress`.


Dodatkowy przegląd wykrył brak związania liczby partycji z topologią odbiorcy:
ponownie zahashowany capture mógł pominąć pustą partycję. `restore` wymaga teraz
liczby partycji ze zaufanej konfiguracji; test usuwa pustą partycję, poprawia hash
i nadal wymaga odmowy/resync. 53/53 testy passed w 15,45 s, mypy/docs passed;
native 1.2 proof powtórzony na poprawce. [Draft AI #23](https://github.com/Oskar-Stachowski/retailops-ai-intelligence/pull/23),
końcowy head `b674f73c752f760449e69879168e58c09e128867`, tree
`b7ff72a039d63aab4c7b0f4130207140e7238d14`. Pierwszy run `37231463688` został
zastąpiony; odbiór końcowego headu nadal pending. Bez merge/deploy.


CI na `b674f73` odebrało pełne persistence, real PG/outbox oraz 66 testów
integracji (53 nowe replay). Broad: 1840 passed / 1 failed / 1 skipped,
2226,61 s. Błąd był w starym teście ochrony workflow: mutował ostatni krok,
który po dodaniu artefaktu jest uploadem, więc nie zmieniał `make bootstrap check`.
Poprawka wskazuje właściwy krok po komendzie; 24/24 testy ochrony passed w 0,25 s.
CI uruchamia je teraz przed długą regresją. Nie osłabiono żadnej bramki.
Nowy końcowy head `873116daf88a50c84cb4e7fe22c93a05907acb1a`, tree
`68ee4f2bd82a29295753e118180926dabe6a9452`; nowy odbiór pending. Mechanika replay
jest niezmieniona względem odebranego persistence; poprzedni run nie jest pełnym
odbiorem. Bez merge/deploy i ingerencji w sesje AI07/08.


### Końcowy odbiór dziesiątego przyrostu

[Required CI PR 37234285984](https://github.com/Oskar-Stachowski/retailops-ai-intelligence/actions/runs/37234285984)
i [push 37234283136](https://github.com/Oskar-Stachowski/retailops-ai-intelligence/actions/runs/37234283136)
na końcowym headzie: **success, po 4/4 jobs**. Broad **1841 passed / zero failed /
1 skipped**, 2154,72 s (push 2261,21 s). Jedyny skip to outbox, osobno wykonany
na rzeczywistym PG: **1 passed, 11,78 s**. Razem **1842 różne wykonane testy
passed**, bez sumowania PR/push i powtórzeń. Integration replay 66 passed,
33,57 s, jest już w broad; obejmuje wszystkie 53 nowe testy. Wczesny guard
workflow 24 passed, 0,20 s; działa przed kosztownym pytest.

Ruff, format 585 plików, mypy 347 + 10 isolated native owner, niezmienione byte
pins i locki, kontrakty, package, dokumentacja, dotychczasowy snapshot/curated,
Compose/MLflow, lifecycle/kolejka/wejścia/publikacja/read/catalog/evaluations
oraz backup/restore obu baz i recovery v12 passed. Cleanup własnych zasobów passed.

Zweryfikowano **dwa ZIPy** PR/push i JSON raportu. Raport jest identyczny bajtowo
w PR, push i lokalnym odbiorze: SHA `e5e583e030b330856f1dd3458873a8f42d4dbd53451082e2b088ca78e617a24e`.
Capture 806/1612 wersji, granice `[260,263,283]`, końcowe `[550,533,531]`,
pełny replay równy capture + overlap + nowe fakty + korekta/duplicate;
11 047 → 11 050 jednostek. Trzy as-of zgodne z frozen CuratedReader. Wszystkie
ograniczenia pozostają jawne: source live capture / ACK / pełny 43-table handoff false.

[Draft AI #23](https://github.com/Oskar-Stachowski/retailops-ai-intelligence/pull/23)
ma head `873116daf88a50c84cb4e7fe22c93a05907acb1a`. Techniczny merge CI
`507a7499c15434b756c0294c1317400180a30ff6` ma rodziców base #21 i końcowy head
oraz identyczne drzewo `68ee4f2bd82a29295753e118180926dabe6a9452`.
[Końcowy receipt](ai10-observation-replay-ci-receipt.json), SHA `3c695f0b668ad59713d196dd8881d3424688930152ba9cc0418a20c646b20d31`,
wiąże wyniki, artefakty, poprzedni nieudany odbiór i poprawki. Główne checkouty
zachowały HEAD/branch i tracked files; worktree AI10 czysty. **13 draftów AI10**
gotowych do przeglądu. Bez merge/deploy, lokalnego Dockera ani ingerencji w AI07/08.

Dziesiąty przyrost zakończony; cały AI10 nadal `in_progress`. Pozostają spójny
source SQL capture/pełny scope, trwałe AI fact replay i ACK/raw quarantine/fencing,
kwalifikowane finalne 07/08, projekcje anomalii i modelowego stockout przez v2
oraz scoped read API/UI i three-model E2E na 102 dniach. Odczytany bez zmian
nowy AI08 head `8010adf113b8f0ca76f07c58321cfa40b1ba65c0` ma niezależną ocenę
i kartę; właściciel nadal oznacza cały etap jako not ready (kalibracja, jakość,
progi/capacity, finalna kampania i lifecycle/batch/read). Rzeczywisty emiter
agenta należy do AI12; AI10 sugestie są odebrane na jawnym fixture.


## Jedenasty przyrost — trwała projekcja obserwacji AI (2026-10-05)

[Draft AI #25](https://github.com/Oskar-Stachowski/retailops-ai-intelligence/pull/25),
osobny worktree `/private/tmp/retailops-ai10-durable-observation-replay`, gałąź
`ai/10-durable-observation-replay`, base #23. Migracja `0021_observation_replay`
dodaje sześć tabel AI: pinned stream, partycje z epoch/owner, identity, wersje,
raw receipts/quarantine i sealed captures. Fakt, raw receipt/kwarantanna oraz
checkpoint zapisują się atomowo. ACK callback dopiero po commit; lease fence,
retention/log rewind, broker ahead, luka, kolizja i zmienione bytes fail closed.
Capture tej projekcji SQL czyta facts/receipts/vector w REPEATABLE READ i zapisuje
kanoniczne bajty/hash w tej samej transakcji; concurrent write blokuje publikację
mieszanego capture. To nadal jeden bounded scope, nie source operational capture.

Lokalnie 125 różnych targeted tests passed, Ruff/format, mypy 349 + 10 native
i docs/workflow passed. Osobny wymagany job PostgreSQL zachowuje wszystkie
dotychczasowe bramki. Pierwszy real PG run: 29 passed / 1 failed, 22,55 s.
Jedyny failure: oczekiwana nazwa powodu w teście pomijała `history_`; production
zwróciło poprawny powód i zachowało kwarantannę. Poprawiono test, final head
`571b174018937dad7b5b3b14ba44b4d6f4397c76`, tree
`564aa3a61f948e0f9451431b77cf8f6b929e36d8`; nowy Required CI pending.

Real PG drill obejmuje SIGKILL przed commit i po commit/przed ACK, utratę
połączenia, raw quarantine, concurrency, capture integrity, pełne 1612 native
wersji z prefix 806 + overlap, rollback migracji i pg_dump/pg_restore wszystkich
sześciu niepustych tabel. ACK i pozycje brokera są jawnymi fixtures; rzeczywisty
adapter brokera nadal do odbioru. Source REST/bundle unsupported / handoff=false;
bez zmian Source, merge/deploy, lokalnego Dockera lub ingerencji w AI07/08.
[Receipt](ai10-durable-observation-replay-ci-receipt.json). Cały AI10 `in_progress`.


Nowy Required CI: PR `37272703816`, push `37272699953`. Rzeczywisty PostgreSQL
**30/30 passed, zero failed/skipped**, 23,03 s PR / 17,23 s push. Secrets passed;
pełne checks i stare persistence w toku. Dwa ZIPy oraz 30-case JUnit
zweryfikowane; SQL JSON w PR/push identyczny bajtowo, SHA
`2bdb619deeb19a584aebd365e19161ab377e0b0d23a544a0d8e21c417539e535`.
Prefix capture dokładnie ten sam co w przyroście 10: 806 wersji / `[260,263,283]`;
SQL pełne 1612 wersji / `[548,533,531]` równe full replay i capture+overlap,
SHA capture `afbe4df1ca369aafe2bd803dc11081b83dc8fc93da234ad4cca8c50ea1a3923e`.
Korekty wersji sprawdza osobny rzeczywisty test SQL. Granice Source/real broker/
full 43-table nadal false. Pełny odbiór bieżącego headu czeka na pozostałe jobs.

Odczytane bez zmian nowe owner statusy: AI07 `eb15ac94398761ff2b2c10c4fe1a1073634d3b1e`
ma pełny lokalny odbiór obu v4 (56/56 bramek), real lifecycle/1232 wyniki/HTTP/
SIGKILL, status `pending_required_ci`; publikacja czeka na zgodę we własnym
zakresie AI07. AI08 `633ccf80937f65ae10334cadb75ff0c77aba105c` ma worker/HTTP/priority/
backup, przygotowane final receipts/kartę i kampanię 9296 punktów, nadal
`not_ready`: finalna zgoda/ocena, orchestration/qualification i końcowy CI są
otwarte. Nie uruchomiono kampanii ani nie opublikowano prywatnych owner artefaktów.


Push `37272699953` zakończył Required CI **5/5 success**. Broad **1874 passed /
0 failed / 31 skipped**, 1438,68 s; 30 observation i 1 outbox wykonane osobno
na real PG, razem **1905 różnych testów passed** (bez sumowania powtórzeń).
PR pierwszej próby końcowego headu miał również 1874 passed / zero failed /
31 skipped (2325,98 s), pełne persistence/backup/restore/recovery/secrets/nowy
SQL replay passed. Limit checks 45 min przerwał ostatni offline replay check;
contracts/package były już passed, artefakt tego jobu nie powstał. Run jest
`cancelled`, aggregate `failure`, zatem nie jest pełnym odbiorem PR.
Ponowiono tylko cancelled checks i zależny aggregate na identycznym headzie;
pozostałe bramki zachowały wynik. Nowy checks job `111656630454` w attempt 2,
pełny odbiór PR nadal pending. Nie zmieniano kodu, limitu ani bramek.


### Końcowy odbiór jedenastego przyrostu

[PR Required CI](https://github.com/Oskar-Stachowski/retailops-ai-intelligence/actions/runs/37272703816)
(attempt 2) i [push Required CI](https://github.com/Oskar-Stachowski/retailops-ai-intelligence/actions/runs/37272699953)
zakończone **5/5 success**, na tym samym headzie
`571b174018937dad7b5b3b14ba44b4d6f4397c76`, tree
`564aa3a61f948e0f9451431b77cf8f6b929e36d8`. Techniczny merge CI
`1c53df5a9a56e79cf1eeef4948496ca4107867a6` ma identyczne drzewo i oczekiwane
parenty base/head; draft #25 pozostaje otwarty, bez merge/deploy.

PR broad **1874 passed / 0 failed / 31 skipped**, 1397,55 s; push broad
1874/0/31, 1438,68 s. Wszystkie 31 Docker-only cases wykonane w wymaganych
osobnych bramkach: **30 actual PostgreSQL observation + 1 actual PostgreSQL
outbox**. Razem **1905 różnych testów passed**, zero niewykonanych skips w
całym wymaganym zakresie. Przyrost dodaje 63 różne testy; targeted subsets,
PR/push ani rerun nie powiększają tego wyniku.

Retry objął tylko cancelled checks i zależny aggregate; zakończył się po
26 min 51 s. Wcześniej zaliczone observation/persistence/secrets zachowały
wynik i nie były wykonywane ponownie. Historia pierwszego assertion failure
i timeoutu jest w receipt; nie zmieniono kodu po final headzie ani limitów CI.

Weryfikacja objęła **cztery ZIPy** z zgodnym head/run/digest i wszystkie
raporty/JUnit. SQL JSON PR/push jest identyczny, SHA
`2bdb619deeb19a584aebd365e19161ab377e0b0d23a544a0d8e21c417539e535`:
prefix 806 wersji / `[260,263,283]`, pełne 1612 / `[548,533,531]`,
SQL capture + overlap = pełny replay. Rzeczywisty backup/restore porównuje
zawartość wszystkich sześciu **niepustych** nowych tabel, w tym raw
kwarantanny i sealed capture. Odrębny offline report z jawną korektą i
duplikatem ma SHA
`e5e583e030b330856f1dd3458873a8f42d4dbd53451082e2b088ca78e617a24e`;
jego końcowy vector `[550,533,531]` nie jest vectorem SQL bazowych 1612 wersji.

Ruff/format 591 plików, mypy 349 + 10 native, 27 workflow guards, kontrakty,
zamrożone byte pins, package, docs, snapshot/curated, forecast acceptance
oraz dotychczasowe real persistence/lifecycle/backup/restore/recovery passed.
Status modeli i uzgodnione wyjątki quality acceptance nie zostały zmienione.
[Końcowy receipt](ai10-durable-observation-replay-ci-receipt.json), SHA
`4b30f11498fb5d222f9b779dfaaa71bdc2e8fe0985551b2da9aaf25d1035f2f1`, zawiera dokładne run/job/artifact IDs i hashe.

Protected checkout AI `ai/11-completion` /
`abf3f69a6a78c444b5a8a910fef8b3b43ba047a9` bez zmian tracked files;
Source `ai/16a-infrastructure-contracts` /
`599735a5fd13328e05f6100a07f644f4676e1ced` czysty. Własny worktree czysty.
Sesje AI07/08 nietknięte, lokalny Docker nieuruchomiony. Łącznie 14 draftów
AI10; jedenasty przyrost odebrany, **cały AI10 nadal in_progress**.

Pozostały zakres AI10:

1. Spójny operational SQL capture Source obejmujący 43 tabele oraz zaufany
   handoff offsetów. Obecny capture dotyczy projekcji AI daily_demand_versions.
2. Rzeczywisty adapter brokera obserwacji: topology, auth/TLS i ACK po commit
   istniejącego odbiornika SQL. Trwały checkpoint, fence i raw quarantine są
   już odebrane; dotychczasowe transport positions/ACK są fixtures.
3. Końcowe kwalifikowane lifecycle/serving AI07 i AI08 z dowodami właścicieli
   oraz zatwierdzonymi publicznymi pinami.
4. Anomaly i model-stockout intelligence.v2: outbox → atomowe projectors
   Source → scoped read API/UI. Odebrane forecast/suggestions nie zamykają
   tych dwóch modeli.
5. Rzeczywiste temporal E2E forecast/anomaly/stockout na 102 dniach.

Plan przyspieszeń uzupełniono pomiarami CI i impactem netto; podział CI-B
pozostaje propozycją. Rzeczywisty emiter agenta jest zakresem AI12.


## Dwunasty przyrost — rzeczywisty adapter brokera obserwacji (2026-10-05)

[Draft AI #26](https://github.com/Oskar-Stachowski/retailops-ai-intelligence/pull/26),
worktree `/private/tmp/retailops-ai10-observation-broker`, branch
`ai/10-observation-broker`, base #25. Adapter Confluent korzysta z TLS/SCRAM,
weryfikuje CA/hostname oraz rzeczywisty cluster/native topic UUID, delete-only
policy i pełny vector partycji. Range/eager assignment, SQL epoch fence,
raw value/key/ordered headers/timestamp; synchroniczny ACK po commit obejmuje
tylko dostarczony next offset, również przy historycznym overlap. Błąd
poll/identity/SQL/ACK zatrzymuje instancję. CLI przyjmuje private config 0600
i emituje wyłącznie count lub stały error.

Lokalnie 165 różnych targeted tests passed, Ruff/format 597, mypy 351 + 10
native, docs/workflow/dependency guards i CLI help passed. Frozen core
`uv.lock` bez zmian; odizolowany delivery tool dodaje dev pytest w istniejącej
przypiętej wersji. Pełny CI rejestruje `--durations=30` zgodnie z krokiem
pomiaru w planie; podział CI nadal nie został wdrożony.

Pierwszy actual broker gate: 9 passed / 3 failed / 1 teardown error, 20,68 s.
SQL prawidłowo odrzucił korektę z powtórzonym event ID fixture. Test SIGKILL
bez broker commit prawidłowo wznowił od durable SQL next; do testu overlap
potrzebny jest jawny broker checkpoint 0. Poprawiono wyłącznie fixture,
bez zmiany kodu produkcyjnego; cały nowy Required CI jest pending.

Nowa osobna mandatory gate zachowuje wszystkie stare bramki i obejmuje
12 rzeczywistych testów PostgreSQL + immutable Redpanda TLS/SCRAM: subscription,
correction/dedup/quarantine/ACK, SQL capture/overlap, SIGKILL obu granic,
connection termination, drugi owner fence, SCRAM/CA, literal ACL denials,
compaction i topic UUID replacement. Publisher/authority to jawne fixtures;
operational Source emitter/capture/full 43-table handoff nie zostały wykonane.
[Receipt](ai10-observation-broker-ci-receipt.json). Cały AI10 `in_progress`;
bez merge/deploy, zmian Source, uruchamiania lokalnego Dockera i dotykania AI07/08.


Final head `b887b90e12036499870d0de455511f2cca4ba441`, tree
`71cb93905e788f5c03d902f7913c564cd4469755`. Required CI PR `37283423931`,
push `37283419605`: **actual broker 12/12 passed**, zero failed/skipped,
19,99 s PR / 21,75 s push; actual SQL 30/30 passed; secrets passed.
Wszystkie cztery ZIPy/JUnit zweryfikowane. Broker JSON 952 B identyczny
w PR/push, SHA `c9e162111857489e04476c89405d6969699c2baa18fe3ee3cf6817e9e0c926e8`,
`actual_broker_ack_performed=true`, Source capture/full scope/models false.
Detached wheel ładuje trzy moduły z instalowanej paczki, bez editable .pth,
z tymi samymi zamrożonymi dependency wheels; opcjonalny Kafka client nie
wchodzi do core. Full checks i stary persistence nadal w toku.


Odczytana immutable rewizja AI08
`6fb5c7ae3b01e1e63deefe6acdc0fcdc4391068d` raportuje pełny odbiór: 9296 punktów,
90 kontroli passed / 3 zaakceptowane ostrzeżenia / zero blokad; real lifecycle,
cold worker, batch, 40 wyników scoped API i 15 attention_queue. Formalny ready
wymaga finalnego CI PR14, normalnego merge i merge CI właściciela; wcześniejsze
wpisy o niewykonanej kampanii są historyczne. AI07 immutable
`eb15ac94398761ff2b2c10c4fe1a1073634d3b1e` nadal full local acceptance /
pending_required_ci z publikacją wymagającą własnej zgody. Tylko odczyt statusów;
bez kopii prywatnych owner artefaktów lub zmian sesji.


### Końcowy odbiór dwunastego przyrostu

[Draft AI #26](https://github.com/Oskar-Stachowski/retailops-ai-intelligence/pull/26)
odbierany na headzie `b887b90e12036499870d0de455511f2cca4ba441`, tree
`71cb93905e788f5c03d902f7913c564cd4469755`.
[PR Required CI](https://github.com/Oskar-Stachowski/retailops-ai-intelligence/actions/runs/37283423931)
i [push Required CI](https://github.com/Oskar-Stachowski/retailops-ai-intelligence/actions/runs/37283419605)
zakończone **6/6 success**, oba attempt 1, bez retry na finalnym headzie.
Czas workflow: **36 min 10 s PR / 43 min 31 s push**. Wcześniejsze wpisy
pending opisują etapy odbioru; poniższy wynik jest końcowy dla przyrostu.

Broad pytest: **1926 passed / 0 failed / 43 skipped** w obu przebiegach,
1828,85 s PR / 2228,18 s push. Wszystkie 43 Docker-only cases wykonane
w obowiązkowych osobnych bramkach: **30 actual PostgreSQL observation,
12 actual broker + PostgreSQL, 1 actual PostgreSQL outbox**. Łącznie
**1969 różnych testów passed**, zero niewykonanych skips w wymaganym zakresie;
**64 nowe testy**. PR/push, targeted subsets i historyczne próby nie są sumowane.

Dwanaście broker cases passed bez failed/skipped: 19,99 s PR / 21,75 s push.
Pierwsza nieudana próba pozostaje w historii receipt; poprawiono tylko event ID
i początkowy checkpoint fixtures, bez zmiany produkcyjnego receivera.
Weryfikacja obejmuje **sześć ZIPów**: zgodne GitHub run/head/digest, hashe
każdego JSON/JUnit i zerowe failures/errors/skips czterech JUnitów.
Wszystkie trzy pary raportów PR/push są identyczne bajtowo. Broker report
`c9e162111857489e04476c89405d6969699c2baa18fe3ee3cf6817e9e0c926e8`
potwierdza rzeczywisty ACK, TLS i SCRAM. Odrębny SQL report zachowuje
bazowe 1612 wersji / `[548,533,531]`; offline report zawiera jawną korektę
1613 faktów / 1614 receipts / `[550,533,531]`. Ich zakresów nie utożsamiamy.

Ruff/format 597 plików, mypy 351 + 10 native, 32 workflow guards, kontrakty,
byte pins, core dependency identity, package/docs, snapshot/curated i forecast
acceptance oraz cały dotychczasowy actual persistence/lifecycle/backup/restore/
recovery passed. Oryginalne v12 `not_ready` i trzy zaakceptowane wyjątki jakości
zachowane. Core `uv.lock` bez zmian. Detached installed-wheel smoke potwierdza
parytet trzech modułów produkcyjnych; opcjonalny Kafka client pozostaje poza core.

[Receipt](ai10-observation-broker-ci-receipt.json), SHA256
`67c08c8d63516b67fa6b4022830fc81ec0f8f7308a3abe77b3671bccaa40b69b`, zawiera dokładne run/job/artifact IDs,
czasy 30 najwolniejszych testów PR/push i pełną historię prób.
Plan przyspieszeń ma końcowy pomiar CI-B.1 oraz warunkowy limit zysku przez
skrócenie checks: 4 min 53 s PR / 12 min 32 s push. Sharding pozostaje propozycją.

Protected checkout AI `ai/11-completion` /
`abf3f69a6a78c444b5a8a910fef8b3b43ba047a9` bez zmian tracked files;
Source `ai/16a-infrastructure-contracts` /
`599735a5fd13328e05f6100a07f644f4676e1ced` czysty. Własny worktree czysty.
Sesje AI07/08 nietknięte; Source bez edycji, lokalny Docker nieuruchomiony.
Łącznie 15 draftów AI10; bez merge/deploy. **Dwunasty przyrost odebrany,
cały AI10 nadal `in_progress`.**

Pozostały zakres AI10:

1. Operacyjny producent/topic obserwacji Source, spójny operational SQL capture
   obejmujący 43 tabele oraz zaufany handoff offsetów. Obecny actual receiver
   jest odebrany; acceptance publishers/authority są jawnie fixtures.
2. Końcowa publikacja/CI AI07 za zgodą właściciela oraz formalne PR/main CI AI08;
   integracja zatwierdzonych qualified lifecycle/serving releases z AI10.
3. Anomaly i model-stockout intelligence.v2: outbox → atomowe projectors Source
   → scoped read API/UI. Odebrane forecast/suggestions nie zamykają tych modeli.
4. Rzeczywiste temporal E2E forecast/anomaly/stockout na 102 dniach.

Rzeczywisty emiter agenta pozostaje zakresem AI12.


## Trzynasty przyrost — producent obserwacji Source (2026-10-05)

[Draft Source #98](https://github.com/Oskar-Stachowski/retailops-cloud-native-platform/pull/98),
branch `ai/10-source-observation-outbox`, base #93. Head
`a885b2a28f53bcd44e066558915e5e06e9124090`, tree
`9d78a98dfbf7d302d3ca1e714260bd6844bf34c2`. Source-owned writer zapisuje
natywną wersję obserwacji i zdarzenie outbox w jednej transakcji SQL. Publisher
TLS/SCRAM weryfikuje realną topology/UUID, policy delete oraz bytes/hash/ID/partycję
i związanie z faktem. Delivery callback poprzedza SQL completion; crash w tej
granicy wznawia identyczne zdarzenie. SQL serializuje authority, topic binding
jest unikalny w bazie. Korekty są ciągłe i nie cofają dostępności.

Addytywna migracja `a10f0c7e0500` dodaje cztery tabele. Historyczne migracje
bez zmian; fingerprint rollbacku rozszerzony jawnie, populated downgrade
blokowany. Private inputs 0600, brak automatycznego topic creation, stałe errors
bez credentials/faktów; [runbook Source](https://github.com/Oskar-Stachowski/retailops-cloud-native-platform/blob/ai/10-source-observation-outbox/docs/runbooks/source-observation-outbox.md).
Schema przypięty do odebranego AI #26, z wykonywalnym type/schema byte parity.

Lokalnie 94 focused pytest cases passed (w tym 48 nowych), 25 release/rollback
i 2 CI guards passed; mypy 37, Ruff/format 259 production files, Bandit high/high,
202 immutable references, CLI i pełny offline migration SQL passed. 12 actual
SQL/broker cases **nie wykonano lokalnie**; obowiązkowy remote CI ma SIGKILL,
SQL disconnect, corrections/dedup, concurrency, auth/CA/identity/integrity/policy
i niepusty backup/restore z pending resume. Combined coverage zachowuje wykonanie
tego modułu raz; JSON/JUnit obowiązkowe. Required CI **pending**.

[Receipt](ai10-source-observation-outbox-ci-receipt.json). Kod producenta jest
opt-in; nie podłączono automatycznie legacy sales lub demo do nowej historii.
Receipts publishera nie są pełnym capture watermark: wcześniejsza wysyłka może
nie mieć SQL potwierdzenia po crash. Pełny spójny capture 43 tabel/trusted handoff,
modele i końcowe E2E pozostają otwarte. Cały AI10 `in_progress`, łącznie 16 draftów;
bez merge/deploy i bez ingerencji w AI07/08.


Pierwszy remote CI `37292670658`: combined early 51 passed / 1 failed /
1 teardown error w 80,67 s. Błąd wyłącznie fixture dziecka SIGKILL: SecretStr
username został zamaskowany przez serializację Pydantic i SCRAM prawidłowo
odrzucił logowanie. Poprawiono private round trip username/password i dodano
test regresji. Kod produkcyjny bez zmian. Finalny obecny head
`2a759afc562dcef8d364b3ce067473876cc4e37d`, tree
`56ac25acfa3b154c6782fe0040cca76de1127a4e`. 49 nowych unit tests passed;
95 różnych focused cases łącznie. Pełny odbiór nowego headu nadal pending.


Drugi run `37293339664` ma **successful early durability step**, w tym wszystkie
12 actual Source producer cases, ale kontrola diff zatrzymała ogólny odbiór na
blank EOF w fixture. Usunięto pustą linię i zweryfikowano pełny diff base → head.
Dodatkowy przegląd writer API zabezpieczył caller autocommit bez aktywnej
transakcji oraz ustawił lokalne timeouty także dla caller; rzeczywisty rollback
test rozszerzono o odmowę przed zapisem. Finalny head
`767d34273440fd86306768e96edaf24afcc4bdac`, tree
`0cecc9586debdce16c1eae0ba400baa06c4a07fb`; końcowy CI tego headu pending.
Nie sumujemy pośrednich prób jako dodatkowych passed tests.


### Końcowy odbiór trzynastego przyrostu

[Draft Source #98](https://github.com/Oskar-Stachowski/retailops-cloud-native-platform/pull/98)
na finalnym headzie `767d34273440fd86306768e96edaf24afcc4bdac`, tree
`0cecc9586debdce16c1eae0ba400baa06c4a07fb`:
[Required CI](https://github.com/Oskar-Stachowski/retailops-cloud-native-platform/actions/runs/37294138449)
**29/29 success**, attempt 1. Czas od utworzenia do końcowego statusu:
**31 min 22 s**; pierwszy job rozpoczął się po **9 min 1 s**, a przedział
wykonania jobów wyniósł **22 min 19 s**. Wcześniejszy odczyt queued był opóźniony
względem faktycznych timestampów runnerów; końcowy pomiar pochodzi z completed
job metadata. Przyczyna opóźnienia przydziału nieustalona; innych runów nie zmieniano.
Source Required CI wyzwala PR i push do main, więc osobny feature-branch push run
nie jest wymagany. Techniczny CI merge ma dokładnie drzewo finalnego headu.

API: **1030 broad + 58 early durability = 1088 różnych tests passed**,
zero failures/errors/skips, coverage **87,66%** przy zachowanym progu 80%.
Broad trwał 714,72 s; obowiązkowy early step 88,48 s. Wszystkie **12 nowych
actual Source PostgreSQL + TLS/SCRAM broker cases passed**; suma ich czasów
JUnit wynosi 15,955 s, bez wspólnego setup/teardown. 21 fixed-origin assessment
cases to istniejący subset, nie dodano ich ponownie do total. Nowy przyrost:
**63 różne testy = 49 unit + 12 actual runtime + 2 CI guards**; guards są poza
liczbą API. Lokalnie 95 focused pytest, 25 release/rollback i 2 guards passed.
Nie sumujemy pośrednich prób ani lokalnych subsetów z finalnym CI.

Odebrane rzeczywiste cases obejmują zapis/korektę/dedup, caller rollback i
odmowę autocommit przed zapisem, concurrent writers, kolizje historii, realny
SQL disconnect i SIGKILL po delivery/przed SQL commit, odmowy po zmianie
integrity/topic binding/policy oraz niewłaściwym SCRAM/CA, a także niepusty
backup/restore z wznowieniem pending publication. Zachowano poprzednie durability,
Source03/06, full data/transport/bundle, scoped forecast/suggestion UI, Docker
update/rollback, kind persistence/rollback, security i Terraform gates.

Zweryfikowano **dwa ZIPy** według dokładnego run/head/GitHub digest oraz bytes
każdego JSON/XML. JUnit 58 cases ma zero failures/errors/skips, w tym 12 nowych.
Source outbox JSON (1135 B), SHA256
`fd1e6b5dcf2037ab2c3429a5829bed92d314747001359a182a354da2a3f6d135`,
potwierdza actual SQL outbox + broker publication, TLS i SCRAM. Business facts
pozostają jawnymi acceptance fixtures; pełny capture 43 tabel i qualified models
nadal false. To odbiór Source-owned opt-in writer/publisher, bez automatycznego
podłączenia legacy sales lub generatora. Nie jest to nowe uruchomienie AI SQL
receivera ani pełne E2E trzech modeli. Receipts delivery nie są capture watermark.

[Receipt](ai10-source-observation-outbox-ci-receipt.json), SHA256
`dcffea32867ee8d747be54aaa779030857b49a33d281301ee01abd8afe5bb867`, zawiera dokładne czasy,
run/job/artifact IDs, hashes, finalne wyniki i historię wcześniejszych błędów.
Plan usprawnień zawiera końcowy pomiar CI-A; nie przypisano hipotetycznej
oszczędności jako faktycznie odzyskanego czasu całego projektu.

Protected checkout AI `ai/11-completion` /
`abf3f69a6a78c444b5a8a910fef8b3b43ba047a9`, Source
`ai/16a-infrastructure-contracts` /
`599735a5fd13328e05f6100a07f644f4676e1ced`: brak zmian tracked files.
Własny Source worktree czysty; sesje AI07/08 nietknięte, lokalny Docker
nieuruchomiony. Łącznie **16 draftów AI10**, bez merge/deploy.
**Trzynasty przyrost odebrany; cały AI10 nadal `in_progress`.**

Pozostały zakres AI10:

1. Podłączenie opt-in writer/publisher do zatwierdzonej operacyjnej ścieżki Source,
   spójny SQL capture 43 tabel oraz zaufany pełny snapshot/replay barrier.
2. Zatwierdzone qualified AI07/AI08 releases i integracja grafu migracji.
3. Anomaly i model-stockout intelligence.v2: outbox → projectors Source → scoped API/UI.
4. Rzeczywiste temporal E2E forecast/anomaly/stockout na 102 dniach.

Wcześniejsze wpisy pending i poprzedni wykaz brakującego producenta opisują
historię. Powyższy odbiór oraz wykaz pozostałej pracy zastępują je dla przyrostu 13.
