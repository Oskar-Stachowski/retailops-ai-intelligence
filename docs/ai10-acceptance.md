# AI 10 — odbiór integracji RetailOps

Aktualizacja: 2026-10-07. **Status: ready — pełny odbiór integracji.**
[Końcowy bounded raport](evidence/ai10-bounded-acceptance.json) wiąże SHA wszystkich
receipts; [mapa siedmiu wymagań](evidence/ai10-final-ready-review.json) odpowiada
kanonicznemu planowi Source. [Pełny oryginalny V12](evidence/ai10-native-v12-22de575-accepted.json)
na `22de575`, run `37665627162`, zaliczył 273 boundary tests oraz rzeczywisty Source E2E:
56 wyników/oryginalnych SQL publisher ACK/projekcji/duplikatów, pełny authenticated
TCP API, dwie strony built UI i live revocation. Wcześniejsze failures oraz odtworzona
diagnostyka pozostają historią; nie zastępują tego pełnego receipt.

Oryginalne kwalifikacje modeli i quality V12 `not_ready` zachowano. Forecast działa
wyłącznie w owner-approved development namespace; nie ma refitów ani produkcyjnego
wdrożenia. [Zgodność native runtime](evidence/ai10-native-runtime-main-compatibility.json)
wiąże odebrane commity z integracją, bez twierdzenia że całe src jest identyczne.
[Nowszy main AI09 `5ed0544`](evidence/ai10-ai09-5ed-main-integration.json)
zachowuje wszystkie 21 native komponentów AI10; finalny zintegrowany head wymaga
własnego pełnego Required CI.
[AI main `b0e2de`](evidence/ai10-ai-b0-main-ci.json) ma 17/17 success,
[Source baseline `467f990`](evidence/ai10-source-code-main-ci.json) 30/30,
a [Source `b723489`](evidence/ai10-source-b723489-main-ci.json) 21 success / 4 celowe skipped.
Kod wcześniejszych przyrostów jest na main po PR #28/#100. Poprawkę launchera i
końcowe rekordy dostarcza [AI #38](https://github.com/Oskar-Stachowski/retailops-ai-intelligence/pull/38)
oraz [Source #104](https://github.com/Oskar-Stachowski/retailops-cloud-native-platform/pull/104),
wyłącznie normalnym protected merge z Required CI dokładnych headów i wynikowych main.
Stan tej ostatniej bramki sprawdza się w rzeczywistych checks GitHub; ten immutable
rekord nie przypisuje przyszłemu, samoodnoszącemu się commitowi niewykonanego CI.

## Zakres i stan dowodów

| Granica | Rzeczywisty odbiór | Stan |
| --- | --- | --- |
| Immutable snapshot | Kompletny SourceSnapshot, typed Parquet, SHA, immutable import i HTTP bundle z osobnym service grantem | Odebrany komponent; [receipt](ai10-source-bundle-ci-receipt.json) |
| Bounded REST | Rzeczywisty Source OpenAPI, jawne legacy ziarno i capabilities, 50/100 pagination, deadline/retry i auth | Odebrany komponent; [receipt](ai10-rest-ci-receipt.json); pełne sales grain zwraca unsupported |
| Snapshot REST/SQL 43 tabel | Brak ogólnej historii i wspólnego wektora dla wszystkich tabel | Jawnie unsupported; bounded live reads nie reklamują snapshotu |
| Stream obserwacji | Source SQL outbox i capture pełnego TLS/SCRAM prefixu; osobny AI SQL, checkpoint, ACK, overlap i późna korekta | **Passed**: [oryginalny receipt](evidence/ai10-source-sql-handoff-accepted.json), 9 testów, sumy 4 → 7, ACK `[0,0,3]` → `[0,0,4]` |
| Qualified stockout | Frozen model, registry/cold worker, oryginalny publisher AI SQL i 40 ACK, Source SQL/API i built UI | **Passed**: [oryginalny SQL receipt](evidence/ai10-native-stockout-original-sql-accepted.json), 40 wyników/40 duplikatów, ACK `[34,46]` |
| Qualified anomaly | Oryginalny model AI07, OCI/registry/SIGKILL recovery, oryginalny publisher SQL i 1232 ACK, Source SQL/API i 25 stron built UI | **Passed**: [oryginalny SQL receipt](evidence/ai10-native-anomaly-original-sql-accepted.json), 1232 wyników/1232 duplikatów, ACK `[1064,1400]` |
| Oryginalny forecast v12 | 664 oryginalne pliki i frozen wheel, nowy Source 102 dni/43 tabele, 56 wyników/4 szeregi/horyzont 14, MLflow/SQL/publisher/Source API/UI | **Passed**: [pełny original receipt](evidence/ai10-native-v12-22de575-accepted.json), 273 boundary tests + actual Source E2E, 56 ACK/projekcji/duplikatów, ACK `[70,42]`, 2 strony UI i revocation |
| Awarie i trwałość | Brak ACK przed SQL commit, SIGKILL po commit, fencing, gaps, raw quarantine, niedostępne SQL/broker/DLQ i recovery | Odebrane obowiązkowe komponenty; [SQL](ai10-durable-observation-replay-ci-receipt.json), [TLS broker](ai10-observation-broker-ci-receipt.json), [checkpoints](ai10-checkpoint-ci-receipt.json) |
| Sugestie | Jawny poprawny fixture, human approval, auth, trwałe read API i istniejący UI | Odebrany zakres AI10; rzeczywisty producent należy do AI12: [receipt](ai10-suggestion-ui-ci-receipt.json) |
| Publikacja | Niezmieniona bramka: normalne protected merges oraz Required CI dokładnych headów i wynikowych main | Wcześniejsze code/main CI passed; końcowe rekordy w PR #38/#104, weryfikowane przez ich aktualne GitHub checks |

Każdy receipt podaje własny dokładny commit i run ID. Ich zestaw jest odbiorem
integracji; nie jest nowym wspólnym eksperymentem jakości trzech modeli.
Modele zachowują swoje zaakceptowane Source snapshots, originy i kwalifikacje.
Stream obsługuje jawnie `daily_demand_versions`; jego fixture sprawdza protokół
sum/korekt, nie kwalifikuje ML. Pełny immutable bundle pozostaje osobną ścieżką
43 tabel. Nie wolno dopisywać do niego fikcyjnych granic brokera.
Kompletne native ziarno sprzedaży, wersje i availability dostarcza wersjonowany
immutable bundle. Bounded REST zachowuje swoje rzeczywiste ograniczenia;
`require_full_sales_grain()` zwraca `unsupported_grain`.

## Overlay istniejących projektów

[Wykonawcza instrukcja krok po kroku](https://github.com/Oskar-Stachowski/retailops-cloud-native-platform/blob/main/docs/runbooks/intelligence-existing-projects.md)
wiąże Source `docker-compose.intelligence.yml` i AI `infra/compose-intelligence.yaml`.
[Rzeczywista kompilacja i 11 guard tests](evidence/ai10-existing-project-overlays.json)
potwierdzają jedną Source-owned external network, aliasy `retailops-api`/`retailops-ai`,
reachable advertised listener `redpanda:9092` oraz odrębne prywatne bazy/MLflow.
AI nie tworzy drugiego brokera i nie usuwa Source network ani stosu.
`Dockerfile.intelligence` używa istniejącego osobnego locked delivery runtime;
worker jest jawnym bounded profilem z private configuration mounted read-only.
Required CI kompiluje oba rzeczywiste projekty i sprawdza build/SDK/CLI tego obrazu.
Kompilacja nie uruchamia daemonu; podstawowy lokalny Compose nie zyskuje TLS
przez zmianę sieci. Zachowaj prywatne, wcześniej skonfigurowane granty i transport.
Pełne native modele/API/UI są odebrane odrębnymi rzeczywistymi receipts.

## Własność danych i granice zapisu

```mermaid
flowchart LR
  subgraph Source[RetailOps — własny SQL i istniejący frontend]
    OPS[Operacyjne fakty i wersje]
    SNAP[Niezmienny bundle 43 tabel]
    REST[Bounded REST]
    SO[Source observation outbox]
    SC[Checkpoint consumer]
    READ[SQL wyników i inbox]
    API[Read API ze scope principal]
    UI[Forecasts / Anomalies / Recommendations]
    OPS --> SNAP
    OPS --> REST
    OPS --> SO
    SC -->|jeden commit: wynik / raw receipt / checkpoint| READ
    READ --> API --> UI
  end
  subgraph AI[RetailOps AI — osobny SQL i registry]
    IMP[Zweryfikowany import i features]
    ML[Oryginalne frozen modele]
    AO[Wynik i outbox w jednym commit]
    PUB[Oryginalny SQL publisher]
    LIVE[Ograniczony klient REST]
    OBS[SQL faktów / raw receipts / checkpoint]
    IMP --> ML --> AO --> PUB
  end
  subgraph Broker[Broker — osobne topic i grupy]
    IN[retailops.source-observations.v1]
    OUT[retailops.intelligence.v2]
  end
  SNAP --> IMP
  REST --> LIVE
  SO -->|ACK przed SQL delivery receipt| IN
  IN --> OBS
  OBS -->|ACK po SQL commit| IN
  PUB -->|ACK przed SQL delivery receipt| OUT
  OUT --> SC
  SC -->|ACK po SQL commit| OUT
```

Repozytorium Source jest właścicielem faktów operacyjnych, eksportów,
read models i interfejsu. AI jest właścicielem importów, kwalifikacji,
registry, obliczeń i własnego outbox. Workery używają oddzielnych baz i grantów;
API nie zapisuje modelowych wyników. Broker przenosi eventy i pozycje transportu,
a SQL rozstrzyga trwałość i deduplikację. Ścieżka obserwacji zachowuje osobny
bounded zakres `daily_demand_versions`; nie stanowi dowodu kwalifikacji ML
ani streamu wszystkich 43 tabel. HTTP/broker credentials pozostają poza Git.

## Odtworzenie krok po kroku

1. Przypnij oba commity i schema/registry SHA. Używaj własnych baz, grup brokera,
   service/personal credentials poza Git oraz osobnych sieci baz danych.
   Migracje AI i Source wykonuj jawnie; API nie migruje przy starcie.
2. W trybie snapshot pobierz pełny niezmienny manifest i każdy zadeklarowany plik;
   zweryfikuj wszystkie bytes/schema/rows przez importer. Buduj curated i features
   wyłącznie z tego zweryfikowanego źródła. Bundle nie deklaruje replay handoff.
3. W trybie REST użyj [typowanego klienta](source-rest-v2.md): 50 rekordów domyślnie,
   najwyżej 100, faktyczne filtry OpenAPI, ograniczony deadline/retry GET i scope.
   Świeżość ocenia się według biznesowej dostępności; czas HTTP nie odświeża faktu.
   Brak spójnego snapshotu/history zwraca jawny unsupported.
4. Dla streamu wykonaj Source append/publish i [capture](source-observation-replay.md).
   Odbiorca sprawdza authority, cluster/topic IDs, wszystkie partycje, pełny prefix
   i wersje historii. SQL facts/raw receipt/checkpoint/quarantine mają jeden commit;
   dopiero potem wolno ACKować faktycznie odczytany offset. Korekta zastępuje wkład,
   a overlap nie dodaje drugiej ilości. Utrata retencji wymaga resync.
5. Uruchom dedykowany `AI10 Source capture handoff`. Przypięty Source zachowuje
   własny broker podczas niezależnego odbioru SQL AI. Wymagaj równości capture
   Source = prefix AI SQL oraz capture + overlap = full SQL replay. Artefakt
   `independent-ai-sql-handoff.json` zawiera rzeczywiste ACK i sumy 4 → 7.
6. Dla stockout/anomaly uruchom `AI10 qualified native output`. Zachowaj oryginalne
   kwalifikacje i frozen modele; nie ponawiaj ocen końcowych ani refitów. Source,
   jego oddzielny SQL/broker, built UI i Chromium muszą być przygotowane przed
   wykonaniem acceptora. Baza AI pozostaje aktywna do końca odbioru Source.
   Ręcznie wybierz `lane=all`, `stockout` albo `anomaly`; odbiór obu modeli wymaga
   dwóch zaliczonych ścieżek. Zmiany wyłącznie znanego acceptora wybierają właściwy
   model, a wspólne/nieznane zmiany albo brak poprawnego zakresu Git wybierają oba.
7. Istniejący publisher musi dostarczyć pełny census z oryginalnego AI SQL,
   uzyskać ACK, następnie utrwalić partition/offset. Source porównuje pełne
   original native IDs/payloads i wiąże original ACK coordinates z checkpoint
   receipts. Stockout/anomaly dodatkowo sprawdzają SHA odczytanych wire bytes
   oraz pełny SQL transport fingerprint z key/headers/timestamp. V12 sprawdza
   original event SHA w publisher receipt i pełną zgodność native payloadów
   SQL/API/UI; ten receipt nie poświadcza osobnego bounded reread wire bytes.
   Jednorazowy replay daje dokładnie N wyników i N duplikatów.
   [Instrukcja modeli](intelligence-models-v2.md).
8. Dla v12 użyj `AI10 original v12 native output` i [instrukcji recovery](ai10-v12-original-recovery.md).
   Pełny oryginalny verifier i wszystkie dziesięć review gates są obowiązkowe.
   Nowe wejście inference ma 102 dni historii, 14 dni planów, 43 tabele i brak truth.
   Oryginalny wheel/recipe i lock pozostają niezmienione. Kwalifikacja wejścia
   obejmuje dwa cold predictions, rzeczywisty registry/SQL i atomowy outbox.
9. V12 zachowuje oryginalną ocenę `not_ready` i dokładną decyzję właściciela
   `11cd0e1fdd10629543bcb66aaedb3bd9b7b7fc5b31473575b998d951c93f00d2`.
   Działa wyłącznie w `retailops-demand-forecast-v12-development`. Standardowy
   model nie dziedziczy tej decyzji. Source owner review wymaga dokładnego
   acceptance SHA, a istniejący Forecasts UI wyświetla zakres development.
10. W istniejących panelach RetailOps odczytaj każdy oryginalny native ID,
    pełny payload, ziarno, jednostkę, source/model/run/dataset lineage i freshness.
    Użyj osobistego grantu trzymanego wyłącznie w pamięci przeglądarki.
    Brak credential → 401; obcy scope → 403/404; `user_id` nie rozszerza praw.
    Built UI musi potwierdzić wszystkie strony oraz live revocation/wyczyszczenie.
11. Weryfikuj awarie przez obowiązkowe real SQL/broker/SIGKILL jobs, nie tylko
    fixtures kontraktów. Zachowaj raw poison przed ACK; niedostępna kwarantanna
    zatrzymuje partycję. Nie omijaj luki ani nie commituj nieodczytanego suffixu.
    Po naprawie wznów tę samą grupę; zachowane SQL receipts deduplikują retry.
12. Pobierz publiczne artefakty i sprawdź ZIP size/SHA, dokładne HEAD/run IDs,
    counts oraz zakresy. Zachowaj receipts bez credentials, signed URLs i 32 GB
    archiwum. Cleanup usuwa tylko zasoby z własnym UUID/label i własne pomocnicze
    handoff objects/secret; oryginalne S3 i inne sesje pozostają zachowane.
13. Zaktualizuj status, README, registry etapów i checklist po zaliczeniu pełnego
    odbioru. Zaliczone Required CI dokładnych HEAD umożliwia normalny protected
    merge obu PR-ów; potem zweryfikuj obu `origin/main` oraz ich Required CI.
    Starsze draft PR-y zamykaj dopiero po sprawdzeniu zachowania wszystkich zmian.

Lokalnie Docker pozostaje wyłączony; długie i kontenerowe odbiory wykonują
jednorazowe runnery GitHub. Rezerwa lokalnego dysku pozostaje co najmniej 50 GiB.

## Lokalne komendy i ich granice

```sh
make integration-replay-test
make integration-failure-test
make observation-persistence-test
make observation-broker-test
```

`integration-replay-test` sprawdza kontrakty i mechanikę; nie kwalifikuje modeli.
Pozostałe trzy komendy wymagają własnego runtime Docker, więc w tej sesji wykonuje
je obowiązkowy CI. Pełne native modele/API/UI i Source capture mają osobne
dedykowane workflowy opisane wyżej. Zaliczenie samej komendy nie zamyka AI10.

## Końcowy checklist i bramka publikacji

- [x] Immutable snapshot, bounded REST i jawny unsupported ogólnego snapshot REST.
- [x] Rzeczywisty Source SQL/capture → ten sam TLS broker → AI SQL/ACK/overlap.
- [x] Qualified stockout/anomaly przez rzeczywiste Source API i built UI.
- [x] Stockout przez oryginalny AI SQL publisher przed cleanupem.
- [x] Anomaly przez oryginalny AI SQL publisher przed cleanupem.
- [x] Oryginalny v12 przez pełny registry/SQL/publisher/Source API/UI na 102 dniach.
- [x] Końcowy raport bounded odbioru, aktualne statusy i registry.
- [x] Bramka dostarczenia rekordu: normalne protected merges #38/#104 i Required CI dokładnych headów oraz obu main; ostatni wynik sprawdzany w GitHub.
