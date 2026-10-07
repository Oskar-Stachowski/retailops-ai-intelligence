# AI 08.21 — zamrożona kampania i verifier stockout MLflow

Na 2026-10-05 UTC sześć źródeł jest rzeczywiście przygotowanych. Końcowe wyniki
pozostają nieotwarte. [Dokładna propozycja](../reference/stockout-final-campaign-v1.md)
i jej manifest przypinają 9296 eligible TEST: matching 477/473/482 i future
2627/2609/2628 dla seedów 42/137/2026. SHA ZIP, checkpoint i resource oraz wszystkie
ID rodziców zostały odczytane z rzeczywistych artefaktów. Nie ma ponownej generacji.

Campaign ID: stockout-final-campaign-sha256-28bcfec3f32039a0cdfb713cee78230825026a2d9202c24e5da18e4bd8c4274d.
Osobna zgoda właściciela na tę kampanię i proponowaną politykę jest nadal oczekiwana.
Plik zgody nie został utworzony. Workflow końcowy uruchamia się dopiero po jego
wprowadzeniu; sam commit workflow nie otwiera etykiet.

Evaluator odmawia dostępu bez zgodnej zgody, kodu i locka. Weryfikuje pełne
archiwa/rodziców, PIT i mature labels, nie fituje modelu/kalibratora, utrzymuje
jedną globalną kolejkę origin przed projekcjami segmentów. Raportuje osobno
światy/seedy, AP/prevalence, Brier, reliability/coverage, raw comparison,
recall/precision/cost, scenariusze i kontrole. Brak obu klas jest not_evaluable.
Aggregate wymaga wszystkich sześciu kompletnych raportów i zachowuje niezaliczone
bramki. Rzeczywista ocena końcowa oraz native/wheel replay są jeszcze niewykonane.

Supervisor zdalnej oceny ma limit 1280 MiB worker-tree RSS, 640 MiB scratch,
2700 s wall i 6 GiB wolnego miejsca. Lokalny pipeline wymaga 50 GiB rezerwy;
nie uruchomiono lokalnej generacji ani nie dotknięto stosu innej sesji.
Archiwa źródeł i receipts oceny mają być zachowane w artefaktach GitHuba 30 dni.

[Paczka serving](../reference/stockout-serving-capsule.md) ma pełny byte verifier,
deterministyczny smoke replay, signature i oddzielny stockout MLflow registry/
publisher. Testy korzystają z kompletnych syntetycznych paczek; nie są odbiorem
produkcyjnej jakości. Checker real PostgreSQL/MLflow oraz backup został rozszerzony,
ale jego nowy rzeczywisty odbiór CI nadal jest wymagany.

CI b0b1303e naprawiło startup API i przeszło pełne checks/secrets. Persistence
odrzuciło zapis nowego stockout run. Diagnostyka 37254828389 wskazała
UndefinedFunction w queue.py:182 podczas testu check_stockout_jobs.py:112.
Migracja 0021 miała nieogrupowane operandy JSON przy @>/<@. Dodano nawiasy;
kontrole zakresu pozostają. Zasady kolejności operatorów potwierdza
[dokumentacja PostgreSQL](https://www.postgresql.org/docs/current/sql-syntax-lexical.html#SQL-PRECEDENCE).
Oddzielny test poprawki 37255271458 jest jeszcze w toku.

Weryfikacja lokalna:

- 118 focused tests passed w 8,46 s: final archive/campaign/download/scenarios,
  byte capsule/registry, batch/lifecycle i API/startup.
- Ruff oraz format 741 plików zaliczone.
- Konfigurowany Mypy src+scripts: 447 source files bez błędów.
- check_repository.py: linki dokumentacji i required CI poprawne.
- Real checker lifecycle zbiera się poprawnie; real SQL/MLflow/backup wymaga CI.

Otwarte: zgoda i niezależna jakość sześciu światów, real receipt nowego MLflow/SQL,
worker, priorytety w API, finalna karta/kwalifikacja, zasoby/integracja, osobny
odbiór i promocja, merge oraz Required CI main. **Cały AI 08 pozostaje not ready.**
