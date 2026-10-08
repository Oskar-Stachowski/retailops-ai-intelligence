# Audytowane przygotowanie rodziców AI 09

`evaluation_campaign.campaign_generation.generate_campaign_parent` wykonuje
rzeczywiste przygotowanie źródła, snapshotu i curated wewnątrz jednej rezerwacji
`source_generate`. Receptura [v13](../contracts/evaluation/v13/campaign_generation_plan.schema.json)
przypina requested i resolved parameters, producenta przez source recipe,
osobny lock eksportera Parquet, plan anomalii, wersję snapshotu, parent limits
oraz limity czasu, pamięci, scratch i rezerwy. Jej digest musi być w protokole
przed uruchomieniem. Canonical profile nadal wynosi 365 × 100 × 5 × 3 dla
development i 730 × 200 × 10 × 4 dla final. Mały kontrolny `ai-load` nie może
przejść `plan.bind(CampaignSourceRecipe)`.

Runner rezerwuje próbę przed Git/producer/configuration/artifact I/O. Następnie
uruchamia sześć osobnych procesów: generation, qualification, export, import,
curation oraz pełny source/curated replay. Generation korzysta z istniejącego
`source_cohort_batch_v2` dla source 2.7 lub ordinary planned-anomaly source 2.8.
Szybka ścieżka zachowuje zwykły writer i reader; nie dodajemy drugiego identycznego
odczytu po jej własnym zakończonym verifierze. Qualification, snapshot i curated
zachowują swoje samodzielne kontrole.

Publiczny `import_snapshot().summary()["destination"]` wskazuje katalog
importu zawierający `snapshot/`, manifest importu i jego checksum. Curation
otrzymuje ten katalog importu. Końcowa weryfikacja i wynik runnera wskazują
natomiast jego podkatalog `snapshot/`, gdzie znajduje się raw manifest.
Kontrole nie zmieniają kontraktu importera ani akceptowanych rodziców AI 07–08.

Nowe źródła kampanii wymagają osobnego `exporter_lock_sha256`. Source 2.7/2.8
wiąże dependency fingerprint plików API, podczas gdy snapshot exporter wiąże
`data/requirements-parquet.txt`. Nie oczekujemy, aby source manifest zawierał
lock eksportera w swoim własnym fingerprint. Rzeczywisty exporter lock jest
sprawdzany względem wartości zamrożonej przed generacją; zainstalowane wersje
pinned API/Parquet dependencies również są sprawdzane. Starsza deklaracja bez
tego pola nie może uruchomić nowego generation plan.

