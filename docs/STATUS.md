# Aktualny status

Aktualizacja: **2026-09-29**. **Etap 11 — RAG jest odebrany lokalnie.**
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

**Praca nad blokadami jakości AI 04 — 2026-09-30:**
[wersjonowana korekta i kalibracja](forecast-remediation.md) zachowują
oryginalne progi oraz validation-only dopasowanie. Na dotychczasowych danych
liczba bramek zmieniła się na **192 passed / 32 failed / 8 not_ready**.
Drugi fold poprawia MAE o 7,32% wobec baseline'u, pokrycie wysokiego wolumenu
rośnie do 92,62%, a szerokość niskiego wolumenu / średnie actuals spada do 1,024.
Pozostają bias/regresje części segmentów oraz brak koszyka zero.
[Evidence](evidence/04-quality-remediation.md) rozróżnia ten pomiar rozwojowy
od późniejszych kampanii. Kontrola próbek zatrzymuje trening, gdy brakuje
krytycznego koszyka w danym foldzie. Późniejsza kampania V8 przyjęła 626238
wierszy bez quarantine, ale górna granica liczebności koszyka zero wyniosła
0 w validation foldów 1–2 i holdoucie folda 3 (wymagane ≥30). Zakończyła
się przed cechami i treningiem; metryk nowych holdoutów nie oceniano.
Regresja kodu: **940 passed**, testy korekty/CI **22 passed**;
końcowa regresja producenta/weryfikatora źródła **560 passed**.
Model nadal jest `not_ready`; punkt blokad jakości nie jest jeszcze zamknięty.

**Kontynuacja — źródło przerywane i receptura 2.1:**
[Nowe evidence](evidence/04-quality-intermittent.md) opisuje jawny syntetyczny
profil `ai-intermittent-v1` i wybór korekty z kontrolą bias/MAE w obu połowach
validation. Na starym development: **195 passed / 29 failed / 8 not_ready**;
pooled low mieści się teraz w limicie regresji MAE (+9,84%). Zamrożona
kampania V10 przyjęła nowe źródło: 616547 wierszy w 43 tabelach. Pierwszy bieg
zatrzymał brak miejsca na dysku podczas curated, przed cechami, treningiem
i metrykami nowych holdoutów. Surowa historia ma przypadki zero we wszystkich
sześciu oknach; pełna kwalifikacja cech pozostaje do sprawdzenia. Kod:
949 zaliczonych testów przed zmianą zapisu wejść oraz 66 testów po tej zmianie;
powtórne przeliczenie starego wyniku 2.1 potwierdziło jego tożsamość.
Punkt 3 pozostaje otwarty.

**Przygotowanie protokołu jakości 2.0:**
[Nowe reguły](forecast-quality-v2.md) rozdzielają medianę (MAE) i średnią
(MSE + bias), wymagają obu i oceniają przedziały przez interval score oraz
coverage, bez dzielenia przez małą sprzedaż. Poprawne zera dają się ocenić;
fałszywy popyt i realne pogorszenia nadal są błędami. Stare wyniki, snapshot
i zablokowana v10 pozostają zachowane z sumami kontrolnymi. Pełnej kampanii
nie uruchomiono. Planistyczne minimum miejsca to 16 GiB.
[Integracja dwóch celów i kampania v11](forecast-functional-v2.md) dodaje pięć
osobnych modeli, baseline’y mediany/średniej, wybór na walidacji, replay oraz
eksport v2. Zapis wejść 1.2 usuwa ryzyko pełnego panelu przez kompresję indeksów
i uzasadniony limit logiczny 5 GiB, bez zmiany progów jakości. Przygotowanie
danych zakończono: 254 800 cech i zweryfikowany split. Wszystkie 32 kontrole
próbki przeszły (najmniejszy koszyk: 528 wierszy). [Freeze v11](../contracts/forecast/v2/campaign-v11.freeze.json)
zapisano przed treningiem i oceną nowych holdoutów; pełna kwalifikacja jest w toku.
[Evidence przygotowania](evidence/04-functional-preparation-v11.json) zachowuje
975 zaliczonych przypadków testowych, zielone Required CI dla `5aa8047`,
kontrolę zachowania 3070 plików oraz 31,2 GiB wolnego miejsca przed startem.
Robocze PR-y: [AI #7](https://github.com/Oskar-Stachowski/retailops-ai-intelligence/pull/7)
i [źródło #77](https://github.com/Oskar-Stachowski/retailops-cloud-native-platform/pull/77).

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
  również przechodzi. Pełny odbiór Compose obejmuje migrację `0008_rag_semantic`,
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
