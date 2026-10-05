[Poprawka startu API i pełnej kontroli typów AI 08](evidence/08-20-stockout-api-startup.md)
wyjaśnia niezaliczone CI 08.19 oraz aktualny komplet sześciu źródeł.

# Dokumentacja RetailOps AI

[Stockout: trwała kolejka i API](reference/stockout-jobs.md) oraz
[odbiór przyrostu 08.19](evidence/08-19-stockout-jobs.md).

Zacznij od [statusu](STATUS.md), [decyzji](architecture/decisions.md) i
[poleceń lokalnych](development.md). [Contributing](contributing.md) opisuje
zmiany i PR-y, [security](security.md) — granice dostępu i zgłoszenia.
[Semantyczny RAG](knowledge-semantic.md) opisuje rzeczywiste embeddings,
kwalifikację jakości, aktywację i rollback Etapu 11. Historyczne evidence
zachowuje zakres poszczególnych pomiarów; bieżące bramki podaje status.
[Kontrakty danych/run/tool](data-contracts.md) i [ich odbiór](evidence/01-contracts.md)
opisują wersje, lineage i walidację offline.
[Handoff źródła 03.3](source-snapshot-handoff.md) opisuje samowystarczalny fixture,
registry snapshotu i niezależny checker. [Importer 03.4](source-snapshot-import.md)
sprawdza typed Parquet i publikuje immutable source. [Curated 03.5](curated.md)
normalizuje fakty, mapuje lokalizacje, zachowuje kwarantannę i odczyt as-of.
[Forecasting 04.1](forecasting.md) definiuje zadanie, kalendarz i granice wiedzy;
[odbiór](evidence/04-01-calendar.md) potwierdza lokalne testy i smoke.
[Panel i cechy 04.2](forecast-features.md) zachowują aktywny kalendarz,
braki/zera, kategorię i znane plany oraz przypiętą historię dla wszystkich horyzontów.
[Manifesty i split 04.3](forecast-manifests.md) opisują dojrzałe etykiety,
pełne coverage i preprocessing dopasowany wyłącznie na train.
[Baseline'y i evaluator 04.4](forecast-baselines.md) zachowują wspólne klucze,
wybierają na validation i raportują poprawne MAE/WAPE.
[Modele RF i HGB 04.5](forecast-models.md) mają pełny train-only pipeline,
wspólny evaluator i ograniczony trening; wybór pozostaje diagnostyczny.
[Backtesting 04.6](forecast-backtesting.md) wyznacza chronologiczne foldy,
kontroluje dojrzałość etykiet i wspólne klucze oraz raportuje pooled MAE/WAPE.
[Metryki i niepewność 04.7](forecast-quality.md) dodają przekroje, bias,
kalibrację przedziałów i jawne bramki jakości bez promocji modelu.
[Plikowy run 04.8](forecast-run.md) utrwala komplet dowodów do AI 05.
[Rzeczywisty lokalny przepływ finalnego v12](evidence/05-v12-real-serving.md)
jest odebrany: pełny import, trzy udane trwałe zadania / 42 wiersze, API,
restart i rollback. Świeży mały snapshot daje 14/14 `current` po restarcie.
AI 05 ma scalone PR-y i zielone Required CI obu mainów;
[końcowy odbiór publikacji](https://github.com/Oskar-Stachowski/retailops-cloud-native-platform/blob/main/docs/evidence/ai/05/final/README.md)
zastępuje wcześniejsze informacje o oczekiwaniu na CI.
[Adapter v12](mlflow-v12-evidence.md), [loader offline](forecast-v12-runtime.md),
[kwalifikacja inference](forecast-v12-release.md) i [lifecycle v12](mlflow-v12-lifecycle.md)
opisują przygotowanie obsługi nowego eksportu oraz osobne bramki dopuszczenia.
[Kolejka i worker v12](forecast-v12-worker.md) dodają przypięte zadania,
dzielenie batchu i pełny receipt obliczeń przed publikacją.
[Publikacja i odczyt v12](forecast-v12-publication.md) zachowują medianę,
średnią, przedział i baseline oraz ograniczają dane do scope użytkownika.
[API zadań v12](forecast-v12-jobs-api.md) przyjmuje trwałe zlecenia,
pokazuje stan i historię prób oraz rozróżnia obliczenie i publikację prognoz.
[Katalog modeli i oryginalne oceny v12](forecast-v12-metadata.md) udostępniają
wersje i raporty z kontrolą całego zakresu kampanii oraz odczytem po restarcie.
[Backup i odtworzenie v12](forecast-v12-backup.md) zachowują obie bazy
PostgreSQL, pliki MLflow, zadania oraz niedokończone decyzje rejestracji.
[Inventory snapshot 1.1 — AI 06.6b.2c.1](reference/inventory-snapshot-11.md)
rozszerza typed import o source 2.7, native ledger i private qualification.
[Odbiór](evidence/06-inventory-complete.md) obejmuje curated 1.1 i oba profile.
[Wspólny lifecycle stockout/v12](reference/stockout-lifecycle.md) i
[odbiór podstawy](evidence/08-18-stockout-lifecycle-foundation.md) wiążą
zatwierdzenia oraz wersje; rzeczywista integracja registry i SQL jest oczekująca.
[Późniejsze źródła i chronione checkpointy](reference/stockout-future-sources.md)
oraz [odbiór 08.17](evidence/08-17-stockout-remote-checkpoints.md) przygotowują
trzy nowe światy bez ponownego uczenia i oceny końcowej.
[Etykiety nowych braków AI 08](reference/stockout-labels.md) rozdzielają
nowy brak, istniejący brak i niepełne okna obserwacji.
[Niezależna ocena i karta partycji](reference/stockout-independent-qualification.md)
oraz [odbiór](evidence/08-15-stockout-independent-development.md) rozdzielają
dopasowanie sigmoidu od późniejszej oceny; zapisują także niezaliczone bramki.
[Lokalny odbiór etykiet](evidence/08-01-stockout-labels.md) obejmuje prywatny
handoff AI 06, niezależne odtworzenie ledgeru i zainstalowany wheel.
[Prywatne partycje etykiet 2.0](reference/stockout-label-partitions.md)
i [odbiór](evidence/08-09-stockout-label-partitions.md) dodają magazyn jednej
fizycznej serii, mały Parquet i iterator po pełnym replay.
[Cechy PIT i podział w czasie](reference/stockout-features.md) obejmują
znany zapas, obserwowaną sprzedaż, znane plany dostaw i wykluczanie
okien przekraczających granice train/tune/calibration/test.
[Odbiór 102 dni](evidence/08-02-stockout-features.md) wiąże 1632 origin
z pełnym odtworzeniem cech i licznikami wyłączeń.
[Historyczny forecast upstream](reference/stockout-upstream.md) dodaje
prognozę stałego baseline na granicy wiedzy każdego dnia oraz identyczne
wejścia wariantów z tą cechą i bez niej; [odbiór](evidence/08-03-stockout-upstream.md)
obejmuje pokrycie prognozą wszystkich dopuszczonych okien splitu.
[Ograniczone przygotowanie upstream 2.0](reference/stockout-upstream-storage.md)
wiąże cechy 2.2, jednorazowy panel faktów i chronologiczne części Parquet.
[Odbiór tej samej próbki](evidence/08-10-stockout-upstream-storage.md)
porównuje wszystkie prognozy, comparison i sześć modeli development.
[Temporalne połączenie partycji](reference/stockout-temporal-storage.md)
łączy comparison i membership w ograniczonych porcjach oraz przekazuje
wyłącznie dojrzały development do treningu. [Odbiór](evidence/08-11-stockout-temporal-storage.md)
porównuje rzeczywisty Parquet, wybrane wejścia i sześć modeli z v1.
[Porównanie LR/HGB na development](reference/stockout-training.md) oddziela
train, wybór na tune i dopasowanie sigmoid na calibration, z wejściami
z forecastem i bez niego oraz kontrolą cenzorowania sprzedaży.
[Odbiór modeli](evidence/08-04-stockout-models.md) ujawnia ograniczenia
małej próby, metryki rankingowe i ilustracyjne capacity, bez final test.
[Karta modelu i diagnostyka cech](reference/stockout-model-card.md) dodaje
współczynniki LR, znaczenie grup wejść na tune i faktyczny kontekst PIT.
[Odbiór karty](evidence/08-05-stockout-card.md) zachowuje granice development;
[projekt większego profilu](reference/stockout-profile-resources.md) określa
partycjonowanie i pomiary potrzebne przed dalszą generacją.
[Partycje cech v2](reference/stockout-partitions.md) dodają ograniczony
zapis Parquet i indeks faktów znanych w origin; [odbiór 102 dni](evidence/08-06-stockout-partitions.md)
zachowuje każde pole 1632 punktów oraz wyniki modeli development.
[Odczyt faktów z dysku 2.1](reference/stockout-disk-facts.md) dodaje
jednorazowy, ograniczony magazyn SQLite i cache jednej fizycznej serii.
[Odbiór](evidence/08-07-stockout-disk-facts.md) zachowuje wszystkie punkty,
Parquet i modele, z osobnym pomiarem czasu i pamięci w świeżych procesach.
[Indeks historii 2.2](reference/stockout-history-index.md) dodaje sumy
prefiksowe ledgeru i dzienne grupowanie popytu znanego w origin.
[Odbiór](evidence/08-08-stockout-history-index.md) porównuje tę samą próbkę
z wcześniejszą projekcją oraz zachowuje granice całego AI 08.
[Korekta jakości](forecast-remediation.md) dodaje wersjonowaną recepturę
validation-only i ponowną ocenę na późniejszych development holdoutach.
[Uprawnienia API](access-control.md) i [odbiór](evidence/01-access.md) opisują
zweryfikowane poświadczenia, scope i bezpieczne uruchomienie.
[Lokalny stos DB/API/MLflow](local-stack.md) opisuje persistence i migracje.
[Magazyn MLflow AI 05.1](mlflow-store.md) opisuje backup, restore i retencję.
[Import evidence AI 05.2](mlflow-evidence.md) opisuje historyczny run bez
rejestracji modelu i [odbiór](evidence/05-02-import.md).
[Review i odrzucenie AI 05.3a](mlflow-registry.md) dokumentują lokalny
rejestr bez wersji oraz [audyt decyzji](evidence/05-03-review.md).
[Registry i recovery AI 05.3b](mlflow-lifecycle.md) opisują wersje, aliasy,
niezależny audyt i [odbiór mechaniki](evidence/05-03-lifecycle.md).
[Wspólny backup AI/MLflow 05.3c](lifecycle-backup.md) opisuje blokadę zapisów,
wznowienie i [odtworzenie do nowego projektu](evidence/05-03-store.md).
[Kolejka i worker AI 05.4a](forecast-worker.md) opisują trwałe runy, lease,
retry i [odbiór awarii](evidence/05-04-queue.md) na izolowanych fixture.
[Wejście i loader AI 05.5a](forecast-runtime.md) opisują zgodność nowego
pakietu z zamrożonym modelem i [odbiór adapterów](evidence/05-05-runtime.md),
bez publikacji prognoz ani zatwierdzenia jakości AI 04.
[Trwałe wejścia i supervisor AI 05.5b](forecast-input-store.md) opisują
prywatną rejestrację, limity procesu i [odbiór PostgreSQL](evidence/05-05-input-store.md).
[Atomowa publikacja AI 05.6](forecast-publication.md) opisuje przyjęcie profili,
ograniczony batch i transakcję manifestu, partycji oraz pointera.
[Bieżący odbiór](evidence/05-06-publication.md) podaje kontrolę i braki.
[Odczyt prognoz AI 05.7a](forecast-read.md) opisuje scope, stabilną paginację,
kontrolę kompletności i zachowawczą freshness. [Odbiór](evidence/05-07-read.md)
wiąże realne HTTP/PostgreSQL z jawnymi ograniczeniami jakości.
[Katalog modeli i wersji AI 05.7b](model-catalog.md) opisuje metadane ograniczone
do publikacji w scope użytkownika, bez globalnych ocen i deklaracji wdrożenia.
[Odbiór katalogu](evidence/05-07-catalog.md) podaje testy i pozostałe bramki.
[Historyczne oceny AI 05.7c](evaluations.md) udostępniają MAE/WAPE wyłącznie
w zakresie całego raportu. [Odbiór](evidence/05-07-evaluations.md) wiąże
odtworzone metryki z archiwum i rzeczywistym HTTP/PostgreSQL.
[Watermark i świeżość AI 05.7d](forecast-freshness.md) opisują `current/stale/unknown`,
deklarację kompletności, cutoff i zgodność starszych outputów.
[Dowody persistence](evidence/01-persistence.md) pokazują rzeczywiste próby awarii.
[Instrukcja HTTP](http-service.md) opisuje lokalny serwis i granice dostępu.
[Weryfikacja HTTP](evidence/01-http.md) i [fundamentu](evidence/01-foundation.md)
podają faktyczny zakres prób. [Odbiór zdalnego CI](evidence/01-remote-ci.md)
potwierdza kontrolę PR oraz push na main i ochronę obu repozytoriów.
[Korpus wiedzy](knowledge-corpus.md) opisuje pierwszy zakres etapu 11,
kandydacki rejestr obu repo i walidację źródeł Git.
[Dowody korpusu](evidence/11-corpus.md) podają pomiar deterministyczności,
testy negatywne i granice tego zakresu.
[Parser i chunker](knowledge-chunks.md) opisuje budowę fragmentów, ich tożsamość
i cytaty do przypiętych rewizji Git.
[Odbiór chunków](evidence/11-chunks.md) potwierdza zmiany/usunięcia źródeł,
pełną mapę fragmentów i odtwarzalność rzeczywistego korpusu.
[Fake embeddings i kandydacki indeks](knowledge-index.md) opisują przypiętą
przestrzeń, cache treści, kontrolę wymiaru oraz transakcyjny zapis w pgvector.
[Odbiór indeksu](evidence/11-index.md) potwierdza 433 testy i realny smoke
na świeżej bazie oraz zapis pełnego korpusu 302 fragmentów.
[Lifecycle indeksu](knowledge-lifecycle.md) opisuje jawne zgody, bramki jakości,
atomową aktywację testową, przypinanie wersji i rollback.
[Odbiór lifecycle](evidence/11-lifecycle.md) potwierdza 463 testy, współbieżność,
rollback i zachowanie pełnego pin po restartach na świeżej bazie.
[Retrieval i golden set](knowledge-retrieval.md) opisują exact cosine, kontrolę
scope, statusy, live deny i wersjonowany golden set (aktualnie 44 pytania).
[Odbiór retrieval](evidence/11-retrieval.md) podaje 498 testów, rzeczywisty PG/HTTP,
historyczny raport fake.
[Administracja indeksami](knowledge-administration.md) opisuje osobny grant,
trwałe runy, zatwierdzone snapshoty, worker i odczyt bieżącego indeksu.
[Odbiór administracji](evidence/11-administration.md) potwierdza 542 testy,
rzeczywisty HTTP/PG, wznowienie workera, idempotencję i trwałość runów.
[Kontrola podobnych treści](knowledge-review.md) opisuje raport dokładnych/near
powtórzeń, pełną listę referencji i limity bez automatycznego usuwania lub zgody.
[Odbiór podobieństwa](evidence/11-similarity.md) podaje kontrolę 20 dokumentów/
302 fragmentów, 573 testy oraz niezależne porównanie wszystkich par.
[Odświeżenie źródeł i etykiet](knowledge-sources.md) opisuje przypięte snapshoty,
scope twierdzeń i binding obu list golden sections przed ewaluacją.
[Odbiór źródeł](evidence/11-sources.md) podaje 29 dokumentów/451 fragmentów,
44 pytania, 578 testów i identyczny indeks odtworzony z czystego checkoutu.
Aktualny raport fake nie otwiera aktywacji.
[Kontrola przed kwalifikacją](knowledge-qualification.md) wiąże konfiguracje,
etykiety, raport oraz decyzje i zwraca jawną listę blokad w manifestach.
[Odbiór kwalifikacji](evidence/11-qualification.md) potwierdza 611 testów,
identyczny manifest z czystego checkoutu, odtwarzanie wyników
i brak aktywacji nawet dla idealnego fake z oboma zgodami.

[Zatwierdzone profile golden](knowledge-golden-jobs.md) wiążą zgody właściciela,
etykiety i progi. Worker zachowuje pełny raport także po niezaliczonym progu.
[Odbiór profili](evidence/11-golden-jobs.md) opisuje testy oraz realne PG/HTTP.

## Mapa repo

| Lokalizacja | Bieżąca zawartość |
|---|---|
| `src/retailops_ai/` | CLI/settings oraz warstwy api/domain/pipelines/adapters lokalnego serwera |
| `contracts/` | OpenAPI/CLI, access/intelligence/knowledge oraz source_snapshot, curated i forecast/v1 |
| `knowledge/` | Przypięty rejestr RAG, konfiguracje, golden set oraz osobne zgody właściciela |
| `tests/` | Konfiguracja, HTTP i socket, awarie, korelacja, kontrakty oraz bramki CI |
| `scripts/` | Kontroler Compose, rzeczywisty smoke, kontrakty i bramki workflow |
| `.github/` | Required CI, szablon PR, wskaźnik do security |
| `docs/` | Aktualne zasady, status, ADR-y i evidence |
| `compose.yaml`, `infra/` | Lokalny stos oraz bootstrap odrębnych baz i ról |
| `uv.lock` | Jedyna blokada zależności projektu |

Dokumentację dla ludzi utrzymujemy w `docs/`; root README jest wejściem,
a licencja i konfiguracje narzędzi pozostają przy kodzie. Usuwamy rozwiązane
wnioski i wykonane zadania z aktywnej listy. Evidence opisuje pomiar i jego
ograniczenia; nie jest listą zakończonych zadań ani deklaracją wdrożenia.

- [Pełna bramka cross-repo AI 03.6](evidence/03-06-cross-repo.md) — wspólny odbiór RetailOps i AI, publikacja oraz kolejne etapy.
- [Lokalny odbiór zaakceptowanego v12 i import APFS](forecast-v12-development.md).