Plan forecast jest także obsługiwany przez anomaly snapshot 1.2. Jego generator
zachowuje prawdziwą wersję 1.0.0; obecność requested/resolved known plans i
watermarks jest sprawdzana oddzielnie. Dodano dokładne współczesne schematy
producenta jako nowe warianty. Producent wymaga osobnych plan-aware wariantów schematów i dopuszczenia tej
kombinacji w ordinary source reader, przy zachowaniu niezależnego process replay.
Source follow-up [PR #103](https://github.com/Oskar-Stachowski/retailops-cloud-native-platform/pull/103)
jest scalony jako `b7234899`. Jego head i dokładny source main mają pełny odbiór:
21 success oraz 4 przewidziane skipped; main CI `37651259188` jest zakończony.
Kontrolny producer `16d34887` jest przodkiem tej zaakceptowanej integracji.
Zaliczył rzeczywiste eksporty demand/physical z plans, 31 kontroli cohort/forecast
plans, pełny lint/format i skonfigurowany mypy.
Poprzedni exact source main `cbcac6eb` zakończył CI37629010997 pojedynczą
porażką odczytu koordynatora Kafka; poprawka ogranicza ponawianie tego odczytu,
zachowując wszystkie asercje trwałości. Porażka pozostaje w historii.
Starsze schematy i publiczne frozen snapshoty
pozostają akceptowane, z ich dotychczasowymi kontrolami i zgodnością bajtów.
Zmiana metadanych lub identyfikatorów bez rzeczywistej generacji nie kwalifikuje
nowego źródła.

Supervisor mierzy własny RSS i całe własne drzewo workera, dolną granicę CPU,
logical/allocated scratch, wolny dysk i dostępną pamięć. Prywatny TMPDIR należy
do tej próby i wchodzi w scratch. Limity obejmują wspólny czas wszystkich faz.
Każdy worker zapisuje także systemowy peak własnego RSS. Przekroczenie limitu
zatrzymuje wyłącznie własną grupę procesów i zachowuje porażkę oraz dostępny
koszt. Próbkowanie nie jest ciągłym pomiarem chwilowych skoków pamięci. Nie ma
retry, refund ani automatycznego podwyższenia budżetu. SIGKILL supervisora
pozostawia nierozstrzygniętą rezerwację z nieznanym kosztem.

Przed sukcesem verifier sprawdza pełne snapshot/curated IDs, konfigurację,
manifesty, źródłowy commit/clean state i oba locki; odtwarza całą transformację
w prywatnych kopiach i sprawdza niezmienność rodziców. Receipt jest trwale
publikowany z uprawnieniami 0600/0700 i bez podmiany istniejącego pliku, zanim
pojawi się journal completion. Receipt pozwala sprawdzić zakończenie własnej
operacji; następny odczyt nadal wymaga odrębnej rezerwacji. Final generation
pozostaje blokowana przed `SelectionFreeze` przez journal.

## Odbiór i pozostały zakres

Pełna regresja publicznych rodziców zaliczyła 240 testów; osobny końcowy
odbiór runner/export/journal i nowych typowanych metadanych ma 97 passed.
Zbudowany wheel ma identyczne bajty wszystkich 503 modułów Python oraz nowych
schematów v13 i planned-anomaly. Ruff/format, Mypy 615 plików i docs przechodzą.

Kontrolowane testy obejmują ordering, zmianę zamrożonego planu, awarię każdej
fazy, brak retry, zamknięty final przed freeze i rzeczywiste procesy z limitami.
To testy mechanizmu z kontrolowanymi metadanymi, nie projektowa kampania.
[Receipt przygotowania](evidence/09-20-audited-generation-preparation.json)
rozdziela zaliczone kontrole od niezrealizowanych odbiorów.

Próba małego native wykonania na lokalnym komputerze została zatrzymana przez
preflight, zanim uruchomiono generation: dostępne około 1.3 GiB RAM nie spełniało
budżetu 1 GiB plus rezerwy 1 GiB. Obie kontrolne porażki pozostają zapisane.
Nie zmieniono rezerwy ani procesów innych sesji. Osobny manual workflow
`AI09 real generation worker control` przygotowuje po jednym rzeczywistym
planned-forecast source 2.7 i planned-physical-anomaly source 2.8, przechodzi
wszystkie sześć faz i zachowuje pomiary/awarie. Nie inicjalizuje project journal,
nie wykonuje fitów ani final test i nie kwalifikuje canonical.

[Rzeczywisty run 37664228881](https://github.com/Oskar-Stachowski/retailops-ai-intelligence/actions/runs/37664228881)
na `16a02ae` wykonał pięć z sześciu faz dla obu profili. Verify nie znalazł
`snapshot_manifest.json`, ponieważ odczytywał katalog importu zamiast jego
podkatalogu `snapshot/`. [Receipt 09.26](evidence/09-26-generation-snapshot-verification.json)
zachowuje pełny koszt wszystkich faz, porażkę i hash artefaktu. Poprawka obu
ścieżek ma 72 zaliczone kontrole generation/export/capacity, w tym rzeczywisty
import publicznego smoke i pełny prywatny replay bez mockowania plików lub
transformacji. Nowy [native run 37669667688](https://github.com/Oskar-Stachowski/retailops-ai-intelligence/actions/runs/37669667688)
na poprawionym `e3bc68e` zaliczył wszystkie sześć faz dla obu profili.
Receipt 09.26 zawiera także hash jego artefaktu, pełne koszty i zweryfikowane
źródła. Jest to odbiór małych rzeczywistych danych. Publikacja tej implementacji
i pełny CI zaakceptowanego main pozostają wymagane przed pomiarem canonical.

Pełna kampania nadal wymaga odbioru pełnych profili, final-only exporter,
uczciwych fitów i kalibracji, freeze, trzech końcowych seedów i wszystkich
zastosowań, segmentów/robustness/niepewności/kosztów, MLflow/lifecycle,
trzech kart i raportów oraz publikacji i końcowego CI na main. Końcowe daty
należy ustalić po wszystkich wcześniejszych ekspozycjach, także kontrolach
kohort obejmujących 2026-09-30. Nie rozpoczęto nowej projektowej generacji.

Przed publiczną generacją lub eksportem końcowych danych wymagane są teraz
również `selection_bundles` dla forecast, anomaly i stockout. Wspólny
`verify_completed_campaign_selection` sprawdza trwałe dowody ukończonej
niezależnej oceny development, zgodność zamrożonych rodziców, segmentów
i niepewności oraz artefakty. Jest wykonywany wewnątrz rezerwacji, przed
producer inspection lub otwarciem końcowego source/curated. Generic journal
freeze sam audytuje kolejność i nie zastępuje tych dowodów. Brak dowodu
kończy zarezerwowaną próbę porażką bez dostępu do końcowych danych.
Aktualny forecast component z nieukończonymi segmentami/niepewnością nie może
autoryzować takiego dostępu. [Evidence kontroli](evidence/09-31-independent-forecast-components.json)
oddziela rzeczywisty verifier od jawnie mockowanych kontroli publikacji.
